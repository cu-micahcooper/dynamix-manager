# tests/test_executive.py
import pandas as pd
import pytest

from dynamix_manager.executive import summarize_executive_snapshot


def test_new_tickets_this_week_counts_tickets_created_since_monday():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # Monday — in week
        {"ticket_id": 2, "created_at": "2026-03-31T10:00:00Z"},  # Tuesday — in week
        {"ticket_id": 3, "created_at": "2026-03-29T23:59:59Z"},  # Sunday before — out
        {"ticket_id": 4, "created_at": "2026-03-23T10:00:00Z"},  # prior week — out
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["new_tickets_this_week"] == 2


def test_avg_weekly_tickets_created_divides_total_by_weeks_elapsed():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-16T10:00:00Z"},  # week 12
        {"ticket_id": 2, "created_at": "2026-03-23T10:00:00Z"},  # week 13
        {"ticket_id": 3, "created_at": "2026-03-30T10:00:00Z"},  # week 14
        {"ticket_id": 4, "created_at": "2026-03-31T10:00:00Z"},  # week 14
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    # oldest = week 12 (starts 2026-03-16), as_of = week 14 (starts 2026-03-30) → 3 weeks
    assert snapshot["avg_weekly_tickets_created"] == pytest.approx(4 / 3, rel=1e-3)


def test_week_over_week_delta_pct_compares_current_to_prior_week():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week
        {"ticket_id": 2, "created_at": "2026-03-31T10:00:00Z"},  # this week
        {"ticket_id": 3, "created_at": "2026-03-23T10:00:00Z"},  # prior week
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    # this_week=2, prior_week=1 → (2-1)/1 * 100 = 100.0
    assert snapshot["week_over_week_delta_pct"] == pytest.approx(100.0)


def test_week_over_week_delta_pct_is_none_when_prior_week_empty():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week only
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_over_week_delta_pct"] is None


def test_week_label_formats_monday_to_sunday():
    tickets = pd.DataFrame([{"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"}])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_label"] == "Mar 30 – Apr 5"
