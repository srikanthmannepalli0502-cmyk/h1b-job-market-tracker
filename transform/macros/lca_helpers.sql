{# Convert a pay rate to an annual amount (2,080 working hours per year). #}
{% macro annualize(rate, unit) -%}
    case {{ unit }}
        when 'Year' then {{ rate }}
        when 'Month' then {{ rate }} * 12
        when 'Bi-Weekly' then {{ rate }} * 26
        when 'Week' then {{ rate }} * 52
        when 'Hour' then {{ rate }} * 2080
    end
{%- endmacro %}


{# US federal fiscal year: October-December belong to the next year. #}
{% macro fiscal_year(date_col) -%}
    (year({{ date_col }}) + case when month({{ date_col }}) >= 10 then 1 else 0 end)
{%- endmacro %}


{#
  Grouping key for employer names: upper-case, "&" spelled "AND" (USCIS writes
  "JPMORGAN CHASE AND CO" where DOL has "JPMorgan Chase & Co."), punctuation removed,
  common legal suffixes dropped. "Amazon.com Services, LLC" and "AMAZON.COM SERVICES LLC"
  match; separate legal entities (e.g. Amazon Web Services) stay separate.
#}
{% macro normalize_employer(name) -%}
    nullif(trim(regexp_replace(
        regexp_replace(
            regexp_replace(replace(upper({{ name }}), '&', ' AND '), '[^A-Z0-9]+', ' ', 'g'),
            '(\s+(L\s?L\s?C|L\s?L\s?P|L\s?P|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PC|PLLC|P\s?C))+\s*$', '', 'g'),
        '\s+', ' ', 'g')), '')
{%- endmacro %}
