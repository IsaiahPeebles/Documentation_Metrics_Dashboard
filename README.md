# Documentation Metrics Dashboard

## Question
How well does a large open-source documentation program handle its incoming work? How much does it finish, how long do requests wait, how old is the backlog, how fast do people get a first reply, and is the queue growing? Measured on the public GitHub issues and pull requests of [mdn/content](https://github.com/mdn/content).

**Current state (2026-11-11):** pipeline, metric queries, tests and dashboard are built. The full 2-year fetch is waiting for a GitHub token, so the database holds only a small sample and the dashboard says so in a banner at the top. There is no status report until the full fetch has run.

## Data
- **Source:** GitHub REST API, public data only.
- **Window:** everything created, closed, or otherwise updated since **2026-08-13** (the last 90 days), plus everything still open, plus the repo-wide issue comment stream from 2026-08-13 on (for time to first response). Each metric then filters by its own date: close date for throughput and cycle time, open date for volume, opened vs. closed and response time. See "Data window" in `METRIC_DEFINITIONS.md`.
- **Issues vs. PRs:** GitHub's issues endpoint returns pull requests too. An item is a PR when the API object has a `pull_request` key (`items.kind`).
- **Bots:** an item or comment is a bot's when the account type is `Bot` (`author_type`).
- **Privacy:** usernames are stored in the raw data only to tell people from bots and authors from responders. No query returns a username, and a test checks that.
- **Stored in** `data/metrics.db` (SQLite, not committed). `schema.sql` documents every table and column. The raw API JSON is kept, so `fetch.py --rebuild` reloads the cleaned tables without any requests.

## Method
1. `fetch.py` pulls three streams, page by page, saving each page and its resume point in one transaction:
   - `issues`: every issue and PR **updated** on or after the window start (`since`), oldest-created first. Anything created, closed, merged or commented on in the window was updated in it, so an item opened in 2023 and closed in 2025 is included. Oldest first is the safe resume order: a newly updated item can only cause a duplicate, never a skip.
   - `open`: every open item, oldest first, with no date limit.
   - `comments`: the repo-wide comment stream, oldest first, with `since` set to the window start. For comments, `since` is a safe superset (a comment created after the date was also updated after it).
   It then fills `items.first_response_at` with `first_response.sql`.
2. Each metric is one commented SQL file in `queries/`, with the date range as parameters. The dashboard and the tests run those same files through `metrics.py`; no metric logic lives in Python.
3. `app.py` is a Streamlit dashboard: a headline row, then one chart per metric, each with a table view and a plain-language caption, and a date filter.

## How to run
```
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v      # offline tests, no network

# Full fetch (needs GITHUB_TOKEN in an untracked .env; without it you get 60 requests/hour)
.venv/bin/python fetch.py --fresh --max-requests 1000
.venv/bin/python fetch.py --max-requests 1000          # rerun to resume if it stopped

# Dashboard, local only, on the registered port
.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8803 \
    --server.headless true --browser.gatherUsageStats false
```
Other `fetch.py` options: `--streams issues,open,comments` (which to fetch), `--window-start YYYY-MM-DD`, `--rebuild` (reload from raw JSON, no requests). `fetch.py` checks the free `/rate_limit` endpoint first and stops when fewer than 5 requests remain this hour.

## Limits
- **Snapshot, not live.** Each item is as it was when its page was fetched. Once a stream is complete, a rerun fetches nothing; `--fresh` refetches everything. Without a token, a full fetch is spread over days and items can change in between. With a token it finishes in one sitting, which gives a consistent snapshot.
- **Paging drift.** Pages are numbered. In the `issues` and `comments` streams, a change during the fetch only pushes rows later (re-seen, not lost). But an item closed during the `open` fetch, or one deleted or transferred, can shift a page and be skipped. A `--fresh` run with a token keeps that window short.
- **Only the latest close.** GitHub keeps one `closed_at`, so a closed, reopened, closed-again item counts once, at its last close.
- **Cycle time is lead time.** GitHub does not record when work started, only when an item opened. Items still open are not in it, so recent weeks look faster than they will.
- **First response is comments only.** Labels, assignments, closing without a word, and PR code reviews do not count.
- **Labels are current labels,** not the labels at the time.
- **Untested at scale:** behavior over several hundred pages on a repo this size, and a real rate-limit 403 (both are covered by offline tests with invented pages).

## Files
- `fetch.py`, `schema.sql`, `first_response.sql`: data pipeline.
- `queries/*.sql`, `metrics.py`: the metrics.
- `app.py`: the dashboard.
- `METRIC_DEFINITIONS.md`: what each metric means, its exact rule and limits (approved 2026-11-11).
- `tests/`: offline tests on small fictional fixtures.
- `requirements.txt`: pinned Python packages.

License: [MIT](LICENSE). The data comes from the public GitHub API and is not included in this repo.
