-- Fill items.first_response_at from the comments table (approach B in METRIC_DEFINITIONS.md).
-- First response = the earliest comment on an issue by someone who is not the issue's
-- author and is not a bot (account type 'Bot'). PRs are left NULL: their first response
-- is usually a code review, which this data does not include.
-- Safe to run any number of times; fetch.py runs it after every comment page and after --rebuild.
UPDATE items
SET first_response_at = (
    SELECT MIN(c.created_at)
    FROM comments AS c
    WHERE c.repo = items.repo
      AND c.issue_number = items.number
      AND c.author_type IS NOT 'Bot'
      AND c.author IS NOT items.author
)
WHERE kind = 'issue';
