"""Prompts for the two fine-tuned tasks, shared by training and serving.

Training (`model_a/train_qlora.py`), data preparation (`model_c/prepare_data.py`)
and the endpoint handler (`model_a/inference.py`) all build prompts through this
module. A fine-tuned adapter only works on the exact text shape it was trained
on, so having one definition is what stops the three from drifting apart.

Both tasks are served by ONE base model carrying two LoRA adapters
(`sql` = Module A, `chart` = Module C); `TASKS` is the list of adapter names.

The SQL strings are byte-for-byte what the Module A smoke runs trained on.
The chart user block mirrors `backend/app/orchestrator/nodes/visualize.py`'s
`_prompt_block` — question, row count, a COLUMNS profile (type + distinct count +
business label) and a few sample rows — so the backend's adapter call can build
its input from the same pieces it already has.
"""

import json
import re
from typing import Any, Optional

TASKS = ("sql", "chart")

# ── Module A: Text-to-SQL ─────────────────────────────────────────────────────

SQL_SYSTEM_PROMPT = (
    "You are an expert SQL assistant. Given a database schema and a natural language question, "
    "generate a syntactically correct and minimal SQL SELECT query. "
    "Return only the SQL query, nothing else."
)


def sql_user(schema: str, question: str) -> str:
    return f"### Schema:\n{schema}\n\n### Question:\n{question}\n\n### SQL:"


# ── Module C: chart specification ─────────────────────────────────────────────

CHART_SYSTEM_PROMPT = (
    "You are Module C of a business intelligence assistant. Given the shape of a query result "
    "(a column profile and a few sample rows) and the question that produced it, choose the single "
    "most readable chart and reply with JSON only: "
    '{"chart_type": "bar|line|area|point|arc|rect|none", "spec": {<Vega-Lite v5 specification>}}.\n'
    "Rules: use only the marks bar, line, area, point, arc, rect; every \"field\" must be a column "
    "name from COLUMNS, spelled exactly; never include data, values or url; declare a \"type\" on "
    "every channel (use the type from COLUMNS); a quantity over time is a line, a quantity across "
    "categories is a bar, two quantities is a point, parts of a whole is an arc with theta, a "
    "spread is a bar with bin; give every axis and legend a plain-words \"title\" from the business "
    "label and the chart a short \"title\"; no config, width, height or colours. When a single "
    "value, a single row or a single column is the whole result, reply "
    '{"chart_type": "none", "spec": null}.'
)

_AGGREGATE = re.compile(r"^(count|sum|avg|min|max)\((.*)\)$", re.IGNORECASE)
_AGG_WORD = {"count": "Count", "sum": "Total", "avg": "Average", "min": "Minimum", "max": "Maximum"}


def humanize(name: str) -> str:
    """Business label for a column name: `total_rev` -> `Total Rev`, `count(*)` -> `Number of rows`."""
    agg = _AGGREGATE.match(name.strip())
    if agg:
        fn, inner = agg.group(1).lower(), agg.group(2).strip()
        if inner == "*":
            return "Number of rows"
        return f"{_AGG_WORD[fn]} of {humanize(inner).lower()}"
    return re.sub(r"[_\s]+", " ", name.strip()).strip().title()


def chart_user(
    question: str,
    row_count: int,
    columns: list[dict[str, Any]],
    sample_rows: list[dict[str, Any]],
    truncated: bool = False,
) -> str:
    """`columns` items: {"name", "dtype", "cardinality", optional "label"}."""
    lines = []
    lines.append(f"The user asked: {question}" if question.strip() else "The user ran a report of their own.")
    lines.append(f"Rows in the result: {row_count}")
    if truncated:
        lines.append("The result was cut off; the sample below is the beginning of it.")
    lines.append("")
    lines.append("COLUMNS:")
    for c in columns:
        label = c.get("label") or humanize(c["name"])
        lines.append(f"- {c['name']} ({c['dtype']}, {c['cardinality']} distinct) — business label: {label}")
    lines.append("")
    lines.append(f"SAMPLE ROWS ({len(sample_rows)} of {row_count}):")
    lines.append(json.dumps(sample_rows, ensure_ascii=False, default=str))
    return "\n".join(lines)


def chart_target(chart_type: str, spec: Optional[dict]) -> str:
    return json.dumps({"chart_type": chart_type, "spec": spec}, ensure_ascii=False, separators=(",", ":"))


# ── ChatML rendering (Qwen family) ────────────────────────────────────────────

def chatml(system: str, user: str, assistant: Optional[str] = None) -> str:
    """Full training text when `assistant` is given; otherwise the inference prompt
    ending on the open assistant turn."""
    text = f"<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n<|im_start|>assistant\n"
    return text if assistant is None else f"{text}{assistant}<|im_end|>"


def render_training_text(task: str, sample: dict) -> str:
    """One dataset row -> the text SFTTrainer trains on.

    sql   rows: {"schema", "question", "sql"}
    chart rows: {"prompt", "target"}   (built by model_c/prepare_data.py)
    """
    if task == "sql":
        return chatml(SQL_SYSTEM_PROMPT, sql_user(sample.get("schema", ""), sample.get("question", "")),
                      sample.get("sql", ""))
    if task == "chart":
        return chatml(CHART_SYSTEM_PROMPT, sample["prompt"], sample["target"])
    raise ValueError(f"unknown task {task!r}; expected one of {TASKS}")


def render_inference_prompt(task: str, payload: dict) -> str:
    if task == "sql":
        return chatml(SQL_SYSTEM_PROMPT, sql_user(payload.get("schema", ""), payload.get("question", "")))
    if task == "chart":
        user = chart_user(
            payload.get("question", ""),
            int(payload.get("row_count", len(payload.get("rows", [])))),
            payload["columns"],
            payload.get("rows", [])[:5],
            bool(payload.get("truncated", False)),
        )
        return chatml(CHART_SYSTEM_PROMPT, user)
    raise ValueError(f"unknown task {task!r}; expected one of {TASKS}")
