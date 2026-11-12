-- Metric 5b: Top labels by delay. See METRIC_DEFINITIONS.md, section 5.
-- Parameters: :start and :end (exclusive). Items are picked by the date they closed (merged, for PRs).
--
-- For each label: the median cycle time (same rule as cycle_time.sql) of items closed in the
-- range that carry it. Issues: all closed issues. PRs: merged PRs only.
-- Labels with fewer than 5 closed items are left out. Unlabelled items are '(no label)'.
-- Issues and PRs are ranked separately, slowest first, top 10 each. Bot-opened items included.
WITH closed AS (
    SELECT
        CASE WHEN i.kind = 'pr' THEN 'PRs' ELSE 'Issues' END AS kind,
        COALESCE(l.label, '(no label)') AS label,
        julianday(COALESCE(i.merged_at, i.closed_at)) - julianday(i.created_at) AS days
    FROM items AS i
    LEFT JOIN item_labels AS l ON l.repo = i.repo AND l.number = i.number
    WHERE i.closed_at IS NOT NULL
      AND (i.kind = 'issue' OR i.merged_at IS NOT NULL)
      AND COALESCE(i.merged_at, i.closed_at) >= :start
      AND COALESCE(i.merged_at, i.closed_at) < :end
),
ranked AS (
    SELECT kind, label, days,
           ROW_NUMBER() OVER (PARTITION BY kind, label ORDER BY days) AS rn,
           COUNT(*) OVER (PARTITION BY kind, label) AS n
    FROM closed
),
medians AS (
    SELECT kind, label, n AS closed_items,
           ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN days END), 2) AS median_days
    FROM ranked
    WHERE n >= 5
    GROUP BY kind, label
),
top AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY kind ORDER BY median_days DESC, label) AS rank
    FROM medians
)
SELECT kind, rank, label, closed_items, median_days
FROM top
WHERE rank <= 10
ORDER BY kind, rank;
