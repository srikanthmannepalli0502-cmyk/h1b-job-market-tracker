{# Fails if any combination of `columns` appears more than once (like dbt_utils, without the package). #}
{% test dbt_utils_unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
