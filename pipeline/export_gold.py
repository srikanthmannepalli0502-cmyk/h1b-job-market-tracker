"""Export the gold tables the "Ask the data" assistant can query.

Small, aggregated, documented tables only (no row-level cases), written to
data/gold/*.parquet. The refresh workflow uploads them to the lake's gold
container, where the ask Function reads them.

Usage:
    python -m pipeline.export_gold
"""

import sys
from pathlib import Path

import duckdb

WAREHOUSE = Path("data/warehouse.duckdb")
OUT_DIR = Path("data/gold")

# table name -> SQL over the warehouse marts. Column names are what the model sees.
GOLD_TABLES = {
    "employer_role_year": """
        select a.display_name as employer, a.hq_state, a.fiscal_year, a.role_family,
               a.certified_applications, a.new_employment, a.change_employer, a.entry_level,
               a.worksite_states, round(a.median_wage) as median_wage,
               round(a.p25_wage) as p25_wage, round(a.p75_wage) as p75_wage
        from agg_employer_role_year a
    """,
    "employer_state_role_year": """
        select e.display_name as employer, f.fiscal_year, f.role_family, f.worksite_state,
               count(*) as certified_applications,
               round(median(f.annual_wage) filter (where f.is_wage_valid and f.is_full_time)) as median_wage
        from fct_lca_applications f
        join dim_employers e using (employer_key)
        where f.is_certified and f.is_h1b and f.worksite_state is not null
        group by all
    """,
    "employer_approvals": """
        select e.display_name as employer, e.hq_state, u.fiscal_year, u.match_type,
               u.new_employment_approved, u.new_employment_denied,
               u.change_of_employer_approved, u.change_of_employer_denied,
               u.continuation_approved, u.continuation_denied,
               u.total_approved, u.total_denied,
               round(u.new_employment_denial_rate, 4) as new_employment_denial_rate,
               round(u.total_denial_rate, 4) as total_denial_rate
        from agg_employer_uscis_year u
        join dim_employers e using (employer_key)
    """,
    "role_wages": """
        select fiscal_year, role_family, wage_level, worksite_state, applications,
               round(p10_wage) as p10_wage, round(p25_wage) as p25_wage, round(median_wage) as median_wage,
               round(p75_wage) as p75_wage, round(p90_wage) as p90_wage
        from agg_role_wages
    """,
    "state_role_year": """
        select fiscal_year, worksite_state as state, role_family,
               count(*) as certified_applications,
               count(distinct employer_key) as employers,
               round(median(annual_wage) filter (where is_wage_valid and is_full_time)) as median_wage
        from fct_lca_applications
        where is_certified and is_h1b and worksite_state is not null
        group by all
    """,
    "coverage": """
        select fiscal_year, first_decision, last_decision, months_covered, is_complete,
               applications, certified_h1b
        from fiscal_year_coverage
    """,
}


def main() -> int:
    if not WAREHOUSE.exists():
        print(f"{WAREHOUSE} not found. Run dbt build first.")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        for name, sql in GOLD_TABLES.items():
            path = OUT_DIR / f"{name}.parquet"
            con.execute(f"copy ({sql}) to '{path.as_posix()}' (format parquet, compression zstd)")
            rows = con.execute(f"select count(*) from read_parquet('{path.as_posix()}')").fetchone()[0]
            print(f"wrote {path} ({rows:,} rows, {path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
