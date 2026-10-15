"""Export one row per H-1B sponsor for the job hunter (job-hunter-auto).

Covers certified H-1B LCAs decided in the last two fiscal years loaded (currently
Oct 2024 - Jun 2026), for every employer with at least MIN_FILINGS of them. The job
hunter matches job postings' company names to `employer_key` and scores how actively
each company sponsors the roles you target.

Usage:
    python -m pipeline.export_sponsors                      # -> exports/h1b_sponsors.csv
    python -m pipeline.export_sponsors --out ../Automated-Job-Hunting/data/h1b_sponsors.csv
"""

import argparse
import sys
from pathlib import Path

import duckdb

WAREHOUSE = Path("data/warehouse.duckdb")
DEFAULT_OUT = Path("exports/h1b_sponsors.csv")
MIN_FILINGS = 3

# Role groups the job hunter can target (profile.toml [sponsorship] roles).
ROLE_GROUPS = {
    "analyst": ("data_analyst", "bi_analyst"),
    "data_science": ("data_scientist", "ml_engineer"),
    "ai": ("ai_engineer",),
    "data_engineering": ("data_engineer", "analytics_engineer"),
    "software": ("software_engineer",),
}

# A name-based guess; only some universities, affiliated hospitals and research
# nonprofits are cap-exempt, so the job hunter labels these "possibly".
CAP_EXEMPT_PATTERN = r"(?i)universit|college|institute|hospital|medical cent|health system|research|foundation"


def build_sql(min_filings: int) -> str:
    group_cols = ",\n        ".join(
        f"count(*) filter (where role_family in {tuple(roles) if len(roles) > 1 else f'({roles[0]!r})'}) as filings_{g}"
        for g, roles in ROLE_GROUPS.items()
    )
    return f"""
with recent as (
    select max(fiscal_year) - 1 as from_fy from fiscal_year_coverage
),
f as (
    select l.*
    from fct_lca_applications l, recent
    where l.is_certified and l.is_h1b and l.fiscal_year >= recent.from_fy
),
by_employer as (
    select
        employer_key,
        count(*) as filings_all,
        {group_cols},
        count(distinct date_trunc('month', decision_date)) as active_months,
        count(*) filter (where is_new_employment and fiscal_year = (select max(fiscal_year) from f)) as new_hires_latest_fy,
        round(avg(is_new_employment::int), 3) as new_hire_share,
        round(median(annual_wage) filter (where is_data_role and is_wage_valid)) as median_wage_data,
        max(decision_date) as last_filing,
        bool_or(is_willful_violator) as willful_violator,
        mode(naics_sector) as naics_sector,
        string_agg(distinct worksite_state, ',' order by worksite_state) as worksite_states
    from f
    group by employer_key
    having count(*) >= {min_filings}
),
uscis as (
    select employer_key,
           sum(new_employment_approved) as uscis_new_approved,
           sum(new_employment_denied) as uscis_new_denied
    from agg_employer_uscis_year
    where fiscal_year = (select max(fiscal_year) from fiscal_year_coverage where is_complete)
    group by employer_key
)
select
    e.employer_key,
    d.display_name as employer,
    e.* exclude (employer_key, willful_violator, worksite_states),
    (select count(distinct date_trunc('month', decision_date)) from f) as months_covered,
    coalesce(u.uscis_new_approved, 0) as uscis_new_approved,
    coalesce(u.uscis_new_denied, 0) as uscis_new_denied,
    regexp_matches(d.display_name, '{CAP_EXEMPT_PATTERN}') or e.naics_sector = '61' as possibly_cap_exempt
from by_employer e
join dim_employers d using (employer_key)
left join uscis u using (employer_key)
where not e.willful_violator
order by e.filings_all desc
"""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--min-filings", type=int, default=MIN_FILINGS)
    args = p.parse_args()

    if not WAREHOUSE.exists():
        sys.exit(f"{WAREHOUSE} not found; run `dbt build --project-dir transform --profiles-dir transform` first.")
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    rel = con.sql(build_sql(args.min_filings))
    rel.write_csv(str(args.out))
    rows = con.sql(f"select count(*) from read_csv('{args.out.as_posix()}')").fetchone()[0]
    print(f"wrote {args.out} ({rows:,} employers, {args.out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
