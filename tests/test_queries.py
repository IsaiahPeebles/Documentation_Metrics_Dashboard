"""Offline tests: every queries/*.sql file against hand-computed answers on fixture_metrics.json.

Run: .venv/bin/python -m unittest discover -s tests -v
Range under test: 2026-01-05 (Mon) up to, not including, 2026-01-20.
W1 = week of 2026-01-05, W2 = 2026-01-12, W3 = 2026-01-19.
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import fetch  # noqa: E402
import metrics  # noqa: E402

REPO = "example/fictional-docs"
START, END = "2026-01-05", "2026-01-20"
W1, W2, W3 = "2026-01-05", "2026-01-12", "2026-01-19"


class QueryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = json.loads((HERE / "fixture_metrics.json").read_text())
        cls.conn = fetch.connect(":memory:")
        fetch.load_items(cls.conn, REPO, fixture["items"])
        fetch.load_comments(cls.conn, REPO, fixture["comments"])
        cls.conn.execute("INSERT INTO fetch_state VALUES (?, 'comments', '2026-01-01', 1, 1, "
                         "'2026-02-01T00:00:00Z', NULL, '2026-02-01T00:00:00Z')", (REPO,))

    def rows(self, name):
        return metrics.run(self.conn, name, START, END)[1]

    def test_throughput(self):
        # Closed in W1: issues #1 #2 #3 completed (#3 has no reason -> completed),
        # #4 not_planned + #5 duplicate -> without a fix; PR #10 merged; PR #11 unmerged;
        # bot PR #12 merged. W3: issue #7 completed. Rolling average over 1, 2, 3 weeks.
        self.assertEqual(self.rows("throughput"), [
            (W1, "Bot PRs merged", 1, 1.0), (W2, "Bot PRs merged", 0, 0.5), (W3, "Bot PRs merged", 0, 0.33),
            (W1, "Issues closed: completed", 3, 3.0), (W2, "Issues closed: completed", 0, 1.5),
            (W3, "Issues closed: completed", 1, 1.33),
            (W1, "Issues closed: without a fix", 2, 2.0), (W2, "Issues closed: without a fix", 0, 1.0),
            (W3, "Issues closed: without a fix", 0, 0.67),
            (W1, "PRs closed unmerged", 1, 1.0), (W2, "PRs closed unmerged", 0, 0.5),
            (W3, "PRs closed unmerged", 0, 0.33),
            (W1, "PRs merged", 1, 1.0), (W2, "PRs merged", 0, 0.5), (W3, "PRs merged", 0, 0.33),
        ])

    def test_cycle_time(self):
        # Issues W1 days: 1, 2, 3, 4, 5 -> median 3, p85 rank ceil(4.25)=5 -> 5.
        # All issues: 1, 2, 3, 4, 5, 7.5 -> median (3+4)/2 = 3.5, p85 rank ceil(5.1)=6 -> 7.5.
        # Completed only: #1 #2 #3 in W1, #7 in W3 -> n < 5 everywhere, so no percentiles.
        self.assertEqual(self.rows("cycle_time"), [
            ("Bot PRs merged", W1, 1, None, None), ("Bot PRs merged", "All", 1, None, None),
            ("Issues (all closes)", W1, 5, 3.0, 5.0), ("Issues (all closes)", W3, 1, None, None),
            ("Issues (all closes)", "All", 6, 3.5, 7.5),
            ("Issues (completed only)", W1, 3, None, None), ("Issues (completed only)", W3, 1, None, None),
            ("Issues (completed only)", "All", 4, None, None),
            ("PRs merged", W1, 1, None, None), ("PRs merged", "All", 1, None, None),
        ])

    def test_backlog_age(self):
        # Open on 2026-01-20: issue #6 (13 days), issue #8 (31 days), PR #13 (7 days).
        self.assertEqual(self.rows("backlog_age"), [
            ("Issues", "8 to 30 days", 2, 1), ("Issues", "31 to 90 days", 3, 1),
            ("PRs", "0 to 7 days", 1, 1),
        ])

    def test_time_to_first_response(self):
        # Hours: #1 5 (own comment and bot ignored), #2 10, #3 none, #4 2, #5 1 (earliest),
        # #6 20, #7 3. PR #10's comment is ignored.
        # W1 responded [1, 2, 5, 10, 20] -> median 5, p85 rank 5 -> 20; #3 has no response.
        # All [1, 2, 3, 5, 10, 20] -> median (3+5)/2 = 4, p85 rank 6 -> 20.
        self.assertEqual(self.rows("time_to_first_response"), [
            (W1, 5, 1, 5.0, 20.0), (W2, 1, 0, None, None), ("All", 6, 1, 4.0, 20.0),
        ])

    def test_time_to_first_response_respects_coverage(self):
        # Issues opened on or after covered_until are left out, not counted as unanswered.
        self.conn.execute("UPDATE fetch_state SET covered_until='2026-01-07T00:00:00Z'")
        try:
            # Only #1 to #5 (opened before 01-07) count: responded [5, 10, 2, 1], #3 none.
            self.assertEqual(self.rows("time_to_first_response"),
                             [(W1, 4, 1, None, None), ("All", 4, 1, None, None)])
        finally:
            self.conn.execute("UPDATE fetch_state SET covered_until='2026-02-01T00:00:00Z'")

    def test_labels_by_volume(self):
        # Opened in range: issues #1-#7 (typo x6, needs triage, css); PRs #10 (content), #11-#13 none.
        self.assertEqual(self.rows("labels_by_volume"), [
            ("Issues", 1, "typo", 6), ("Issues", 2, "css", 1), ("Issues", 3, "needs triage", 1),
            ("PRs", 1, "(no label)", 3), ("PRs", 2, "content", 1),
        ])

    def test_labels_by_delay(self):
        # Only typo has 5+ closed items: 1, 2, 3, 4, 5, 7.5 days -> median 3.5.
        self.assertEqual(self.rows("labels_by_delay"), [("Issues", 1, "typo", 6, 3.5)])

    def test_opened_vs_closed(self):
        self.assertEqual(self.rows("opened_vs_closed"), [
            (W1, "Bot items", 1, 1, 0), (W2, "Bot items", 0, 0, 0), (W3, "Bot items", 0, 0, 0),
            (W1, "Issues", 6, 5, 1), (W2, "Issues", 1, 0, 1), (W3, "Issues", 0, 1, -1),
            (W1, "PRs", 2, 2, 0), (W2, "PRs", 1, 0, 1), (W3, "PRs", 0, 0, 0),
        ])

    def test_no_usernames_in_any_query_output(self):
        logins = {r[0] for r in self.conn.execute("SELECT author FROM items UNION SELECT author FROM comments")}
        for sql in metrics.QUERIES.glob("*.sql"):
            for row in metrics.run(self.conn, sql.stem, START, END)[1]:
                self.assertFalse(logins & set(map(str, row)), sql.name)


if __name__ == "__main__":
    unittest.main()
