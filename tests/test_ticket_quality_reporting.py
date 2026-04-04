import pandas as pd

from dynamix_manager.reporting import render_ticket_quality_html


def test_render_ticket_quality_html_includes_flagged_sections():
    frame = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "ticket_title": "Printer issue",
                "team_name": "Tech Services",
                "requestor_name": "Pat Client",
                "last_public_interaction_at": "2026-03-01T10:00:00Z",
                "last_private_interaction_at": None,
                "last_private_interaction_by": None,
                "last_public_interaction_actor_type": "client",
                "last_public_interaction_by": "Pat Client",
                "client_last_interaction_flag": True,
                "it_follow_up_streak": 0,
                "it_follow_up_without_client_response_flag": False,
                "stale_public_update_business_days": 5,
                "stale_public_update_flag": True,
                "private_activity_since_last_public_flag": False,
            },
            {
                "ticket_id": 99,
                "ticket_title": "Laptop setup",
                "team_name": "User Services",
                "requestor_name": "Riley Requestor",
                "last_public_interaction_at": "2026-03-05T09:00:00Z",
                "last_private_interaction_at": "2026-03-06T09:30:00Z",
                "last_private_interaction_by": "Alex Analyst",
                "last_public_interaction_actor_type": "it",
                "last_public_interaction_by": "Alex Analyst",
                "client_last_interaction_flag": False,
                "it_follow_up_streak": 5,
                "it_follow_up_without_client_response_flag": True,
                "stale_public_update_business_days": 2,
                "stale_public_update_flag": False,
                "private_activity_since_last_public_flag": True,
            },
        ]
    )

    html = render_ticket_quality_html(frame)

    assert "Ticket Quality" in html
    assert "Client Last Interaction" in html
    assert "Repeated IT Follow-Up" in html
    assert "Stale Public Update" in html
    assert "Private Activity Since Last Public Update" in html
    assert "Printer issue" in html
    assert "Laptop setup" in html
