-- Most USCIS approvals for employers that also file LCAs should match a DOL employer on
-- name + tax ID digits. A sharp drop means the name normalization or a source format changed.
-- (Fails only when there is enough data to judge, so small CI fixtures don't trip it.)

with by_year as (
    select
        fiscal_year,
        sum(total_approved) as approved,
        sum(total_approved) filter (where match_type = 'name_and_tax_id') as matched
    from {{ ref('int_uscis__employer_year') }}
    group by 1
)

select fiscal_year, approved, matched, matched * 1.0 / approved as match_share
from by_year
where approved >= 10000 and matched * 1.0 / approved < 0.85
