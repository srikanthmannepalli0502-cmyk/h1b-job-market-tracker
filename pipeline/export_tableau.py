"""Export Tableau-ready CSVs from the warehouse into tableau/data/.

Friendly role labels, role groups and precomputed ranks (so "top 15" filters are
plain row filters in Tableau, not table calculations). tableau/build_workbook.py
packages these with the workbook.

Usage:
    python -m pipeline.export_tableau
"""

import sys
from pathlib import Path

import duckdb

WAREHOUSE = Path("data/warehouse.duckdb")
OUT_DIR = Path("tableau/data")

ROLES = {
    "data_analyst": ("Data Analyst", "Data & Analytics"),
    "bi_analyst": ("BI Analyst / Engineer", "Data & Analytics"),
    "data_engineer": ("Data Engineer", "Data & Analytics"),
    "analytics_engineer": ("Analytics Engineer", "Data & Analytics"),
    "data_scientist": ("Data Scientist", "Data & Analytics"),
    "ai_engineer": ("AI Engineer / Scientist", "AI & ML"),
    "ml_engineer": ("ML Engineer", "AI & ML"),
    "cybersecurity": ("Cybersecurity", "Security"),
    "cloud_engineer": ("Cloud Engineer / Architect", "Cloud & DevOps"),
    "devops_engineer": ("DevOps / SRE / Platform", "Cloud & DevOps"),
}
COMPARISON = {"software_engineer": ("Software Engineer", "Comparison"),
              "business_analyst": ("Business Analyst", "Comparison")}


def roles_cte(roles: dict) -> str:
    values = ", ".join(f"('{k}', '{label}', '{group}')" for k, (label, group) in roles.items())
    return f"roles(role_family, role, role_group) as (values {values})"


QUERIES = {
    # One row per employer, fiscal year and role, with USCIS new-hire decisions for the employer.
    "sponsors": f"""
        with {roles_cte(ROLES)}
        select a.display_name as employer, a.hq_state, a.fiscal_year, r.role, r.role_group,
               a.certified_applications, a.entry_level, a.new_employment as new_hires,
               round(a.median_wage) as median_wage,
               u.new_employment_approved as uscis_new_approved,
               u.new_employment_denied as uscis_new_denied,
               rank() over (partition by a.fiscal_year, a.role_family
                            order by a.certified_applications desc, a.display_name) as rank_in_role
        from agg_employer_role_year a
        join roles r using (role_family)
        left join agg_employer_uscis_year u on u.employer_key = a.employer_key and u.fiscal_year = a.fiscal_year
        order by a.fiscal_year, r.role, rank_in_role
    """,
    # National salary distribution per wage level.
    "wages": f"""
        with {roles_cte(ROLES)}
        select w.fiscal_year, r.role, r.role_group, w.wage_level, w.applications,
               round(w.p10_wage) as p10_wage, round(w.p25_wage) as p25_wage, round(w.median_wage) as median_wage,
               round(w.p75_wage) as p75_wage, round(w.p90_wage) as p90_wage
        from agg_role_wages w join roles r using (role_family)
        where w.worksite_state = 'ALL' and w.wage_level in ('I', 'II', 'III', 'IV', 'ALL')
        order by w.fiscal_year, r.role, w.wage_level
    """,
    # Certified applications by worksite state.
    "states": f"""
        with {roles_cte(ROLES)}, by_state as (
            select f.fiscal_year, r.role, r.role_group, f.worksite_state as state,
                   count(*) as certified_applications,
                   round(median(f.annual_wage) filter (where f.is_wage_valid and f.is_full_time)) as median_wage
            from fct_lca_applications f join roles r using (role_family)
            where f.is_certified and f.is_h1b and f.worksite_state is not null
            group by all
        )
        select *, rank() over (partition by fiscal_year, role order by certified_applications desc, state) as rank_in_role
        from by_state
        order by fiscal_year, role, rank_in_role
    """,
}


def growth_query(con) -> str:
    """Year-over-year change per role over the months both years cover (same rule as the web dashboard)."""
    months = dict(con.execute(
        "select fiscal_year, list(distinct month(decision_date)) from fct_lca_applications group by 1"
    ).fetchall())
    parts = []
    all_roles = {**ROLES, **COMPARISON}
    for fy in sorted(months):
        shared = sorted(set(months[fy]) & set(months.get(fy - 1, [])))
        if not shared:
            continue
        period = (f"FY{fy} vs FY{fy - 1} (full year)" if len(shared) == 12
                  else f"FY{fy} vs FY{fy - 1} (same {len(shared)} months)")
        parts.append(f"""
            select {fy} as fiscal_year, '{period}' as comparison, r.role, r.role_group,
                   count(*) filter (where f.fiscal_year = {fy - 1}) as prior_year,
                   count(*) filter (where f.fiscal_year = {fy}) as this_year
            from fct_lca_applications f join roles r using (role_family)
            where f.is_certified and f.is_h1b and month(f.decision_date) in ({", ".join(map(str, shared))})
            group by all""")
    union = " union all ".join(parts)
    return f"""
        with {roles_cte(all_roles)}, g as ({union})
        select *, this_year * 1.0 / nullif(prior_year, 0) - 1 as pct_change
        from g order by fiscal_year, pct_change desc
    """


def main() -> int:
    if not WAREHOUSE.exists():
        print(f"{WAREHOUSE} not found. Run dbt build first.")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        queries = {**QUERIES, "growth": growth_query(con)}
        for name, sql in queries.items():
            path = OUT_DIR / f"{name}.csv"
            con.execute(f"copy ({sql}) to '{path.as_posix()}' (header, delimiter ',')")
            rows = con.execute(f"select count(*) from read_csv('{path.as_posix()}')").fetchone()[0]
            print(f"wrote {path} ({rows:,} rows, {path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
