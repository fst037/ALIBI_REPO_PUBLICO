"""Aggregate an evaluation run into a Markdown report.

Prices are on-demand list prices per million tokens **as assumed here** —
confirm them against the Bedrock price list before quoting a cost in the thesis.
They only scale the token counts, which are measured.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

#: USD per 1M tokens (input, output). Assumed list prices; see the module docstring.
PRICES = {
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": (1.0, 5.0),
    "us.anthropic.claude-sonnet-4-6": (3.0, 15.0),
    "us.anthropic.claude-opus-4-6-v1": (5.0, 25.0),
    "us.anthropic.claude-opus-4-5-20251101-v1:0": (5.0, 25.0),
}
SYSTEMS = ("oneshot", "quick", "deep")
KINDS = ("lookup", "multi", "explore")


def cost(usage: dict[str, list[int]]) -> float:
    total = 0.0
    for model, (_calls, tokens_in, tokens_out) in usage.items():
        price_in, price_out = PRICES.get(model, (0.0, 0.0))
        total += tokens_in / 1e6 * price_in + tokens_out / 1e6 * price_out
    return total


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def _fmt(value: float | None, digits: int = 2, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def render(runs: list[dict[str, Any]], judgments: list[dict[str, Any]], name: str) -> str:
    verdicts = {(j["question"], j["system"]): j for j in judgments}
    by_system: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_system[run["system"]].append(run)
    systems = [s for s in SYSTEMS if s in by_system]

    lines = [f"# Harness evaluation — {name}", ""]
    lines += [
        f"{len({r['question'] for r in runs})} questions × {len(systems)} systems = {len(runs)} answers, "
        f"{len(judgments)} judged. `oneshot` is the product before the agent harness (one query + the narrative); "
        "`quick` and `deep` are the agent harness. Rubric score: share of rubric points the judge found "
        "correct (partial = ½) — over answered questions, and over all (a failure scores 0). Grounded: "
        "share of figures in the answer text found in some executed result. Cost uses the assumed list "
        "prices in `eval/report.py`.",
        "",
        "## Overall",
        "",
        "| | " + " | ".join(systems) + " |",
        "|---|" + "---|" * len(systems),
    ]

    def row(label: str, fn) -> None:  # noqa: ANN001
        lines.append(f"| {label} | " + " | ".join(fn(by_system[s]) for s in systems) + " |")

    def answered(rs):  # noqa: ANN001, ANN202
        return [r for r in rs if r["status"] == "done"]

    def judged(rs):  # noqa: ANN001, ANN202
        return [verdicts[(r["question"], r["system"])] for r in answered(rs) if (r["question"], r["system"]) in verdicts]

    row("Answered", lambda rs: f"{len(answered(rs))}/{len(rs)}")
    row("Failed / guard", lambda rs: f"{sum(r['status'] == 'failed' for r in rs)} / {sum(r['status'] == 'guard' for r in rs)}")
    row("Rubric score (answered)", lambda rs: _fmt(_mean([j["rubric_score"] for j in judged(rs)])))
    row("Rubric score (all)", lambda rs: _fmt(
        sum(j["rubric_score"] for j in judged(rs)) / len(rs) if rs else None))
    row("Usefulness (1–5)", lambda rs: _fmt(_mean([float(j.get("usefulness") or 0) for j in judged(rs)])))
    row("Wrong claims per answer", lambda rs: _fmt(_mean([float(len(j.get("wrong_claims") or [])) for j in judged(rs)])))
    row("Grounded figures", lambda rs: _fmt(
        100 * sum(r.get("grounded_figures", 0) for r in answered(rs)) / max(1, sum(r.get("figures", 0) for r in answered(rs))),
        1, "%"))
    row("Figures per answer", lambda rs: _fmt(_mean([float(r.get("figures", 0)) for r in answered(rs)]), 1))
    row("Latency median (s)", lambda rs: _fmt(_pct([r["seconds"] for r in rs], 0.5), 1))
    row("Latency p90 (s)", lambda rs: _fmt(_pct([r["seconds"] for r in rs], 0.9), 1))
    row("Queries executed", lambda rs: _fmt(_mean([float(r.get("queries_executed", 0)) for r in rs]), 1))
    row("Findings shown", lambda rs: _fmt(_mean([float(r["answer"]["findings"]) for r in answered(rs)]), 1))
    row("Charts shown", lambda rs: _fmt(_mean([float(r["answer"]["charts"]) for r in answered(rs)]), 1))
    row("Cut short by a cap", lambda rs: str(sum(1 for r in rs if r.get("cut_short"))))
    row("Cost per answer (USD)", lambda rs: _fmt(_mean([cost(r.get("usage") or {}) for r in rs]), 4))

    lines += ["", "## By kind of question", "", "Rubric score (answered) / usefulness.", "",
              "| kind | n | " + " | ".join(systems) + " |", "|---|---|" + "---|" * len(systems)]
    for kind in KINDS:
        cells = []
        n = 0
        for s in systems:
            rs = [r for r in by_system[s] if r["kind"] == kind]
            n = max(n, len(rs))
            js = judged(rs)
            cells.append(f"{_fmt(_mean([j['rubric_score'] for j in js]))} / "
                         f"{_fmt(_mean([float(j.get('usefulness') or 0) for j in js]), 1)}")
        lines.append(f"| {kind} | {n} | " + " | ".join(cells) + " |")

    lines += ["", "## Per question", "", "Rubric score, or the failure code.", "",
              "| question | kind | " + " | ".join(systems) + " |", "|---|---|" + "---|" * len(systems)]
    for qid in sorted({r["question"] for r in runs}, key=lambda q: (q[0], int(q[1:]))):
        cells, kind = [], ""
        for s in systems:
            run = next((r for r in by_system[s] if r["question"] == qid), None)
            if run is None:
                cells.append("")
                continue
            kind = run["kind"]
            if run["status"] != "done":
                cells.append(f"✗ {(run.get('error') or {}).get('code', run['status'])}")
            elif (qid, s) in verdicts:
                v = verdicts[(qid, s)]
                cells.append(f"{v['rubric_score']:.2f} ({run['seconds']:.0f}s)")
            else:
                cells.append(f"unjudged ({run['seconds']:.0f}s)")
        lines.append(f"| {qid} | {kind} | " + " | ".join(cells) + " |")

    wrong = [(j["question"], j["system"], claim) for j in judgments for claim in (j.get("wrong_claims") or [])]
    if wrong:
        lines += ["", "## Claims the judge found wrong", ""]
        lines += [f"- **{q} {s}**: {claim}" for q, s, claim in sorted(wrong)]
    return "\n".join(lines) + "\n"


__all__ = ["PRICES", "cost", "render"]
