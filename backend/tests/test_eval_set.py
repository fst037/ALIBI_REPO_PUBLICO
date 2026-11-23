"""The evaluation set (`eval/`) stays internally consistent.

Offline and free: no model is called. These pin what the evaluation's
conclusions rest on — that the data is the same on every run, that every
reference query runs, and that the findings the rubrics expect are really in
the data. If a generator changes, a rubric that quietly stopped being true would
otherwise turn the judge's scores into noise.
"""

from __future__ import annotations

from eval.data import folders, reference_connection
from eval.questions import QUESTIONS


def test_the_data_is_deterministic() -> None:
    assert [t.csv for f in folders() for t in f.tables] == [t.csv for f in folders() for t in f.tables]


def test_every_question_is_well_formed_and_its_reference_runs() -> None:
    connection = reference_connection()
    known = {f.key for f in folders()}
    assert len({q.id for q in QUESTIONS}) == len(QUESTIONS) == 24
    for q in QUESTIONS:
        assert q.folder in known and q.kind in ("lookup", "multi", "explore"), q.id
        assert isinstance(q.rubric, tuple) and q.rubric and all(isinstance(p, str) for p in q.rubric), q.id
        for sql in q.reference_sql:
            assert connection.execute(sql).fetchall(), f"{q.id}: reference returned nothing: {sql}"


def test_the_planted_findings_are_in_the_data() -> None:
    c = reference_connection()
    one = lambda sql: c.execute(sql).fetchall()  # noqa: E731

    assert one("SELECT region FROM sales GROUP BY 1 ORDER BY SUM(amount) DESC LIMIT 1") == [("Buenos Aires",)]
    growth = dict(one("SELECT region, SUM(amount) FILTER (WHERE month >= '2024-10-01') / "
                      "SUM(amount) FILTER (WHERE month < '2024-04-01') FROM sales GROUP BY 1"))
    assert max(growth, key=growth.get) == "Mendoza"
    peak = one("SELECT month FROM sales WHERE product = 'Termo' GROUP BY 1 ORDER BY SUM(units) DESC LIMIT 3")
    assert {m for (m,) in peak} == {"2024-06-01", "2024-07-01", "2024-08-01"}

    satisfaction = dict(one("SELECT agent, AVG(satisfaction) FROM tickets GROUP BY 1"))
    assert min(satisfaction, key=satisfaction.get) == "Lucía"
    billing_q3 = one("SELECT AVG(resolution_hours) FILTER (WHERE month(CAST(opened_on AS DATE)) BETWEEN 7 AND 9) / "
                     "AVG(resolution_hours) FILTER (WHERE month(CAST(opened_on AS DATE)) NOT BETWEEN 7 AND 9) "
                     "FROM tickets WHERE category = 'Billing'")[0][0]
    assert billing_q3 > 2

    assert one("SELECT department FROM employees GROUP BY 1 HAVING COUNT(*) < 20") == [("Finance",)]
    sales_ratings = dict(one("SELECT r.year, AVG(r.rating) FROM reviews r JOIN employees e USING (employee_id) "
                             "WHERE e.department = 'Sales' GROUP BY 1"))
    assert sales_ratings[2023] - sales_ratings[2024] > 0.8


def test_every_question_has_its_spanish_version():
    from eval.questions import QUESTIONS, SPANISH, asked

    assert set(SPANISH) == {q.id for q in QUESTIONS}
    for q in QUESTIONS:
        assert asked(q, "en") == q.text
        assert asked(q, "es") == SPANISH[q.id] != q.text
