"""The narrative-synthesis node: verification, retry, and failing loudly.

No network. The model is a stub, which is the point — every rule in the
specification is checked against text the test supplies, so a failure here names
the rule that broke rather than blaming a model that happened to answer badly.

**This is how the suite stays free while local development stays fully online.**
The product has no offline mode any more: there is no template narrative and no
canned result, so `pytest` cannot rely on ALIBI degrading gracefully to get a
deterministic answer. It substitutes the transport instead. `conftest.py` blanks
the credentials so nothing can reach a provider by accident; these tests put a
stub in front of `client.complete` so the node still has something to talk to.
"""

from __future__ import annotations

import json

import pytest

from app.orchestrator import validation
from app.orchestrator.contracts import ExplainInput, Explanation
from app.orchestrator.nodes import explain_result as node

RANKING_ROWS = [
    {"region": "Buenos Aires", "share": 36.4},
    {"region": "Cordoba", "share": 18.2},
    {"region": "Santa Fe", "share": 12.1},
    {"region": "Mendoza", "share": 9.7},
]
RANKING_SQL = "SELECT region, share FROM sales GROUP BY region ORDER BY share DESC"


def make_input(
    rows: list[dict] | None = None,
    columns: list[str] | None = None,
    sql: str = RANKING_SQL,
    question: str = "which region sold the most?",
    **kwargs,
) -> ExplainInput:
    rows = RANKING_ROWS if rows is None else rows
    return ExplainInput(
        question=question,
        sql_query=sql,
        columns=columns if columns is not None else ["region", "share"],
        rows=rows,
        row_count=kwargs.pop("row_count", len(rows)),
        **kwargs,
    )


# --------------------------------------------------------------------------
# Grounding (rule 1) — the check that matters most
# --------------------------------------------------------------------------


def test_a_fabricated_figure_is_rejected():
    problems = validation.check_grounding(
        "Buenos Aires leads with 36.4%, ahead of a national average of 22.8%.",
        RANKING_ROWS,
    )
    assert any("22.8" in p for p in problems)
    assert not any("36.4" in p for p in problems)


def test_honest_rounding_is_accepted_and_misleading_rounding_is_not():
    rows = [{"share": 36.42}]
    assert validation.check_grounding("It reached 36.4%.", rows) == []
    assert validation.check_grounding("It reached 36.9%.", rows) != []


def test_a_stored_fraction_may_be_written_as_a_percentage():
    """0.364 and 36.4% are the same fact in the unit the reader expects."""
    assert validation.check_grounding("It holds 36.4% of the total.", [{"share": 0.364}]) == []


def test_counting_the_result_is_grounded():
    """"The top three" describes the answer's shape, which the narrative can see."""
    assert (
        validation.check_grounding(
            "The top 3 of 4 areas account for most of the activity.",
            RANKING_ROWS,
            row_count=4,
        )
        == []
    )


def test_a_figure_from_the_question_is_grounded():
    """Repeating the user's own "in 2025" is not inventing data."""
    assert (
        validation.check_grounding(
            "Buenos Aires led 2025 with 36.4%.", RANKING_ROWS, question="sales in 2025?"
        )
        == []
    )


# --------------------------------------------------------------------------
# Jargon (rule 2) and length (rule 7)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The query returned Buenos Aires first.",
        "Grouping the table by region shows Buenos Aires on top.",
        "The first row of the result set is Buenos Aires.",
        "After the join, Buenos Aires leads.",
    ],
)
def test_technical_vocabulary_is_rejected(text):
    assert validation.check_jargon(text) != []


@pytest.mark.parametrize(
    "text",
    [
        "La consulta SQL devolvió primero a Buenos Aires.",
        "Agrupando la tabla por región, Buenos Aires queda primera.",
        "La primera fila del resultado es Buenos Aires.",
        "En la base de datos, Buenos Aires encabeza las ventas.",
        "La columna de participación muestra a Buenos Aires arriba.",
    ],
)
def test_spanish_technical_vocabulary_is_rejected(text):
    """The answer is written in Spanish, so the jargon check has to read it."""
    assert validation.check_jargon(text) != []


def test_ordinary_spanish_business_words_are_allowed():
    """"consulta" and "registro" are business nouns too, and stay allowed."""
    text = "Las consultas de clientes y el registro de ventas se concentran en Buenos Aires."
    assert validation.check_jargon(text) == []


def test_a_spanish_answer_with_data_formatted_figures_is_grounded():
    text = "Buenos Aires lidera con el 36.4% de las ventas, seguida por Cordoba con el 18.2%."
    assert validation.check_grounding(text, RANKING_ROWS) == []


def test_a_figure_in_spanish_number_format_is_rejected():
    """"36,4" is not 36.4 to the checker; the retry asks for the data's format."""
    assert validation.check_grounding("Buenos Aires lidera con el 36,4 %.", RANKING_ROWS) != []


def test_ordinary_english_that_happens_to_be_sql_is_allowed():
    """Banning "from", "where", "order" and "count" would reject correct prose."""
    text = "Orders from the coastal areas count for the largest share, where activity is highest."
    assert validation.check_jargon(text) == []


def test_an_internal_identifier_leaking_is_rejected():
    assert validation.check_jargon("The fch_vcto value peaks in March.", ["fch_vcto"]) != []


def test_short_identifiers_are_not_treated_as_jargon():
    """"id" and "qty" collide with ordinary words far more often than they leak."""
    assert validation.check_jargon("Madrid leads the ranking.", ["id", "qty"]) == []


def test_a_column_whose_name_is_an_ordinary_word_may_be_said():
    """"region" and "share" are what the sentence is *supposed* to call them.

    Flagging every column name would reject the correct narrative for using the
    right English word. Only names written for a machine (separators, digits,
    camelCase) are identifiers.
    """
    text = "The coastal region holds the largest share of sales."
    assert validation.check_jargon(text, ["region", "share", "total_revenue"]) == []
    assert validation.check_jargon("Total_revenue peaked in March.", ["total_revenue"]) != []


def test_a_paragraph_is_rejected():
    assert validation.check_length("One. Two. Three. Four.") != []
    assert validation.check_length("One. Two. Three.") == []


# --------------------------------------------------------------------------
# Value formatting
# --------------------------------------------------------------------------


def test_numbers_are_formatted_for_an_english_reader():
    from app.orchestrator import formatting

    assert formatting.format_number(1234567.891) == "1,234,567.89"
    assert formatting.format_number(42.0) == "42"


def test_a_business_label_is_preferred_over_the_column_name():
    from app.orchestrator import formatting

    assert formatting.humanize("fch_vcto", {"fch_vcto": "Due date"}) == "Due date"
    assert formatting.humanize("total_revenue") == "Total revenue"


# --------------------------------------------------------------------------
# The node end to end, with a stubbed model
# --------------------------------------------------------------------------


class StubModel:
    """Returns canned completions in order and records the prompts it received."""

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    def __call__(self, config, messages, **kwargs):
        self.calls.append(messages)
        from app.orchestrator.client import Completion

        text = self.replies.pop(0) if self.replies else "{}"
        return Completion(text=text, model="stub", prompt_tokens=1, completion_tokens=1)


@pytest.fixture
def configured(monkeypatch):
    """A reachable model, without a provider."""
    from app.orchestrator import client

    monkeypatch.setattr(
        client,
        "config_from_settings",
        lambda *_a, **_k: client.LlmConfig(model_id="d"),
    )


def _reply(narrative: str, confidence: float = 0.9) -> str:
    return json.dumps(
        {"narrative": narrative, "key_figures": [], "confidence_score": confidence}
    )


GOOD = "Buenos Aires leads with 36.4% of sales, ahead of Cordoba at 18.2%."


def test_a_valid_narrative_is_returned(monkeypatch, configured):
    monkeypatch.setattr(node.client, "complete", StubModel(_reply(GOOD)))
    result = node.explain_result(make_input())
    assert result.narrative == GOOD
    assert result.source == "model"


def test_no_model_configured_is_an_error_not_a_fallback(monkeypatch):
    """The whole point of removing the template: silence beats a fake answer."""
    from app.orchestrator import client

    monkeypatch.setattr(
        client,
        "config_from_settings",
        lambda *_a, **_k: client.LlmConfig(model_id=""),
    )
    with pytest.raises(node.NarrativeFailed):
        node.explain_result(make_input())


def test_a_hallucinated_figure_triggers_one_retry_with_the_critique(monkeypatch, configured):
    stub = StubModel(
        _reply("Buenos Aires leads with 36.4%, against a national average of 22.8%."),
        _reply(GOOD),
    )
    monkeypatch.setattr(node.client, "complete", stub)

    result = node.explain_result(make_input())
    assert result.narrative == GOOD
    assert result.source == "model_retry"
    assert len(stub.calls) == 2
    # The critique names the offending figure, so the second attempt differs
    # from the first by more than a reroll.
    assert "22.8" in stub.calls[1][-1]["content"]


def test_two_bad_answers_fail_rather_than_inventing_one(monkeypatch, configured):
    bad = _reply("Buenos Aires leads with a national average of 22.8%.")
    monkeypatch.setattr(node.client, "complete", StubModel(bad, bad))
    with pytest.raises(node.NarrativeFailed):
        node.explain_result(make_input())


def test_an_unreachable_provider_is_reported(monkeypatch, configured):
    def boom(*_args, **_kwargs):
        raise node.client.LlmUnavailable("connection refused")

    monkeypatch.setattr(node.client, "complete", boom)
    with pytest.raises(node.NarrativeFailed):
        node.explain_result(make_input())


def test_malformed_json_is_treated_as_a_retryable_failure(monkeypatch, configured):
    stub = StubModel("not json at all", _reply(GOOD))
    monkeypatch.setattr(node.client, "complete", stub)
    assert node.explain_result(make_input()).narrative == GOOD
    assert len(stub.calls) == 2


def test_a_fenced_json_object_is_still_read(monkeypatch, configured):
    monkeypatch.setattr(
        node.client, "complete", StubModel(f"```json\n{_reply(GOOD)}\n```")
    )
    assert node.explain_result(make_input()).narrative == GOOD


def test_the_prompt_carries_business_labels_and_never_the_sql(monkeypatch, configured):
    stub = StubModel(_reply("Due date activity peaks at 36.4%."))
    monkeypatch.setattr(node.client, "complete", stub)

    node.explain_result(
        make_input(
            rows=[{"fch_vcto": 36.4}],
            columns=["fch_vcto"],
            sql="SELECT fch_vcto FROM ds_a8kf2 JOIN other ON 1=1",
            column_labels={"fch_vcto": "Due date"},
        )
    )
    prompt = "\n".join(m["content"] for m in stub.calls[0])
    assert "Due date" in prompt
    assert "fch_vcto" not in prompt
    assert "JOIN" not in prompt
    assert "SELECT" not in prompt


def test_a_source_label_reaches_the_prompt(monkeypatch, configured):
    stub = StubModel(_reply(GOOD))
    monkeypatch.setattr(node.client, "complete", stub)
    node.explain_result(make_input(source_label="Q1 sales export"))
    assert "Q1 sales export" in stub.calls[0][-1]["content"]


def test_an_empty_result_is_still_narrated_by_the_model(monkeypatch, configured):
    """Previously short-circuited to a fixed template. There is no template now."""
    empty = "Nothing matches those conditions."
    stub = StubModel(_reply(empty))
    monkeypatch.setattr(node.client, "complete", stub)
    result = node.explain_result(make_input(rows=[], columns=["region"], row_count=0))
    assert result.narrative == empty
    assert len(stub.calls) == 1


def test_the_output_schema_rejects_a_confidence_outside_the_range():
    with pytest.raises(Exception):
        Explanation(narrative="x", confidence_score=1.5)
