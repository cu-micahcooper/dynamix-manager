import pandas as pd
import pytest

from dynamix_manager.cfo import _count_open_at, summarize_cfo_snapshot


_AS_OF = pd.Timestamp("2025-04-09 15:00:00", tz="UTC")  # Wednesday
_WEEK_START = pd.Timestamp("2025-04-07 00:00:00", tz="UTC")  # Monday
_PRIOR_START = pd.Timestamp("2025-03-31 00:00:00", tz="UTC")
_YEAR_START = pd.Timestamp("2024-04-08 00:00:00", tz="UTC")  # 52 weeks prior


def _make_tickets(created_dates, resolved_dates=None, status_classes=None):
    n = len(created_dates)
    df = pd.DataFrame(
        {
            "ticket_id": range(n),
            "created_at": created_dates,
            "resolved_at": resolved_dates if resolved_dates is not None else [None] * n,
            "status_class": status_classes if status_classes is not None else [1] * n,
        }
    )
    return df


def _make_surveys(completed_dates, effort_labels):
    return pd.DataFrame(
        {
            "survey_id": range(len(completed_dates)),
            "survey_completed_at": completed_dates,
            "customer_effort_label": effort_labels,
        }
    )


def test_tickets_created_this_week():
    tickets = _make_tickets(
        created_dates=[
            "2025-04-08T10:00:00Z",  # this week
            "2025-04-08T11:00:00Z",  # this week
            "2025-03-31T10:00:00Z",  # prior week
            "2024-04-08T10:00:00Z",  # year ago
        ]
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    assert snap["tickets_created_this_week"] == 2
    assert snap["tickets_created_prior_week"] == 1
    assert snap["tickets_created_year_ago"] == 1


def test_tickets_closed_this_week():
    tickets = _make_tickets(
        created_dates=["2025-04-01T09:00:00Z"] * 3,
        resolved_dates=[
            "2025-04-08T14:00:00Z",  # this week
            "2025-04-01T14:00:00Z",  # prior week
            None,                    # still open
        ],
        status_classes=[3, 3, 1],
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    assert snap["tickets_closed_this_week"] == 1
    assert snap["tickets_closed_prior_week"] == 1


def test_total_open_tickets_excludes_resolved_and_completed():
    tickets = _make_tickets(
        created_dates=["2025-04-01T09:00:00Z"] * 4,
        resolved_dates=[None, None, "2025-04-05T12:00:00Z", None],
        status_classes=[1, 2, 3, 4],
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    # status_class 3 and 4 → closed; status_class 1 and 2 with no resolved_at → open
    assert snap["total_open_tickets"] == 2


def test_total_open_tickets_week_over_week_delta_uses_open_series():
    tickets = _make_tickets(
        created_dates=[
            "2025-03-25T09:00:00Z",
            "2025-04-02T09:00:00Z",
            "2025-04-08T09:00:00Z",
        ],
        resolved_dates=[
            None,
            None,
            None,
        ],
        status_classes=[1, 1, 1],
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    assert snap["total_open_tickets_prior_week"] == 2
    assert snap["total_open_tickets"] == 3
    assert snap["total_open_tickets_ww_delta"] == 1


def test_delta_pct_computed_correctly():
    tickets = _make_tickets(
        created_dates=[
            "2025-04-08T10:00:00Z",  # this week ×2
            "2025-04-08T11:00:00Z",
            "2025-03-31T10:00:00Z",  # prior week ×1
        ]
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    # 2 vs 1 → +100%
    assert snap["tickets_created_ww_delta_pct"] == pytest.approx(100.0)


def test_survey_effort_last_7_days():
    surveys = _make_surveys(
        completed_dates=[
            "2025-04-08T10:00:00Z",   # within 7 days
            "2025-04-08T11:00:00Z",   # within 7 days
            "2025-03-30T10:00:00Z",   # older than 7 days — excluded
        ],
        effort_labels=["Very Easy", "Easy", "Difficult"],
    )
    snap = summarize_cfo_snapshot(pd.DataFrame(), surveys, as_of=_AS_OF)
    assert snap["survey_effort_counts"].get("Very Easy", 0) == 1
    assert snap["survey_effort_counts"].get("Easy", 0) == 1
    assert snap["survey_effort_counts"].get("Difficult", 0) == 0
    assert snap["survey_easy_rate"] == pytest.approx(1.0)


def test_survey_easy_rate_partial():
    surveys = _make_surveys(
        completed_dates=["2025-04-08T10:00:00Z"] * 4,
        effort_labels=["Very Easy", "Easy", "Difficult", "Very Difficult"],
    )
    snap = summarize_cfo_snapshot(pd.DataFrame(), surveys, as_of=_AS_OF)
    assert snap["survey_easy_rate"] == pytest.approx(0.5)


def test_survey_comments_use_same_7_day_window_as_ticket_metrics():
    tickets = pd.DataFrame(
        [
            {"ticket_id": 1, "ticket_title": "Password Reset"},
            {
                "ticket_id": 2,
                "ticket_title": "Laptop does not connect to the campus wireless network after update",
            },
        ]
    )
    surveys = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "survey_completed_at": "2025-04-08T11:00:00Z",
                "satisfaction_label": "Very Satisfied",
                "commenter_name": "Jane Doe",
                "comment_text": "Helpful support",
                "team_name": "Client Services",
            },
            {
                "ticket_id": 2,
                "survey_completed_at": "2025-04-07T09:00:00Z",
                "satisfaction_label": "Satisfied",
                "commenter_name": "John Smith",
                "comment_text": "Quick turnaround",
                "team_name": "Infrastructure",
            },
            {
                "survey_completed_at": "2025-03-31T08:59:59Z",
                "satisfaction_label": "Dissatisfied",
                "comment_text": "Outside the current window",
                "team_name": "Client Services",
            },
            {
                "survey_completed_at": "2025-04-08T12:00:00Z",
                "satisfaction_label": "Satisfied",
                "comment_text": "   ",
                "team_name": "Client Services",
            },
        ]
    )

    snap = summarize_cfo_snapshot(tickets, surveys, as_of=_AS_OF)

    assert snap["survey_comments"] == [
        {
            "survey_completed_at": "2025-04-08T11:00:00+00:00",
            "satisfaction_label": "Very Satisfied",
            "commenter_name": "Jane Doe",
            "ticket_title": "Password Reset",
            "team_name": "Client Services",
            "comment_text": "Helpful support",
        },
        {
            "survey_completed_at": "2025-04-07T09:00:00+00:00",
            "satisfaction_label": "Satisfied",
            "commenter_name": "John Smith",
            "ticket_title": "Laptop does not connect to the campus wireless network after update",
            "team_name": "Infrastructure",
            "comment_text": "Quick turnaround",
        },
    ]


def test_youtrack_projects_passed_through():
    projects = [
        {"name": "Support", "short_name": "SUP", "description": "", "leader_name": "Jane"},
        {"name": "Dev", "short_name": "DEV", "description": "", "leader_name": ""},
    ]
    snap = summarize_cfo_snapshot(
        pd.DataFrame(), pd.DataFrame(), youtrack_projects=projects, as_of=_AS_OF
    )
    assert snap["youtrack_projects"] == projects


def test_youtrack_project_movement_uses_same_7_day_window():
    youtrack_snapshot = {
        "projects": [
            {"id": "YT-1", "summary": "Portal refresh", "it_team": "Apps", "assignee": "Jane"},
            {"id": "YT-2", "summary": "Wireless refresh", "it_team": "Infra", "assignee": "John"},
        ],
        "sprint_issues": [
            {
                "id": "YT-1",
                "summary": "Portal refresh",
                "stage": "In Progress",
                "created_at": "2025-04-08T10:00:00+00:00",
                "resolved_at": "",
                "updated_at": "2025-04-08T10:00:00+00:00",
            },
            {
                "id": "YT-2",
                "summary": "Wireless refresh",
                "stage": "Done",
                "created_at": "2025-03-25T10:00:00+00:00",
                "resolved_at": "2025-04-08T14:00:00+00:00",
                "updated_at": "2025-04-08T14:00:00+00:00",
            },
            {
                "id": "YT-3",
                "summary": "Outside window",
                "stage": "Done",
                "created_at": "2025-03-20T10:00:00+00:00",
                "resolved_at": "2025-03-31T14:00:00+00:00",
                "updated_at": "2025-03-31T14:00:00+00:00",
            },
        ],
    }

    snap = summarize_cfo_snapshot(
        pd.DataFrame(),
        pd.DataFrame(),
        youtrack_projects=youtrack_snapshot,
        as_of=_AS_OF,
    )

    assert snap["youtrack_projects"] == [
        {
            "id": "YT-1",
            "summary": "Portal refresh",
            "it_team": "Apps",
            "assignee": "Jane",
            "is_new_this_week": True,
        },
        {
            "id": "YT-2",
            "summary": "Wireless refresh",
            "it_team": "Infra",
            "assignee": "John",
            "is_new_this_week": False,
        },
    ]
    assert snap["youtrack_project_movement"] == {
        "in_progress_count": 2,
        "new_this_week": 1,
        "completed_this_week": 1,
    }


def test_weekly_trend_has_8_entries():
    tickets = _make_tickets(
        created_dates=[
            "2025-04-08T10:00:00Z",
            "2025-03-31T10:00:00Z",
            "2025-03-24T10:00:00Z",
        ]
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    assert len(snap["created_weekly"]) == 8
    assert len(snap["closed_weekly"]) == 8
    assert len(snap["open_weekly"]) == 8


def test_weekly_trend_current_week_is_last():
    tickets = _make_tickets(created_dates=["2025-04-08T10:00:00Z"])
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    # Last entry should include the ticket created this week
    assert snap["created_weekly"][-1]["count"] == 1
    # All prior entries should be 0 for this single ticket
    assert all(w["count"] == 0 for w in snap["created_weekly"][:-1])


def test_open_weekly_counts_unresolved_as_of_each_week():
    tickets = _make_tickets(
        created_dates=["2025-04-01T10:00:00Z"],
        resolved_dates=["2025-04-10T10:00:00Z"],  # resolved after as_of
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=_AS_OF)
    # Ticket created Apr 1 and resolved Apr 10 → open as of any point before Apr 10
    assert snap["open_weekly"][-1]["count"] == 1


# ── _count_open_at unit tests ──────────────────────────────────────────────────


def _ts(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _series(*values):
    s = pd.Series([_ts(v) if v else pd.NaT for v in values])
    # ensure tz-aware so comparisons with UTC timestamps don't raise
    if s.dt.tz is None:
        s = s.dt.tz_localize("UTC")
    return s


_SNAP = _ts("2025-04-09T15:00:00Z")


def test_count_open_at_created_before_not_resolved():
    created = _series("2025-04-01T10:00:00Z")
    resolved = _series(None)
    assert _count_open_at(created, resolved, _SNAP) == 1


def test_count_open_at_resolved_before_snapshot():
    created = _series("2025-04-01T10:00:00Z")
    resolved = _series("2025-04-05T10:00:00Z")  # resolved before snapshot
    assert _count_open_at(created, resolved, _SNAP) == 0


def test_count_open_at_resolved_after_snapshot():
    created = _series("2025-04-01T10:00:00Z")
    resolved = _series("2025-04-10T10:00:00Z")  # resolved after snapshot
    assert _count_open_at(created, resolved, _SNAP) == 1


def test_count_open_at_created_after_snapshot():
    created = _series("2025-04-10T10:00:00Z")  # not yet created at snapshot
    resolved = _series(None)
    assert _count_open_at(created, resolved, _SNAP) == 0


def test_count_open_at_null_created_excluded():
    created = _series(None)
    resolved = _series(None)
    assert _count_open_at(created, resolved, _SNAP) == 0


def test_count_open_at_created_and_resolved_on_snapshot_day():
    created = _series("2025-04-09T10:00:00Z")   # same day, before snapshot
    resolved = _series("2025-04-09T16:00:00Z")  # same day, after snapshot time
    assert _count_open_at(created, resolved, _SNAP) == 1


def test_count_open_at_resolved_exactly_at_snapshot():
    # resolved_at == snapshot → already resolved, not open
    created = _series("2025-04-01T10:00:00Z")
    resolved = _series("2025-04-09T15:00:00Z")
    assert _count_open_at(created, resolved, _SNAP) == 0


def test_count_open_at_mixed_tickets():
    # 3 tickets: open, resolved-before, not-yet-created
    created = _series(
        "2025-04-01T10:00:00Z",   # open
        "2025-04-01T10:00:00Z",   # resolved before
        "2025-04-10T10:00:00Z",   # not yet created
    )
    resolved = _series(
        None,
        "2025-04-05T10:00:00Z",
        None,
    )
    assert _count_open_at(created, resolved, _SNAP) == 1


def test_open_weekly_series_tracks_resolution_over_time():
    """Open count should drop in the window after a ticket is resolved."""
    # Ticket created 3 weeks ago, resolved 1 week ago
    as_of = _ts("2025-04-09T15:00:00Z")
    tickets = _make_tickets(
        created_dates=["2025-03-19T10:00:00Z"],   # 3 weeks ago
        resolved_dates=["2025-04-02T10:00:00Z"],  # resolved ~1 week ago
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=as_of)
    weekly = snap["open_weekly"]
    # Should be open in earlier windows, closed in the most recent window
    # The ticket was resolved Apr 2; the last window is (Apr 2, Apr 9)
    # Apr 2 is the start; resolved_at Apr 2 10:00 is within the window
    # At end of last window (Apr 9), resolved_at < Apr 9 → not open
    assert weekly[-1]["count"] == 0
    # At the end of the 3rd-to-last window the ticket was open
    # Window[-3] ends around Mar 26, ticket created Mar 19 and not yet resolved
    assert weekly[-3]["count"] == 1


def test_open_weekly_closed_without_resolved_at_not_counted():
    """Tickets closed via status_class (no resolved_at) must not inflate sparkline.

    TDX sometimes closes a ticket by setting status_class=3 without writing a
    resolved_at date, leaving it as the C# DateTime.MinValue sentinel which
    pd.to_datetime coerces to NaT. Without the status_class fallback these
    tickets would be counted as "open forever" and corrupt the trend chart.
    """
    as_of = _ts("2025-04-09T15:00:00Z")
    # Both tickets created 20 weeks ago so they appear in all 8 windows
    tickets = _make_tickets(
        created_dates=[
            "2024-11-01T10:00:00Z",   # closed via status_class, no resolved_at
            "2024-11-01T10:00:00Z",   # truly open
        ],
        resolved_dates=[None, None],
        status_classes=[3, 1],        # one closed (sc=3), one open (sc=1)
    )
    snap = summarize_cfo_snapshot(tickets, pd.DataFrame(), as_of=as_of)
    # Only the sc=1 ticket should be counted as open in every window
    for entry in snap["open_weekly"]:
        assert entry["count"] == 1, f"Expected 1 open ticket, got {entry['count']} in window {entry['week']}"


def test_empty_inputs_produce_zero_counts():
    snap = summarize_cfo_snapshot(pd.DataFrame(), pd.DataFrame(), as_of=_AS_OF)
    assert snap["tickets_created_this_week"] == 0
    assert snap["tickets_closed_this_week"] == 0
    assert snap["total_open_tickets"] == 0
    assert snap["survey_easy_rate"] is None
    assert snap["youtrack_projects"] == []
