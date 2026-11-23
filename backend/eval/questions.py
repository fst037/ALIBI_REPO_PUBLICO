"""The evaluation questions: 24, over the three folders in `data.py`.

Each question carries:

* `kind` — what it asks of the system:
  - `lookup`: one query answers it (the one-shot pipeline's home ground);
  - `multi`: two or more things to establish (a comparison plus a cause, a
    finding plus "does it hold across …");
  - `explore`: open-ended ("what should I know about …"), where depth is the point.
* `reference_sql` — run directly in DuckDB over the raw CSVs (`data.reference_connection`),
  never through ALIBI. Its results are the ground truth the judge sees.
* `rubric` — the points a complete answer must make, each checkable against the
  reference results. Written from the planted patterns, then confirmed against
  the reference numbers.

Each question is asked in Spanish by default (`SPANISH`, `--lang es` in
`run_eval.py`): ALIBI is for Spanish-speaking PyMEs, and the answers are written
in Spanish. The English `text` is kept: it is what
the rubric was written against, and `--lang en` reruns the set in English to
measure what the language costs. Module A's fine-tune was trained on English
questions, but it never sees the user's question directly: the planner writes
the Spider/BIRD-style questions it gets.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Question:
    id: str
    folder: str
    kind: str
    text: str
    reference_sql: tuple[str, ...]
    rubric: tuple[str, ...]


QUESTIONS: list[Question] = [
    # ---- Sales -----------------------------------------------------------------
    Question(
        "S1", "sales", "lookup", "What were total sales by region in 2024?",
        ("SELECT region, ROUND(SUM(amount), 2) AS total_amount FROM sales GROUP BY region ORDER BY total_amount DESC",),
        ("Buenos Aires has the highest total sales, with its correct total.",
         "The other regions are given in the correct order (Córdoba, Santa Fe, Mendoza) or with correct totals."),
    ),
    Question(
        "S2", "sales", "lookup", "Which product sold the most units?",
        ("SELECT product, SUM(units) AS units FROM sales GROUP BY product ORDER BY units DESC",),
        ("Yerba sold the most units, with its correct unit count.",),
    ),
    Question(
        "S3", "sales", "multi", "Which region is growing fastest, and is it driven by one product?",
        ("SELECT region, ROUND(SUM(amount) FILTER (WHERE month < '2024-04-01'), 2) AS q1, "
         "ROUND(SUM(amount) FILTER (WHERE month >= '2024-10-01'), 2) AS q4 FROM sales GROUP BY region ORDER BY region",
         "SELECT product, ROUND(SUM(amount) FILTER (WHERE month < '2024-04-01'), 2) AS q1, "
         "ROUND(SUM(amount) FILTER (WHERE month >= '2024-10-01'), 2) AS q4 FROM sales WHERE region = 'Mendoza' "
         "GROUP BY product ORDER BY product"),
        ("Mendoza is identified as the fastest-growing region.",
         "Termo is identified as the product driving Mendoza's growth (the other products grow far less)."),
    ),
    Question(
        "S4", "sales", "multi", "Is there a seasonal pattern in Termo sales?",
        ("SELECT month, SUM(units) AS units FROM sales WHERE product = 'Termo' GROUP BY month ORDER BY month",),
        ("Termo sales peak in June–August (the southern winter).",
         "The size of the peak is quantified or the shoulder months (May, September) are noted as elevated."),
    ),
    Question(
        "S5", "sales", "multi", "Did any region and product combination have an unusual drop during the year?",
        ("SELECT region, product, month, SUM(units) AS units FROM sales WHERE product = 'Yerba' AND region = 'Córdoba' "
         "GROUP BY ALL ORDER BY month",),
        ("Córdoba's Yerba sales drop sharply in August and September.",
         "Sales recover afterwards (October onwards), i.e. the drop is temporary."),
    ),
    Question(
        "S6", "sales", "lookup", "How did the online share of units change over the year?",
        ("SELECT month, ROUND(100.0 * SUM(units) FILTER (WHERE channel = 'Online') / SUM(units), 1) AS online_pct "
         "FROM sales GROUP BY month ORDER BY month",),
        ("The online share rises steadily through the year.",
         "It goes from about 20% in January to about 42% in December."),
    ),
    Question(
        "S7", "sales", "lookup", "Compare first-quarter and fourth-quarter sales for each region.",
        ("SELECT region, ROUND(SUM(amount) FILTER (WHERE month < '2024-04-01'), 2) AS q1, "
         "ROUND(SUM(amount) FILTER (WHERE month >= '2024-10-01'), 2) AS q4 FROM sales GROUP BY region ORDER BY region",),
        ("Every region's Q4 is compared with its Q1, with correct figures.",
         "Mendoza shows by far the largest relative increase."),
    ),
    Question(
        "S8", "sales", "explore", "What should I know about our 2024 sales?",
        ("SELECT region, ROUND(SUM(amount), 2) AS total_amount FROM sales GROUP BY region ORDER BY total_amount DESC",
         "SELECT month, SUM(units) FILTER (WHERE product = 'Termo') AS termo_units, "
         "ROUND(100.0 * SUM(units) FILTER (WHERE channel = 'Online') / SUM(units), 1) AS online_pct "
         "FROM sales GROUP BY month ORDER BY month",
         "SELECT region, ROUND(SUM(amount) FILTER (WHERE month < '2024-04-01'), 2) AS q1, "
         "ROUND(SUM(amount) FILTER (WHERE month >= '2024-10-01'), 2) AS q4 FROM sales GROUP BY region ORDER BY region"),
        ("Buenos Aires is the largest region.",
         "Mendoza is growing fastest.",
         "Termo has a winter (Jun–Aug) peak.",
         "The online share is rising."),
    ),
    # ---- Support tickets -------------------------------------------------------
    Question(
        "T1", "tickets", "lookup", "How many tickets did we get in each category?",
        ("SELECT category, COUNT(*) AS tickets FROM tickets GROUP BY category ORDER BY tickets DESC",),
        ("Technical is the largest category, with its correct count.",
         "The counts for Billing, Account and Shipping are correct or correctly ordered."),
    ),
    Question(
        "T2", "tickets", "lookup", "Which channel resolves tickets fastest?",
        ("SELECT channel, ROUND(AVG(resolution_hours), 1) AS avg_hours FROM tickets GROUP BY channel ORDER BY avg_hours",),
        ("Chat is the fastest channel, with its correct average.",
         "Email is the slowest."),
    ),
    Question(
        "T3", "tickets", "multi", "Did resolution times get worse at any point in the year, and for which category?",
        ("SELECT category, EXTRACT(quarter FROM CAST(opened_on AS DATE)) AS quarter, "
         "ROUND(AVG(resolution_hours), 1) AS avg_hours FROM tickets GROUP BY ALL ORDER BY category, quarter",),
        ("Resolution times worsen in the third quarter (July–September).",
         "The worsening is specific to Billing; the other categories stay flat."),
    ),
    Question(
        "T4", "tickets", "multi",
        "Which agent has the lowest customer satisfaction, and is it because they get harder tickets?",
        ("SELECT agent, ROUND(AVG(satisfaction), 2) AS avg_satisfaction, COUNT(*) AS tickets, "
         "ROUND(100.0 * AVG(CASE WHEN priority IN ('High', 'Urgent') THEN 1 ELSE 0 END), 1) AS pct_high_or_urgent, "
         "ROUND(AVG(resolution_hours), 1) AS avg_hours FROM tickets GROUP BY agent ORDER BY avg_satisfaction",),
        ("Lucía has the lowest satisfaction (about 2.5 against about 3.7 for the others).",
         "Her ticket mix (priority share, or resolution time) is similar to the other agents', so harder "
         "tickets do not explain it."),
    ),
    Question(
        "T5", "tickets", "multi", "Is there a relationship between how long a ticket takes and customer satisfaction?",
        ("SELECT satisfaction, COUNT(*) AS tickets, ROUND(AVG(resolution_hours), 1) AS avg_hours FROM tickets "
         "GROUP BY satisfaction ORDER BY satisfaction",),
        ("Longer resolution times go with lower satisfaction.",
         "The relationship is shown with figures (e.g. average hours per satisfaction score)."),
    ),
    Question(
        "T6", "tickets", "lookup", "What is the average satisfaction for each priority level?",
        ("SELECT priority, ROUND(AVG(satisfaction), 2) AS avg_satisfaction FROM tickets GROUP BY priority "
         "ORDER BY avg_satisfaction DESC",),
        ("Each priority's average satisfaction is given correctly.",
         "Urgent (the fastest-resolved) has the highest satisfaction and Low the lowest."),
    ),
    Question(
        "T7", "tickets", "explore", "Give me an overview of our support performance in 2024.",
        ("SELECT COUNT(*) AS tickets, ROUND(AVG(resolution_hours), 1) AS avg_hours, "
         "ROUND(AVG(satisfaction), 2) AS avg_satisfaction FROM tickets",
         "SELECT category, EXTRACT(quarter FROM CAST(opened_on AS DATE)) AS quarter, "
         "ROUND(AVG(resolution_hours), 1) AS avg_hours FROM tickets GROUP BY ALL ORDER BY category, quarter",
         "SELECT agent, ROUND(AVG(satisfaction), 2) AS avg_satisfaction FROM tickets GROUP BY agent "
         "ORDER BY avg_satisfaction"),
        ("The overall volume (1,600 tickets) or averages are stated correctly.",
         "Billing's third-quarter slowdown is mentioned.",
         "Lucía's low satisfaction is mentioned.",
         "Channel or priority differences in resolution time are mentioned."),
    ),
    Question(
        "T8", "tickets", "lookup", "How many urgent tickets took more than 8 hours to resolve?",
        ("SELECT COUNT(*) AS urgent_over_8h FROM tickets WHERE priority = 'Urgent' AND resolution_hours > 8",),
        ("The correct count is given.",),
    ),
    # ---- People (two tables) ------------------------------------------------------
    Question(
        "H1", "people", "lookup", "What is the average monthly salary in each department?",
        ("SELECT department, ROUND(AVG(monthly_salary)) AS avg_salary FROM employees GROUP BY department "
         "ORDER BY avg_salary DESC",),
        ("Engineering has the highest average salary, with a correct figure.",
         "The other departments' averages are correct or correctly ordered."),
    ),
    Question(
        "H2", "people", "multi", "Which department's performance ratings changed the most from 2023 to 2024?",
        ("SELECT e.department, r.year, ROUND(AVG(r.rating), 2) AS avg_rating FROM reviews r "
         "JOIN employees e USING (employee_id) GROUP BY ALL ORDER BY e.department, r.year",),
        ("Sales is identified as the department that changed most.",
         "The change is a drop, from about 3.8 to about 2.5."),
    ),
    Question(
        "H3", "people", "multi", "Do remote employees get better performance ratings than office employees?",
        ("SELECT e.location, r.year, ROUND(AVG(r.rating), 2) AS avg_rating, COUNT(*) AS reviews FROM reviews r "
         "JOIN employees e USING (employee_id) GROUP BY ALL ORDER BY r.year, avg_rating DESC",),
        ("Yes: Remote employees rate higher than those in Buenos Aires and Córdoba.",
         "The gap is quantified (about half a point)."),
    ),
    Question(
        "H4", "people", "lookup", "How many people did we hire each year?",
        ("SELECT EXTRACT(year FROM CAST(hire_date AS DATE)) AS year, COUNT(*) AS hires FROM employees "
         "GROUP BY year ORDER BY year",),
        ("The hires for each year 2018–2024 are correct.",),
    ),
    Question(
        "H5", "people", "multi", "Is there a link between salary and performance rating in 2024?",
        ("SELECT r.rating, COUNT(*) AS employees, ROUND(AVG(e.monthly_salary)) AS avg_salary FROM reviews r "
         "JOIN employees e USING (employee_id) WHERE r.year = 2024 GROUP BY r.rating ORDER BY r.rating",
         "SELECT ROUND(CORR(e.monthly_salary, r.rating), 3) AS correlation FROM reviews r "
         "JOIN employees e USING (employee_id) WHERE r.year = 2024"),
        ("The link is weak at most (correlation about 0.16): average salary differs little between ratings "
         "3, 4 and 5. An answer claiming a strong link is wrong.",),
    ),
    Question(
        "H6", "people", "multi",
        "Which location has the highest average 2024 rating, and does that hold across departments?",
        ("SELECT e.department, e.location, ROUND(AVG(r.rating), 2) AS avg_rating, COUNT(*) AS reviews "
         "FROM reviews r JOIN employees e USING (employee_id) WHERE r.year = 2024 GROUP BY ALL "
         "ORDER BY e.department, avg_rating DESC",
         "SELECT e.location, ROUND(AVG(r.rating), 2) AS avg_rating FROM reviews r JOIN employees e "
         "USING (employee_id) WHERE r.year = 2024 GROUP BY e.location ORDER BY avg_rating DESC"),
        ("Remote has the highest average 2024 rating (about 3.6 against about 3.1).",
         "It holds in Engineering, Marketing, Operations and Sales; Finance is a tie (3.50 in every "
         "location, small groups). A claim that it holds everywhere without exception, or fails broadly, is wrong."),
    ),
    Question(
        "H7", "people", "explore", "Give me an overview of our workforce.",
        ("SELECT department, COUNT(*) AS headcount, ROUND(AVG(monthly_salary)) AS avg_salary FROM employees "
         "GROUP BY department ORDER BY headcount DESC",
         "SELECT location, COUNT(*) AS headcount FROM employees GROUP BY location ORDER BY headcount DESC",
         "SELECT e.department, r.year, ROUND(AVG(r.rating), 2) AS avg_rating FROM reviews r "
         "JOIN employees e USING (employee_id) GROUP BY ALL ORDER BY e.department, r.year"),
        ("Headcount (160) or headcount by department is stated correctly.",
         "Engineering is the largest and best-paid department.",
         "Sales' fall in ratings, or the Remote rating advantage, is mentioned."),
    ),
    Question(
        "H8", "people", "lookup", "Which departments have fewer than 20 employees?",
        ("SELECT department, COUNT(*) AS headcount FROM employees GROUP BY department ORDER BY headcount",),
        ("Only Finance (15) is listed; Marketing (exactly 20) is not.",),
    ),
]


# Rioplatense Spanish, in the voseo the product uses ("Preguntá sobre tus datos…").
# Product and category names stay as they are in the data (Yerba, Termo, Online).
SPANISH: dict[str, str] = {
    "S1": "¿Cuánto vendimos por región en 2024?",
    "S2": "¿Qué producto vendió más unidades?",
    "S3": "¿Qué región está creciendo más rápido, y se debe a un solo producto?",
    "S4": "¿Hay un patrón estacional en las ventas de Termo?",
    "S5": "¿Alguna combinación de región y producto tuvo una caída fuera de lo normal durante el año?",
    "S6": "¿Cómo cambió durante el año la participación online en las unidades vendidas?",
    "S7": "Compará las ventas del primer y del cuarto trimestre de cada región.",
    "S8": "¿Qué debería saber sobre nuestras ventas de 2024?",
    "T1": "¿Cuántos tickets recibimos en cada categoría?",
    "T2": "¿Qué canal resuelve los tickets más rápido?",
    "T3": "¿Empeoraron los tiempos de resolución en algún momento del año, y en qué categoría?",
    "T4": "¿Qué agente tiene la satisfacción de clientes más baja, y es porque le tocan tickets más difíciles?",
    "T5": "¿Hay relación entre lo que tarda un ticket y la satisfacción del cliente?",
    "T6": "¿Cuál es la satisfacción promedio de cada nivel de prioridad?",
    "T7": "Dame un panorama de cómo rindió el soporte en 2024.",
    "T8": "¿Cuántos tickets urgentes tardaron más de 8 horas en resolverse?",
    "H1": "¿Cuál es el sueldo mensual promedio de cada departamento?",
    "H2": "¿Qué departamento tuvo el mayor cambio en sus calificaciones de desempeño de 2023 a 2024?",
    "H3": "¿Los empleados remotos tienen mejores calificaciones de desempeño que los que van a la oficina?",
    "H4": "¿Cuántas personas contratamos cada año?",
    "H5": "¿Hay relación entre el sueldo y la calificación de desempeño en 2024?",
    "H6": "¿Qué ubicación tiene la calificación promedio más alta en 2024, y se mantiene en todos los departamentos?",
    "H7": "Dame un panorama de nuestra plantilla.",
    "H8": "¿Qué departamentos tienen menos de 20 empleados?",
}


def asked(question: Question, lang: str) -> str:
    """The question as it is put to ALIBI: Spanish (`es`, the default) or the English original."""
    if lang == "en":
        return question.text
    if lang == "es":
        return SPANISH[question.id]
    raise ValueError(f"unknown language {lang!r} (es or en)")


__all__ = ["QUESTIONS", "SPANISH", "Question", "asked"]
