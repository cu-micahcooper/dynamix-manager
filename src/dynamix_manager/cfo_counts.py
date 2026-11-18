"""Report-vintage daily counts and point-in-time backlog, without ticket identifiers."""

import json
from itertools import pairwise

import duckdb
import pandas as pd

from .cfo import (
    _build_7day_windows,
    _delta_pct,
    _fmt,
    _open_weekly_series,
    _parse_dates,
)
from .tickets import exclude_cancelled_tickets


def periods(start, end):
    span = end - start
    return {
        "current": (start, end),
        "prior": (start - span, start),
        "year": (start - pd.Timedelta(weeks=52), end - pd.Timedelta(weeks=52)),
        **{f"week_{i}": window for i, window in enumerate(_build_7day_windows(end))},
    }


def save_counts(
    path, tickets, start, end, *, observed=None, include_archive_history=False
):
    tickets = exclude_cancelled_tickets(tickets).reset_index(drop=True)
    dates = {
        key: _parse_dates(tickets.get(col, pd.Series(dtype=str)))
        for key, col in (("created", "created_at"), ("closed", "resolved_at"))
    }
    # Split partial days at report cutoffs. TDX event timestamps have at most
    # microsecond precision. The legacy report includes both endpoints.
    ranges = [
        (lo, hi + pd.Timedelta(microseconds=1))
        for lo, hi in periods(start, end).values()
    ]
    if include_archive_history and dates["created"].notna().any():
        ranges.append(
            (dates["created"].min().normalize(), end + pd.Timedelta(microseconds=1))
        )
    edges = set()
    for lo, stop in ranges:
        edges.update([lo, stop])
        edges.update(pd.date_range(lo.ceil("D"), stop.floor("D"), freq="D"))
    boundaries = sorted(edges)
    ordered = {
        k: series.dropna().dt.as_unit("us").sort_values() for k, series in dates.items()
    }
    records = []
    for lo, stop in pairwise(boundaries):
        if any(begin <= lo and stop <= finish for begin, finish in ranges):
            counts = [
                int(series.searchsorted(stop) - series.searchsorted(lo))
                for series in ordered.values()
            ]
            records.append((end, start, lo, stop, *counts))
    daily_counts = pd.DataFrame(
        records,
        columns=[
            "run_end",
            "run_start",
            "bucket_start",
            "bucket_end",
            "created",
            "closed",
        ],
    )
    windows = _build_7day_windows(end)
    backlog = pd.DataFrame(
        {
            "run_end": end,
            "observed_at": [hi for _, hi in windows],
            "open_count": _open_weekly_series(tickets, windows),
        }
    )
    with duckdb.connect(str(path)) as db:
        db.execute("begin transaction")
        for table, frame in (
            ("cfo_daily_counts", daily_counts),
            ("cfo_backlog_snapshots", backlog),
        ):
            db.register("incoming", frame)
            db.execute(
                f"create table if not exists {table} as select * from incoming limit 0"
            )
            db.execute(f"delete from {table} where run_end=?", [end])
            db.execute(f"insert into {table} by name select * from incoming")
            db.unregister("incoming")
        if observed is not None:
            _save_queue_snapshot(db, end, observed)
        db.execute("commit")


def load_metrics(path, start, end):
    with duckdb.connect(str(path)) as db:
        rows = db.execute(
            """select bucket_start,bucket_end,created,closed from cfo_daily_counts
            where run_start=? and run_end=? order by bucket_start""",
            [start, end],
        ).fetchdf()
        backlog = (
            db.execute(
                """select open_count from cfo_backlog_snapshots where run_end=?
            order by observed_at""",
                [end],
            )
            .fetchnumpy()["open_count"]
            .tolist()
        )
    counts = {}
    for label, (lo, hi) in periods(start, end).items():
        stop = hi + pd.Timedelta(microseconds=1)
        part = rows[(rows.bucket_start >= lo) & (rows.bucket_end <= stop)]
        if part.empty or (part.bucket_end - part.bucket_start).sum() != stop - lo:
            raise ValueError("Daily counts do not cover the CFO period")
        counts[label] = (int(part.created.sum()), int(part.closed.sum()))
    if len(backlog) != 8:
        raise ValueError("Incomplete CFO reporting period")
    result = {}
    labels = [_fmt(lo) for lo, _ in _build_7day_windows(end)]
    for i, kind in enumerate(("created", "closed")):
        current, prior, year = (counts[p][i] for p in ("current", "prior", "year"))
        prefix = f"tickets_{kind}_"
        result.update(
            {
                prefix + "this_week": current,
                prefix + "prior_week": prior,
                prefix + "year_ago": year,
                prefix + "ww_delta_pct": _delta_pct(current, prior),
                prefix + "yy_delta_pct": _delta_pct(current, year),
            }
        )
        result[kind + "_weekly"] = [
            {"week": label, "count": counts[f"week_{n}"][i]}
            for n, label in enumerate(labels)
        ]
    result.update(
        open_weekly=[
            {"week": label, "count": qty} for label, qty in zip(labels, backlog)
        ],
        total_open_tickets=backlog[-1],
        total_open_tickets_prior_week=backlog[-2],
        total_open_tickets_ww_delta=backlog[-1] - backlog[-2],
    )
    return result


def _save_queue_snapshot(db, end, observed):
    db.execute("""create table if not exists cfo_observed_queue
                  (run_end timestamptz primary key, summary json)""")
    db.execute(
        "insert or replace into cfo_observed_queue values (?,?)",
        [end, json.dumps(observed)],
    )


def load_queue_snapshot(path, end):
    with duckdb.connect(str(path), read_only=True) as db:
        row = db.execute(
            "select summary from cfo_observed_queue where run_end=?", [end]
        ).fetchone()
    return json.loads(row[0]) if row else {}
