"""Fetch a repo's GitHub issues and PRs into SQLite (raw JSON + cleaned rows).

Usage:
    .venv/bin/python fetch.py --max-requests 100         # fetch the window (needs a token)
    .venv/bin/python fetch.py --fresh --max-requests 3   # delete the db first
    .venv/bin/python fetch.py --rebuild                  # reload cleaned tables, no requests

Three streams (see STREAMS): every item created, closed or otherwise updated
since the window start, every item still open, and the repo-wide comment stream.
Restartable: progress is saved per page and per stream in fetch_state, so a rerun
continues where the last one stopped. Reads GITHUB_TOKEN from the environment or an
untracked .env file if present; works unauthenticated (60 requests/hour).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DB_PATH = HERE / "data" / "metrics.db"
API = "https://api.github.com"
PER_PAGE = 100
WINDOW_START = "2026-08-13"  # Isaiah 2026-11-11: cut to the last 90 days (fits the no-token limit)
MIN_REMAINING = 5  # stop before using the last few requests of the hour (shared by this machine)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_token():
    """Return GITHUB_TOKEN from env or .env, or None. Never printed or logged."""
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "GITHUB_TOKEN" and value.strip():
                return value.strip().strip('"').strip("'")
    return None


def connect(path=DB_PATH):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript((HERE / "schema.sql").read_text())
    return conn


def clean(repo, obj):
    """Map one API issue/PR object to an items row (dict)."""
    pr = obj.get("pull_request")  # the /issues endpoint returns PRs too; this key marks them
    return {
        "repo": repo,
        "number": obj["number"],
        "kind": "pr" if pr is not None else "issue",
        "title": obj["title"],
        "state": obj["state"],
        "state_reason": obj.get("state_reason"),
        "author": (obj.get("user") or {}).get("login"),
        "author_type": (obj.get("user") or {}).get("type"),
        "author_association": obj.get("author_association"),
        "created_at": obj["created_at"],
        "closed_at": obj.get("closed_at"),
        "merged_at": pr.get("merged_at") if pr else None,
        "updated_at": obj["updated_at"],
        "comments": obj.get("comments", 0),
    }


def load_items(conn, repo, objs):
    """Upsert API objects into raw_items, items and item_labels. Idempotent.

    first_response_at is not in the list data; callers refill it with fill_first_response.
    """
    fetched = now()
    for obj in objs:
        conn.execute(
            "INSERT OR REPLACE INTO raw_items (id, repo, number, fetched_at, json) VALUES (?,?,?,?,?)",
            (obj["id"], repo, obj["number"], fetched, json.dumps(obj)),
        )
        load_clean(conn, repo, obj)


def rebuild(conn):
    """Recreate items and item_labels from raw_items, then refill first_response_at
    from the comments table. No API requests."""
    # Drop and recreate (not just DELETE) so schema.sql column changes reach an existing db.
    conn.executescript("DROP TABLE IF EXISTS items; DROP TABLE IF EXISTS item_labels;"
                       + (HERE / "schema.sql").read_text())
    with conn:
        for repo, raw in conn.execute("SELECT repo, json FROM raw_items").fetchall():
            load_clean(conn, repo, json.loads(raw))
        fill_first_response(conn)


def load_clean(conn, repo, obj):
    """Upsert one API object into items and item_labels."""
    row = clean(repo, obj)
    cols = ", ".join(row)
    marks = ", ".join("?" for _ in row)
    updates = ", ".join(f"{c}=excluded.{c}" for c in row if c not in ("repo", "number"))
    conn.execute(
        f"INSERT INTO items ({cols}) VALUES ({marks}) "
        f"ON CONFLICT(repo, number) DO UPDATE SET {updates}",
        list(row.values()),
    )
    conn.execute("DELETE FROM item_labels WHERE repo=? AND number=?", (repo, obj["number"]))
    conn.executemany(
        "INSERT OR IGNORE INTO item_labels (repo, number, label) VALUES (?,?,?)",
        [(repo, obj["number"], lab["name"]) for lab in obj.get("labels", [])],
    )


def get(url, token):
    """GET a URL. Returns (parsed JSON, remaining-requests int or None, next-page URL or None)."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "docs-metrics-dashboard",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")  # never logged
    with urllib.request.urlopen(req, timeout=30) as resp:
        remaining = resp.headers.get("X-RateLimit-Remaining")
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', resp.headers.get("Link") or "")
        return json.load(resp), (int(remaining) if remaining is not None else None), nxt and nxt.group(1)


# Each stream: list path, fixed query, and whether "since" (window start) is applied.
# "since" filters by last UPDATE time. That is what we want: anything created, closed, merged,
# labelled or commented on inside the window was updated inside it, whatever its creation date,
# so an item opened in 2023 and closed in 2025 is included.
# Oldest-created first is the safe resume order: membership only grows (updated_at never goes
# back), and a newly updated old item is inserted at an earlier position, which pushes later
# items back a slot. That causes a duplicate on the next page (harmless upsert), never a skip.
STREAMS = {
    "issues": ("issues", "state=all&sort=created&direction=asc", True),
    # Every item open now, any age: old open items nobody touched in the window are still backlog.
    # Not drift-proof: an item closed mid-fetch leaves the list and can shift one item past us.
    "open": ("issues", "state=open&sort=created&direction=asc", False),
    # Repo-wide comments: every comment created in the window was also updated in it.
    "comments": ("issues/comments", "sort=created&direction=asc", True),
}


def load_comments(conn, repo, objs):
    """Upsert comment objects into comments, then refill first_response_at. Idempotent."""
    fetched = now()
    for c in objs:
        user = c.get("user") or {}
        conn.execute(
            "INSERT OR REPLACE INTO comments (id, repo, issue_number, author, author_type, created_at,"
            " fetched_at, json) VALUES (?,?,?,?,?,?,?,?)",
            (c["id"], repo, int(c["issue_url"].rsplit("/", 1)[1]), user.get("login"), user.get("type"),
             c["created_at"], fetched, json.dumps(c)),
        )
    fill_first_response(conn)


def fill_first_response(conn):
    conn.execute((HERE / "first_response.sql").read_text())


def fetch(conn, repo, stream, window_start, max_requests, token, remaining=None):
    """Fetch up to max_requests pages of one stream. Returns (requests used, remaining)."""
    path, query, uses_since = STREAMS[stream]
    since = window_start if uses_since else None
    row = conn.execute("SELECT since, last_page, done, next_url FROM fetch_state WHERE repo=? AND stream=?",
                       (repo, stream)).fetchone()
    if row and row[0] != since:
        print(f"{stream}: saved with window {row[0]}, asked for {since}; use --fresh to change it")
        return 0, remaining
    if row and row[2]:
        print(f"{stream}: already complete; use --fresh to refetch")
        return 0, remaining
    page = (row[1] if row else 0) + 1
    next_url = row[3] if row else None
    if since:
        query += f"&since={since}T00:00:00Z"
    used = 0
    while used < max_requests:
        if remaining is not None and remaining < MIN_REMAINING:
            print(f"stopping: only {remaining} requests left this hour; rerun later to resume")
            break
        # Follow GitHub's cursor link when given: page numbers fail with 422 past 1000 results.
        url = next_url or f"{API}/repos/{repo}/{path}?{query}&per_page={PER_PAGE}&page={page}"
        try:
            objs, remaining, next_url = get(url, token)
        except urllib.error.HTTPError as e:
            # 403/429 when rate limited: nothing on this page was saved, so a rerun retries it.
            print(f"stopping: HTTP {e.code} on {stream} page {page} ({e.reason}); rerun later to resume")
            break
        used += 1
        done = len(objs) < PER_PAGE  # a short page is the end of the list
        with conn:  # one transaction per page: data and resume point land together
            if stream == "comments":
                load_comments(conn, repo, objs)
            else:
                load_items(conn, repo, objs)
                fill_first_response(conn)
            # Oldest-created first, so everything created up to the newest row saved is complete.
            covered = now() if done else max((o["created_at"] for o in objs), default=None)
            conn.execute(
                "INSERT OR REPLACE INTO fetch_state (repo, stream, since, last_page, done, covered_until,"
                " next_url, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (repo, stream, since, page, int(done), covered, next_url, now()),
            )
        print(f"{stream} page {page}: {len(objs)} saved; remaining {remaining}")
        if done:
            print(f"{stream}: complete")
            break
        page += 1
    return used, remaining


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default="mdn/content")
    ap.add_argument("--window-start", default=WINDOW_START,
                    help="window start, YYYY-MM-DD: activity on or after this date is fetched (default %(default)s)")
    ap.add_argument("--streams", default="issues,open,comments",
                    help="comma-separated streams to fetch, in order (default %(default)s)")
    ap.add_argument("--max-requests", type=int, default=5,
                    help="list-page requests to spend this run across all streams "
                         "(the free /rate_limit check is extra)")
    ap.add_argument("--fresh", action="store_true", help="delete the database and start over")
    ap.add_argument("--rebuild", action="store_true",
                    help="reload the cleaned tables from raw JSON; no API requests")
    args = ap.parse_args()
    if args.fresh and DB_PATH.exists():
        DB_PATH.unlink()
    conn = connect()
    used = 0
    if args.rebuild:
        rebuild(conn)
    else:
        token = read_token()
        # /rate_limit does not count against the limit, so check it first.
        remaining = get(f"{API}/rate_limit", token)[0]["resources"]["core"]["remaining"]
        print(f"auth: {'token' if token else 'none'}; requests remaining this hour: {remaining}")
        for stream in args.streams.split(","):
            n, remaining = fetch(conn, args.repo, stream, args.window_start,
                                 args.max_requests - used, token, remaining)
            used += n
    counts = dict(conn.execute("SELECT kind, COUNT(*) FROM items WHERE repo=? GROUP BY kind", (args.repo,)))
    n_comments = conn.execute("SELECT COUNT(*) FROM comments WHERE repo=?", (args.repo,)).fetchone()[0]
    print(f"used {used} list requests; items in db: {counts}; comments: {n_comments}")


if __name__ == "__main__":
    sys.exit(main())
