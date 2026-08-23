-- int_role_spans must have exactly one row per (company, role_id).
select company, role_id, count(*) as n
from {{ ref('int_role_spans') }}
group by company, role_id
having count(*) > 1
