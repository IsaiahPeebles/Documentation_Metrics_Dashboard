-- Metric 3: Open backlog age buckets. See METRIC_DEFINITIONS.md, section 3.
-- Parameters: :end is the as-of date (the backlog at 00:00 UTC that day). :start is not used:
-- the backlog includes everything still open, however old.
--
-- Open on the as-of date = opened before it, and not closed (or closed only after it).
-- Age = whole days from opening to the as-of date.
-- Series: 'Issues', 'PRs', and 'Bot items' for anything opened by a bot account.
-- Only non-empty buckets are returned; bucket_order sorts them youngest to oldest.
WITH open_items AS (
    SELECT
        CASE WHEN author_type = 'Bot' THEN 'Bot items'
             WHEN kind = 'pr' THEN 'PRs'
             ELSE 'Issues' END AS series,
        CAST(julianday(:end) - julianday(created_at) AS INTEGER) AS age_days
    FROM items
    WHERE created_at < :end
      AND (closed_at IS NULL OR closed_at >= :end)
)
SELECT
    series,
    CASE WHEN age_days <= 7 THEN '0 to 7 days'
         WHEN age_days <= 30 THEN '8 to 30 days'
         WHEN age_days <= 90 THEN '31 to 90 days'
         WHEN age_days <= 365 THEN '91 to 365 days'
         ELSE 'over 365 days' END AS bucket,
    CASE WHEN age_days <= 7 THEN 1
         WHEN age_days <= 30 THEN 2
         WHEN age_days <= 90 THEN 3
         WHEN age_days <= 365 THEN 4
         ELSE 5 END AS bucket_order,
    COUNT(*) AS open_items
FROM open_items
GROUP BY series, bucket, bucket_order
ORDER BY series, bucket_order;
