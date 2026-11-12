"""Run a metric query from queries/<name>.sql. The dashboard and the tests both use this,
so the SQL files are the single place each metric is defined."""
from pathlib import Path

QUERIES = Path(__file__).parent / "queries"


def run(conn, name, start, end):
    """Return (column names, rows) for queries/<name>.sql over [start, end), dates as YYYY-MM-DD."""
    cur = conn.execute((QUERIES / f"{name}.sql").read_text(), {"start": start, "end": end})
    return [d[0] for d in cur.description], cur.fetchall()
