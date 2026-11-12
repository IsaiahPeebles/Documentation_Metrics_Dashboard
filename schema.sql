-- Documentation Metrics Dashboard: SQLite schema.
-- Source: GitHub REST "list repository issues" endpoint (GET /repos/{owner}/{repo}/issues).
-- That endpoint returns issues AND pull requests. A row is a PR when the API
-- object has a "pull_request" key. We keep both and mark them with items.kind.
-- All timestamps are ISO 8601 UTC text exactly as GitHub sends them
-- (e.g. 2026-01-05T09:00:00Z), so they sort and compare correctly as text.

-- Raw layer: one row per issue/PR, the full API object as fetched.
-- Lets us rebuild the cleaned tables without spending API requests (fetch.py --rebuild).
CREATE TABLE IF NOT EXISTS raw_items (
    id          INTEGER PRIMARY KEY,  -- GitHub's global id for the issue/PR
    repo        TEXT NOT NULL,        -- "owner/name", e.g. "mdn/content"
    number      INTEGER NOT NULL,     -- the #number shown on GitHub
    fetched_at  TEXT NOT NULL,        -- when we saved it (UTC)
    json        TEXT NOT NULL         -- the full API object, unchanged
);

-- Cleaned layer: one row per issue or PR, only the fields the metrics use.
CREATE TABLE IF NOT EXISTS items (
    repo               TEXT NOT NULL,
    number             INTEGER NOT NULL,
    kind               TEXT NOT NULL CHECK (kind IN ('issue', 'pr')),  -- 'pr' when the API object has "pull_request"
    title              TEXT NOT NULL,
    state              TEXT NOT NULL CHECK (state IN ('open', 'closed')),
    state_reason       TEXT,          -- issues only: 'completed', 'not_planned', 'duplicate', 'reopened', or NULL
    author             TEXT,          -- GitHub login. Raw data only: report in aggregate, never by name
    author_type        TEXT,          -- GitHub user.type: 'Bot' for app accounts, 'User' for people (and for
                                      -- automations run under a normal account, which look like people)
    author_association TEXT,          -- OWNER, MEMBER, COLLABORATOR, CONTRIBUTOR, NONE, ...: used to tell maintainers from outsiders
    created_at         TEXT NOT NULL,
    closed_at          TEXT,          -- NULL while open. For PRs, closed includes merged and not-merged
    merged_at          TEXT,          -- PRs only: NULL if the PR was closed without merging (or is open)
    updated_at         TEXT NOT NULL,
    comments           INTEGER NOT NULL, -- comment count from the list endpoint (excludes the opening post)
    first_response_at  TEXT,          -- issues only: earliest comment by a non-bot who is not the author.
                                      -- Filled from the comments table by first_response.sql
    PRIMARY KEY (repo, number)
);

-- One row per (item, label). A separate table so SQL can count and group labels.
CREATE TABLE IF NOT EXISTS item_labels (
    repo    TEXT NOT NULL,
    number  INTEGER NOT NULL,
    label   TEXT NOT NULL,
    PRIMARY KEY (repo, number, label)
);

-- Comments from the repo-wide comment stream (GET /repos/{owner}/{repo}/issues/comments).
-- Covers issue AND PR conversation comments; only issue comments are used (time to first response).
-- Raw JSON is kept here too, so nothing needs refetching after a schema change.
CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY,  -- GitHub's comment id
    repo          TEXT NOT NULL,
    issue_number  INTEGER NOT NULL,     -- the issue or PR the comment is on (from issue_url)
    author        TEXT,                 -- login. Raw data only: never reported by name
    author_type   TEXT,                 -- 'Bot' for app accounts, 'User' otherwise
    created_at    TEXT NOT NULL,
    fetched_at    TEXT NOT NULL,
    json          TEXT NOT NULL         -- the full API object, unchanged
);

-- Resume point for fetch.py, one row per (repo, stream).
-- Streams: 'issues' (every item created, closed or otherwise updated since the window start),
-- 'open' (every open item, any age, for the backlog), 'comments' (repo-wide comment stream).
-- All three are read oldest-created first.
CREATE TABLE IF NOT EXISTS fetch_state (
    repo           TEXT NOT NULL,
    stream         TEXT NOT NULL,
    since          TEXT,               -- window start sent as GitHub's since (update time); NULL for 'open'
    last_page      INTEGER NOT NULL,   -- last page fully saved
    done           INTEGER NOT NULL DEFAULT 0,  -- 1 once the stream reached its end or the window boundary
    covered_until  TEXT,               -- issues and comments (fetched oldest-created first): rows created
                                       -- up to this time are complete. The fetch time once done
    next_url       TEXT,               -- GitHub's cursor link to the next page (page numbers stop at 1000 results)
    updated_at     TEXT NOT NULL,
    PRIMARY KEY (repo, stream)
);
