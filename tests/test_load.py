"""Offline test: the fictional fixture loads into the cleaned tables correctly.

Run: .venv/bin/python -m unittest discover -s tests -v
No network. Expected values were worked out by hand from fixture_issues.json.
"""
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
import fetch  # noqa: E402

REPO = "example/fictional-docs"


class LoadTest(unittest.TestCase):
    def setUp(self):
        fetch.urllib.request.urlopen = None  # any network call would now fail loudly
        self.conn = fetch.connect(":memory:")
        self.objs = json.loads((HERE / "fixture_issues.json").read_text())
        fetch.load_items(self.conn, REPO, self.objs)

    def q(self, sql, *args):
        return self.conn.execute(sql, args).fetchall()

    def test_counts_and_kinds(self):
        # 4 objects: #2 and #3 have "pull_request" -> PRs; #1 and #4 -> issues.
        self.assertEqual(self.q("SELECT COUNT(*) FROM raw_items"), [(4,)])
        self.assertEqual(self.q("SELECT kind, COUNT(*) FROM items GROUP BY kind ORDER BY kind"),
                         [("issue", 2), ("pr", 2)])

    def test_cleaned_fields(self):
        rows = self.q("SELECT number, state, closed_at, merged_at, comments, author_association "
                      "FROM items ORDER BY number")
        self.assertEqual(rows, [
            (1, "closed", "2026-01-07T09:00:00Z", None, 2, "NONE"),
            (2, "closed", "2026-01-07T08:00:00Z", "2026-01-07T08:00:00Z", 1, "CONTRIBUTOR"),
            (3, "closed", "2026-01-20T12:00:00Z", None, 0, "NONE"),  # closed, not merged
            (4, "open", None, None, 0, "NONE"),
        ])
        # first_response_at is not in the list endpoint, so it stays NULL after a list load.
        self.assertEqual(self.q("SELECT COUNT(*) FROM items WHERE first_response_at IS NOT NULL"), [(0,)])

    def test_labels(self):
        # typo on #1 and #2; good first issue on #1; needs triage on #4; #3 has none.
        self.assertEqual(
            self.q("SELECT label, COUNT(*) FROM item_labels GROUP BY label ORDER BY label"),
            [("good first issue", 1), ("needs triage", 1), ("typo", 2)])

    def test_author_type(self):
        # Only #2 is authored by an app account (user.type "Bot").
        self.assertEqual(self.q("SELECT number FROM items WHERE author_type='Bot'"), [(2,)])

    def test_rebuild_from_raw(self):
        # Corrupt the cleaned tables, then rebuild them from raw_items alone.
        self.conn.execute("UPDATE items SET state='open', author_type=NULL")
        self.conn.execute("DELETE FROM item_labels")
        fetch.rebuild(self.conn)
        self.assertEqual(self.q("SELECT kind, COUNT(*) FROM items GROUP BY kind ORDER BY kind"),
                         [("issue", 2), ("pr", 2)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM items WHERE state='closed'"), [(3,)])
        self.assertEqual(self.q("SELECT number FROM items WHERE author_type='Bot'"), [(2,)])
        self.assertEqual(self.q("SELECT COUNT(*) FROM item_labels"), [(4,)])

    def test_reload_is_idempotent_and_updates(self):
        # Simulate a refetch where #4 got closed and lost its label.
        self.objs[3].update(state="closed", closed_at="2026-02-10T00:00:00Z", labels=[])
        fetch.load_items(self.conn, REPO, self.objs)
        self.assertEqual(self.q("SELECT COUNT(*) FROM items"), [(4,)])
        self.assertEqual(self.q("SELECT state, closed_at FROM items WHERE number=4"),
                         [("closed", "2026-02-10T00:00:00Z")])
        self.assertEqual(self.q("SELECT COUNT(*) FROM item_labels"), [(3,)])


if __name__ == "__main__":
    unittest.main()
