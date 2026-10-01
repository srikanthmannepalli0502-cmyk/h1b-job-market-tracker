-- Which months of each fiscal year are loaded, so partial years are labeled honestly.

select
    fiscal_year,
    min(decision_date)                                  as first_decision,
    max(decision_date)                                  as last_decision,
    count(distinct date_trunc('month', decision_date))  as months_covered,
    count(distinct date_trunc('month', decision_date)) = 12 as is_complete,
    count(*)                                            as applications,
    count(*) filter (where is_certified and is_h1b)     as certified_h1b
from {{ ref('fct_lca_applications') }}
group by 1
order by 1
