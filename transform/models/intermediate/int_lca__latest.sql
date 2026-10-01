-- One row per case: when a case shows up in more than one release (for example
-- certified in one quarter and withdrawn in a later file), the latest release wins.

select * exclude (release_rank)
from (
    select
        *,
        row_number() over (
            partition by case_number
            order by source_fiscal_year desc, source_quarter desc
        ) as release_rank
    from {{ ref('stg_lca__applications') }}
)
where release_rank = 1
