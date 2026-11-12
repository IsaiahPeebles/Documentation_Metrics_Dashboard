-- Metric 6: Opened vs. closed trend. See METRIC_DEFINITIONS.md, section 6.
-- Parameters: :start and :end (exclusive).
-- One row per week and series, including weeks with zero.
--
-- opened = items created in the week; closed = items closed in the week (for PRs, merged and
-- closed-unmerged both count: either way the item left the queue). net = opened - closed;
-- a positive net means the queue grew that week.
-- Series: 'Issues' and 'PRs' (opened by people), and 'Bot items' (anything opened by a bot account).
WITH RECURSIVE
weeks(week) AS (
    SELECT date(:start, 'weekday 0', '-6 days')
    UNION ALL
    SELECT date(week, '+7 days') FROM weeks WHERE date(week, '+7 days') < :end
),
tagged AS (
    SELECT *,
           CASE WHEN author_type = 'Bot' THEN 'Bot items'
                WHEN kind = 'pr' THEN 'PRs'
                ELSE 'Issues' END AS series
    FROM items
),
events AS (
    SELECT series, date(created_at, 'weekday 0', '-6 days') AS week, 1 AS opened, 0 AS closed
    FROM tagged WHERE created_at >= :start AND created_at < :end
    UNION ALL
    SELECT series, date(closed_at, 'weekday 0', '-6 days'), 0, 1
    FROM tagged WHERE closed_at >= :start AND closed_at < :end
),
grid AS (
    SELECT w.week, s.series
    FROM weeks AS w CROSS JOIN (SELECT DISTINCT series FROM events) AS s
)
SELECT
    g.week,
    g.series,
    COALESCE(SUM(e.opened), 0) AS opened,
    COALESCE(SUM(e.closed), 0) AS closed,
    COALESCE(SUM(e.opened), 0) - COALESCE(SUM(e.closed), 0) AS net
FROM grid AS g
LEFT JOIN events AS e ON e.week = g.week AND e.series = g.series
GROUP BY g.week, g.series
ORDER BY g.series, g.week;
