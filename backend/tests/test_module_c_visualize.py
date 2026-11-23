"""Module C: the Vega-Lite validator, the column profile, and the node's three paths.

No network, same as the narrative node's tests: the model is a stub so a failure
here names the rule that broke rather than blaming a model that answered badly.

The three paths the plan requires
covering are all here — a valid spec, an invalid spec, and no reply from the
model — except that the third outcome is *no chart* rather than a template
fallback. That difference is the decision recorded on 2026-08-07, and the tests
assert it deliberately: `test_an_unreachable_model_yields_no_chart` and
`test_two_invalid_specs_yield_no_chart` exist to fail if a fallback is ever
quietly reintroduced.
"""

from __future__ import annotations

import json

import pytest

from app.orchestrator import client, profile, vega
from app.orchestrator.contracts import VisualizeInput
from app.orchestrator.nodes import visualize as node
from app.schemas.charts import ChartType

ROWS = [
    {"region": "Buenos Aires", "share": 36.4},
    {"region": "Cordoba", "share": 18.2},
    {"region": "Santa Fe", "share": 12.1},
]
COLUMNS = ["region", "share"]

BAR_SPEC = {
    "$schema": vega.SCHEMA_URL,
    "title": "Share of sales by region",
    "mark": "bar",
    "encoding": {
        "x": {"field": "share", "type": "quantitative", "title": "Share of sales"},
        "y": {"field": "region", "type": "nominal", "title": "Region", "sort": "-x"},
    },
}


def make_input(rows=None, columns=None, **kwargs) -> VisualizeInput:
    rows = ROWS if rows is None else rows
    return VisualizeInput(
        question=kwargs.pop("question", "which region sold the most?"),
        sql_query="SELECT region, share FROM sales",
        columns=columns if columns is not None else COLUMNS,
        rows=rows,
        row_count=kwargs.pop("row_count", len(rows)),
        **kwargs,
    )


def reply(payload: dict) -> client.Completion:
    return client.Completion(
        text=json.dumps(payload), model="stub", prompt_tokens=0, completion_tokens=0
    )


@pytest.fixture
def answers(monkeypatch):
    """Queue the model's replies in order; returns the list of prompts it saw."""

    def _install(*payloads):
        seen: list[list[dict]] = []
        queue = list(payloads)

        def _complete(config, messages, **kwargs):
            seen.append(messages)
            if not queue:
                raise AssertionError("the node made more model calls than the test queued")
            nxt = queue.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt if isinstance(nxt, client.Completion) else reply(nxt)

        monkeypatch.setattr(node.client, "complete", _complete)
        return seen

    return _install


# --------------------------------------------------------------------------
# The validator — the part with no model in it
# --------------------------------------------------------------------------


def test_a_well_formed_spec_passes_every_check():
    assert vega.check(BAR_SPEC, COLUMNS) == []


def test_a_field_that_is_not_a_result_column_is_rejected():
    spec = json.loads(json.dumps(BAR_SPEC))
    spec["encoding"]["x"]["field"] = "profit_margin"
    problems = vega.check(spec, COLUMNS)
    assert any("profit_margin" in p for p in problems)


def test_a_field_invented_inside_a_transform_is_rejected():
    """The schema cannot catch this: a transform over a missing column is legal
    Vega-Lite that fails in the browser, where nothing can be done about it."""
    spec = dict(BAR_SPEC, transform=[{"filter": {"field": "quarter", "equal": "Q1"}}])
    assert any("quarter" in p for p in vega.check(spec, COLUMNS))


def test_a_column_the_spec_computes_for_itself_is_not_an_invented_one():
    """Without this the check would be enforcing "no transforms" rather than
    "no invented columns" — every legitimate aggregate would be rejected."""
    spec = {
        "mark": "bar",
        "transform": [
            {"aggregate": [{"op": "sum", "field": "share", "as": "total"}], "groupby": ["region"]}
        ],
        "encoding": {
            "x": {"field": "region", "type": "nominal"},
            "y": {"field": "total", "type": "quantitative"},
        },
    }
    assert vega.check(vega.normalize(spec), COLUMNS) == []


def test_an_unsupported_mark_is_rejected_even_though_vega_lite_allows_it():
    spec = dict(BAR_SPEC, mark="boxplot")
    problems = vega.check(spec, COLUMNS)
    assert any("boxplot" in p for p in problems)


def test_a_channel_without_a_declared_type_is_rejected():
    spec = json.loads(json.dumps(BAR_SPEC))
    del spec["encoding"]["x"]["type"]
    assert any("`x`" in p and "type" in p for p in vega.check(spec, COLUMNS))


def test_a_spec_with_no_encoding_is_rejected():
    assert vega.check({"mark": "bar"}, COLUMNS) != []


def test_a_structurally_invalid_spec_fails_the_json_schema():
    """Every semantic check passes; only the official schema catches this one."""
    spec = json.loads(json.dumps(BAR_SPEC))
    spec["encoding"]["x"]["type"] = "quantitativo"  # not a Vega-Lite type
    problems = vega.check(spec, COLUMNS)
    assert problems and "not valid Vega-Lite v5" in problems[0]


def test_normalize_strips_model_supplied_data_and_stamps_the_schema():
    """A `data` block from the model is invented numbers drawn over the user's own."""
    spec = dict(BAR_SPEC, data={"values": [{"region": "Nowhere", "share": 99.9}]})
    del spec["$schema"]
    cleaned = vega.normalize(spec)
    assert "data" not in cleaned
    assert cleaned["$schema"] == vega.SCHEMA_URL


@pytest.mark.parametrize(
    "mark, expected",
    [
        ("bar", ChartType.bar),
        ("line", ChartType.line),
        ("area", ChartType.line),
        ("point", ChartType.scatter),
        ("circle", ChartType.scatter),
        ("arc", ChartType.pie),
        ("rect", ChartType.heatmap),
        ("boxplot", None),
    ],
)
def test_every_mark_maps_to_a_chart_type_or_to_nothing(mark, expected):
    assert vega.chart_type_of(dict(BAR_SPEC, mark=mark)) is expected


def test_a_binned_bar_is_a_histogram_not_a_bar():
    """There is no `histogram` mark in Vega-Lite, but there is a histogram
    `ChartType`, and saved charts and history icons key off it."""
    spec = {
        "mark": "bar",
        "encoding": {
            "x": {"field": "share", "type": "quantitative", "bin": True},
            "y": {"aggregate": "count", "type": "quantitative"},
        },
    }
    assert vega.chart_type_of(spec) is ChartType.histogram
    assert vega.check(vega.normalize(spec), COLUMNS) == []


def test_the_vendored_schema_matches_the_version_the_frontend_renders_with():
    """If these drift, a spec can validate here and fail to render there."""
    assert vega.schema_version().startswith("5.")


# --------------------------------------------------------------------------
# The column profile
# --------------------------------------------------------------------------


def test_the_profile_reads_types_off_the_values():
    columns = profile.column_profile(
        [
            {"month": "2025-01-01", "units": 12, "flag": True, "name": "a"},
            {"month": "2025-02-01", "units": 15, "flag": False, "name": "b"},
        ],
        ["month", "units", "flag", "name"],
    )
    assert [c.dtype for c in columns] == ["temporal", "quantitative", "nominal", "nominal"]


def test_a_month_written_as_text_is_a_period_not_a_category():
    rows = [{"mes": "2024-11", "codigo": "2024-13"}, {"mes": "2024-12", "codigo": "1999-00"}]
    columns = profile.column_profile(rows, ["mes", "codigo"])
    assert [c.dtype for c in columns] == ["temporal", "nominal"]


def test_the_profile_counts_distinct_values_not_rows():
    columns = profile.column_profile(
        [{"region": "BA"}, {"region": "BA"}, {"region": "CBA"}], ["region"]
    )
    assert columns[0].cardinality == 2


def test_an_all_null_column_is_nominal_rather_than_a_guess():
    columns = profile.column_profile([{"x": None}, {"x": None}], ["x"])
    assert columns[0].dtype == "nominal" and columns[0].cardinality == 0


# --------------------------------------------------------------------------
# The node — the three paths §2.3 asks for
# --------------------------------------------------------------------------


def test_a_valid_spec_is_returned_with_its_chart_type_and_profile(answers):
    answers({"chart_type": "bar", "spec": BAR_SPEC, "reason": "a ranking"})
    chart = node.visualize(make_input())
    assert chart is not None
    assert chart.chartType is ChartType.bar
    assert chart.source == "model"
    assert [c.name for c in chart.columns] == COLUMNS
    assert "data" not in chart.spec


def test_an_invalid_spec_is_retried_with_a_critique_naming_the_problem(answers):
    bad = json.loads(json.dumps(BAR_SPEC))
    bad["encoding"]["x"]["field"] = "profit"
    seen = answers(
        {"chart_type": "bar", "spec": bad},
        {"chart_type": "bar", "spec": BAR_SPEC},
    )

    chart = node.visualize(make_input())

    assert chart is not None and chart.source == "model_retry"
    critique = seen[1][-1]["content"]
    assert "profit" in critique, "the retry must say what was wrong, not just 'invalid'"


def test_two_invalid_specs_yield_no_chart_and_no_fallback(answers):
    bad = {"chart_type": "bar", "spec": {"mark": "boxplot", "encoding": {}}}
    answers(bad, bad)
    assert node.visualize(make_input()) is None


def test_an_unreachable_model_yields_no_chart(answers):
    answers(client.LlmUnavailable("timeout"))
    assert node.visualize(make_input()) is None


def test_the_model_may_decline_to_draw_anything(answers):
    """Rule 7: a one-bar chart manufactured to have something to return is worse
    than none. Declining costs one call and no retry."""
    seen = answers({"chart_type": "none", "spec": None, "reason": "a single total"})
    assert node.visualize(make_input(rows=[{"total": 42}], columns=["total"])) is None
    assert len(seen) == 1


def test_an_empty_result_never_reaches_the_model(answers):
    seen = answers()  # queue nothing: a call would raise
    assert node.visualize(make_input(rows=[], row_count=0)) is None
    assert seen == []


def test_the_prompt_carries_raw_column_names_and_business_labels(answers):
    """`field` must match the row keys the browser injects, so the raw name has
    to be in the prompt — the pretty name goes in the axis title instead."""
    seen = answers({"chart_type": "none", "spec": None})
    node.visualize(make_input(column_labels={"share": "Share of sales"}))
    prompt = seen[0][1]["content"]
    assert "share (quantitative" in prompt
    assert "Share of sales" in prompt


def test_the_node_can_be_switched_off_without_unsetting_credentials(answers, monkeypatch):
    from app.core.config import get_settings

    seen = answers()
    settings = get_settings()
    monkeypatch.setattr(settings, "visualization_enabled", False)
    assert node.visualize(make_input()) is None
    assert seen == []


# --------------------------------------------------------------------------
# The fine-tuned adapter (ALIBI_VISUALIZATION_PROVIDER=sagemaker)
# --------------------------------------------------------------------------


@pytest.fixture
def adapter(monkeypatch):
    """Route the node to the SageMaker adapter; queue its replies; record payloads."""
    from app.core import sagemaker
    from app.core.config import get_settings

    monkeypatch.setenv("ALIBI_VISUALIZATION_PROVIDER", "sagemaker")
    monkeypatch.setenv("ALIBI_SAGEMAKER_ENDPOINT_NAME", "model-a-dev")
    get_settings.cache_clear()

    def _bedrock_must_not_be_called(*_a, **_k):
        raise AssertionError("the adapter path called the prompted model")

    monkeypatch.setattr(node.client, "complete", _bedrock_must_not_be_called)

    def _install(*replies):
        sent: list[dict] = []
        queue = list(replies)

        def _invoke(_target, payload):
            sent.append(payload)
            if not queue:
                raise AssertionError("the node made more adapter calls than the test queued")
            nxt = queue.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        monkeypatch.setattr(sagemaker, "invoke", _invoke)
        return sent

    yield _install
    get_settings.cache_clear()


def _adapter_reply(payload: dict) -> dict:
    text = json.dumps(payload)
    return {"task": "chart", "chart_type": payload.get("chart_type"), "spec": payload.get("spec"),
            "valid_json": True, "error": None, "raw": text, "model_type": "qlora"}


def test_the_adapter_gets_the_training_inputs_not_a_prompt(adapter):
    rows = ROWS * 3  # 9 rows: more than the 5 the adapter was trained on
    sent = adapter(_adapter_reply({"chart_type": "bar", "spec": BAR_SPEC}))

    chart = node.visualize(make_input(rows=rows, column_labels={"share": "Share of sales"}))

    assert chart is not None and chart.chartType is ChartType.bar
    payload = sent[0]
    assert payload["task"] == "chart"
    assert payload["question"] == "which region sold the most?"
    assert payload["row_count"] == 9 and payload["truncated"] is False
    assert payload["rows"] == rows[:5]
    assert payload["columns"] == [
        {"name": "region", "dtype": "nominal", "cardinality": 3, "label": "Region"},
        {"name": "share", "dtype": "quantitative", "cardinality": 3, "label": "Share of sales"},
    ]


def test_an_adapter_spec_goes_through_the_same_validator(adapter):
    bad = json.loads(json.dumps(BAR_SPEC))
    bad["encoding"]["x"]["field"] = "profit_margin"
    sent = adapter(_adapter_reply({"chart_type": "bar", "spec": bad}))

    assert node.visualize(make_input()) is None
    # One attempt: greedy decoding would repeat the same spec.
    assert len(sent) == 1


def test_an_adapter_reply_that_is_not_json_is_no_chart(adapter):
    adapter({"task": "chart", "chart_type": None, "spec": None, "valid_json": False,
             "error": "Expecting value", "raw": '{"chart_type": "bar", "spec": {'})
    assert node.visualize(make_input()) is None


def test_the_adapter_may_decline(adapter):
    adapter(_adapter_reply({"chart_type": "none", "spec": None}))
    assert node.visualize(make_input()) is None


def test_an_unreachable_adapter_yields_no_chart(adapter):
    from app.core import sagemaker

    adapter(sagemaker.ModelAsleep("no running copy"))
    assert node.visualize(make_input()) is None


def test_the_adapter_labels_columns_the_way_training_did():
    """Held against `ML/src/common/prompts.py::humanize`, the function that
    labelled every training prompt."""
    import importlib.util
    from pathlib import Path

    prompts = Path(__file__).resolve().parents[2] / "ML" / "src" / "common" / "prompts.py"
    if not prompts.exists():
        pytest.skip("ML/ is not beside backend/ in this checkout")
    spec = importlib.util.spec_from_file_location("ml_common_prompts", prompts)
    trained = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trained)

    for name in ["COUNT(*)", "count(*)", "SUM(revenue)", "avg(T1.unit_price)", "MAX(order_date)",
                 "total_revenue", "Region", "STRFTIME('%Y', d)", "min(sum(x))"]:
        assert node._trained_label(name) == trained.humanize(name), name


def test_a_module_b_label_wins_over_the_trained_one(adapter):
    rows = [{"region": "North", "COUNT(*)": 3}, {"region": "South", "COUNT(*)": 5}]
    sent = adapter(_adapter_reply({"chart_type": "none", "spec": None}))

    node.visualize(make_input(rows=rows, columns=["region", "COUNT(*)"], column_labels={"region": "Sales region"}))

    labels = {c["name"]: c["label"] for c in sent[0]["columns"]}
    assert labels == {"region": "Sales region", "COUNT(*)": "Number of rows"}
