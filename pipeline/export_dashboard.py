"""Export small JSON files from the DuckDB warehouse for the static web dashboard.

Usage:
    python -m pipeline.export_dashboard
"""

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

WAREHOUSE = Path("data/warehouse.duckdb")
OUT_DIR = Path("dashboard/data")

DATA_ROLES = ("data_analyst", "bi_analyst", "data_engineer", "analytics_engineer", "data_scientist", "ml_engineer")
# Employers need at least this many certified data-role applications to be listed.
MIN_EMPLOYER_APPS = 3


def rows(con, sql: str, params=None) -> list[dict]:
    cur = con.execute(sql, params or [])
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "__int__"):
        return int(value)
    raise TypeError(f"not JSON serializable: {type(value)}")


def columnar(records: list[dict]) -> dict:
    """{"cols": [...], "rows": [[...], ...]}: field names stored once, much smaller than a list of dicts."""
    cols = list(records[0]) if records else []
    return {"cols": cols, "rows": [[r[c] for c in cols] for r in records]}


def write(name: str, payload) -> None:
    path = OUT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, default=_json_default, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


def export(con) -> None:
    roles = list(DATA_ROLES)
    placeholders = ",".join("?" * len(roles))

    coverage = rows(con, "select * from fiscal_year_coverage order by fiscal_year")
    totals = rows(con, f"""
        select fiscal_year, role_family, count(*) as certified,
               count(distinct employer_key) as employers
        from fct_lca_applications
        where is_certified and is_h1b and role_family in ({placeholders})
        group by 1, 2 order by 1, 2
    """, roles)
    write("meta", {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "coverage": coverage,
        "totals": totals,
        "roles": roles,
        "min_employer_apps": MIN_EMPLOYER_APPS,
    })

    # Year-over-year comparison over the same months, so a partial year is compared fairly.
    latest = max(c["fiscal_year"] for c in coverage)
    latest_months = rows(con, """
        select distinct month(decision_date) as m from fct_lca_applications where fiscal_year = ?
    """, [latest])
    months = [r["m"] for r in latest_months]
    month_ph = ",".join("?" * len(months))
    trend_rows = rows(con, f"""
        select role_family,
               count(*) filter (where fiscal_year = ? - 1) as prior,
               count(*) filter (where fiscal_year = ?)     as current
        from fct_lca_applications
        where is_certified and is_h1b
          and month(decision_date) in ({month_ph})
          and (is_data_role or role_family in ('software_engineer', 'business_analyst'))
        group by 1 order by current desc
    """, [latest, latest, *months])
    write("trend", {"current_fy": latest, "months": sorted(months), "rows": trend_rows})

    write("wages", columnar(rows(con, f"""
        select fiscal_year, role_family, wage_level, worksite_state, applications,
               round(p10_wage) p10, round(p25_wage) p25, round(median_wage) p50,
               round(p75_wage) p75, round(p90_wage) p90
        from agg_role_wages
        where role_family in ({placeholders}) and applications >= 10
        order by fiscal_year, role_family, wage_level, worksite_state
    """, roles)))

    write("employers", columnar(rows(con, f"""
        with by_role as (
            select employer_key, display_name as name, hq_state as hq, fiscal_year as fy, role_family as role,
                   certified_applications as apps, entry_level as entry, new_employment as new_hires,
                   round(median_wage) as median_wage
            from agg_employer_role_year
            where role_family in ({placeholders})
        ),
        eligible as (
            select employer_key from by_role group by 1 having sum(apps) >= ?
        )
        select b.* exclude (employer_key) from by_role b join eligible using (employer_key)
        order by apps desc
    """, [*roles, MIN_EMPLOYER_APPS])))

    write("states", columnar(rows(con, f"""
        select fiscal_year, worksite_state as state, role_family, count(*) as apps,
               round(median(annual_wage) filter (where is_wage_valid and is_full_time)) as median_wage
        from fct_lca_applications
        where is_certified and is_h1b and role_family in ({placeholders}) and worksite_state is not null
        group by all
        having count(*) >= 5
    """, roles)))


def main() -> int:
    if not WAREHOUSE.exists():
        print(f"{WAREHOUSE} not found. Run dbt build first (see README).")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(WAREHOUSE), read_only=True) as con:
        export(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
