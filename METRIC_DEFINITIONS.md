# Metric definitions

**Status:** approved by Isaiah Peebles, 2026-11-11. Each metric is implemented as one SQL file in `queries/`, named in its section.
**Data:** public GitHub issues and pull requests (PRs) from the `mdn/content` repository, stored in the `items` and `item_labels` tables (see `schema.sql`).

## Data window
- **Items:** everything **created, closed, or otherwise updated since 2026-08-13** (the last 90 days; cut from 2 years on 2026-11-11 so it fits without a token), **plus everything still open**. `fetch.py` asks GitHub for every issue and PR updated on or after that date (GitHub's `since` parameter, which filters by last update time). Anything opened, closed, merged, labelled, or commented on in the window was updated in it, so an item opened in 2023 and closed in 2025 is included. That keeps throughput and cycle time honest: a long-running item that finally closed in the window counts, with its full wait.
- **Each metric still uses its own date.** Close-date metrics (throughput, cycle time, labels by delay) count items closed in the chosen range, whenever they were opened. Open-date metrics (opened vs. closed, labels by volume, time to first response) count only items opened in the range, so older items in the table do not leak in. The date filter cannot go earlier than 2026-08-13.
- **Old open items nobody touched** in the window are fetched separately, so the backlog (metric 3) includes them.
- **Comments** (for metric 4) come from the repo-wide comment stream from 2026-08-13 on. Here `since` (update time) is correct: every comment created after that date was also updated after it.

## How to read this page
Each metric has three parts:
1. **In plain words:** what it tells a manager.
2. **The exact rule:** what counts, what is left out, and how issues and PRs are treated. This is what the SQL will do.
3. **Limits:** where the number can mislead.

## Rules that apply to every metric
- **Issues and PRs are reported separately.** GitHub returns both from the same list, and a row is a PR when the API object has a `pull_request` key. An issue is a request ("this page is wrong"). A PR is a proposed change. Mixing them would hide what is going on, so every metric has an issue view and a PR view.
- **A "closed" PR is not always a delivered PR.** A PR closes when it is merged (accepted) or when it is closed without merging (rejected or abandoned). Where a metric means "work delivered", it counts merged PRs only.
- **Bots are counted apart.** A bot is an item whose author has GitHub account type "Bot" (`author_type = 'Bot'`), which GitHub sets for app accounts. Bots opened 76 of the 300 items in the first (2018 to 2020) sample. They are excluded from the people-focused metrics (time to first response, opened-vs-closed for issues) and shown as their own series where they matter. Limit: an automation that runs under a normal user account still looks like a person.
- **Time is UTC.** Weeks run Monday to Sunday (UTC) and are labelled by the date of their Monday.
- **The data is a snapshot, not live.** Each item is as it was when its page was fetched (`fetched_at`). A rerun only fetches pages after the last saved one, so an item that was open when fetched and closed later still shows as open. A slow fetch without a token spreads the snapshot over days. A fresh rebuild (`fetch.py --fresh`), ideally with a token so it finishes in one sitting, gives a consistent snapshot.
- **The date filter** picks items by the date the metric is about: close date for throughput and cycle time, open date for volume, and an "as of" date for backlog age.
- **People appear only in totals.** Logins are stored in the raw data so bots and authors can be told apart, but no chart or table lists or ranks individual people.

## 1. Throughput (closed per week): `queries/throughput.sql`
**In plain words:** how much work the team finishes each week.

**The exact rule:**
- Issues: count issues whose `closed_at` falls in the week. Shown split by close reason: `completed` (fixed) in one group, and `not_planned` and `duplicate` together in a "closed without a fix" group (declined, duplicate, or stale). A closed issue with no recorded reason goes in the `completed` group, since that is GitHub's default when closing.
- PRs: count PRs whose `merged_at` falls in the week. PRs closed without merging are a separate, smaller line.
- An item reopened and still open is not counted. An item closed, reopened, then closed again counts once, in the week of its last close, because GitHub keeps only the latest `closed_at`.

**Limits:** counts are not effort. A one-word typo fix and a full page rewrite each count as 1. Week-to-week swings are normal, so the chart shows a 4-week rolling average next to the weekly bars.

## 2. Cycle time (median and 85th percentile): `queries/cycle_time.sql`
**In plain words:** how long an item typically waits from being opened to being closed. The median is the typical case. The 85th percentile is the "almost all" case: 85 out of 100 items close at or faster than this.

**The exact rule:**
- Cycle time = `closed_at` minus `created_at`, in days (decimal).
- Issues: all closed issues. PRs: merged PRs only.
- Grouped by the week the item closed.
- Median: the middle value (the average of the two middle values for an even count). 85th percentile: nearest-rank method, the value at position ceil(0.85 x n) in the sorted list. SQLite has no built-in percentile function, so the SQL computes both by rank and the tests check them against hand-worked answers.
- A week with fewer than 5 closed items shows no percentile, because a percentile of 3 values is noise.

**Limits:** GitHub does not record when someone started working, only when the item opened. So this is really *lead time* (the requester's wait), not hands-on work time. Issues closed as `not_planned` are often closed fast and pull the median down, so the chart can be filtered to `completed` only. Items still open are not in cycle time at all, so recent weeks look faster than they will once their slow items close; read the backlog (metric 3) alongside it.

## 3. Open backlog age buckets: `queries/backlog_age.sql`
**In plain words:** how old the unfinished work is. A healthy backlog has most items young. A pile of very old items means requests are being parked, not decided.

**The exact rule:**
- Items whose `state` is `open` on the as-of date (default: the date of the latest fetch).
- Age = as-of date minus `created_at`, in whole days.
- Buckets: 0 to 7 days, 8 to 30 days, 31 to 90 days, 91 to 365 days, over 365 days.
- Issues and PRs as separate bars.

**Limits:** the database knows the current state, not past states. "Backlog as of last March" can be rebuilt only for items that are still in the data, using their open and close dates; reopened items may be slightly off.

## 4. Time to first response: `queries/time_to_first_response.sql`
**In plain words:** after someone reports a problem, how long until a person other than the reporter answers. This is the metric contributors feel most.

**The exact rule:**
- Issues only. PRs get their first response mainly through code reviews, which live in a different API, so PR response time is out of scope for now.
- First response = the earliest comment by a human who is not the issue's author. Bot comments do not count.
- Time to first response = that comment's time minus `created_at`, in hours. Reported as median and 85th percentile, grouped by the week the issue opened.
- Issues with no response yet are reported as a count ("waiting for a first response"), not dropped silently.

**The API cost, honestly:** the issue list does not include comments, only a comment *count*. Two ways to get the first comment:

| Approach | Requests needed | Notes |
|---|---|---|
| A. Ask for each issue's comments | 1 per issue that has comments. Tens of thousands for mdn/content. | Without a token (60 per hour) this takes weeks. With a token (5,000 per hour) several hours. |
| B. Read the repo-wide comment stream (`/repos/mdn/content/issues/comments`, oldest first, 100 per page) | Total comments divided by 100. Roughly 1 request per 100 comments. | Many times cheaper. Restartable the same way as the issue fetch. Works on comments only. |

**Decision: approach B** (approved 2026-11-11). It gets the same answer for a small fraction of the requests, and it fills `first_response_at` in the existing table without changing the schema. It still needs a token for the full history; without one, limit time to first response to a recent window (for example, the last 6 months). Labels, assignments, and closing without a comment do not count as a response under this rule; that is a deliberate, simple choice and can be revisited.

**Limits:** a maintainer may respond by labeling or closing without a word; this rule misses that. Deleted comments are invisible.

## 5. Top labels by volume and by delay: `queries/labels_by_volume.sql`, `queries/labels_by_delay.sql`
**In plain words:** which kinds of work arrive most, and which kinds wait longest. Together they show where extra help would pay off.

**The exact rule:**
- **By volume:** for each label, the number of items opened in the date range that carry it. Items with several labels count once under each label. Items with no label appear as "(no label)".
- **By delay:** for each label, the median cycle time (metric 2) of items closed in the range. Labels with fewer than 5 closed items are left out.
- Top 10 of each, issues and PRs separately.

**Limits:** labels are applied by people, inconsistently, and can change. We see only the labels an item has *now*, not when they were added. In the first 300 items there were 24 distinct labels.

## 6. Opened vs. closed trend: `queries/opened_vs_closed.sql`
**In plain words:** is the backlog growing or shrinking? If more arrives than leaves each week, the team is falling behind, however busy it looks.

**The exact rule:**
- Per week: opened = items with `created_at` in the week; closed = items with `closed_at` in the week (PRs: merged plus closed-unmerged, since both leave the queue).
- Net change = opened minus closed. Shown as two lines (opened, closed) plus a table with the net change. No second y-axis.
- Issues and PRs separately; bot-authored items as their own series.

**Limits:** same "last close only" caveat as throughput.

## Decisions (Isaiah Peebles, 2026-11-11)
1. **Date window:** the last 90 days, activity since 2026-08-13 (see "Data window").
2. **Bots:** kept, as their own series, never mixed into the people series.
3. **Issue throughput:** all closes, split by reason (completed vs. closed without a fix).
4. **Time to first response:** approach B (repo-wide comment stream), issues only, comments only.
5. **Token:** yes. Isaiah creates it and stores it in an untracked `.env`; the agent never sees it.
