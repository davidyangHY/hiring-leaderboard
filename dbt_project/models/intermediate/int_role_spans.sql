-- Collapse the daily snapshots into one row per role, with its lifespan.
--
-- effective_posted_date bootstraps history from the ATS-reported publish date
-- (so the leaderboard has a real time series from day one), but only when that
-- date is sane; otherwise it falls back to the first day we captured the role.
-- Going forward, first_capture (from our own snapshots) is the ground truth.
with snaps as (
    select * from {{ ref('stg_role_snapshots') }}
)

select
    company,
    role_id,
    min(captured_at)                    as first_capture,
    max(captured_at)                    as last_capture,
    min(published_at)                   as published_at,
    case
        when min(published_at) between date '2018-01-01' and current_date
            then min(published_at)
        else min(captured_at)
    end                                 as effective_posted_date,
    arg_max(title, captured_at)         as title,
    arg_max(function, captured_at)      as function,
    arg_max(department, captured_at)    as department,
    arg_max(location, captured_at)      as location,
    arg_max(is_remote, captured_at)     as is_remote,
    arg_max(url, captured_at)           as url,
    -- still open on the most recent snapshot day?
    max(captured_at) = (select max(captured_at) from snaps) as is_open
from snaps
group by company, role_id
