"""Streamlit dashboard for the docs metrics. Every number comes from queries/*.sql via metrics.run.

Run (local only):
    .venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8803 \
        --server.headless true --browser.gatherUsageStats false
"""
import sqlite3
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

import fetch
import metrics

st.set_page_config(page_title="Docs program metrics", layout="wide")

if not fetch.DB_PATH.exists():
    st.error("No database yet. Run `.venv/bin/python fetch.py` first (see README).")
    st.stop()
conn = sqlite3.connect(f"file:{fetch.DB_PATH}?mode=ro", uri=True)
state = {r[0]: r[1:] for r in conn.execute("SELECT stream, done, covered_until, updated_at FROM fetch_state")}
STREAM_NAMES = {"issues": "issues and PRs active in the window", "open": "items still open",
                "comments": "comments (time to first response)"}
incomplete = [s for s in STREAM_NAMES if not state.get(s, (0,))[0]]
is_sample = bool(incomplete)  # sample until all three streams are complete
window_start = date.fromisoformat(fetch.WINDOW_START)
fetched_at = max((s[2] for s in state.values()), default="never")


def stream_note(s):
    covered = state.get(s, (0, None))[1]
    return f"{STREAM_NAMES[s]} (only those created up to {covered[:10]})" if covered else \
        f"{STREAM_NAMES[s]} (not fetched)"


title = "mdn/content docs program metrics"
st.title(title + (" (SAMPLE DATA)" if is_sample else ""))
if is_sample:
    st.warning(
        "**SAMPLE DATA, NOT THE FULL WINDOW.** Only a small sample was fetched (no GitHub token yet). "
        "Incomplete: " + "; ".join(stream_note(s) for s in incomplete) + ". Totals are undercounts "
        "and trends are not meaningful. Do not quote these numbers. This banner disappears once all "
        "three streams are fully fetched.")
st.caption(f"Source: public GitHub issues and PRs of mdn/content: everything created, closed, or "
           f"otherwise updated since {fetch.WINDOW_START}, plus everything still open. "
           f"Last fetch: {fetched_at} UTC. All times UTC. Weeks run Monday to Sunday and are "
           "labelled by their Monday. Definitions: METRIC_DEFINITIONS.md.")

picked = st.sidebar.date_input("Date range", (window_start, date.today()),
                               min_value=window_start, max_value=date.today())
if len(picked) != 2:
    st.info("Pick an end date.")
    st.stop()
start, end = picked[0].isoformat(), (picked[1] + timedelta(days=1)).isoformat()  # end is exclusive
st.sidebar.caption("Throughput and cycle time use the close date, volume and response time the "
                   "open date, and the backlog is as of the end of the last day picked.")


def q(name):
    cols, rows = metrics.run(conn, name, start, end)
    return pd.DataFrame(rows, columns=cols)


def show(chart, table, caption):
    """A chart with a table view and a plain-language caption."""
    tab_chart, tab_table = st.tabs(["Chart", "Table"])
    with tab_chart:
        if chart is None:
            st.info("No data in this range.")
        else:
            st.altair_chart(chart, width="stretch")
    with tab_table:
        st.dataframe(table, hide_index=True)
    st.caption(caption)


def lines(df, x, names, y_title, x_title="Week (starting Monday)"):
    """Two or more measures as lines. Color, dash pattern and point shape all tell them apart."""
    long = df.rename(columns=names).melt(id_vars=[x], value_vars=list(names.values()),
                                         var_name="Measure", value_name="value")
    enc = dict(color=alt.Color("Measure:N", title="Measure"),
               strokeDash=alt.StrokeDash("Measure:N", title="Measure"),
               shape=alt.Shape("Measure:N", title="Measure"))
    return alt.Chart(long).mark_line(point=True).encode(
        x=alt.X(f"{x}:T", title=x_title, axis=alt.Axis(format="%b %d")), y=alt.Y("value:Q", title=y_title),
        tooltip=[f"{x}:T", "Measure:N", "value:Q"], **enc)


thr, cyc, back, ttfr = q("throughput"), q("cycle_time"), q("backlog_age"), q("time_to_first_response")
vol, delay, ovc = q("labels_by_volume"), q("labels_by_delay"), q("opened_vs_closed")


def all_row(df, series_col, series, col):
    row = df[(df[series_col] == series) & (df["period"] == "All")] if series_col else df[df["period"] == "All"]
    if not len(row):
        return "n/a (no data)"
    v = row[col].iloc[0]
    return "n/a (under 5 items)" if v is None or pd.isna(v) else v


# Headline numbers: sums and 'All' rows of the queries below.
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Issues closed", int(thr[thr.series.str.startswith("Issues")].closed.sum()))
c2.metric("PRs merged (people)", int(thr[thr.series == "PRs merged"].closed.sum()))
c3.metric("Median issue cycle time (days)", all_row(cyc, "series", "Issues (all closes)", "median_days"))
c4.metric("Median time to first response (hours)", all_row(ttfr, None, None, "median_hours"))
c5.metric("Open backlog at end date", int(back.open_items.sum()))
st.caption("Headline numbers are totals of the tables below (throughput, cycle time 'All' row, "
           "time to first response 'All' row, backlog). Bot-opened items are excluded from the "
           "issue and PR counts and shown in their own series below; the backlog includes them.")

st.header("1. Throughput: closed per week")
choices = sorted(thr.series.unique())
pick = st.selectbox("Series", choices, key="thr_series", index=choices.index("Issues closed: completed")
                    if "Issues closed: completed" in choices else 0) if choices else None
chart = None
if pick:
    d = thr[thr.series == pick]
    base = alt.Chart(d).encode(x=alt.X("week:T", title="Week (starting Monday)", axis=alt.Axis(format="%b %d")))
    bars = base.transform_calculate(Measure="'Weekly count'").mark_bar(opacity=0.6).encode(
        y=alt.Y("closed:Q", title="Items"), color=alt.Color("Measure:N", title="Measure"),
        tooltip=["week:T", "closed:Q"])
    avg = base.transform_calculate(Measure="'4-week average'").mark_line(point=True).encode(
        y="rolling_4wk_avg:Q", color="Measure:N", tooltip=["week:T", "rolling_4wk_avg:Q"])
    chart = (bars + avg).properties(title=f"{pick}, per week, with 4-week average")
show(chart, thr, "How much work finished each week. Bars are weekly counts; the line smooths "
     "out week-to-week swings. A count is not effort: a typo fix and a page rewrite each count as 1.")

st.header("2. Cycle time: open to close")
choices = sorted(cyc.series.unique())
pick = st.selectbox("Series", choices, key="cyc_series", index=choices.index("Issues (all closes)")
                    if "Issues (all closes)" in choices else 0) if choices else None
weekly = cyc[(cyc.series == pick) & (cyc.period != "All")].dropna(subset=["median_days"])
chart = lines(weekly.rename(columns={"period": "week"}), "week",
              {"median_days": "Median", "p85_days": "85th percentile"}, "Days").properties(
    title=f"{pick}: days from open to close, by close week") if len(weekly) else None
show(chart, cyc, "How long items wait from opening to closing. Half close faster than the median; "
     "85 in 100 close faster than the 85th percentile. Weeks with fewer than 5 closed items have "
     "no percentile and are not drawn (they are still in the table, with their count). Items still "
     "open are not included, so recent weeks look faster than they will once slow items close.")

st.header("3. Open backlog by age")
chart = alt.Chart(back).mark_bar().encode(
    x=alt.X("bucket:N", title="Age of open item", sort=alt.SortField("bucket_order")),
    y=alt.Y("open_items:Q", title="Open items"),
    color=alt.Color("series:N", title="Series"),
    column=alt.Column("series:N", title=None),
    tooltip=["series:N", "bucket:N", "open_items:Q"],
).properties(title="Open items by age at the end date", width=220) if len(back) else None
show(chart, back, "How old the unfinished work is, one panel per series. A healthy backlog is "
     "mostly young; a pile of items over a year old means requests are parked, not decided.")

st.header("4. Time to first response (issues)")
weekly = ttfr[(ttfr.period != "All")].dropna(subset=["median_hours"])
chart = lines(weekly.rename(columns={"period": "week"}), "week",
              {"median_hours": "Median", "p85_hours": "85th percentile"}, "Hours",
              "Week the issue opened (starting Monday)").properties(
    title="Hours until someone other than the reporter comments") if len(weekly) else None
no_resp = int(ttfr[ttfr.period == "All"].no_response.sum()) if len(ttfr) else 0
show(chart, ttfr, f"How long people wait for a first human reply. Bot comments and the reporter's "
     f"own comments do not count. {no_resp} issues in this range have no reply yet (see the "
     "table's no_response column). Only issues opened while comment data is complete are counted.")

st.header("5. Labels: what arrives most, and what waits longest")
kind = st.radio("Show", ["Issues", "PRs"], horizontal=True)
col_a, col_b = st.columns(2)
with col_a:
    d = vol[vol.kind == kind]
    chart = alt.Chart(d).mark_bar().encode(
        y=alt.Y("label:N", title="Label", sort=alt.SortField("rank")),
        x=alt.X("items:Q", title="Items opened"), tooltip=["label:N", "items:Q"],
    ).properties(title=f"Top labels by volume ({kind})") if len(d) else None
    show(chart, d, "How many items opened in the range carry each label. An item with two labels "
         "counts under both. Unlike the other charts, bot-opened items are included here, because a "
         "label describes the kind of work, not who opened it.")
with col_b:
    d = delay[delay.kind == kind]
    chart = alt.Chart(d).mark_bar().encode(
        y=alt.Y("label:N", title="Label", sort=alt.SortField("rank")),
        x=alt.X("median_days:Q", title="Median days open to close"),
        tooltip=["label:N", "closed_items:Q", "median_days:Q"],
    ).properties(title=f"Slowest labels by median cycle time ({kind})") if len(d) else None
    show(chart, d, "Labels whose items wait longest, by median days from open to close. Labels "
         "with fewer than 5 closed items in the range are left out. Bot-opened items are included, as "
         "in the volume chart.")

st.header("6. Opened vs. closed")
choices = sorted(ovc.series.unique())
pick = st.selectbox("Series", choices, key="ovc_series") if choices else None
d = ovc[ovc.series == pick]
chart = lines(d, "week", {"opened": "Opened", "closed": "Closed"}, "Items").properties(
    title=f"{pick}: opened and closed per week") if len(d) else None
show(chart, ovc, "Is the queue growing? When the opened line sits above the closed line, more "
     "work arrives than leaves. The table's net column is opened minus closed for each week.")
