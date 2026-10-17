-- Collapse the daily snapshots into one row per role, with its lifespan.
--
-- effective_posted_date is the ATS-reported publish date (the earliest one we
-- ever saw, so a company re-dating a posting later doesn't move it). Old dates
-- are trusted: a long-running "evergreen" posting really was posted years ago.
-- Only a missing or future date falls back to the first day we captured it.
--
-- Open vs. closed: a role is closed once it has been missing from
-- `closed_after_missed_snapshots` consecutive snapshot days (see
-- dbt_project.yml). Days we have no snapshot for (e.g. 2026-08-27) don't count
-- as misses. closed_on is the first day it went missing.
with snaps as (
    select * from {{ ref('stg_role_snapshots') }}
),

snapshot_days as (
    select distinct captured_at as day from snaps
),

spans as (
    select
        company,
        role_id,
        min(captured_at)                    as first_capture,
        max(captured_at)                    as last_capture,
        min(published_at)                   as published_at,
        case
            when min(published_at) <= current_date then min(published_at)
            else min(captured_at)           -- missing or future-dated
        end                                 as effective_posted_date,
        arg_max(title, captured_at)         as title,
        arg_max(function, captured_at)      as function,
        arg_max(department, captured_at)    as department,
        arg_max(location, captured_at)      as location,
        arg_max(is_remote, captured_at)     as is_remote,
        arg_max(url, captured_at)           as url
    from snaps
    group by company, role_id
),

with_misses as (
    select
        spans.*,
        -- every snapshot day after its last sighting is a day it was missing
        (select count(*) from snapshot_days d where d.day > spans.last_capture) as missed_snapshots,
        (select min(d.day) from snapshot_days d where d.day > spans.last_capture) as first_missing_day
    from spans
)

select
    * exclude (first_missing_day),
    missed_snapshots < {{ var('closed_after_missed_snapshots') }} as is_open,
    case when missed_snapshots >= {{ var('closed_after_missed_snapshots') }}
         then first_missing_day end          as closed_on
from with_misses
