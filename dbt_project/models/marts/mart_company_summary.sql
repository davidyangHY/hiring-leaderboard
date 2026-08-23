-- Company dimension for the UI: identity, headcount (for the per-1k-employees
-- normalized view), and currently-open role count.
with spans as (
    select * from {{ ref('int_role_spans') }}
),

open_now as (
    select company, count(*) as open_roles
    from spans
    where is_open
    group by company
)

select
    c.company,
    c.ats,
    c.headcount,
    c.hq,
    c.notes,
    coalesce(o.open_roles, 0) as open_roles
from {{ source('raw', 'companies') }} c
left join open_now o using (company)
