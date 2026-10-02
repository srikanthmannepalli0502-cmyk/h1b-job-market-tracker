"""Write a small synthetic bronze dataset so CI can run the full dbt pipeline
without downloading DOL's ~700 MB of Excel files.

It deliberately includes the messy cases the real data has: the same case in two
releases with different statuses, employer-name spelling variants, a wage typo,
an hourly wage, and seniority in titles.

Usage:
    python -m tests.fixtures.make_bronze_fixture [output_dir]
"""

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from pipeline.ingest_lca import KEEP_COLUMNS

EMPLOYERS = [
    ("Amazon.com Services LLC", "WA"), ("AMAZON.COM SERVICES LLC", "WA"),
    ("Acme Analytics, Inc.", "NY"), ("ACME ANALYTICS INC", "NY"),
    ("Globex Corporation", "TX"), ("Initech LLC", "CA"), ("Umbrella Health, Inc", "MA"),
]
TITLES = [
    "Data Analyst", "Senior Data Analyst", "Business Intelligence Engineer II", "Data Engineer",
    "Data Scientist", "Software Engineer", "Analytics Engineer", "Machine Learning Engineer",
    "Business Analyst", "Assistant Professor of Data Science", "Accountant",
    "Security Engineer II", "DevSecOps Engineer", "Cloud Data Engineer", "Cloud Solution Architect",
    "Site Reliability Engineer", "Senior AI/ML Engineer", "Generative AI Engineer",
    "Quality Reliability Engineer",
]
STATES = ["WA", "NY", "TX", "CA", "MA", "NJ", "IL"]
LEVELS = ["I", "II", "III", "IV", None]


def make_rows(fy: int, quarter: int, start: date, n: int, rng: random.Random, case_offset: int) -> list[dict]:
    rows = []
    for i in range(n):
        employer, emp_state = rng.choice(EMPLOYERS)
        decided = start + timedelta(days=rng.randrange(0, 89))
        hourly = i % 25 == 0
        wage = rng.randrange(40, 90) if hourly else rng.randrange(65_000, 190_000)
        row = {c: None for c in KEEP_COLUMNS}
        row.update({
            "CASE_NUMBER": f"I-200-{fy}-{case_offset + i:06d}",
            "CASE_STATUS": rng.choices(["Certified", "Certified - Withdrawn", "Withdrawn", "Denied"], [90, 5, 3, 2])[0],
            "RECEIVED_DATE": f"{decided - timedelta(days=7)} 00:00:00",
            "DECISION_DATE": f"{decided} 00:00:00",
            "VISA_CLASS": "H-1B" if i % 20 else "E-3 Australian",
            "JOB_TITLE": rng.choice(TITLES),
            "SOC_CODE": "15-2051.00",
            "FULL_TIME_POSITION": "Y" if i % 15 else "N",
            "TOTAL_WORKER_POSITIONS": "1",
            "NEW_EMPLOYMENT": str(rng.choice([0, 1])),
            "CHANGE_EMPLOYER": str(rng.choice([0, 1])),
            "CONTINUED_EMPLOYMENT": "0",
            "EMPLOYER_NAME": employer,
            "EMPLOYER_STATE": emp_state,
            "NAICS_CODE": "541511",
            "WORKSITE_CITY": "Seattle",
            "WORKSITE_STATE": rng.choice(STATES),
            "WORKSITE_POSTAL_CODE": "98109",
            "WAGE_RATE_OF_PAY_FROM": str(wage),
            "WAGE_UNIT_OF_PAY": "Hour" if hourly else "Year",
            "PREVAILING_WAGE": str(wage),
            "PW_UNIT_OF_PAY": "Hour" if hourly else "Year",
            "PW_WAGE_LEVEL": rng.choice(LEVELS),
            "WILLFUL_VIOLATOR": "N",
        })
        rows.append(row)
    return rows


def main(out_dir: Path = Path("data/bronze/lca")) -> None:
    rng = random.Random(42)
    out_dir.mkdir(parents=True, exist_ok=True)
    releases = [(2025, 1, date(2024, 10, 1)), (2025, 2, date(2025, 1, 1)),
                (2025, 3, date(2025, 4, 1)), (2025, 4, date(2025, 7, 1)), (2026, 1, date(2025, 10, 1))]

    first_case = None
    for n, (fy, q, start) in enumerate(releases):
        rows = make_rows(fy, q, start, 300, rng, case_offset=n * 1000)
        if n == 0:
            first_case = dict(rows[0])  # I-200-2025-000000, published in Q1
        if n == 1:
            # The same case re-published in Q2 with a new status.
            again = dict(first_case)
            again["CASE_STATUS"] = "Certified - Withdrawn"
            rows.append(again)
        if n == 2:
            rows[1]["WAGE_RATE_OF_PAY_FROM"] = "1111111111"  # the real data has typos like this
        df = pl.DataFrame(rows, schema={c: pl.String for c in KEEP_COLUMNS}).with_columns(
            pl.lit(fy).alias("SOURCE_FISCAL_YEAR"),
            pl.lit(q).alias("SOURCE_QUARTER"),
            pl.lit(f"LCA_Disclosure_Data_FY{fy}_Q{q}.xlsx").alias("SOURCE_FILE"),
        )
        df.columns = [c.lower() for c in df.columns]
        path = out_dir / f"lca_fy{fy}_q{q}.parquet"
        df.write_parquet(path)
        print(f"wrote {path} ({df.height} rows)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/bronze/lca"))
