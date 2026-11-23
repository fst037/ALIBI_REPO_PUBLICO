"""The evaluation's data: three synthetic folders with patterns planted on purpose.

Synthetic, not real exports, for one reason: every question in `questions.py`
needs an answer that is *known*, not one we believe. Each generator is seeded
and plants a small number of findings an investigation should surface — a
fast-growing region driven by one product, a seasonal peak, a stockout, a
quarter in which one category's resolution time doubled, an agent whose low
satisfaction is not explained by the tickets they get, a department whose
ratings fell. The reference SQL in `questions.py` then measures the truth from
the same rows, independently of ALIBI.

Plain comma-separated files with ISO dates and dot decimals, so the evaluation
measures the harness and not the CSV dialect detection (Module B has its own
tests for that).
"""

from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class Table:
    #: The name the reference SQL uses for it (never what ALIBI calls it).
    name: str
    filename: str
    csv: str


@dataclass(frozen=True)
class Folder:
    key: str
    name: str
    tables: list[Table]


def _csv(header: list[str], rows: list[list[object]]) -> str:
    out = io.StringIO()
    out.write(",".join(header) + "\n")
    for row in rows:
        out.write(",".join(str(v) for v in row) + "\n")
    return out.getvalue()


def sales() -> Folder:
    """Retail sales, 2024: 4 regions × 3 products × 2 channels × 12 months.

    Planted: Buenos Aires is the largest region; Mendoza grows fastest, almost
    entirely through Termo (+8 %/month there, ~1 % elsewhere); Termo peaks in the
    southern winter (Jun–Aug); Córdoba's Yerba collapses in Aug–Sep (a stockout);
    the online share of units climbs from ~20 % to ~42 %.
    """
    rng = random.Random(2024)
    regions = {"Buenos Aires": 1.6, "Córdoba": 1.0, "Mendoza": 0.7, "Santa Fe": 0.9}
    products = {"Yerba": (120, 32.0), "Mate": (60, 54.0), "Termo": (40, 210.0)}
    rows: list[list[object]] = []
    for month in range(1, 13):
        online_share = 0.20 + 0.02 * (month - 1)
        for region, weight in regions.items():
            for product, (base, price) in products.items():
                if region == "Mendoza":
                    trend = 1 + (0.08 if product == "Termo" else 0.01) * (month - 1)
                else:
                    trend = 1 + 0.005 * (month - 1)
                season = 1.0
                if product == "Termo":
                    season = {6: 1.5, 7: 1.5, 8: 1.5, 5: 1.2, 9: 1.2}.get(month, 1.0)
                stockout = 0.3 if (region == "Córdoba" and product == "Yerba" and month in (8, 9)) else 1.0
                units = base * weight * trend * season * stockout * rng.uniform(0.92, 1.08)
                online = round(units * online_share)
                store = round(units) - online
                for channel, n in (("Online", online), ("Store", store)):
                    rows.append([region, product, channel, f"2024-{month:02d}-01", n, round(n * price, 2)])
    return Folder(
        "sales",
        "Ventas 2024",
        [Table("sales", "ventas_2024.csv",
               _csv(["region", "product", "channel", "month", "units", "amount"], rows))],
    )


def tickets() -> Folder:
    """Support tickets, 2024 (1,600).

    Planted: Technical is the largest category; Chat resolves fastest and Email
    slowest; Billing's resolution time is ~2.5× in Jul–Sep; satisfaction falls
    as resolution time grows; agent Lucía scores ~1.4 points lower than everyone
    else although tickets are assigned to agents at random (so her mix is the
    same as theirs).
    """
    rng = random.Random(1600)
    categories = [("Technical", 0.40), ("Billing", 0.25), ("Account", 0.20), ("Shipping", 0.15)]
    priorities = [("Low", 0.35, 30.0), ("Medium", 0.35, 20.0), ("High", 0.20, 10.0), ("Urgent", 0.10, 4.0)]
    channels = [("Email", 0.45, 1.2), ("Chat", 0.35, 0.6), ("Phone", 0.20, 0.8)]
    agents = ["Ana", "Bruno", "Carla", "Diego", "Elena", "Facundo", "Lucía", "Martín"]

    def pick(options):  # noqa: ANN001, ANN202
        r, acc = rng.random(), 0.0
        for option in options:
            acc += option[1]
            if r <= acc:
                return option
        return options[-1]

    rows: list[list[object]] = []
    start = date(2024, 1, 1)
    for i in range(1, 1601):
        opened = start + timedelta(days=rng.randrange(366))
        category = pick(categories)[0]
        priority, _, base_hours = pick(priorities)
        channel, _, channel_factor = pick(channels)
        agent = rng.choice(agents)
        spike = 2.5 if (category == "Billing" and opened.month in (7, 8, 9)) else 1.0
        hours = base_hours * channel_factor * spike * math.exp(rng.gauss(0, 0.4))
        score = 4.6 - 0.04 * hours - (1.4 if agent == "Lucía" else 0.0) + rng.gauss(0, 0.6)
        satisfaction = max(1, min(5, round(score)))
        rows.append([f"T{i:05d}", opened.isoformat(), category, priority, channel, agent,
                     round(hours, 1), satisfaction])
    return Folder(
        "tickets",
        "Soporte 2024",
        [Table("tickets", "tickets_2024.csv",
               _csv(["ticket_id", "opened_on", "category", "priority", "channel", "agent",
                     "resolution_hours", "satisfaction"], rows))],
    )


def people() -> Folder:
    """Employees and their annual reviews — two tables, so questions need a JOIN.

    Planted: Engineering pays most; Finance is the only department under 20
    people (Marketing is exactly 20); Remote employees rate ~0.5 higher in every
    department; Sales' ratings fall ~0.8 from 2023 to 2024; rating is
    independent of salary; nobody hired in 2024 has a 2023 review.
    """
    rng = random.Random(160)
    departments = [("Engineering", 50, 3_200_000), ("Sales", 40, 2_400_000), ("Operations", 35, 2_000_000),
                   ("Marketing", 20, 2_200_000), ("Finance", 15, 2_600_000)]
    levels = [("Junior", 0.75), ("Mid", 1.0), ("Senior", 1.35)]
    locations = ["Buenos Aires", "Córdoba", "Remote"]
    employees: list[list[object]] = []
    reviews: list[list[object]] = []
    n = 0
    for department, count, base in departments:
        for _ in range(count):
            n += 1
            level, factor = rng.choice(levels)
            location = rng.choice(locations)
            hired = date(2018, 1, 1) + timedelta(days=rng.randrange(365 * 7))
            salary = round(base * factor * rng.uniform(0.9, 1.1), -3)
            employee_id = f"E{n:04d}"
            employees.append([employee_id, department, level, location, hired.isoformat(), int(salary)])
            for year in (2023, 2024):
                if hired.year >= year:
                    continue
                rating = 3.4 + (0.5 if location == "Remote" else 0.0)
                if department == "Sales" and year == 2024:
                    rating -= 0.8
                reviews.append([employee_id, year, max(1, min(5, round(rating + rng.gauss(0, 0.7))))])
    return Folder(
        "people",
        "Personas",
        [
            Table("employees", "empleados.csv",
                  _csv(["employee_id", "department", "level", "location", "hire_date", "monthly_salary"],
                       employees)),
            Table("reviews", "evaluaciones.csv", _csv(["employee_id", "year", "rating"], reviews)),
        ],
    )


def folders() -> list[Folder]:
    return [sales(), tickets(), people()]


def reference_connection():  # noqa: ANN201 - duckdb connection
    """DuckDB with every table under its reference name, read from the same CSV text."""
    import duckdb
    import pandas as pd

    connection = duckdb.connect()
    for folder in folders():
        for table in folder.tables:
            frame = pd.read_csv(io.StringIO(table.csv))
            connection.register(f"{table.name}_df", frame)
            connection.execute(f"CREATE TABLE {table.name} AS SELECT * FROM {table.name}_df")
    return connection


__all__ = ["Folder", "Table", "folders", "reference_connection"]
