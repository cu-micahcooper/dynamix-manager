import pandas as pd

from dynamix_manager.models import build_ticket_linked_survey_model


def test_build_ticket_linked_survey_model_joins_ticket_context_and_flags_orphans():
    surveys = pd.DataFrame(
        [
            {
                "response_id": 1,
                "ticket_id": 42,
                "survey_completed_at": "2026-03-01T12:00:00Z",
                "satisfaction_label": "Very Satisfied",
                "comment_text": "Helpful support",
            },
            {
                "response_id": 2,
                "ticket_id": 999,
                "survey_completed_at": "2026-03-01T13:00:00Z",
                "satisfaction_label": "Dissatisfied",
                "comment_text": "Slow response",
            },
        ]
    )
    tickets = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "service_name": "Desktop Support",
                "team_name": "Client Services",
                "assignee_name": "Analyst One",
                "ticket_app_name": "InfoTech Tickets",
                "created_at": "2026-03-01T08:00:00Z",
                "responded_at": "2026-03-01T08:30:00Z",
                "resolved_at": "2026-03-01T09:00:00Z",
            }
        ]
    )

    model = build_ticket_linked_survey_model(surveys, tickets)

    assert list(model["ticket_linked"]) == [True]
    assert model.loc[0, "service_name"] == "Desktop Support"
    assert model.loc[0, "team_name"] == "Client Services"
    assert model.loc[0, "response_time_hours"] == 0.5
    assert model.loc[0, "resolution_time_hours"] == 1.0
    assert set(model["ticket_id"]) == {42}
