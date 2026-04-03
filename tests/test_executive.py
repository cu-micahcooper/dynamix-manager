# tests/test_executive.py
import pandas as pd
import pytest

from dynamix_manager.executive import _business_hours_between, summarize_executive_snapshot


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


def test_week_over_week_delta_pct_excludes_prior_week_tickets_after_proportionate_cutoff():
    # as_of = Wed Apr 1 12:00 → prior window = Mon Mar 23 00:00 – Wed Mar 25 12:00
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week
        {"ticket_id": 2, "created_at": "2026-03-23T10:00:00Z"},  # prior week, before cutoff — counted
        {"ticket_id": 3, "created_at": "2026-03-26T10:00:00Z"},  # prior week Thu — after Wed cutoff — excluded
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    # this_week=1, prior_week=1 (ticket 3 excluded) → 0.0% delta
    assert snapshot["week_over_week_delta_pct"] == pytest.approx(0.0)


def test_week_over_week_delta_pct_is_none_when_prior_week_empty():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week only
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_over_week_delta_pct"] is None


def test_range_labels_reflect_as_of_and_prior_window():
    tickets = pd.DataFrame([{"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"}])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")  # Wednesday noon

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_range_label"] == "Mar 30 – Apr 1"
    assert snapshot["prior_week_range_label"] == "Mar 23 – Mar 25"
    assert snapshot["as_of_label"] == "Apr 1"


def test_week_label_formats_monday_to_sunday():
    tickets = pd.DataFrame([{"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"}])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_label"] == "Mar 30 – Apr 5"


def test_business_hours_between_returns_8_for_one_business_day():
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-03-31 08:00:00", tz="UTC")
    assert _business_hours_between(start, end, set()) == 8.0


def test_business_hours_between_excludes_holiday():
    # Mon→Wed = 2 business days normally, but Tuesday is a holiday → 1 day = 8h
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-04-01 08:00:00", tz="UTC")
    assert _business_hours_between(start, end, {"2026-03-31"}) == 8.0


def test_business_hours_between_returns_zero_for_same_day():
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-03-30 16:00:00", tz="UTC")
    assert _business_hours_between(start, end, set()) == 0.0


def test_completion_hours_this_week_lists_resolved_tickets():
    tickets = pd.DataFrame([
        {
            "ticket_id": 1,
            "created_at": "2026-03-30T08:00:00Z",   # Mon this week
            "resolved_at": "2026-03-31T08:00:00Z",   # Tue this week → 1 biz day = 8h
            "status_class": 3,
        },
        {
            "ticket_id": 2,
            "created_at": "2026-01-05T08:00:00Z",
            "resolved_at": "2026-01-07T08:00:00Z",   # 2 biz days = 16h; resolved last quarter
            "status_class": 3,
        },
        {
            "ticket_id": 3,
            "created_at": "2026-03-30T08:00:00Z",
            "resolved_at": None,                      # still open — excluded
            "status_class": 1,
        },
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    tw = {b["label"]: b["count"] for b in snapshot["completion_hours_this_week"]}
    assert tw["8–24h"] == 1   # 8h ticket resolved this week
    assert tw["0–8h"] == 0

    all_t = {b["label"]: b["count"] for b in snapshot["completion_hours_all_time"]}
    assert all_t["8–24h"] == 2   # 8h + 16h tickets, both in [8, 24)


def test_satisfaction_counts_groups_by_label():
    surveys = pd.DataFrame([
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-30T12:00:00Z"},  # within 30d — in
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-31T12:00:00Z"},  # within 30d — in
        {"satisfaction_label": "Satisfied",      "survey_completed_at": "2026-03-31T09:00:00Z"},  # within 30d — in
        {"satisfaction_label": "Dissatisfied",   "survey_completed_at": "2026-02-25T12:00:00Z"},  # >30d ago — excluded
    ])
    snapshot = summarize_executive_snapshot(
        pd.DataFrame(), surveys, as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["satisfaction_counts"] == {
        "Very Satisfied": 2,
        "Satisfied": 1,
    }


def test_satisfaction_trend_includes_last_six_months():
    surveys = pd.DataFrame([
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-15T12:00:00Z"},
        {"satisfaction_label": "Dissatisfied",   "survey_completed_at": "2026-03-20T12:00:00Z"},
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2025-09-01T12:00:00Z"},  # >6 months ago
    ])
    as_of = pd.Timestamp("2026-04-01", tz="UTC")
    snapshot = summarize_executive_snapshot(pd.DataFrame(), surveys, as_of=as_of)

    months = [m["month"] for m in snapshot["satisfaction_trend"]]
    assert "2026-03" in months
    assert "2025-09" not in months

    march = next(m for m in snapshot["satisfaction_trend"] if m["month"] == "2026-03")
    assert march["total"] == 2
    assert march["positive_rate"] == pytest.approx(0.5)


def test_sla_compliance_rate_excludes_tickets_without_sla():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "is_sla_violated": False},
        {"ticket_id": 2, "is_sla_violated": False},
        {"ticket_id": 3, "is_sla_violated": True},
        {"ticket_id": 4, "is_sla_violated": None},   # no SLA — excluded
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["sla_compliance_rate"] == pytest.approx(2 / 3)


def test_stale_open_count_excludes_resolved_and_recent_tickets():
    tickets = pd.DataFrame([
        {
            "ticket_id": 1,
            "created_at": "2026-03-01T08:00:00Z",   # ~22 biz days old — stale
            "resolved_at": None,
            "status_class": 1,
        },
        {
            "ticket_id": 2,
            "created_at": "2026-03-30T08:00:00Z",   # 2 biz days old — not stale
            "resolved_at": None,
            "status_class": 1,
        },
        {
            "ticket_id": 3,
            "created_at": "2026-01-01T08:00:00Z",
            "resolved_at": "2026-03-01T08:00:00Z",  # resolved — excluded
            "status_class": 3,
        },
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")
    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)
    assert snapshot["stale_open_count"] == 1


def test_top_services_returns_top_5_by_count():
    rows = [{"ticket_id": i, "service_name": "Printing"} for i in range(5)]
    rows += [{"ticket_id": i + 100, "service_name": "Network"} for i in range(3)]
    rows += [{"ticket_id": i + 200, "service_name": "Software"} for i in range(2)]
    tickets = pd.DataFrame(rows)
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["top_services"][0] == {"service_name": "Printing", "count": 5}
    assert snapshot["top_services"][1] == {"service_name": "Network", "count": 3}


def test_median_first_response_hours_computed_from_created_and_responded_at():
    tickets = pd.DataFrame([
        {"created_at": "2026-03-30T08:00:00Z", "responded_at": "2026-03-31T08:00:00Z"},  # 24 calendar hours
        {"created_at": "2026-03-30T08:00:00Z", "responded_at": "2026-04-01T08:00:00Z"},  # 48 calendar hours
        {"created_at": "2026-03-30T08:00:00Z", "responded_at": "2026-04-02T08:00:00Z"},  # 72 calendar hours
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-02", tz="UTC")
    )
    assert snapshot["median_first_response_hours"] == pytest.approx(48.0)


def test_unassigned_count_only_counts_open_tickets():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "assignee_name": None,   "resolved_at": None,                   "status_class": 1},  # open, unassigned
        {"ticket_id": 2, "assignee_name": "Alex", "resolved_at": None,                   "status_class": 1},  # open, assigned
        {"ticket_id": 3, "assignee_name": None,   "resolved_at": "2026-03-01T10:00:00Z", "status_class": 3},  # resolved, unassigned
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["unassigned_count"] == 1


def test_customer_effort_counts_and_easy_rate_within_30d_window():
    surveys = pd.DataFrame([
        {"customer_effort_label": "Very Easy",  "survey_completed_at": "2026-03-30T12:00:00Z"},  # in window
        {"customer_effort_label": "Easy",       "survey_completed_at": "2026-03-28T12:00:00Z"},  # in window
        {"customer_effort_label": "Difficult",  "survey_completed_at": "2026-03-25T12:00:00Z"},  # in window
        {"customer_effort_label": "Very Easy",  "survey_completed_at": "2026-02-01T12:00:00Z"},  # >30d — excluded
    ])
    as_of = pd.Timestamp("2026-04-01", tz="UTC")
    snapshot = summarize_executive_snapshot(pd.DataFrame(), surveys, as_of=as_of)

    assert snapshot["customer_effort_counts"] == {"Very Easy": 1, "Easy": 1, "Difficult": 1}
    assert snapshot["customer_effort_easy_rate"] == pytest.approx(2 / 3)
