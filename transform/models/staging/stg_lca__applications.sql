-- Typed, cleaned LCA rows. Still one row per case per source file.

with source as (
    select * from {{ source('bronze', 'lca') }}
),

typed as (
    select
        case_number,
        case_status,
        visa_class,
        try_cast(received_date as timestamp)::date        as received_date,
        try_cast(decision_date as timestamp)::date        as decision_date,
        try_cast(begin_date as timestamp)::date           as begin_date,
        try_cast(end_date as timestamp)::date             as end_date,

        trim(employer_name)                               as employer_name,
        {{ normalize_employer('employer_name') }}         as employer_key,
        upper(trim(employer_state))                       as employer_state,
        employer_fein,
        left(naics_code, 2)                               as naics_sector,
        naics_code,

        trim(job_title)                                   as job_title,
        lower(trim(job_title))                            as job_title_lower,
        left(soc_code, 7)                                 as soc_code,
        soc_title,
        full_time_position = 'Y'                          as is_full_time,
        coalesce(try_cast(total_worker_positions as double), 1)::integer as worker_positions,
        coalesce(try_cast(new_employment as double), 0) > 0      as is_new_employment,
        coalesce(try_cast(change_employer as double), 0) > 0     as is_change_employer,
        coalesce(try_cast(continued_employment as double), 0) > 0 as is_continued_employment,

        upper(trim(worksite_city))                        as worksite_city,
        upper(trim(worksite_state))                       as worksite_state,
        left(worksite_postal_code, 5)                     as worksite_zip,

        try_cast(wage_rate_of_pay_from as double)         as wage_from,
        try_cast(wage_rate_of_pay_to as double)           as wage_to,
        wage_unit_of_pay                                  as wage_unit,
        try_cast(prevailing_wage as double)               as prevailing_wage,
        pw_unit_of_pay                                    as prevailing_wage_unit,
        nullif(pw_wage_level, 'N/A')                      as wage_level,

        willful_violator = 'Y'                            as is_willful_violator,
        source_fiscal_year,
        source_quarter,
        source_file
    from source
)

select
    *,
    {{ fiscal_year('decision_date') }}                    as fiscal_year,
    {{ annualize('wage_from', 'wage_unit') }}             as annual_wage,
    {{ annualize('prevailing_wage', 'prevailing_wage_unit') }} as annual_prevailing_wage,
    case_status in ('Certified', 'Certified - Withdrawn') as is_certified,
    coalesce(
        {{ annualize('wage_from', 'wage_unit') }}
            between {{ var('min_valid_annual_wage') }} and {{ var('max_valid_annual_wage') }},
        false
    )                                                     as is_wage_valid
from typed
