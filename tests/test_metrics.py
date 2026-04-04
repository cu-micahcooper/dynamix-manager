import pandas as pd

from dynamix_manager.metrics import summarize_survey_health


def test_summarize_survey_health_computes_score_comment_and_team_metrics():
    frame = pd.DataFrame(
        [
            {
                "response_id": 1,
                "ticket_linked": True,
                "satisfaction_label": "Very Satisfied",
                "comment_text": "Helpful support",
                "team_name": "Client Services",
            },
            {
                "response_id": 2,
                "ticket_linked": True,
                "satisfaction_label": "Dissatisfied",
                "comment_text": "Slow response",
                "team_name": "Client Services",
            },
            {
                "response_id": 3,
                "ticket_linked": False,
                "satisfaction_label": "Satisfied",
                "comment_text": None,
                "team_name": None,
            },
        ]
    )

    summary = summarize_survey_health(frame)

    assert summary["total_responses"] == 3
    assert summary["linked_responses"] == 2
    assert summary["comment_count"] == 2
    assert summary["comment_rate"] == 2 / 3
    assert summary["average_score"] == (5 + 2 + 4) / 3
    assert summary["negative_response_rate"] == 1 / 3
    assert summary["top_teams"][0] == ("Client Services", 2)
    assert summary["recent_comments"][0]["comment_text"] == "Helpful support"
