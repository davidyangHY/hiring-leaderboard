-- The core series: new roles per (day x company x function). The dashboard's
-- timeframe slider sums this over the selected date range to build any window's
-- leaderboard — so no window needs precomputing.
select
    effective_posted_date as posted_date,
    company,
    function,
    count(*)              as new_roles
from {{ ref('int_role_spans') }}
group by 1, 2, 3
