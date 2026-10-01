-- Guards the wage cleaning rules: if more than 1% of certified H-1B rows are flagged
-- as invalid wages, a DOL format change (or a bug) is the likely cause.

select
    count(*) filter (where not is_wage_valid) * 1.0 / count(*) as invalid_share
from {{ ref('fct_lca_applications') }}
where is_certified and is_h1b
having count(*) filter (where not is_wage_valid) * 1.0 / count(*) > 0.01
