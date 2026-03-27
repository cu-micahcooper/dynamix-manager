from pathlib import Path

import json
import pandas as pd

from dynamix_manager.reporting import render_survey_health_html, write_survey_health_report, _serialize_survey_rows


def test_render_survey_health_html_includes_tailwind_and_summary_metrics():
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
                "ticket_linked": False,
                "satisfaction_label": "Dissatisfied",
                "comment_text": None,
                "team_name": None,
            },
        ]
    )

    html = render_survey_health_html(frame)

    assert "https://cdn.tailwindcss.com" in html
    assert "Survey Health" in html
    assert ">2<" in html
    assert ">1<" in html
    assert "Very Satisfied" in html
    assert "Average score" in html
    assert "Negative response rate" in html


def test_write_survey_health_report_creates_output_file(tmp_path: Path):
    frame = pd.DataFrame(
        [
            {
                "response_id": 1,
                "ticket_linked": True,
                "satisfaction_label": "Very Satisfied",
                "comment_text": "Helpful support",
                "team_name": "Client Services",
            }
        ]
    )
    output_path = tmp_path / "survey_health.html"

    write_survey_health_report(frame, output_path)

    assert output_path.exists()
    assert "Survey Health" in output_path.read_text()


def test_serialize_survey_rows_emits_required_columns():
    frame = pd.DataFrame([
        {
            "response_id": 1,
            "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
            "satisfaction_label": "Very Satisfied",
            "team_name": "Client Services",
            "ticket_linked": True,
            "comment_text": "Great service",
        },
        {
            "response_id": 2,
            "survey_completed_at": None,
            "satisfaction_label": "Dissatisfied",
            "team_name": None,
            "ticket_linked": False,
            "comment_text": None,
        },
    ])
    result = _serialize_survey_rows(frame)
    rows = json.loads(result)
    assert len(rows) == 2
    assert rows[0]["response_id"] == 1
    assert rows[0]["satisfaction_label"] == "Very Satisfied"
    assert "survey_completed_at" in rows[0]
    assert "team_name" in rows[0]
    assert "ticket_linked" in rows[0]
    assert "comment_text" in rows[0]
    # Columns not in frame should not appear
    assert "created_at" not in rows[0]
    # ISO date format preserved for non-null timestamps
    assert rows[0]["survey_completed_at"].startswith("2026-01-15")
    # None timestamp serializes as null
    assert rows[1]["survey_completed_at"] is None
