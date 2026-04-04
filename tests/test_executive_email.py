# tests/test_executive_email.py
from pathlib import Path

from dynamix_manager.reporting import render_executive_email_html, write_executive_email


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


def test_render_executive_email_no_script_tags():
    html = render_executive_email_html(_sample_snapshot())
    assert "<script" not in html.lower()


def test_render_executive_email_no_plotly():
    html = render_executive_email_html(_sample_snapshot())
    assert "plotly" not in html.lower()


def test_render_executive_email_no_details_tags():
    html = render_executive_email_html(_sample_snapshot())
    assert "<details" not in html
    assert "<summary" not in html


def test_render_executive_email_includes_kpi_values():
    html = render_executive_email_html(_sample_snapshot())
    assert "12" in html        # new tickets
    assert "10.5" in html      # avg weekly
    assert "87" in html        # SLA 87%
    assert "3" in html         # stale
    assert "7" in html         # unassigned
    assert "2.5" in html       # median first response this week


def test_render_executive_email_includes_completion_chart_buckets():
    html = render_executive_email_html(_sample_snapshot())
    assert "0\u20138h" in html      # "0–8h" (en-dash)
    assert "8\u201324h" in html     # "8–24h"
    assert "All Time" in html
    assert "Mar 30" in html        # week range label


def test_render_executive_email_includes_customer_effort():
    html = render_executive_email_html(_sample_snapshot())
    assert "Customer Effort" in html
    assert "Very Easy" in html
    assert "78%" in html        # easy_rate = 0.78


def test_render_executive_email_includes_top_services():
    html = render_executive_email_html(_sample_snapshot())
    assert "Desktop Support" in html
    assert "Network" in html


def test_write_executive_email_creates_file(tmp_path: Path):
    output_path = tmp_path / "executive_email.html"
    write_executive_email(_sample_snapshot(), output_path)
    assert output_path.exists()
    assert "IT Executive Report" in output_path.read_text()
