-- Metric 2: Cycle time, median and 85th percentile, in days. See METRIC_DEFINITIONS.md, section 2.
-- Parameters: :start and :end (exclusive). Items are picked by the date they closed (merged, for PRs).
-- One row per series and close week, plus one row per series with period 'All' for the whole range.
--
-- Cycle time = close (or merge) time minus open time, in decimal days.
-- Series: 'Issues (all closes)', 'Issues (completed only)' (same rule as throughput's completed
-- group), 'PRs merged', and bot-opened items as 'Bot issues (all closes)' / 'Bot PRs merged'.
-- PRs closed without merging are left out: they were not delivered.
--
-- Percentiles (SQLite has none built in), by rank within each series and period:
--   median = the middle value, or the average of the two middle values when n is even:
--            ranks (n+1)/2 and (n+2)/2 in integer division (n=5 -> 3 and 3; n=4 -> 2 and 3)
--   p85    = nearest rank, ceil(0.85 * n), written as (85*n + 99) / 100 in integer division
-- Both are NULL when n < 5 (a percentile of a handful of values is noise); n is still shown.
WITH base AS (
    SELECT
        CASE
            WHEN author_type = 'Bot' AND kind = 'pr' THEN 'Bot PRs merged'
            WHEN author_type = 'Bot' THEN 'Bot issues (all closes)'
            WHEN kind = 'pr' THEN 'PRs merged'
            ELSE 'Issues (all closes)'
        END AS series,
        date(COALESCE(merged_at, closed_at), 'weekday 0', '-6 days') AS week,
        julianday(COALESCE(merged_at, closed_at)) - julianday(created_at) AS days,
        kind, author_type, state_reason
    FROM items
    WHERE closed_at IS NOT NULL
      AND (kind = 'issue' OR merged_at IS NOT NULL)
      AND COALESCE(merged_at, closed_at) >= :start
      AND COALESCE(merged_at, closed_at) < :end
),
with_completed AS (
    SELECT series, week, days FROM base
    UNION ALL
    SELECT 'Issues (completed only)', week, days FROM base
    WHERE kind = 'issue' AND author_type IS NOT 'Bot'
      AND COALESCE(state_reason, 'completed') NOT IN ('not_planned', 'duplicate')
),
periods AS (
    SELECT series, week AS period, days FROM with_completed
    UNION ALL
    SELECT series, 'All', days FROM with_completed
),
ranked AS (
    SELECT series, period, days,
           ROW_NUMBER() OVER (PARTITION BY series, period ORDER BY days) AS rn,
           COUNT(*) OVER (PARTITION BY series, period) AS n
    FROM periods
)
SELECT
    series,
    period,
    n,
    CASE WHEN n >= 5 THEN ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN days END), 2) END
        AS median_days,
    CASE WHEN n >= 5 THEN ROUND(MAX(CASE WHEN rn = (85 * n + 99) / 100 THEN days END), 2) END
        AS p85_days
FROM ranked
GROUP BY series, period
ORDER BY series, period;
