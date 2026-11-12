-- Metric 4: Time to first response, in hours. See METRIC_DEFINITIONS.md, section 4.
-- Parameters: :start and :end (exclusive). Issues are picked by the date they were opened.
-- One row per open week, plus period 'All' for the whole range.
--
-- Issues only, opened by people (bot-opened issues are left out).
-- first_response_at is filled by first_response.sql: the earliest comment by a non-bot
-- who is not the issue's author.
-- Coverage: only issues opened while the comment data is complete count, that is, on or after
-- the comment stream's start (fetch_state.since) and before covered_until. Outside that, a
-- missing response could just mean the comment was not fetched yet.
-- no_response = issues in the period with no qualifying comment (open or closed), shown, not dropped.
-- Percentiles use the same rank rules as cycle_time.sql, over responded issues, NULL when n < 5.
WITH base AS (
    SELECT
        date(i.created_at, 'weekday 0', '-6 days') AS week,
        (julianday(i.first_response_at) - julianday(i.created_at)) * 24 AS hours  -- NULL if none
    FROM items AS i
    JOIN fetch_state AS f ON f.repo = i.repo AND f.stream = 'comments'
    WHERE i.kind = 'issue'
      AND i.author_type IS NOT 'Bot'
      AND i.created_at >= :start AND i.created_at < :end
      AND i.created_at >= f.since AND i.created_at < f.covered_until
),
periods AS (
    SELECT week AS period, hours FROM base
    UNION ALL
    SELECT 'All', hours FROM base
),
ranked AS (
    SELECT period, hours,
           -- rank responded issues among themselves; unanswered ones (NULL) sit in their own partition
           ROW_NUMBER() OVER (PARTITION BY period, hours IS NULL ORDER BY hours) AS rn,
           COUNT(hours) OVER (PARTITION BY period) AS n
    FROM periods
)
SELECT
    period,
    n AS responded,
    SUM(hours IS NULL) AS no_response,
    CASE WHEN n >= 5 THEN ROUND(AVG(CASE WHEN hours IS NOT NULL AND rn IN ((n + 1) / 2, (n + 2) / 2)
                                         THEN hours END), 2) END AS median_hours,
    CASE WHEN n >= 5 THEN ROUND(MAX(CASE WHEN hours IS NOT NULL AND rn = (85 * n + 99) / 100
                                         THEN hours END), 2) END AS p85_hours
FROM ranked
GROUP BY period
ORDER BY period;
