from pathlib import Path

import duckdb
import nbformat
from nbclient import NotebookClient

from dynamix_manager.notebooks import write_survey_health_notebook


def test_write_survey_health_notebook_creates_duckdb_backed_notebook(tmp_path: Path):
    notebook_path = tmp_path / "survey_health.ipynb"
    db_path = Path("data") / "analytics.duckdb"

    write_survey_health_notebook(
        db_path=db_path,
        output_path=notebook_path,
    )

    notebook = nbformat.read(notebook_path, as_version=4)
    cell_sources = "\n".join(cell["source"] for cell in notebook.cells)
    assert notebook_path.exists()
    assert "duckdb.connect" in cell_sources
    assert "ticket_linked_surveys" in cell_sources
    assert "satisfaction_label" in cell_sources
    assert str(db_path.resolve()) in cell_sources
    assert "read_only=True" in cell_sources
    assert "summarize_survey_health" in cell_sources
    assert "response_time_hours" in cell_sources
    assert "resolution_time_hours" in cell_sources
    assert "service_name" in cell_sources
    assert "survey_completed_at" in cell_sources
    assert "plotly.express as px" in cell_sources
    assert "px.bar" in cell_sources
    assert "px.line" in cell_sources
    assert ".show()" in cell_sources


def test_generated_survey_health_notebook_executes(tmp_path: Path):
    db_path = tmp_path / "analytics.duckdb"
    notebook_path = tmp_path / "survey_health.ipynb"

    with duckdb.connect(str(db_path)) as connection:
        connection.execute(
            """
            create table ticket_linked_surveys as
            select
                1 as response_id,
                true as ticket_linked,
                'Very Satisfied' as satisfaction_label,
                'Client Services' as team_name,
                'Desktop Support' as service_name,
                'Analyst One' as assignee_name,
                'Helpful support' as comment_text,
                0.5 as response_time_hours,
                1.0 as resolution_time_hours,
                TIMESTAMP '2026-03-01 12:00:00' as survey_completed_at,
                TIMESTAMP '2026-03-01 08:00:00' as created_at,
                TIMESTAMP '2026-03-01 08:30:00' as responded_at,
                TIMESTAMP '2026-03-01 09:00:00' as resolved_at
            """
        )

    write_survey_health_notebook(
        db_path=db_path,
        output_path=notebook_path,
    )

    notebook = nbformat.read(notebook_path, as_version=4)
    executed = NotebookClient(notebook, timeout=120, kernel_name="python3").execute()

    assert executed.cells[2]["outputs"]
