# tests/test_executive_reporting.py
from pathlib import Path

from dynamix_manager.reporting import render_executive_report_html, write_executive_report


def _sample_snapshot():
    return {
        "new_tickets_this_week": 12,
        "avg_weekly_tickets_created": 10.5,
        "week_over_week_delta_pct": 20.0,
        "satisfaction_counts": {"Very Satisfied": 100, "Satisfied": 50, "Dissatisfied": 8},
        "completion_hours_this_week": [
            {"label": "0–8h", "count": 0}, {"label": "8–24h", "count": 2},
            {"label": "24–40h", "count": 1}, {"label": "40–80h", "count": 0},
            {"label": "80–160h", "count": 0}, {"label": "160–320h", "count": 0},
            {"label": "320–1000h", "count": 0},
        ],
        "completion_hours_all_time": [
            {"label": "0–8h", "count": 0}, {"label": "8–24h", "count": 2},
            {"label": "24–40h", "count": 0}, {"label": "40–80h", "count": 1},
            {"label": "80–160h", "count": 1}, {"label": "160–320h", "count": 0},
            {"label": "320–1000h", "count": 0},
        ],
        "sla_compliance_rate": 0.87,
        "stale_open_count": 3,
        "top_services": [
            {"service_name": "Desktop Support", "count": 25},
            {"service_name": "Network", "count": 12},
        ],
        "satisfaction_trend": [
            {"month": "2026-03", "positive_rate": 0.85, "total": 50},
        ],
        "median_first_response_hours_this_week": 2.5,
        "median_first_response_hours_all_time": 4.0,
        "unassigned_count": 7,
        "week_label": "Mar 30 – Apr 5",
        "week_range_label": "Mar 30 – Apr 1",
        "prior_week_range_label": "Mar 23 – Mar 25",
        "as_of_label": "Apr 1",
        "satisfaction_period_label": "Mar 2 – Apr 1",
        "customer_effort_counts": {"Very Easy": 8, "Easy": 10, "Difficult": 4, "Very Difficult": 1},
        "customer_effort_easy_rate": 0.78,
        "customer_effort_period_label": "Mar 2 – Apr 1",
        "report_generated_at": "2026-04-01T12:00:00+00:00",
        "new_tickets_detail": [],
        "stale_tickets_detail": [],
        "unassigned_tickets_detail": [],
        "tdx_base_url": None,
    }


def test_render_executive_report_html_includes_kpi_values():
    html = render_executive_report_html(_sample_snapshot())
    assert "12" in html                    # new tickets this week
    assert "10.5" in html                  # avg weekly
    assert "87" in html                    # SLA compliance (87%)
    assert "3" in html                     # stale open
    assert "7" in html                     # unassigned
    assert "2.5" in html                   # median response this week
    assert "4.0" in html                   # median response all time


def test_render_executive_report_html_includes_customer_effort_section():
    html = render_executive_report_html(_sample_snapshot())
    assert "Customer Effort" in html
    assert "Very Easy" in html
    assert "Difficult" in html


def test_render_executive_report_html_no_double_escaped_entities():
    html = render_executive_report_html(_sample_snapshot())
    assert "&amp;" not in html
    assert "&gt;" not in html
    assert "all open & recently closed" in html
    assert "Stale Open (>5 biz days)" in html


def test_render_executive_report_html_drill_down_detail_present():
    snapshot = _sample_snapshot()
    snapshot["new_tickets_detail"] = [
        {"ticket_id": 42, "ticket_title": "Printer broken", "service_name": "Printing",
         "team_name": "IT", "assignee_name": "Alex", "ticket_app_id": 9}
    ]
    snapshot["tdx_base_url"] = "https://tdx.example.edu/TDWebApi"
    html = render_executive_report_html(snapshot)
    assert "Show details" in html
    assert "Printer broken" in html
    assert "TicketID=42" in html


def test_render_executive_report_html_embeds_plotly_and_bar_chart():
    html = render_executive_report_html(_sample_snapshot())
    assert "plotly" in html.lower()
    assert "Mar 30" in html   # week range label appears in chart data and headings
    assert "All Time" in html


def test_render_executive_report_html_includes_top_services():
    html = render_executive_report_html(_sample_snapshot())
    assert "Desktop Support" in html
    assert "Network" in html


def test_render_executive_report_html_shows_week_label():
    html = render_executive_report_html(_sample_snapshot())
    assert "Mar 30" in html


def test_render_executive_report_html_includes_customer_effort():
    html = render_executive_report_html(_sample_snapshot())
    assert "Customer Effort" in html
    assert "78%" in html        # easy_rate = 0.78
    assert "Very Easy" in html
    assert "Difficult" in html


def test_write_executive_report_creates_file(tmp_path: Path):
    output_path = tmp_path / "executive_report.html"
    write_executive_report(_sample_snapshot(), output_path)
    assert output_path.exists()
    assert "IT Executive Report" in output_path.read_text()
