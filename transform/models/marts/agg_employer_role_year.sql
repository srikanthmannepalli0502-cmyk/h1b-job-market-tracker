-- Certified H-1B applications by employer, role family and fiscal year:
-- the "who sponsors this kind of role, and what do they pay" table.

select
    a.employer_key,
    e.display_name,
    e.hq_state,
    a.fiscal_year,
    a.role_family,
    a.is_data_role,
    count(*)                                                     as certified_applications,
    sum(a.worker_positions)                                      as worker_positions,
    count(*) filter (where a.is_new_employment)                  as new_employment,
    count(*) filter (where a.is_change_employer)                 as change_employer,
    count(*) filter (where a.seniority = 'entry' or a.wage_level = 'I') as entry_level,
    count(distinct a.worksite_state)                             as worksite_states,
    median(a.annual_wage) filter (where a.is_wage_valid and a.is_full_time)                   as median_wage,
    quantile_cont(a.annual_wage, 0.25) filter (where a.is_wage_valid and a.is_full_time)      as p25_wage,
    quantile_cont(a.annual_wage, 0.75) filter (where a.is_wage_valid and a.is_full_time)      as p75_wage
from {{ ref('fct_lca_applications') }} a
join {{ ref('dim_employers') }} e using (employer_key)
where a.is_certified and a.is_h1b
group by all
