from pathlib import Path

import duckdb
import nbformat
from nbclient import NotebookClient

from dynamix_manager.notebooks import write_ticket_health_notebook


def test_write_ticket_health_notebook_contains_ticket_analysis(tmp_path: Path):
    notebook_path = tmp_path / "ticket_health.ipynb"
    db_path = Path("data") / "analytics.duckdb"

    write_ticket_health_notebook(
        db_path=db_path,
        output_path=notebook_path,
    )

    notebook = nbformat.read(notebook_path, as_version=4)
    cell_sources = "\n".join(cell["source"] for cell in notebook.cells)
    assert "tickets" in cell_sources
    assert "status_name" in cell_sources
    assert "service_name" in cell_sources
    assert "response_time_hours" in cell_sources
    assert "resolution_time_hours" in cell_sources
    assert "plotly.express as px" in cell_sources
    assert "px.bar" in cell_sources
    assert "px.line" in cell_sources
    assert "Team Completions Per Business Day" in cell_sources
    assert "Member Completions Per Business Day" in cell_sources
    assert "completed_tickets" in cell_sources
    assert "days_off" in cell_sources
    assert "holiday_date" in cell_sources
    assert "Quality Scorecard" in cell_sources
    assert "Stale Public Update" in cell_sources
    assert "Private Activity Since Last Public Update" in cell_sources
    assert "SLA Health" in cell_sources
    assert "Ticket Hygiene" in cell_sources
    assert "High-Touch Tickets" in cell_sources
    assert "Quality-Adjusted SLA" in cell_sources
    assert "SLA Hotspots" in cell_sources
    assert "Backlog Load Hotspots" in cell_sources
    assert "Hygiene Gaps" in cell_sources
    assert "Recurring Issue Candidates" in cell_sources


def test_generated_ticket_health_notebook_executes(tmp_path: Path):
    db_path = tmp_path / "analytics.duckdb"
    notebook_path = tmp_path / "ticket_health.ipynb"

    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            """
            create table tickets as
            select
                1 as ticket_id,
                'Desktop Support' as ticket_title,
                'Closed' as status_name,
                'Client Services' as team_name,
                'Desktop Support' as service_name,
                'Analyst One' as assignee_name,
                'High' as priority_name,
                0.5 as response_time_hours,
                1.0 as resolution_time_hours,
                TIMESTAMP '2026-03-01 10:00:00' as resolved_at,
                TIMESTAMP '2026-03-01 08:00:00' as created_at,
                TIMESTAMP '2026-03-01 08:30:00' as responded_at,
                'Standard' as sla_name,
                TIMESTAMP '2026-03-01 09:00:00' as respond_by_at,
                TIMESTAMP '2026-03-01 12:00:00' as resolve_by_at,
                false as is_sla_violated,
                false as is_sla_respond_by_violated,
                false as is_sla_resolve_by_violated
            union all
            select
                2 as ticket_id,
                'Desktop Support follow-up' as ticket_title,
                'Closed' as status_name,
                'Client Services' as team_name,
                'Desktop Support' as service_name,
                'Analyst Two' as assignee_name,
                'Medium' as priority_name,
                1.0 as response_time_hours,
                2.0 as resolution_time_hours,
                TIMESTAMP '2026-03-01 11:00:00' as resolved_at,
                TIMESTAMP '2026-03-01 08:30:00' as created_at,
                TIMESTAMP '2026-03-01 09:30:00' as responded_at,
                'Standard' as sla_name,
                TIMESTAMP '2026-03-01 10:00:00' as respond_by_at,
                TIMESTAMP '2026-03-01 14:00:00' as resolve_by_at,
                true as is_sla_violated,
                false as is_sla_respond_by_violated,
                true as is_sla_resolve_by_violated
            """
        )
        connection.execute(
            """
            create table days_off as
            select
                1 as day_off_id,
                'Staff Retreat' as name,
                '2026-03-02' as holiday_date
            """
        )
        connection.execute(
            """
            create table ticket_quality_flags as
            select
                1 as ticket_id,
                'Desktop Support' as ticket_title,
                'Client Services' as team_name,
                'Desktop Support' as service_name,
                'Pat Client' as requestor_name,
                TIMESTAMP '2026-03-03 10:00:00' as last_public_interaction_at,
                NULL as last_private_interaction_at,
                NULL as last_private_interaction_by,
                'client' as last_public_interaction_actor_type,
                'Pat Client' as last_public_interaction_by,
                true as client_last_interaction_flag,
                1 as it_follow_up_streak,
                false as it_follow_up_without_client_response_flag,
                1 as interaction_count,
                0 as stale_public_update_business_days,
                false as stale_public_update_flag,
                false as private_activity_since_last_public_flag
            """
        )
        connection.execute(
            """
            create table ticket_quality_interactions as
            select
                1 as ticket_id,
                11 as interaction_id,
                NULL as parent_interaction_id,
                TIMESTAMP '2026-03-03 10:00:00' as created_at,
                'client-1' as created_uid,
                'Pat Client' as created_full_name,
                'Need help' as body,
                false as is_private,
                true as is_communication,
                1 as update_type,
                'client' as actor_type,
                'entry' as interaction_source
            """
        )

    write_ticket_health_notebook(
        db_path=db_path,
        output_path=notebook_path,
    )

    notebook = nbformat.read(notebook_path, as_version=4)
    executed = NotebookClient(notebook, timeout=120, kernel_name="python3").execute()

    assert executed.cells[2]["outputs"]
