from pathlib import Path

from dynamix_manager.reporting import render_cfo_email_html, write_cfo_email


_WEEKLY = [{"week": f"W{i}", "count": i * 5} for i in range(1, 9)]

_SNAPSHOT = {
    "week_label": "Apr 7 – 13",
    "week_range_label": "Apr 7 – Apr 9",
    "prior_week_range_label": "Mar 31 – Apr 2",
    "year_ago_range_label": "Apr 8 – Apr 10",
    "report_generated_at": "2025-04-09T15:00:00+00:00",
    "as_of_label": "Apr 9",
    "tickets_created_this_week": 42,
    "tickets_created_prior_week": 35,
    "tickets_created_year_ago": 38,
    "tickets_created_ww_delta_pct": 20.0,
    "tickets_created_yy_delta_pct": 10.5,
    "tickets_closed_this_week": 30,
    "tickets_closed_prior_week": 28,
    "tickets_closed_year_ago": 25,
    "tickets_closed_ww_delta_pct": 7.1,
    "tickets_closed_yy_delta_pct": 20.0,
    "total_open_tickets": 117,
    "total_open_tickets_prior_week": 105,
    "total_open_tickets_ww_delta": 12,
    "created_weekly": _WEEKLY,
    "closed_weekly": _WEEKLY,
    "open_weekly": _WEEKLY,
    "survey_comments": [
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
    ],
    "youtrack_projects": [
        {"id": "IPS-20", "summary": "MyCU2", "it_team": "User Services", "assignee": "Jane Doe", "project_short": "IPS", "is_new_this_week": True},
        {"id": "TOPS-1144", "summary": "Bolthouse Academic Center Construction", "it_team": "Tech Ops", "assignee": "", "project_short": "TOPS", "is_new_this_week": False},
    ],
    "youtrack_project_movement": {
        "in_progress_count": 2,
        "new_this_week": 1,
        "completed_this_week": 1,
    },
}


def test_cfo_email_no_script_tags():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "<script" not in html.lower()


def test_cfo_email_no_plotly():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "plotly" not in html.lower()


def test_cfo_email_no_details_tags():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "<details" not in html.lower()


def test_cfo_email_shows_ticket_counts():
    html = render_cfo_email_html(_SNAPSHOT)
    assert ">42<" in html
    assert ">30<" in html


def test_cfo_email_shows_open_tickets():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "117" in html
    assert "+12 vs prior week" in html


def test_cfo_email_shows_survey_comments():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "Survey Comments" in html
    assert "Helpful support" in html
    assert "Apr 8 · Very Satisfied · Password Reset" in html
    assert "Quick turnaround" in html
    assert "Jane Doe" in html
    assert "John Smith" in html
    assert "border-top:1px solid #e8edf3" not in html
    assert "Laptop does not connect to the campus wireless network..." in html
    assert "Laptop does not connect to the campus wireless network after update" not in html


def test_cfo_email_escapes_survey_comments():
    snapshot = dict(_SNAPSHOT)
    snapshot["survey_comments"] = [
        {
            "survey_completed_at": "2025-04-08T11:00:00+00:00",
            "satisfaction_label": "Satisfied",
            "team_name": "Client Services",
            "comment_text": '<script>alert("xss")</script>',
        }
    ]

    html = render_cfo_email_html(snapshot)

    assert '<script>alert("xss")</script>' not in html
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html


def test_cfo_email_shows_delta_badge():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "+20.0%" in html


def test_cfo_email_shows_youtrack_projects():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "User Services" in html                          # IT Team badge
    assert "MyCU2" in html                                  # issue summary
    assert "Tech Ops" in html                               # IT Team badge
    assert "Bolthouse Academic Center Construction" in html  # issue summary
    assert "2 in progress · 1 new this week · 1 completed this week" in html
    assert "NEW THIS WEEK" in html


def test_cfo_email_shows_header():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "CFO Update" in html
    assert "Cedarville University" in html


def test_cfo_email_includes_sparklines():
    html = render_cfo_email_html(_SNAPSHOT)
    assert "8-Wk Trend" in html
    # Sparkline bars rendered as nested tables
    assert "border-radius:1px 1px 0 0" in html


def test_write_cfo_email_creates_file(tmp_path: Path):
    out = tmp_path / "cfo_email.html"
    write_cfo_email(_SNAPSHOT, out)
    assert out.exists()
    content = out.read_text()
    assert "CFO Update" in content


def test_cfo_email_no_data_graceful():
    empty_snapshot = {
        "week_label": "Apr 7 – 13",
        "week_range_label": "Apr 7 – Apr 9",
        "prior_week_range_label": "Mar 31 – Apr 2",
        "year_ago_range_label": "Apr 8 – Apr 10",
        "report_generated_at": "2025-04-09T15:00:00+00:00",
        "as_of_label": "Apr 9",
        "tickets_created_this_week": 0,
        "tickets_created_prior_week": 0,
        "tickets_created_year_ago": 0,
        "tickets_created_ww_delta_pct": None,
        "tickets_created_yy_delta_pct": None,
        "tickets_closed_this_week": 0,
        "tickets_closed_prior_week": 0,
        "tickets_closed_year_ago": 0,
        "tickets_closed_ww_delta_pct": None,
        "tickets_closed_yy_delta_pct": None,
        "total_open_tickets": 0,
        "total_open_tickets_prior_week": 0,
        "total_open_tickets_ww_delta": 0,
        "youtrack_projects": [],
        "youtrack_project_movement": {
            "in_progress_count": 0,
            "new_this_week": 0,
            "completed_this_week": 0,
        },
    }
    html = render_cfo_email_html(empty_snapshot)
    assert "no prior data" in html
    assert "No projects found" in html
