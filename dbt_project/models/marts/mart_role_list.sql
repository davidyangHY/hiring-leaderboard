-- One row per role, for the company drill-down (role titles + posting links).
-- Seniority is derived from the title at export time (src/classify.py).
select
    company,
    function,
    title,
    url,
    effective_posted_date as posted_date,
    is_open
from {{ ref('int_role_spans') }}
where title is not null
