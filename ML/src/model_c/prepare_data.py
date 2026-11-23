"""
nvBench -> Module C training data (question + result profile -> Vega-Lite spec).

nvBench's `NVBench.json` is a dict of samples keyed by id; each one has
(https://github.com/TsinghuaDatabaseGroup/nvBench):

  vis_query.vis_part   "Visualize STACKED BAR" / "GROUPING LINE" / ...
  vis_query.data_part  {"sql_part": "SELECT a , b FROM ..."}
  vis_obj              {chart, x_name, y_name, x_data, y_data, classify, describe}
                       x_data / y_data are lists of series (one per group);
                       classify holds the group labels when there are several
  nl_queries           2-6 paraphrases of the same request
  db_id                one of 152 Spider databases

It does NOT carry a Vega-Lite spec or a train/dev split, so both are made here:

* The result table is rebuilt from `vis_obj` (no database needed), then described
  exactly the way the backend describes a real result to Module C
  (`common.prompts.chart_user`: question, row count, COLUMNS with type and
  distinct count, sample rows).
* The target is the backend's reply shape, `{"chart_type", "spec"}`, restricted to
  the marks the backend accepts (bar, line, area, point, arc, rect) and written
  data-less with typed channels and plain-words titles — the same rules as
  `backend/app/orchestrator/prompts/visualize_system.md`.
* Split by DATABASE (hash of db_id), so the eval set is cross-domain like Spider's.
* A few single-row variants are added with the target `none`, because the
  backend prompt teaches that "nothing to draw" is a correct answer.

Output (gitignored):
  data/processed/model_c/train/train.jsonl
  data/processed/model_c/eval/eval.jsonl
Row: {"prompt", "target", "chart_type", "db_id", "id"}

nvBench is heavily skewed (~81% bar), so bar samples are capped (`--max-bar`) to
keep the model from learning "always bar" — chart-type accuracy is a target metric.
"""

import argparse
import hashlib
import json
import logging
import random
import re
from pathlib import Path
from typing import Any, Optional

from common.prompts import chart_target, chart_user, humanize

logger = logging.getLogger("prepare_c")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

_ISO_TEMPORAL = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?$")
_MANY = 12  # more categories than this -> horizontal, sorted bar (backend rule 5)


# ── result table ──────────────────────────────────────────────────────────────

def _dtype(values: list[Any]) -> str:
    """Same rules as backend/app/orchestrator/profile.py::_dtype."""
    values = [v for v in values if v is not None]
    if not values or all(isinstance(v, bool) for v in values):
        return "nominal"
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "quantitative"
    if all(isinstance(v, str) and _ISO_TEMPORAL.match(v) for v in values):
        return "temporal"
    return "nominal"


def _select_names(sql: str) -> list[str]:
    """Column names from the SELECT list, last dotted part, alias respected."""
    m = re.match(r"\s*select\s+(.*?)\s+from\s", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    parts, depth, cur = [], 0, ""
    for ch in m.group(1):
        depth += ch == "("
        depth -= ch == ")"
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    names = []
    for p in parts:
        p = p.strip()
        alias = re.search(r"\s+as\s+(\w+)\s*$", p, re.IGNORECASE)
        names.append(alias.group(1) if alias else p.split(".")[-1].strip())
    return names


_AGG = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)


def _order_of(sql: str, y_name: str) -> Optional[tuple[str, bool]]:
    """The sample's own ORDER BY as (what, descending): what is 'y' (by the value) or 'x'
    (by the category's own label). None when the SQL does not sort — the question's wording
    ("from low to high") is what nvBench turned into this clause, so it is the ground truth."""
    m = re.search(r"\border\s+by\s+(.+?)(?:\s+limit\b|;|$)", sql, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    term = m.group(1).split(",")[0].strip()
    direction = re.search(r"\s(asc|desc)\s*$", term, re.IGNORECASE)
    desc = bool(direction) and direction.group(1).lower() == "desc"
    expr = term[: direction.start()] if direction else term
    by_value = bool(_AGG.search(expr)) or expr.split(".")[-1].strip().lower() == y_name.lower()
    return ("y" if by_value else "x", desc)


def build_table(sample: dict) -> Optional[tuple[list[dict], list[str], Optional[str]]]:
    """Rows, [x, y] column names and the group column name (None if one series)."""
    o = sample["vis_obj"]
    xs, ys = o.get("x_data") or [], o.get("y_data") or []
    if not xs or not ys or len(xs) != len(ys) or not o.get("x_name") or not o.get("y_name"):
        return None
    xn, yn = str(o["x_name"]), str(o["y_name"])
    groups = o.get("classify") or []
    group_name = None
    if len(xs) > 1:
        sel = _select_names(sample["vis_query"]["data_part"].get("sql_part", ""))
        group_name = sel[2] if len(sel) >= 3 and sel[2] not in (xn, yn) else "category"
    rows = []
    for i, (xv, yv) in enumerate(zip(xs, ys)):
        for a, b in zip(xv, yv):
            row = {xn: a, yn: b}
            if group_name:
                row[group_name] = groups[i] if i < len(groups) else f"series {i + 1}"
            rows.append(row)
    return (rows, [xn, yn], group_name) if rows else None


def profile(rows: list[dict], names: list[str]) -> list[dict]:
    cols = []
    for n in names:
        vals = [r.get(n) for r in rows if r.get(n) is not None]
        try:
            card = len(set(vals))
        except TypeError:
            card = len({str(v) for v in vals})
        cols.append({"name": n, "dtype": _dtype(vals), "cardinality": card, "label": humanize(n)})
    return cols


# ── target spec ───────────────────────────────────────────────────────────────

def _chan(col: dict, **extra) -> dict:
    return {"field": col["name"], "type": col["dtype"], "title": col["label"], **extra}


def build_spec(vis_part: str, chart: str, cols: list[dict], group: Optional[dict],
               order: Optional[tuple[str, bool]] = None) -> Optional[tuple[str, dict]]:
    x, y = cols
    kind = vis_part.upper()
    title = f"{y['label']} by {x['label']}"
    enc: dict[str, Any]

    if chart == "pie":
        if y["dtype"] != "quantitative":
            return None
        enc = {"theta": _chan(y), "color": _chan(x)}
        return "arc", {"title": title, "mark": "arc", "encoding": enc}

    if chart == "scatter":
        enc = {"x": _chan(x), "y": _chan(y)}
        if group:
            enc["color"] = _chan(group)
        return "point", {"title": title, "mark": "point", "encoding": enc}

    if chart == "line":
        enc = {"x": _chan(x), "y": _chan(y)}
        if group:
            enc["color"] = _chan(group)
        return "line", {"title": title, "mark": "line", "encoding": enc}

    if chart == "bar":
        if y["dtype"] != "quantitative":
            return None
        if group:
            enc = {"x": _chan(x), "y": _chan(y), "color": _chan(group)}
            if "GROUPING" in kind or "GROUPED" in kind:
                enc["xOffset"] = {"field": group["name"], "type": group["dtype"]}
            return "bar", {"title": title, "mark": "bar", "encoding": enc}
        if x["dtype"] == "nominal":
            horizontal = x["cardinality"] > _MANY
            if order is None:  # no ORDER BY in the sample: the backend's default, largest first
                sort = "-x" if horizontal else "-y"
            elif order[0] == "x":  # sorted by the label itself
                sort = "descending" if order[1] else "ascending"
            else:  # sorted by the value, in the direction the question asked for
                sort = ("-" if order[1] else "") + ("x" if horizontal else "y")
            enc = ({"x": _chan(y), "y": _chan(x, sort=sort)} if horizontal
                   else {"x": _chan(x, sort=sort), "y": _chan(y)})
        else:
            enc = {"x": _chan(x), "y": _chan(y)}
        return "bar", {"title": title, "mark": "bar", "encoding": enc}
    return None


# ── one nvBench sample -> training rows ───────────────────────────────────────

def convert(sid: str, sample: dict, n_questions: int) -> list[dict]:
    built = build_table(sample)
    if built is None:
        return []
    rows, (xn, yn), gname = built
    cols = profile(rows, [xn, yn] + ([gname] if gname else []))
    order = _order_of(sample["vis_query"]["data_part"].get("sql_part", ""), yn)
    spec = build_spec(sample["vis_query"].get("vis_part", ""), sample["vis_obj"]["chart"], cols[:2],
                      cols[2] if gname else None, order)
    if spec is None:
        return []
    chart_type, body = spec
    out = []
    for q in [q.strip() for q in sample.get("nl_queries", []) if q and q.strip()][:n_questions]:
        out.append({
            "prompt": chart_user(q, len(rows), cols, rows[:5]),
            "target": chart_target(chart_type, body),
            "chart_type": chart_type,
            "db_id": sample["db_id"],
            "id": sid,
        })
    return out


def none_variant(sid: str, sample: dict) -> Optional[dict]:
    """The same question over a one-row result: nothing to draw."""
    built = build_table(sample)
    if built is None:
        return None
    rows, (xn, yn), _ = built
    rows = rows[:1]
    cols = profile(rows, [xn, yn])
    q = next((q.strip() for q in sample.get("nl_queries", []) if q and q.strip()), None)
    if not q:
        return None
    return {"prompt": chart_user(q, 1, cols, rows), "target": chart_target("none", None),
            "chart_type": "none", "db_id": sample["db_id"], "id": f"{sid}-none"}


def is_eval_db(db_id: str, frac: float) -> bool:
    return int(hashlib.md5(db_id.encode()).hexdigest(), 16) % 1000 < frac * 1000


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--nvbench-json", required=True, help="Path to NVBench.json")
    p.add_argument("--output-dir", default="data/processed/model_c")
    p.add_argument("--questions-per-sample", type=int, default=2, help="paraphrases used per training sample")
    p.add_argument("--max-bar", type=int, default=2000, help="cap on bar-chart samples (nvBench is ~81%% bar)")
    p.add_argument("--none-fraction", type=float, default=0.04, help="share of extra single-row 'none' rows")
    p.add_argument("--eval-db-fraction", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    data = json.loads(Path(args.nvbench_json).read_text(encoding="utf-8"))
    rng = random.Random(args.seed)
    ids = sorted(data, key=str)  # deterministic order before the seeded shuffle
    rng.shuffle(ids)

    train, ev, bars, skipped = [], [], 0, 0
    for sid in ids:
        s = data[sid]
        is_eval = is_eval_db(s["db_id"], args.eval_db_fraction)
        if s["vis_obj"]["chart"] == "bar" and not is_eval:
            if bars >= args.max_bar:
                continue
            bars += 1
        rows = convert(str(sid), s, 1 if is_eval else args.questions_per_sample)
        if not rows:
            skipped += 1
            continue
        (ev if is_eval else train).extend(rows)
        if rng.random() < args.none_fraction:
            nv = none_variant(str(sid), s)
            if nv:
                (ev if is_eval else train).append(nv)

    rng.shuffle(train)
    out = Path(args.output_dir)
    for name, rows in (("train", train), ("eval", ev)):
        (out / name).mkdir(parents=True, exist_ok=True)
        path = out / name / f"{name}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        mix = {}
        for r in rows:
            mix[r["chart_type"]] = mix.get(r["chart_type"], 0) + 1
        logger.info("wrote %d rows -> %s  mix=%s", len(rows), path, mix)
    logger.info("skipped %d samples (no usable table or spec)", skipped)


if __name__ == "__main__":
    main()
