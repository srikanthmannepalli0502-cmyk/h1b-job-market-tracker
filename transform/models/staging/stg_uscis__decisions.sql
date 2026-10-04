-- USCIS first decisions on H-1B petitions: one row per employer location, fiscal
-- year, petition type and decision. Petition types (from USCIS):
--   new_employment            new H-1B hires, including F-1/OPT change of status
--   change_of_employer        transfers from another H-1B employer
--   continuation              extensions with the same employer, no changes
--   change_with_same_employer amended terms with the same employer
--   new_concurrent, amended   other petition types

with source as (
    select * from {{ source('bronze', 'uscis') }}
    where employer_name is not null
)

select
    employer_name,
    {{ normalize_employer('employer_name') }}                              as employer_key,
    lpad(nullif(regexp_replace(tax_id_last4, '[^0-9]', '', 'g'), ''), 4, '0') as tax_id_last4,
    try_cast(fiscal_year as integer)                                       as fiscal_year,
    upper(petitioner_state)                                                as petitioner_state,
    lower(regexp_replace(trim(regexp_replace(measure_name, '\s+(Approval|Denial)$', '')), '\s+', '_', 'g'))
                                                                           as petition_type,
    case
        when measure_name like '% Approval' then 'approval'
        when measure_name like '% Denial' then 'denial'
    end                                                                    as decision,
    coalesce(try_cast(measure_value as integer), 0)                        as petitions,
    source_file
from source
