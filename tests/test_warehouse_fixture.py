"""Business-logic checks against the warehouse built from the synthetic fixture.

CI runs: make_bronze_fixture -> dbt build -> pytest with JOBS_FIXTURE=1.
Skipped otherwise, because real data doesn't contain these known cases.
"""

import os
from pathlib import Path

import duckdb
import pytest

WAREHOUSE = Path("data/warehouse.duckdb")

pytestmark = pytest.mark.skipif(
    os.getenv("JOBS_FIXTURE") != "1" or not WAREHOUSE.exists(),
    reason="needs the fixture warehouse (set JOBS_FIXTURE=1 after dbt build on fixture data)",
)


@pytest.fixture(scope="module")
def con():
    with duckdb.connect(str(WAREHOUSE), read_only=True) as c:
        yield c


def one(con, sql, *params):
    return con.execute(sql, list(params)).fetchone()


def test_latest_release_wins(con):
    (status,) = one(con, "select case_status from fct_lca_applications where case_number = 'I-200-2025-000000'")
    assert status == "Certified - Withdrawn"


def test_employer_spellings_merge(con):
    keys = con.execute("select employer_key, name_variants from dim_employers where employer_key like 'AMAZON%'").fetchall()
    assert keys == [("AMAZON COM SERVICES", 2)]
    (acme,) = one(con, "select count(*) from dim_employers where employer_key like 'ACME%'")
    assert acme == 1


def test_wage_typo_is_flagged(con):
    (valid,) = one(con, "select is_wage_valid from fct_lca_applications where annual_wage > 1e9")
    assert valid is False


def test_hourly_wages_are_annualized(con):
    bad = one(con, """
        select count(*) from fct_lca_applications f
        join stg_lca__applications s using (case_number, source_file)
        where s.wage_unit = 'Hour' and abs(f.annual_wage - s.wage_from * 2080) > 0.01
    """)[0]
    assert bad == 0


@pytest.mark.parametrize("title, role", [
    ("Data Analyst", "data_analyst"),
    ("Business Intelligence Engineer II", "bi_analyst"),
    ("Analytics Engineer", "analytics_engineer"),
    ("Machine Learning Engineer", "ml_engineer"),
    ("Assistant Professor of Data Science", "academic"),
    ("Accountant", "other"),
])
def test_role_classification(con, title, role):
    roles = {r for (r,) in con.execute("select distinct role_family from fct_lca_applications where job_title = ?", [title]).fetchall()}
    assert roles == {role}


def test_seniority_from_title(con):
    (s,) = one(con, "select distinct seniority from fct_lca_applications where job_title = 'Senior Data Analyst'")
    assert s == "senior"
