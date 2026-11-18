import duckdb
import pandas as pd

from dynamix_manager import cfo_counts
from dynamix_manager.cfo import summarize_cfo_snapshot


def test_report_cutoffs_replay_and_no_ticket_identifiers(tmp_path):
    end = pd.Timestamp("2026-11-18 17:03:20.476971", tz="UTC")
    start = end - pd.Timedelta(days=7)
    events = [
        start - pd.Timedelta(microseconds=1),
        start,
        end,
        end + pd.Timedelta(microseconds=1),
        start - pd.Timedelta(weeks=52),
        start - pd.Timedelta(days=70),
    ]
    tickets = pd.DataFrame(
        {
            "ticket_id": range(6),
            "created_at": events,
            "resolved_at": [end, None, None, None, start, None],
            "status_class": [3, 1, 2, 1, 4, 5],
        }
    )
    legacy = summarize_cfo_snapshot(
        tickets, pd.DataFrame(), period_start=start, as_of=end
    )
    db = tmp_path / "counts.db"
    for _ in range(2):
        cfo_counts.save_counts(
            db, tickets, start, end, observed={"scoped_open_count": 2}
        )
    values = cfo_counts.load_metrics(db, start, end)
    assert all(values[key] == legacy[key] for key in values)
    assert values["tickets_created_this_week"] == 2
    assert cfo_counts.load_queue_snapshot(db, end) == {"scoped_open_count": 2}
    with duckdb.connect(str(db)) as conn:
        assert conn.execute(
            "select count(*) from information_schema.columns where column_name='ticket_id'"
        ).fetchone() == (0,)
        assert conn.execute(
            "select count(*) from cfo_backlog_snapshots"
        ).fetchone() == (8,)
        conn.execute(
            "delete from cfo_daily_counts where bucket_start=(select max(bucket_start) from cfo_daily_counts)"
        )
    import pytest

    with pytest.raises(ValueError, match="cover"):
        cfo_counts.load_metrics(db, start, end)


def test_capped_search_subdivides_and_deduplicates_boundary():
    from dynamix_manager.report_source import date_rows, frame_from_rows

    start = pd.Timestamp("2026-11-01", tz="UTC")
    end = start + pd.Timedelta(days=2)
    mid = start + pd.Timedelta(days=1)

    class Search:
        def search_tickets(self, token, payload, ticket_app_id):
            lo = pd.Timestamp(payload["CreatedDateFrom"])
            hi = pd.Timestamp(payload["CreatedDateTo"])
            if (lo, hi) == (start, end):
                return [{"ID": 9}] * payload["MaxResults"]
            if (lo, hi) == (start, mid):
                return [{"ID": 1}, {"ID": 2}]
            assert (lo, hi) == (mid, end)
            return [{"ID": 2}, {"ID": 3}]

    frame = frame_from_rows(
        date_rows(Search(), "token", 634, "CreatedDate", start, end)
    )
    assert frame.ticket_id.tolist() == [1, 2, 3]
