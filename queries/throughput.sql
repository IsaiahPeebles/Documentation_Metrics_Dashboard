-- Metric 1: Throughput (closed per week). See METRIC_DEFINITIONS.md, section 1.
-- Parameters: :start (first day, YYYY-MM-DD) and :end (the day AFTER the last day).
-- One row per week and series, including weeks with zero, plus a 4-week rolling average.
--
-- Series:
--   Issues closed: completed     -> state_reason 'completed', or no reason recorded
--                                   (GitHub's default), or 'reopened' left on a re-closed issue
--   Issues closed: without a fix -> state_reason 'not_planned' or 'duplicate'
--   PRs merged / PRs closed unmerged
--   Bot ...                      -> the same, for items opened by bot accounts (author_type 'Bot')
-- An item counts in the week of its merge (merged PRs) or its close (everything else).
-- Weeks run Monday to Sunday (UTC) and are labelled by their Monday:
--   date(x, 'weekday 0', '-6 days') moves to that week's Sunday, then back to its Monday.
WITH RECURSIVE
weeks(week) AS (
    SELECT date(:start, 'weekday 0', '-6 days')
    UNION ALL
    SELECT date(week, '+7 days') FROM weeks WHERE date(week, '+7 days') < :end
),
closed AS (
    SELECT
        CASE
            WHEN author_type = 'Bot' AND kind = 'pr' AND merged_at IS NOT NULL THEN 'Bot PRs merged'
            WHEN author_type = 'Bot' AND kind = 'pr' THEN 'Bot PRs closed unmerged'
            WHEN author_type = 'Bot' THEN 'Bot issues closed'
            WHEN kind = 'pr' AND merged_at IS NOT NULL THEN 'PRs merged'
            WHEN kind = 'pr' THEN 'PRs closed unmerged'
            WHEN state_reason IN ('not_planned', 'duplicate') THEN 'Issues closed: without a fix'
            ELSE 'Issues closed: completed'
        END AS series,
        date(COALESCE(merged_at, closed_at), 'weekday 0', '-6 days') AS week
    FROM items
    WHERE closed_at IS NOT NULL  -- a reopened item has closed_at NULL again, so it is not counted
      AND COALESCE(merged_at, closed_at) >= :start
      AND COALESCE(merged_at, closed_at) < :end
),
-- Every week for every series that appears in the range, so empty weeks show as 0.
grid AS (
    SELECT w.week, s.series
    FROM weeks AS w CROSS JOIN (SELECT DISTINCT series FROM closed) AS s
),
counts AS (
    SELECT g.week, g.series, COUNT(c.series) AS closed
    FROM grid AS g
    LEFT JOIN closed AS c ON c.week = g.week AND c.series = g.series
    GROUP BY g.week, g.series
)
SELECT
    week,
    series,
    closed,
    -- The first 3 weeks of a range average over fewer weeks (1, 2, then 3).
    ROUND(AVG(closed) OVER (PARTITION BY series ORDER BY week
                            ROWS BETWEEN 3 PRECEDING AND CURRENT ROW), 2) AS rolling_4wk_avg
FROM counts
ORDER BY series, week;
