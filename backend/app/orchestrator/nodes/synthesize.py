"""`synthesize` — write the answer to an investigation across all of its results.

The agent harness's last model call. Where
`explain_result` narrates one result, this node gets every result the planner
gathered: the **findings** the user will see (each with a chart or table beside
it) and the **background** steps the planner ran to understand the data. It
returns a summary that answers the question and, when there are several
findings, one sentence for each.

    SynthesisInput ─► prompt ─► model ─► JSON ─► review across all results ─► Synthesis
                                                        │
                                       retry once ◄─────┘ with the critique
                                                        │
                                               still bad ▼
                                                 SynthesisFailed

The guarantees are `explain_result`'s, widened to several results:

* **Every figure is grounded in an executed result** — any of them, findings or
  background — or in a figure the result store computed and showed (a row
  count, a distinct count). Checked on every text, every attempt.
* **No technical vocabulary**, including every column and table name any step
  touched and the result handles themselves.
* **The model never sees SQL.** Results are described by the plain-language
  question the planner asked, and their columns by business labels.

A bigger model than the planner's writes this — Haiku for Quick, Sonnet for Deep —
because it is the one text the user reads; the planner's choice is latency, this
one's is quality.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.orchestrator import client, formatting, validation
from app.orchestrator.results import StoredResult

logger = logging.getLogger("alibi.orchestrator.synthesize")

_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "synthesize_system.md"
_HANDLE = re.compile(r"\br\d+\b")
_BACKGROUND_ROWS = 5
_SUMMARY_SENTENCES = 3
_FINDING_SENTENCES = 2


class SynthesisFailed(Exception):
    """No verified answer could be written for this investigation."""


@dataclass(frozen=True)
class SynthesisInput:
    question: str
    findings: list[StoredResult]
    background: list[StoredResult]
    #: Figures the result store computed and showed a model — grounded too.
    shown_figures: set[float] = field(default_factory=set)
    #: Set when a budget stopped the investigation: what could not be checked.
    cut_short: str | None = None
    column_labels: dict[str, str] = field(default_factory=dict)
    source_label: str | None = None
    deep: bool = False


@dataclass(frozen=True)
class Synthesis:
    summary: str
    #: Handle -> its sentence. Empty when there is a single finding.
    finding_texts: dict[str, str]
    confidence: float
    source: str
    prompt_tokens: int
    completion_tokens: int


def _system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _rows_block(result: StoredResult, labels: dict[str, str], limit: int) -> str:
    sample = [
        {formatting.humanize(c, labels): formatting.display(row.get(c), "en-US") for c in result.columns}
        for row in result.rows[:limit]
    ]
    head = f"{result.handle} — about: {result.question}\nEntries: {result.row_count}"
    if result.truncated or len(sample) < result.row_count:
        head += f" (you are seeing the first {len(sample)}; there are more)"
    return head + "\n" + json.dumps(sample, ensure_ascii=False)


def _data_block(payload: SynthesisInput, max_rows: int) -> str:
    lines = [f"The user asked: {payload.question}"]
    if payload.source_label:
        lines.append(f"This is about: {payload.source_label}")
    lines += ["", f"FINDINGS ({len(payload.findings)}):"]
    lines += [_rows_block(r, payload.column_labels, max_rows) for r in payload.findings]
    if payload.background:
        lines += ["", "BACKGROUND:"]
        lines += [_rows_block(r, payload.column_labels, _BACKGROUND_ROWS) for r in payload.background]
    if payload.cut_short:
        lines += ["", f"The investigation was cut short. Not checked: {payload.cut_short}"]
    return "\n".join(lines)


def _internal_names(results: list[StoredResult]) -> list[str]:
    names: list[str] = []
    for result in results:
        names += result.columns
        try:
            import sqlglot
            from sqlglot import exp

            statement = sqlglot.parse_one(result.sql, read="duckdb")
            names += [t.name for t in statement.find_all(exp.Table) if t.name]
        except Exception:  # noqa: BLE001 - best-effort; the column names still apply
            pass
    return [n for n in dict.fromkeys(names) if n]


def _review(payload: SynthesisInput, summary: str, texts: dict[str, str]) -> list[str]:
    results = payload.findings + payload.background
    rows = [r.rows for r in results]
    common: dict[str, Any] = {
        "question": payload.question,
        "extra_values": payload.shown_figures,
        "internal_names": _internal_names(results),
    }
    problems = validation.review_across(summary, rows, max_sentences=_SUMMARY_SENTENCES, **common)
    if len(payload.findings) > 1:
        for finding in payload.findings:
            text = texts.get(finding.handle, "").strip()
            if not text:
                problems.append(f"Write the sentence for finding {finding.handle}.")
                continue
            problems += validation.review_across(text, rows, max_sentences=_FINDING_SENTENCES, **common)
    for text in [summary, *texts.values()]:
        if _HANDLE.search(text):
            problems.append("Do not mention result handles such as “r2” — they are internal.")
            break
    return problems


def _parse(text: str, payload: SynthesisInput) -> tuple[str, dict[str, str], float] | list[str]:
    try:
        raw = client.extract_json(text)
    except (ValueError, json.JSONDecodeError) as err:
        return [f"Your reply was not valid JSON ({err}). Reply with the JSON object only."]
    summary = raw.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return ["Your reply had no `summary`."]
    texts: dict[str, str] = {}
    wanted = {f.handle for f in payload.findings}
    for item in raw.get("findings") or []:
        if isinstance(item, dict) and item.get("result") in wanted and isinstance(item.get("text"), str):
            texts[item["result"]] = item["text"].strip()
    try:
        confidence = float(raw.get("confidence_score", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if len(payload.findings) <= 1:
        texts = {}
    return summary.strip(), texts, confidence


def synthesize(payload: SynthesisInput) -> Synthesis:
    """Write the answer. Raises `SynthesisFailed` if no attempt passes review."""
    if not payload.findings:
        raise SynthesisFailed("nothing to synthesise")
    settings = get_settings()
    config = client.synthesis_config(payload.deep, settings)
    if not config.is_configured:
        raise SynthesisFailed("no synthesis model configured")

    messages: list[client.Message] = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": _data_block(payload, settings.harness_synthesis_rows)},
    ]
    prompt_tokens = completion_tokens = 0
    problems: list[str] = ["no attempt was made"]
    for attempt, source in enumerate(("model", "model_retry")):
        try:
            completion = client.complete(config, messages, max_tokens=settings.synthesis_max_tokens)
        except client.LlmUnavailable as err:
            logger.error("synthesis model unavailable: %s", err)
            raise SynthesisFailed(str(err)) from err
        prompt_tokens += completion.prompt_tokens
        completion_tokens += completion.completion_tokens

        parsed = _parse(completion.text, payload)
        if isinstance(parsed, list):
            problems = parsed
        else:
            summary, texts, confidence = parsed
            problems = _review(payload, summary, texts)
            if not problems:
                logger.debug(
                    "synthesize | source=%s | findings=%d | tokens=%d/%d | summary=%s",
                    source, len(payload.findings), prompt_tokens, completion_tokens, summary,
                )
                return Synthesis(summary, texts, confidence, source, prompt_tokens, completion_tokens)

        logger.info("synthesis attempt %d rejected: %s", attempt + 1, "; ".join(problems))
        if attempt == 0:
            messages = messages + [
                {"role": "assistant", "content": completion.text},
                {"role": "user", "content": validation.Critique(problems=problems).as_prompt()},
            ]

    raise SynthesisFailed("; ".join(problems))


__all__ = ["Synthesis", "SynthesisFailed", "SynthesisInput", "synthesize"]
