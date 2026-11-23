"""
Module C: score the specs `generate_checkpoints.py` produced, with the BACKEND's own validator.

    python -m model_c.score_predictions --eval data/processed/model_c/eval/eval.jsonl \
        --predictions <dir with predictions_<variant>.jsonl> [--out metrics.json]
    python -m model_c.score_predictions --eval ... --gold      # score the gold targets: must be perfect

"Accepted" means what it means in production: `app.orchestrator.vega.check` passes — a valid Vega-Lite v5
document, only columns the result really has, one of the six chart types. A rejected spec costs the
user the chart (there is no fallback), so it is the first-class failure here, not a footnote.

Per row, in order:
  parse_fail   no JSON object in the reply
  none         the model chose "no chart" (correct when gold is `none`)
  rejected     JSON, but the backend would refuse the spec (schema, unknown field, bad mark)
  accepted     the backend would draw it
Headline numbers: `type_acc` (predicted chart type == gold, over every row; `rejected`/`parse_fail`
count as wrong), `balanced_acc` (mean per-class recall — the eval set is 79% bar, so plain accuracy
mostly measures "say bar"), `accepted` rate, and — for rows whose gold is a chart — `channels_match`
(every channel maps to the gold column), `sort_match` (the sort the gold spec has), `exact` (the whole
spec equals gold apart from titles). Intervals are paired bootstrap 95% CIs over questions.
"""

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "backend"))
from app.orchestrator import vega  # noqa: E402  the production validator, not a copy

_COL = re.compile(r"^- (.+?) \((nominal|quantitative|ordinal|temporal)[,)]")
_TYPES = ["bar", "arc", "line", "point", "none"]
# Gold labels are Vega-Lite marks (what prepare_data writes); the backend's ChartType is a product name
# (arc -> pie, point -> scatter), so the predicted label is read from the mark, folding the aliases.
_MARK_LABEL = {"trail": "line", "circle": "point", "square": "point"}


def mark_label(spec: dict) -> str:
    mark = spec.get("mark")
    mark = mark.get("type") if isinstance(mark, dict) else mark
    return _MARK_LABEL.get(mark, mark)


def columns_of(prompt: str) -> list[str]:
    """Column names from the COLUMNS block of the prompt (names may themselves contain parentheses)."""
    names, inside = [], False
    for line in prompt.splitlines():
        if line.startswith("COLUMNS"):
            inside = True
        elif inside and line.startswith("- "):
            m = _COL.match(line)
            if m:
                names.append(m.group(1))
        elif inside and not line.strip():
            break
    return names


def read_reply(text: str):
    """The first JSON object in the reply (fences and chatter tolerated), or None."""
    text = re.sub(r"```(?:json)?", "", text or "")
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _channels(spec: dict) -> dict:
    enc = spec.get("encoding") or {}
    return {ch: (d.get("field") if isinstance(d, dict) else None) for ch, d in enc.items()}


def _sorts(spec: dict) -> dict:
    enc = spec.get("encoding") or {}
    return {ch: d["sort"] for ch, d in enc.items() if isinstance(d, dict) and "sort" in d}


def _untitled(node):
    if isinstance(node, dict):
        return {k: _untitled(v) for k, v in node.items() if k not in ("title", "$schema")}
    if isinstance(node, list):
        return [_untitled(v) for v in node]
    return node


def score_row(row: dict, raw: str) -> dict:
    gold = json.loads(row["target"])
    gold_type = row["chart_type"]
    out = {"gold_type": gold_type, "outcome": None, "pred_type": "invalid", "accepted": False,
           "channels_match": None, "sort_match": None, "exact": None, "problems": []}

    reply = read_reply(raw)
    if reply is None:
        out["outcome"] = "parse_fail"
        return out
    claimed = str(reply.get("chart_type", "")).strip().lower()
    spec = reply.get("spec")
    if claimed == "none":
        out.update(outcome="none", pred_type="none")
        return out
    if not isinstance(spec, dict) or not spec:
        out.update(outcome="rejected", problems=["no spec"])
        return out

    normalized = vega.normalize(spec)
    problems = vega.check(normalized, columns_of(row["prompt"]))
    if problems:
        out.update(outcome="rejected", problems=problems)
        return out

    out.update(outcome="accepted", accepted=True, pred_type=mark_label(normalized))
    if gold_type != "none":
        g = gold["spec"]
        out["channels_match"] = _channels(normalized) == _channels(g)
        if _sorts(g):
            out["sort_match"] = _sorts(normalized) == _sorts(g)
        out["exact"] = _untitled(normalized) == _untitled(vega.normalize(g))
    return out


def _bootstrap(rows_ok: dict, ref: list, draws: int = 2000) -> dict:
    import numpy as np

    rng = np.random.default_rng(42)
    n = len(next(iter(rows_ok.values())))
    picks = rng.integers(0, n, size=(draws, n))
    mat = {k: np.array(v, dtype=float) for k, v in rows_ok.items()}
    means = {k: v[picks].mean(axis=1) for k, v in mat.items()}
    ci = lambda x: [round(float(np.percentile(x, 2.5)), 4), round(float(np.percentile(x, 97.5)), 4)]  # noqa: E731
    out = {"ci95": {k: ci(m) for k, m in means.items()}, "diff": {}}
    for r in ref:
        if r in means:
            for k, m in means.items():
                if k != r:
                    d = m - means[r]
                    out["diff"][f"{k} - {r}"] = {"mean": round(float(mat[k].mean() - mat[r].mean()), 4),
                                                 "ci95": ci(d), "p_better": round(float((d > 0).mean()), 4)}
    return out


def summarise(rows: list[dict], scored: list[dict]) -> dict:
    n = len(scored)
    gold_t = [s["gold_type"] for s in scored]
    pred_t = [s["pred_type"] for s in scored]
    recall, precision = {}, {}
    for t in _TYPES:
        g = [i for i in range(n) if gold_t[i] == t]
        p = [i for i in range(n) if pred_t[i] == t]
        if g:
            recall[t] = round(sum(pred_t[i] == t for i in g) / len(g), 4)
        if p:
            precision[t] = round(sum(gold_t[i] == t for i in p) / len(p), 4)
    charts = [s for s in scored if s["gold_type"] != "none"]
    acc = [s for s in charts if s["accepted"]]
    sorted_rows = [s for s in acc if s["sort_match"] is not None]
    return {
        "n": n,
        "type_acc": round(sum(g == p for g, p in zip(gold_t, pred_t)) / n, 4),
        "balanced_acc": round(sum(recall.values()) / len(recall), 4),
        "recall": recall, "precision": precision,
        "outcomes": dict(Counter(s["outcome"] for s in scored)),
        "accepted_rate_on_charts": round(len(acc) / len(charts), 4),
        "channels_match_of_accepted": round(sum(s["channels_match"] for s in acc) / max(1, len(acc)), 4),
        "sort_match_of_accepted_sorted": round(sum(s["sort_match"] for s in sorted_rows) / max(1, len(sorted_rows)), 4),
        "exact_of_accepted": round(sum(s["exact"] for s in acc) / max(1, len(acc)), 4),
        "useful_rate": round(sum(s["accepted"] and s["pred_type"] == s["gold_type"] and bool(s["channels_match"])
                                 for s in charts) / len(charts), 4),
        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(Counter(zip(gold_t, pred_t)).items())},
        "top_problems": Counter(re.sub(r"`[^`]*`", "`_`", pr)[:90] for s in scored for pr in s["problems"]).most_common(5),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True)
    ap.add_argument("--predictions")
    ap.add_argument("--gold", action="store_true", help="score the gold targets themselves (self-check)")
    ap.add_argument("--out")
    a = ap.parse_args()

    rows = [json.loads(l) for l in Path(a.eval).read_text(encoding="utf-8").splitlines() if l.strip()]
    variants = {}
    if a.gold:
        variants["gold"] = [r["target"] for r in rows]
    else:
        for f in sorted(Path(a.predictions).glob("predictions_*.jsonl")):
            recs = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
            raws = [""] * len(rows)
            for r in recs:
                raws[r["i"]] = r["raw"]
            variants[f.stem[len("predictions_"):]] = raws

    result, flags = {"rows": len(rows), "variants": {}}, {}
    for name, raws in variants.items():
        scored = [score_row(r, t) for r, t in zip(rows, raws)]
        result["variants"][name] = summarise(rows, scored)
        flags[name] = [s["gold_type"] == s["pred_type"] for s in scored]
        flags[name + "|accepted"] = [s["accepted"] or s["gold_type"] == "none" for s in scored]
    if len(variants) > 1:
        refs = [v for v in ("base", "s690") if v in variants]
        result["bootstrap_type_acc"] = _bootstrap({k: v for k, v in flags.items() if "|" not in k}, refs)
        result["bootstrap_accepted"] = _bootstrap({k: v for k, v in flags.items() if "|" in k},
                                                  [r + "|accepted" for r in refs])
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    for name, s in result["variants"].items():
        print(f"{name:7} type_acc {s['type_acc']:.3f}  balanced {s['balanced_acc']:.3f}  accepted(charts) {s['accepted_rate_on_charts']:.3f}  "
              f"channels {s['channels_match_of_accepted']:.3f}  sort {s['sort_match_of_accepted_sorted']:.3f}  "
              f"exact {s['exact_of_accepted']:.3f}  useful {s['useful_rate']:.3f}  {s['outcomes']}")
    return result


if __name__ == "__main__":
    main()
