import pandas as pd

from dynamix_manager.metrics import summarize_ticket_health


def test_summarize_ticket_health_computes_backlog_and_time_metrics():
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "status_name": "Closed",
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "response_time_hours": 0.5,
                "resolution_time_hours": 2.0,
                "resolved_at": "2026-03-01T10:00:00Z",
            },
            {
                "ticket_id": 2,
                "status_name": "Open",
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "response_time_hours": 1.0,
                "resolution_time_hours": None,
                "resolved_at": None,
            },
            {
                "ticket_id": 3,
                "status_name": "Closed",
                "team_name": "Infrastructure",
                "service_name": "Network",
                "response_time_hours": 2.0,
                "resolution_time_hours": 6.0,
                "resolved_at": "2026-03-02T12:00:00Z",
            },
        ]
    )

    summary = summarize_ticket_health(frame)

    assert summary["total_tickets"] == 3
    assert summary["resolved_tickets"] == 2
    assert summary["backlog_tickets"] == 1
    assert summary["average_response_hours"] == (0.5 + 1.0 + 2.0) / 3
    assert summary["average_resolution_hours"] == (2.0 + 6.0) / 2
    assert summary["top_teams"][0] == ("Client Services", 2)
    assert summary["daily_team_completion"] == [
        {"resolved_date": "2026-03-02", "team_name": "Infrastructure", "completed_tickets": 1},
    ]
    assert summary["daily_member_completion"] == [
        {"resolved_date": "2026-03-02", "team_name": "Infrastructure", "assignee_name": "Unassigned", "completed_tickets": 1},
    ]


def test_summarize_ticket_health_uses_business_days_and_days_off():
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "team_name": "Client Services",
                "assignee_name": "Analyst One",
                "resolved_at": "2026-03-02T10:00:00Z",
            },
            {
                "ticket_id": 2,
                "team_name": "Client Services",
                "assignee_name": "Analyst Two",
                "resolved_at": "2026-03-02T12:00:00Z",
            },
            {
                "ticket_id": 3,
                "team_name": "Infrastructure",
                "assignee_name": "Analyst Three",
                "resolved_at": "2026-03-03T09:00:00Z",
            },
            {
                "ticket_id": 4,
                "team_name": "Infrastructure",
                "assignee_name": "Analyst Three",
                "resolved_at": "2026-03-01T09:00:00Z",
            },
            {
                "ticket_id": 5,
                "team_name": "Infrastructure",
                "assignee_name": "Analyst Three",
                "resolved_at": "2026-03-04T09:00:00Z",
            },
        ]
    )
    days_off = pd.DataFrame(
        [
            {"day_off_id": 10, "name": "Staff Retreat", "holiday_date": "2026-03-03"},
        ]
    )

    summary = summarize_ticket_health(frame, days_off=days_off)

    assert summary["daily_team_completion"] == [
        {"resolved_date": "2026-03-02", "team_name": "Client Services", "completed_tickets": 2},
        {"resolved_date": "2026-03-04", "team_name": "Infrastructure", "completed_tickets": 1},
    ]
    assert summary["daily_member_completion"] == [
        {"resolved_date": "2026-03-02", "team_name": "Client Services", "assignee_name": "Analyst One", "completed_tickets": 1},
        {"resolved_date": "2026-03-02", "team_name": "Client Services", "assignee_name": "Analyst Two", "completed_tickets": 1},
        {"resolved_date": "2026-03-04", "team_name": "Infrastructure", "assignee_name": "Analyst Three", "completed_tickets": 1},
    ]


def test_summarize_ticket_health_groups_member_completion_within_team():
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 10,
                "team_name": "Client Services",
                "assignee_name": "Analyst One",
                "resolved_at": "2026-03-03T09:00:00Z",
            },
            {
                "ticket_id": 11,
                "team_name": "Client Services",
                "assignee_name": "Analyst One",
                "resolved_at": "2026-03-03T11:00:00Z",
            },
            {
                "ticket_id": 12,
                "team_name": "Client Services",
                "assignee_name": "Analyst Two",
                "resolved_at": "2026-03-03T13:00:00Z",
            },
            {
                "ticket_id": 13,
                "team_name": "Infrastructure",
                "assignee_name": "Analyst Three",
                "resolved_at": "2026-03-03T15:00:00Z",
            },
        ]
    )

    summary = summarize_ticket_health(frame)

    assert summary["daily_team_completion"] == [
        {"resolved_date": "2026-03-03", "team_name": "Client Services", "completed_tickets": 3},
        {"resolved_date": "2026-03-03", "team_name": "Infrastructure", "completed_tickets": 1},
    ]
    assert summary["daily_member_completion"] == [
        {"resolved_date": "2026-03-03", "team_name": "Client Services", "assignee_name": "Analyst One", "completed_tickets": 2},
        {"resolved_date": "2026-03-03", "team_name": "Client Services", "assignee_name": "Analyst Two", "completed_tickets": 1},
        {"resolved_date": "2026-03-03", "team_name": "Infrastructure", "assignee_name": "Analyst Three", "completed_tickets": 1},
    ]


def test_summarize_ticket_health_adds_quality_scorecard_metrics():
    tickets = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "ticket_title": "Printer issue",
                "status_name": "Open",
                "team_name": "Tech Services",
                "service_name": "Printing",
                "response_time_hours": 1.0,
                "resolution_time_hours": None,
                "priority_name": "",
                "created_at": "2026-03-10T08:00:00Z",
                "modified_at": "2026-03-10T10:00:00Z",
                "resolved_at": None,
                "sla_name": "Standard",
                "respond_by_at": "2026-03-10T16:00:00Z",
                "resolve_by_at": "2026-03-11T16:00:00Z",
                "is_sla_violated": False,
                "is_sla_respond_by_violated": False,
                "is_sla_resolve_by_violated": False,
            },
            {
                "ticket_id": 99,
                "ticket_title": "Printer issue",
                "status_name": "Open",
                "team_name": "Tech Services",
                "service_name": "Printing",
                "response_time_hours": 4.0,
                "resolution_time_hours": None,
                "priority_name": "Medium",
                "created_at": "2026-03-04T08:00:00Z",
                "modified_at": "2026-03-04T10:00:00Z",
                "resolved_at": None,
                "sla_name": "Standard",
                "respond_by_at": "2026-03-05T16:00:00Z",
                "resolve_by_at": "2026-03-06T16:00:00Z",
                "is_sla_violated": True,
                "is_sla_respond_by_violated": False,
                "is_sla_resolve_by_violated": True,
            },
            {
                "ticket_id": 100,
                "ticket_title": "",
                "status_name": "Closed",
                "team_name": "Infrastructure",
                "service_name": "",
                "response_time_hours": 2.0,
                "resolution_time_hours": 8.0,
                "priority_name": "High",
                "created_at": "2026-03-01T08:00:00Z",
                "modified_at": "2026-03-03T10:00:00Z",
                "resolved_at": "2026-03-03T12:00:00Z",
                "sla_name": "Standard",
                "respond_by_at": "2026-03-01T10:00:00Z",
                "resolve_by_at": "2026-03-03T16:00:00Z",
                "is_sla_violated": False,
                "is_sla_respond_by_violated": False,
                "is_sla_resolve_by_violated": False,
            },
        ]
    )
    quality_flags = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "ticket_title": "Printer issue",
                "team_name": "Tech Services",
                "service_name": "Printing",
                "requestor_name": "Pat Client",
                "last_public_interaction_at": "2026-03-10T10:00:00Z",
                "last_private_interaction_at": None,
                "last_private_interaction_by": None,
                "last_public_interaction_actor_type": "client",
                "last_public_interaction_by": "Pat Client",
                "client_last_interaction_flag": True,
                "it_follow_up_streak": 0,
                "it_follow_up_without_client_response_flag": False,
                "interaction_count": 1,
                "stale_public_update_business_days": 0,
                "stale_public_update_flag": False,
                "private_activity_since_last_public_flag": False,
            },
            {
                "ticket_id": 99,
                "ticket_title": "Printer issue",
                "team_name": "Tech Services",
                "service_name": "Printing",
                "requestor_name": "Riley Requestor",
                "last_public_interaction_at": "2026-03-04T10:00:00Z",
                "last_private_interaction_at": "2026-03-05T09:30:00Z",
                "last_private_interaction_by": "Alex Analyst",
                "last_public_interaction_actor_type": "it",
                "last_public_interaction_by": "Alex Analyst",
                "client_last_interaction_flag": False,
                "it_follow_up_streak": 5,
                "it_follow_up_without_client_response_flag": True,
                "interaction_count": 5,
                "stale_public_update_business_days": 4,
                "stale_public_update_flag": True,
                "private_activity_since_last_public_flag": True,
            },
        ]
    )
    interactions = pd.DataFrame(
        [
            {"ticket_id": 42, "interaction_id": 1, "created_at": "2026-03-10T10:00:00Z", "created_uid": "client-1", "created_full_name": "Pat Client", "body": "Any update?", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "client", "interaction_source": "entry"},
            {"ticket_id": 99, "interaction_id": 2, "created_at": "2026-03-01T09:00:00Z", "created_uid": "it-1", "created_full_name": "Alex Analyst", "body": "Following up.", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "it", "interaction_source": "entry"},
            {"ticket_id": 99, "interaction_id": 3, "created_at": "2026-03-02T09:00:00Z", "created_uid": "it-1", "created_full_name": "Alex Analyst", "body": "Following up again.", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "it", "interaction_source": "entry"},
            {"ticket_id": 99, "interaction_id": 4, "created_at": "2026-03-03T09:00:00Z", "created_uid": "it-1", "created_full_name": "Alex Analyst", "body": "Checking in.", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "it", "interaction_source": "entry"},
            {"ticket_id": 99, "interaction_id": 5, "created_at": "2026-03-04T09:00:00Z", "created_uid": "it-1", "created_full_name": "Alex Analyst", "body": "One more follow-up.", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "it", "interaction_source": "entry"},
            {"ticket_id": 99, "interaction_id": 6, "created_at": "2026-03-04T10:00:00Z", "created_uid": "it-1", "created_full_name": "Alex Analyst", "body": "Closing soon.", "is_private": False, "is_communication": True, "update_type": 1, "actor_type": "it", "interaction_source": "entry"},
        ]
    )

    summary = summarize_ticket_health(
        tickets,
        quality_flags=quality_flags,
        interactions=interactions,
        as_of="2026-03-10T12:00:00Z",
    )

    assert summary["response_percentiles_hours"]["p50"] == 2.0
    assert summary["resolution_percentiles_hours"]["p50"] == 8.0
    assert summary["quality_counts"] == {
        "client_last_interaction": 1,
        "repeated_it_followup": 1,
        "stale_open_tickets": 1,
        "stale_public_updates": 1,
        "private_activity_since_last_public": 1,
    }
    assert summary["sla_summary"] == {
        "covered_tickets": 3,
        "violated_tickets": 1,
        "respond_breached": 0,
        "resolve_breached": 1,
        "open_near_respond_due": 1,
        "open_near_resolve_due": 1,
        "coverage_rate": 1.0,
        "violation_rate": 1 / 3,
    }
    assert summary["hygiene_counts"] == {
        "missing_title": 1,
        "missing_service": 1,
        "missing_team": 0,
        "open_unassigned": 2,
        "missing_priority": 1,
    }
    assert summary["quality_adjusted_sla"] == {
        "breached_and_high_touch": 1,
        "breached_and_client_waiting": 0,
        "breached_and_repeated_it_followup": 1,
    }
    assert summary["backlog_age_buckets"][0] == {"bucket": "0-1 business days", "tickets": 1}
    assert summary["backlog_age_buckets"][1] == {"bucket": "3-5 business days", "tickets": 1}
    assert summary["high_touch_tickets"][0]["ticket_id"] == 99
    assert summary["high_touch_tickets"][0]["touch_count"] == 5
    assert summary["sla_hotspots"][0]["team_name"] == "Tech Services"
    assert summary["team_quality_hotspots"][0]["team_name"] == "Tech Services"
    assert summary["team_quality_hotspots"][0]["stale_public_updates"] == 1
    assert summary["team_quality_hotspots"][0]["private_activity_since_last_public"] == 1
    assert summary["member_backlog_hotspots"][0]["team_name"] == "Tech Services"
    assert summary["top_recurrent_titles"][0]["ticket_title"] == "Printer issue"
    assert summary["hygiene_tickets"][0]["ticket_id"] == 42
    assert summary["stale_open_tickets"][0]["ticket_id"] == 99
