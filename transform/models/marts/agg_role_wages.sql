-- Wage distribution of certified, full-time H-1B roles by role family, wage level,
-- worksite state and fiscal year. 'ALL' rows give national totals.

with base as (
    select fiscal_year, role_family, coalesce(wage_level, 'Unknown') as wage_level,
           coalesce(worksite_state, 'UNKNOWN') as worksite_state, annual_wage
    from {{ ref('fct_lca_applications') }}
    where is_certified and is_h1b and is_full_time and is_wage_valid
)

select
    fiscal_year,
    role_family,
    coalesce(wage_level, 'ALL')              as wage_level,
    coalesce(worksite_state, 'ALL')          as worksite_state,
    count(*)                                 as applications,
    quantile_cont(annual_wage, 0.10)         as p10_wage,
    quantile_cont(annual_wage, 0.25)         as p25_wage,
    median(annual_wage)                      as median_wage,
    quantile_cont(annual_wage, 0.75)         as p75_wage,
    quantile_cont(annual_wage, 0.90)         as p90_wage
from base
group by grouping sets (
    (fiscal_year, role_family, wage_level, worksite_state),
    (fiscal_year, role_family, wage_level),
    (fiscal_year, role_family)
)
