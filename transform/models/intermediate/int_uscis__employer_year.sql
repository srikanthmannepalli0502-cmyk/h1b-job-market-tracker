-- USCIS decisions per employer (name key + tax ID last 4) and fiscal year, matched to
-- DOL employers.
--   match_type = 'name_and_tax_id': same normalized name and DOL FEIN ends in the same 4 digits
--   match_type = 'name_only':       same normalized name, tax ID digits differ or missing
--   match_type = 'unmatched':       employer not in the DOL data loaded here

with decisions as (
    select
        employer_key,
        tax_id_last4,
        fiscal_year,
        sum(petitions) filter (where petition_type = 'new_employment' and decision = 'approval')     as new_employment_approved,
        sum(petitions) filter (where petition_type = 'new_employment' and decision = 'denial')       as new_employment_denied,
        sum(petitions) filter (where petition_type = 'change_of_employer' and decision = 'approval') as change_of_employer_approved,
        sum(petitions) filter (where petition_type = 'change_of_employer' and decision = 'denial')   as change_of_employer_denied,
        sum(petitions) filter (where petition_type = 'continuation' and decision = 'approval')       as continuation_approved,
        sum(petitions) filter (where petition_type = 'continuation' and decision = 'denial')         as continuation_denied,
        sum(petitions) filter (where decision = 'approval')                                          as total_approved,
        sum(petitions) filter (where decision = 'denial')                                            as total_denied
    from {{ ref('stg_uscis__decisions') }}
    where employer_key is not null
    group by all
),

dol_tax_ids as (
    select distinct
        employer_key,
        right(regexp_replace(employer_fein, '[^0-9]', '', 'g'), 4) as tax_id_last4
    from {{ ref('stg_lca__applications') }}
    where employer_fein is not null and employer_key is not null
),

dol_employers as (
    select employer_key from {{ ref('dim_employers') }}
)

select
    d.*,
    case
        when t.employer_key is not null then 'name_and_tax_id'
        when e.employer_key is not null then 'name_only'
        else 'unmatched'
    end as match_type
from decisions d
left join dol_tax_ids t using (employer_key, tax_id_last4)
left join dol_employers e using (employer_key)
