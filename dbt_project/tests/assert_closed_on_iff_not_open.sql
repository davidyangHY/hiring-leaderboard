-- A role has a closed date exactly when it is no longer open. Any row where an
-- open role has a closed date (or a closed role has none) fails the build.
select company, role_id, is_open, closed_on
from {{ ref('int_role_spans') }}
where is_open = (closed_on is not null)
