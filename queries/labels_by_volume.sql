-- Metric 5a: Top labels by volume. See METRIC_DEFINITIONS.md, section 5.
-- Parameters: :start and :end (exclusive). Items are picked by the date they were opened.
--
-- For each label: how many items opened in the range carry it. An item with several labels
-- counts once under each; an item with none counts under '(no label)'.
-- Issues and PRs are ranked separately (top 10 each). Bot-opened items are included,
-- because labels describe the kind of work, not who asked for it.
-- Ties are broken alphabetically so the top 10 is stable.
WITH labelled AS (
    SELECT
        CASE WHEN i.kind = 'pr' THEN 'PRs' ELSE 'Issues' END AS kind,
        COALESCE(l.label, '(no label)') AS label
    FROM items AS i
    LEFT JOIN item_labels AS l ON l.repo = i.repo AND l.number = i.number
    WHERE i.created_at >= :start AND i.created_at < :end
),
counted AS (
    SELECT kind, label, COUNT(*) AS items,
           ROW_NUMBER() OVER (PARTITION BY kind ORDER BY COUNT(*) DESC, label) AS rank
    FROM labelled
    GROUP BY kind, label
)
SELECT kind, rank, label, items
FROM counted
WHERE rank <= 10
ORDER BY kind, rank;
