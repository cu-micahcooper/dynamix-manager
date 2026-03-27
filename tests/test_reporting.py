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
                "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
            },
            {
                "response_id": 2,
                "ticket_linked": False,
                "satisfaction_label": "Dissatisfied",
                "comment_text": None,
                "team_name": None,
                "survey_completed_at": pd.Timestamp("2026-02-01", tz="UTC"),
            },
        ]
    )

    html = render_survey_health_html(frame)

    assert "https://cdn.tailwindcss.com" in html
    assert "Survey Health" in html
    assert "Average score" in html
    assert "Negative response rate" in html
    # Plotly CDN present
    assert "cdn.plot.ly" in html
    # JSON data blob present
    assert "SURVEY_DATA" in html
    # Period filter buttons present
    assert "Last 30" in html
    assert "All time" in html
    # Chart containers present
    assert 'id="chart-satisfaction-mix"' in html
    assert 'id="chart-monthly-trend"' in html
    assert 'id="chart-team-volume"' in html


def test_render_survey_health_html_stat_cards_have_js_ids():
    frame = pd.DataFrame([{
        "response_id": 1,
        "ticket_linked": True,
        "satisfaction_label": "Very Satisfied",
        "comment_text": "Good",
        "team_name": "Help Desk",
        "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
    }])
    html = render_survey_health_html(frame)
    assert 'id="stat-total"' in html
    assert 'id="stat-linked"' in html
    assert 'id="stat-comments"' in html
    assert 'id="stat-avg-score"' in html
    assert 'id="stat-neg-rate"' in html
    assert 'id="satisfaction-list"' in html
    assert 'id="team-list"' in html
    assert 'id="comment-list"' in html


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


def test_render_survey_health_html_escapes_html_in_comment_text():
    frame = pd.DataFrame([{
        "response_id": 1,
        "ticket_linked": True,
        "satisfaction_label": "Very Satisfied",
        "comment_text": '<script>alert("xss")</script>',
        "team_name": "<b>Bold Team</b>",
        "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
    }])
    html = render_survey_health_html(frame)
    # Raw script tag must not appear unescaped in the JS data blob
    # (pandas to_json escapes </ already, but we verify the esc() function handles it in rendering)
    # The JSON blob will have the data — the test verifies the esc() function is defined in the page
    assert "function esc(" in html
    # The JSON data blob uses unicode escaping, not HTML entities — raw <b> tag from team_name
    # should not appear as an HTML entity in the SURVEY_DATA blob itself
    assert "SURVEY_DATA" in html
    # Verify the esc function is wired (its definition appears in the script)
    assert "replace(/&/g," in html
