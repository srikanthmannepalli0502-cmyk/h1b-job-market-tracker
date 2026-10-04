-- USCIS approvals and denials per DOL employer and fiscal year: did this sponsor's
-- H-1B petitions actually go through? Only matched employers are included; an
-- employer with several tax IDs under one name (e.g. subsidiaries) is summed.

select
    employer_key,
    fiscal_year,
    -- 'name_and_tax_id' if any of the employer's rows matched on tax ID digits too
    min(match_type)                                       as match_type,
    sum(new_employment_approved)                          as new_employment_approved,
    sum(new_employment_denied)                            as new_employment_denied,
    sum(change_of_employer_approved)                      as change_of_employer_approved,
    sum(change_of_employer_denied)                        as change_of_employer_denied,
    sum(continuation_approved)                            as continuation_approved,
    sum(continuation_denied)                              as continuation_denied,
    sum(total_approved)                                   as total_approved,
    sum(total_denied)                                     as total_denied,
    sum(new_employment_denied) * 1.0
        / nullif(sum(new_employment_approved) + sum(new_employment_denied), 0) as new_employment_denial_rate,
    sum(total_denied) * 1.0
        / nullif(sum(total_approved) + sum(total_denied), 0)                   as total_denial_rate
from {{ ref('int_uscis__employer_year') }}
where match_type <> 'unmatched'
group by 1, 2
