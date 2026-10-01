-- Adds role family (from seeds/role_family_rules.csv) and seniority from the job title.
-- Titles repeat a lot, so each distinct title is classified once and joined back.

with cases as (
    select * from {{ ref('int_lca__latest') }}
),

rules as (
    select * from {{ ref('role_family_rules') }}
),

titles as (
    select distinct job_title_lower from cases where job_title_lower is not null
),

title_roles as (
    select t.job_title_lower, r.role_family, r.is_data_role
    from titles t
    join rules r on regexp_matches(t.job_title_lower, r.pattern)
    qualify row_number() over (partition by t.job_title_lower order by r.priority) = 1
),

title_seniority as (
    select
        job_title_lower,
        case
            when regexp_matches(job_title_lower, '\bintern\b|internship')                     then 'intern'
            when regexp_matches(job_title_lower, '\b(director|vp|vice president|head of)\b') then 'director'
            when regexp_matches(job_title_lower, '\bmanager\b')                              then 'manager'
            when regexp_matches(job_title_lower, '\b(senior|sr|lead|principal|staff)\b|\b(iii|iv|v)\s*$') then 'senior'
            when regexp_matches(job_title_lower, '\b(junior|jr|entry|associate|graduate|new grad)\b|\bi\s*$') then 'entry'
            else 'mid'
        end as seniority
    from titles
)

select
    c.*,
    coalesce(r.role_family, 'other')  as role_family,
    coalesce(r.is_data_role, false)   as is_data_role,
    coalesce(s.seniority, 'mid')      as seniority
from cases c
left join title_roles r using (job_title_lower)
left join title_seniority s using (job_title_lower)
