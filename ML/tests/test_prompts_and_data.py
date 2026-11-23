"""Offline tests for the pieces training and serving share: prompts and the nvBench converter.

    cd ML/src && python -m pytest ../tests -q
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from common.prompts import (  # noqa: E402
    CHART_SYSTEM_PROMPT, SQL_SYSTEM_PROMPT, chart_user, humanize, render_inference_prompt, render_training_text,
)
from model_c.prepare_data import convert, none_variant  # noqa: E402

# The exact template Module A's validated smoke runs trained on.
_OLD_SQL = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n### Schema:\n{schema}\n\n### Question:\n{question}\n\n### SQL:<|im_end|>\n"
    "<|im_start|>assistant\n{sql}<|im_end|>"
)


def test_sql_training_text_is_unchanged():
    row = {"schema": "CREATE TABLE t (a INT);", "question": "how many?", "sql": "SELECT COUNT(*) FROM t"}
    assert render_training_text("sql", row) == _OLD_SQL.format(system=SQL_SYSTEM_PROMPT, **row)


def test_inference_prompt_is_the_training_prompt_minus_the_answer():
    row = {"schema": "S", "question": "Q", "sql": "SELECT 1"}
    train = render_training_text("sql", row)
    infer = render_inference_prompt("sql", row)
    assert train.startswith(infer) and train[len(infer):] == "SELECT 1<|im_end|>"


def test_chart_inference_prompt_is_the_training_prompt_minus_the_answer():
    cols = [{"name": "m", "dtype": "temporal", "cardinality": 3}, {"name": "v", "dtype": "quantitative", "cardinality": 3}]
    rows = [{"m": "2024-01-01", "v": 1}]
    user = chart_user("trend?", 3, cols, rows)
    train = render_training_text("chart", {"prompt": user, "target": "{}"})
    infer = render_inference_prompt("chart", {"question": "trend?", "row_count": 3, "columns": cols, "rows": rows})
    assert train.startswith(infer) and CHART_SYSTEM_PROMPT in infer


def test_humanize():
    assert humanize("count(*)") == "Number of rows"
    assert humanize("AVG(age)") == "Average of age"
    assert humanize("total_rev") == "Total Rev"


_SAMPLE = {
    "db_id": "shop",
    "chart": "ignored",
    "vis_query": {"vis_part": "Visualize BAR", "data_part": {"sql_part": "SELECT region , count(*) FROM s GROUP BY region"}},
    "vis_obj": {"chart": "bar", "x_name": "region", "y_name": "count(*)",
                "x_data": [["N", "S", "E"]], "y_data": [[3, 2, 1]], "classify": [], "describe": ""},
    "nl_queries": ["Orders per region", "How many orders in each region?", "third"],
}


def test_convert_builds_a_backend_shaped_target():
    rows = convert("1", _SAMPLE, n_questions=2)
    assert len(rows) == 2 and rows[0]["db_id"] == "shop"
    target = json.loads(rows[0]["target"])
    assert target["chart_type"] == "bar"
    spec = target["spec"]
    assert "data" not in spec and spec["mark"] == "bar"
    fields = {c["field"] for c in spec["encoding"].values()}
    assert fields == {"region", "count(*)"}
    assert all("type" in c for c in spec["encoding"].values())
    assert "- region (nominal, 3 distinct)" in rows[0]["prompt"]


def test_none_variant_is_a_single_row_with_null_spec():
    row = none_variant("1", _SAMPLE)
    assert json.loads(row["target"]) == {"chart_type": "none", "spec": None}
    assert "Rows in the result: 1" in row["prompt"]


def test_grouped_series_become_a_color_column():
    s = json.loads(json.dumps(_SAMPLE))
    s["vis_query"]["vis_part"] = "Visualize STACKED BAR"
    s["vis_query"]["data_part"]["sql_part"] = "SELECT region , count(*) , tier FROM s GROUP BY region , tier"
    s["vis_obj"].update(x_data=[["N", "S"], ["N", "S"]], y_data=[[1, 2], [3, 4]], classify=["gold", "silver"])
    spec = json.loads(convert("2", s, 1)[0]["target"])["spec"]
    assert spec["encoding"]["color"]["field"] == "tier"
