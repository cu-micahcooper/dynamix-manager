from pathlib import Path

import duckdb
import nbformat
from nbclient import NotebookClient

from dynamix_manager.notebooks import write_ticket_quality_notebook


def test_write_ticket_quality_notebook_contains_quality_analysis(tmp_path: Path):
    notebook_path = tmp_path / "ticket_quality.ipynb"
    db_path = Path("data") / "analytics.duckdb"

    write_ticket_quality_notebook(db_path=db_path, output_path=notebook_path)

    notebook = nbformat.read(notebook_path, as_version=4)
    cell_sources = "\n".join(cell["source"] for cell in notebook.cells)
    assert "ticket_quality_flags" in cell_sources
    assert "client_last_interaction_flag" in cell_sources
    assert "it_follow_up_without_client_response_flag" in cell_sources
    assert "stale_public_update_flag" in cell_sources
    assert "private_activity_since_last_public_flag" in cell_sources
    assert "plotly.express as px" in cell_sources
    assert "px.bar" in cell_sources


def test_generated_ticket_quality_notebook_executes(tmp_path: Path):
    db_path = tmp_path / "analytics.duckdb"
    notebook_path = tmp_path / "ticket_quality.ipynb"

    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            """
            create table ticket_quality_flags as
            select
                42 as ticket_id,
                'Printer issue' as ticket_title,
                'Tech Services' as team_name,
                'Printing' as service_name,
                'Pat Client' as requestor_name,
                TIMESTAMP '2026-03-01 10:00:00' as last_public_interaction_at,
                NULL as last_private_interaction_at,
                NULL as last_private_interaction_by,
                'client' as last_public_interaction_actor_type,
                'Pat Client' as last_public_interaction_by,
                true as client_last_interaction_flag,
                0 as it_follow_up_streak,
                false as it_follow_up_without_client_response_flag,
                5 as stale_public_update_business_days,
                true as stale_public_update_flag,
                false as private_activity_since_last_public_flag
            union all
            select
                99 as ticket_id,
                'Laptop setup' as ticket_title,
                'User Services' as team_name,
                'Endpoint' as service_name,
                'Riley Requestor' as requestor_name,
                TIMESTAMP '2026-03-05 09:00:00' as last_public_interaction_at,
                TIMESTAMP '2026-03-06 09:30:00' as last_private_interaction_at,
                'Alex Analyst' as last_private_interaction_by,
                'it' as last_public_interaction_actor_type,
                'Alex Analyst' as last_public_interaction_by,
                false as client_last_interaction_flag,
                5 as it_follow_up_streak,
                true as it_follow_up_without_client_response_flag,
                2 as stale_public_update_business_days,
                false as stale_public_update_flag,
                true as private_activity_since_last_public_flag
            """
        )

    write_ticket_quality_notebook(db_path=db_path, output_path=notebook_path)

    notebook = nbformat.read(notebook_path, as_version=4)
    executed = NotebookClient(notebook, timeout=120, kernel_name="python3").execute()

    assert executed.cells[2]["outputs"]
