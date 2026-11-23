"""The judge: grade one answer against the ground truth, blind to which system wrote it.

Pointwise, not pairwise: each answer is graded on its own against the reference
results and the rubric, so the order in which systems are shown cannot favour
one, and a judge that prefers longer answers has to justify each point against
the numbers rather than against another answer.

The judge model is deliberately none of the compared ones (default Claude Opus
4.5; the systems use Haiku 4.5, Sonnet 4.6 and Opus 4.6), and it grades against
reference tables computed outside ALIBI — the two together are what keeps a
model from grading its own family's style instead of the facts.
"""

from __future__ import annotations

from typing import Any

from app.core import bedrock
from app.orchestrator.client import extract_json

JUDGE_SYSTEM = """You grade answers written by a business-intelligence assistant for a non-technical user.

You are given the user's question, GROUND TRUTH tables computed directly from the user's data, a RUBRIC
of the points a complete answer must make, and the ANSWER (its text, and the tables shown beside it).

For each rubric point decide:
- "correct": the answer's text states it correctly, or a table shown beside the text displays exactly
  that fact without the user having to compute anything;
- "partial": stated, but incomplete or imprecise (e.g. right direction, wrong or missing figure);
- "missing": not addressed;
- "wrong": addressed, but contradicted by the ground truth.

Then list every claim in the answer's TEXT that the ground truth contradicts (wrong figures, wrong
rankings, wrong causal or comparative statements). Rounding is not an error. Statements the ground
truth cannot check are not errors unless they are presented as facts about the data.

Finally rate usefulness from 1 to 5: how well the answer serves a business user who asked this
question — 5 answers it completely and adds relevant insight, 3 answers it but superficially, 1 does
not answer it.

Reply with JSON only:
{"rubric": [{"point": 1, "verdict": "correct|partial|missing|wrong", "note": "..."}],
 "wrong_claims": ["..."],
 "usefulness": 1,
 "comment": "one sentence"}"""

_VALUE = {"correct": 1.0, "partial": 0.5, "missing": 0.0, "wrong": 0.0}


def _table(rows: list[dict[str, Any]], limit: int = 40) -> str:
    if not rows:
        return "(no rows)"
    columns = list(rows[0])
    lines = [" | ".join(columns)]
    lines += [" | ".join(str(row.get(c, "")) for c in columns) for row in rows[:limit]]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more rows")
    return "\n".join(lines)


def prompt(question: str, truth: list[list[dict[str, Any]]], rubric: tuple[str, ...], answer: dict[str, Any]) -> str:
    parts = [f"QUESTION: {question}", "", "GROUND TRUTH:"]
    for i, rows in enumerate(truth, 1):
        parts += [f"[table {i}]", _table(rows), ""]
    parts.append("RUBRIC:")
    parts += [f"{i}. {point}" for i, point in enumerate(rubric, 1)]
    parts += ["", "ANSWER TEXT:", answer["text"] or "(empty)", "", "TABLES SHOWN BESIDE THE TEXT:"]
    for i, rows in enumerate(answer.get("tables") or [], 1):
        parts += [f"[shown {i}]", _table(rows, limit=15), ""]
    return "\n".join(parts)


def grade(
    model_id: str,
    question: str,
    truth: list[list[dict[str, Any]]],
    rubric: tuple[str, ...],
    answer: dict[str, Any],
) -> dict[str, Any]:
    reply = bedrock.converse(
        model_id,
        [{"role": "system", "content": JUDGE_SYSTEM},
         {"role": "user", "content": prompt(question, truth, rubric, answer)}],
        region="us-east-1",
        timeout_seconds=120,
        max_tokens=1_500,
        temperature=0.0,
    )
    verdict = extract_json(reply.text)
    points = verdict.get("rubric") or []
    scores = [_VALUE.get(str(p.get("verdict", "")).lower(), 0.0) for p in points]
    verdict["rubric_score"] = sum(scores) / len(rubric) if rubric else 0.0
    verdict["judge_tokens"] = [reply.prompt_tokens, reply.completion_tokens]
    return verdict


__all__ = ["JUDGE_SYSTEM", "grade", "prompt"]
