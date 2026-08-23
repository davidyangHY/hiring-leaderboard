-- One cleaned row per daily role observation.
select
    captured_at,
    company,
    ats,
    role_id,
    title,
    department,
    function,
    team,
    location,
    is_remote,
    url,
    published_at
from {{ source('raw', 'role_snapshots') }}
where role_id is not null
