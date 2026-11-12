"""Offline tests for fetch.py paging: end of list, window boundary, rate-limit 403, comments.

Run: .venv/bin/python -m unittest discover -s tests -v
No network: fetch.get is replaced by a fake that serves invented pages.
"""
import re
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import fetch  # noqa: E402
import metrics  # noqa: E402

REPO = "example/fictional-docs"


def item(number, created, **kw):
    obj = {"id": 7000 + number, "number": number, "title": f"Fictional item {number}",
           "state": "open", "user": {"login": "fictional-dana", "type": "User"},
           "created_at": created, "updated_at": created, "comments": 0, "labels": []}
    obj.update(kw)
    return obj


class FakeAPI:
    """Serves pages[n-1] for page=n; pages entries may be an exception to raise."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, url, token):
        page = int(re.search(r"[?&]page=(\d+)", url).group(1))
        self.calls.append(url)
        result = self.pages[page - 1] if page <= len(self.pages) else []
        if isinstance(result, Exception):
            raise result
        # Like GitHub: a cursor link to the next page (the real API rejects page numbers past 1000 results).
        return result, 50, f"https://api.github.com/next?page={page + 1}&after=c{page}"


class FetchTest(unittest.TestCase):
    def setUp(self):
        self.real_get, self.real_per_page = fetch.get, fetch.PER_PAGE
        fetch.PER_PAGE = 2  # tiny pages so a few invented items span several pages
        self.conn = fetch.connect(":memory:")

    def tearDown(self):
        fetch.get, fetch.PER_PAGE = self.real_get, self.real_per_page

    def run_stream(self, pages, stream="issues", start="2026-01-01", max_requests=10):
        fetch.get = api = FakeAPI(pages)
        used, _ = fetch.fetch(self.conn, REPO, stream, start, max_requests, None, 50)
        return used, api

    def state(self, stream="issues"):
        return self.conn.execute("SELECT last_page, done FROM fetch_state WHERE stream=?",
                                 (stream,)).fetchone()

    def test_end_of_list(self):
        # Page 1 full (2 items), page 2 short (1 item) = end of list. A rerun spends nothing.
        pages = [[item(5, "2026-01-09T00:00:00Z"), item(4, "2026-01-08T00:00:00Z")],
                 [item(3, "2026-01-07T00:00:00Z")]]
        used, _ = self.run_stream(pages)
        self.assertEqual(used, 2)
        self.assertEqual(self.state(), (2, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone(), (3,))
        used, api = self.run_stream(pages)
        self.assertEqual((used, api.calls), (0, []))

    def test_pre_window_item_closed_in_window_is_counted(self):
        # The issues stream asks for everything UPDATED since the window start, so an item
        # created before the window but closed inside it comes back, is kept, and counts in the
        # close-date metrics. It must not leak into the open-date metrics.
        old = item(1, "2025-06-01T00:00:00Z", state="closed", state_reason="completed",
                   closed_at="2026-01-06T00:00:00Z", updated_at="2026-01-06T00:00:00Z",
                   labels=[{"name": "old-label"}])
        new = item(2, "2026-01-05T00:00:00Z", state="closed", state_reason="completed",
                   closed_at="2026-01-07T00:00:00Z", updated_at="2026-01-07T00:00:00Z")
        used, api = self.run_stream([[old, new]])
        self.assertIn("since=2026-01-01T00:00:00Z", api.calls[0])
        self.assertIn("direction=asc", api.calls[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone(), (2,))
        start, end = "2026-01-01", "2026-01-12"
        thr = metrics.run(self.conn, "throughput", start, end)[1]
        self.assertIn(("2026-01-05", "Issues closed: completed", 2, 1.0), thr)  # week 2025-12-29 had 0
        cyc = metrics.run(self.conn, "cycle_time", start, end)[1]
        # #1 waited 219 days (2025-06-01 to 2026-01-06), longer than the window itself.
        self.assertIn(("Issues (all closes)", "All", 2, None, None), cyc)
        self.assertEqual(self.conn.execute(
            "SELECT julianday(closed_at) - julianday(created_at) FROM items WHERE number=1").fetchone(),
            (219.0,))
        # Open-date metrics only see #2, created in range.
        ovc = metrics.run(self.conn, "opened_vs_closed", start, end)[1]
        self.assertEqual(ovc, [("2025-12-29", "Issues", 0, 0, 0), ("2026-01-05", "Issues", 1, 2, -1)])
        vol = metrics.run(self.conn, "labels_by_volume", start, end)[1]
        self.assertEqual(vol, [("Issues", 1, "(no label)", 1)])

    def test_rate_limit_403_then_resume(self):
        limited = urllib.error.HTTPError("u", 403, "rate limit exceeded", {}, None)
        page1 = [item(5, "2026-01-10T00:00:00Z"), item(4, "2026-01-08T00:00:00Z")]
        page2 = [item(3, "2026-01-07T00:00:00Z")]
        used, _ = self.run_stream([page1, limited])
        self.assertEqual(used, 1)  # the failed request is not counted as a saved page
        self.assertEqual(self.state(), (1, 0))  # page 1 saved, not done
        # Partial coverage: oldest-created first, so complete up to the newest item saved so far.
        self.assertEqual(self.conn.execute("SELECT covered_until FROM fetch_state").fetchone(),
                         ("2026-01-10T00:00:00Z",))
        used, api = self.run_stream([page1, page2])
        self.assertEqual(used, 1)
        self.assertIn("page=2&after=c1", api.calls[0])  # resumed from the saved cursor, page 1 not refetched
        self.assertEqual(self.state(), (2, 1))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM items").fetchone(), (3,))

    def test_stops_when_few_requests_remain(self):
        fetch.get = api = FakeAPI([[item(1, "2026-01-10T00:00:00Z")]])
        used, _ = fetch.fetch(self.conn, REPO, "issues", "2026-01-01", 10, None, fetch.MIN_REMAINING - 1)
        self.assertEqual((used, api.calls), (0, []))

    def test_comments_fill_first_response(self):
        # Issue #9 by dana. Comments: dana herself, a bot, then erin (the first response),
        # then frank. One comment is on PR #8, which never gets a first response.
        self.run_stream([[item(9, "2026-01-05T00:00:00Z"),
                          item(8, "2026-01-05T00:00:00Z", pull_request={"merged_at": None})]])

        def comment(cid, number, login, created, kind="User"):
            return {"id": cid, "issue_url": f"https://example.invalid/issues/{number}",
                    "user": {"login": login, "type": kind}, "created_at": created}
        pages = [[comment(1, 9, "fictional-dana", "2026-01-05T01:00:00Z"),
                  comment(2, 9, "fictional-helper[bot]", "2026-01-05T02:00:00Z", "Bot")],
                 [comment(3, 9, "fictional-erin", "2026-01-05T03:00:00Z"),
                  comment(4, 8, "fictional-erin", "2026-01-05T04:00:00Z")],
                 [comment(5, 9, "fictional-frank", "2026-01-06T00:00:00Z")]]
        used, api = self.run_stream(pages, stream="comments", max_requests=2)
        self.assertIn("since=2026-01-01T00:00:00Z", api.calls[0])
        self.assertEqual(used, 2)
        # Not done yet, so comments are only known complete up to the newest one saved.
        self.assertEqual(self.conn.execute(
            "SELECT done, covered_until FROM fetch_state WHERE stream='comments'").fetchone(),
            (0, "2026-01-05T04:00:00Z"))
        self.assertEqual(self.conn.execute(
            "SELECT number, first_response_at FROM items ORDER BY number").fetchall(),
            [(8, None), (9, "2026-01-05T03:00:00Z")])
        # A rebuild of the cleaned tables keeps first_response_at (it is refilled from comments).
        fetch.rebuild(self.conn)
        self.assertEqual(self.conn.execute(
            "SELECT first_response_at FROM items WHERE number=9").fetchone(), ("2026-01-05T03:00:00Z",))


if __name__ == "__main__":
    unittest.main()
