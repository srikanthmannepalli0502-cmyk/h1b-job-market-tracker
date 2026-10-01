-- One row per employer (normalized name), with its most common spelling for display.

with apps as (
    select * from {{ ref('fct_lca_applications') }}
    where employer_key is not null
),

name_counts as (
    select employer_key, employer_name, count(*) as n
    from apps
    group by 1, 2
),

display as (
    select employer_key, employer_name as display_name
    from name_counts
    qualify row_number() over (partition by employer_key order by n desc, employer_name) = 1
),

state_counts as (
    select employer_key, employer_state, count(*) as n
    from apps
    where employer_state is not null
    group by 1, 2
),

hq as (
    select employer_key, employer_state as hq_state
    from state_counts
    qualify row_number() over (partition by employer_key order by n desc, employer_state) = 1
)

select
    a.employer_key,
    d.display_name,
    h.hq_state,
    count(distinct a.employer_name)                         as name_variants,
    count(*)                                                as total_applications,
    count(*) filter (where a.is_certified and a.is_h1b)     as certified_h1b,
    count(*) filter (where a.is_certified and a.is_h1b and a.is_data_role) as certified_h1b_data_roles,
    min(a.decision_date)                                    as first_seen,
    max(a.decision_date)                                    as last_seen,
    bool_or(a.is_willful_violator)                          as ever_willful_violator
from apps a
join display d using (employer_key)
left join hq h using (employer_key)
group by all
