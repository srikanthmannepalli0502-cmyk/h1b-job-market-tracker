-- Every distinct case in bronze must survive deduplication exactly once.
-- (Cases without a decision date are excluded from the fact table by design.)

with bronze as (
    select count(distinct case_number) as n
    from {{ source('bronze', 'lca') }}
    where try_cast(decision_date as timestamp) is not null
),

fact as (
    select count(*) as n from {{ ref('fct_lca_applications') }}
)

select bronze.n as bronze_cases, fact.n as fact_cases
from bronze, fact
where bronze.n <> fact.n
