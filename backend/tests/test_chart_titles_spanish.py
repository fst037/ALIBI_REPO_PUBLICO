"""Chart titles come out in Spanish, whatever language the adapter was trained in."""

from app.orchestrator.nodes.visualize import _localize_titles


def test_the_adapters_english_phrasing_is_translated_in_chart_and_axis_titles() -> None:
    spec = {
        "mark": "line",
        "title": "Total Costo by Mes",
        "encoding": {
            "x": {"field": "mes", "type": "temporal", "axis": {"title": "Number of rows"}},
            "y": {"field": "costo", "type": "quantitative", "title": "Total of costo"},
        },
    }
    out = _localize_titles(spec)
    assert out["title"] == "Total Costo por Mes"
    assert out["encoding"]["x"]["axis"]["title"] == "Cantidad de filas"
    assert out["encoding"]["y"]["title"] == "Total de costo"


def test_nothing_but_the_titles_changes_and_the_input_is_left_alone() -> None:
    spec = {"mark": "bar", "encoding": {"x": {"field": "by", "type": "nominal"}}}
    assert _localize_titles(spec) == spec

    original = {"title": "Sales by Region", "encoding": {}}
    _localize_titles(original)
    assert original["title"] == "Sales by Region"
