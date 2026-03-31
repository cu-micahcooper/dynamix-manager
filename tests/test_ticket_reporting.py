from pathlib import Path

import pandas as pd

from dynamix_manager.reporting import render_ticket_health_html, write_ticket_health_report


def test_render_ticket_health_html_includes_ticket_metrics():
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "status_name": "Closed",
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "assignee_name": "Analyst One",
                "priority_name": "High",
                "response_time_hours": 0.5,
                "resolution_time_hours": 2.0,
                "sla_name": "Standard",
                "respond_by_at": "2026-03-03T09:00:00Z",
                "resolve_by_at": "2026-03-03T12:00:00Z",
                "is_sla_violated": False,
                "is_sla_respond_by_violated": False,
                "is_sla_resolve_by_violated": False,
                "resolved_at": "2026-03-03T10:00:00Z",
            },
            {
                "ticket_id": 2,
                "status_name": "Closed",
                "team_name": "Infrastructure",
                "service_name": "Network",
                "assignee_name": "Analyst Two",
                "priority_name": "Medium",
                "response_time_hours": 1.0,
                "resolution_time_hours": 4.0,
                "sla_name": "Standard",
                "respond_by_at": "2026-03-03T10:00:00Z",
                "resolve_by_at": "2026-03-03T14:00:00Z",
                "is_sla_violated": True,
                "is_sla_respond_by_violated": False,
                "is_sla_resolve_by_violated": True,
                "resolved_at": "2026-03-03T12:00:00Z",
            },
        ]
    )

    days_off = pd.DataFrame([{"day_off_id": 1, "name": "Holiday", "holiday_date": "2026-03-02"}])
    quality_flags = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "ticket_title": "Desktop Support",
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "requestor_name": "Pat Client",
                "last_public_interaction_at": "2026-03-03T10:00:00Z",
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
            }
        ]
    )
    interactions = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "interaction_id": 11,
                "created_at": "2026-03-03T10:00:00Z",
                "created_uid": "client-1",
                "created_full_name": "Pat Client",
                "body": "Need help",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "client",
                "interaction_source": "entry",
            }
        ]
    )

    html = render_ticket_health_html(frame, days_off=days_off, quality_flags=quality_flags, interactions=interactions)

    assert "Ticket Health" in html
    assert "Backlog" in html
    assert "Average response" in html
    assert "Client Services" in html
    assert "Business-Day Team Completions" in html
    assert "Business-Day Member Completions" in html
    assert "weekends and TeamDynamix days off" in html
    assert "Analyst One" in html
    assert "Quality Scorecard" in html
    assert "Stale Public Update" in html
    assert "Private Activity Since Last Public Update" in html
    assert "SLA Health" in html
    assert "Ticket Hygiene" in html
    assert "Backlog Aging" in html
    assert "High-Touch Tickets" in html
    assert "Quality-Adjusted SLA" in html
    assert "SLA Hotspots" in html
    assert "Backlog Load Hotspots" in html
    assert "Hygiene Gaps" in html
    assert "Recurring Issue Candidates" in html


def test_render_ticket_health_html_stale_ticket_links():
    """Stale ticket IDs render as hyperlinks when tdx_base_url is provided."""
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 9999,
                "ticket_app_id": 634,
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "assignee_name": "Analyst One",
                "created_at": "2020-01-01T00:00:00Z",
                "modified_at": "2020-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ]
    )

    html = render_ticket_health_html(
        frame,
        tdx_base_url="https://example.teamdynamix.com/TDWebApi",
    )

    assert "TicketDet?TicketID=9999" in html
    assert "example.teamdynamix.com/TDNext/Apps/634/Tickets" in html
    assert 'target="_blank"' in html


def test_render_ticket_health_html_stale_ticket_no_link_without_url():
    """Stale ticket IDs render as plain text when tdx_base_url is not provided."""
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 9999,
                "ticket_app_id": 634,
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "assignee_name": "Analyst One",
                "created_at": "2020-01-01T00:00:00Z",
                "modified_at": "2020-01-01T00:00:00Z",
                "resolved_at": None,
            }
        ]
    )

    html = render_ticket_health_html(frame)

    assert "TicketDet" not in html
    assert "9999" in html


def test_write_ticket_health_report_creates_output_file(tmp_path: Path):
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 1,
                "status_name": "Closed",
                "team_name": "Client Services",
                "service_name": "Desktop Support",
                "assignee_name": "Analyst One",
                "response_time_hours": 0.5,
                "resolution_time_hours": 2.0,
                "resolved_at": "2026-03-01T10:00:00Z",
            }
        ]
    )
    output_path = tmp_path / "ticket_health.html"

    write_ticket_health_report(frame, output_path)

    assert output_path.exists()
    assert "Ticket Health" in output_path.read_text()
