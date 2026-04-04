import pandas as pd

from pathlib import Path

from dynamix_manager.storage import (
    ensure_analytics_db,
    read_table,
    replace_table,
    table_exists,
)


def test_ensure_analytics_db_creates_parent_directory(tmp_path: Path):
    db_path = tmp_path / "nested" / "analytics.duckdb"

    ensure_analytics_db(db_path)

    assert db_path.parent.exists()
    assert db_path.exists()


def test_replace_table_round_trips_dataframe(tmp_path: Path):
    db_path = tmp_path / "analytics.duckdb"
    frame = pd.DataFrame([{"ticket_id": 42, "status_name": "Closed"}])

    replace_table(db_path, "tickets", frame)

    persisted = read_table(db_path, "tickets")
    assert persisted.to_dict(orient="records") == [
        {"ticket_id": 42, "status_name": "Closed"}
    ]


def test_table_exists_reflects_created_tables(tmp_path: Path):
    db_path = tmp_path / "analytics.duckdb"
    frame = pd.DataFrame([{"ticket_id": 42}])

    replace_table(db_path, "tickets", frame)

    assert table_exists(db_path, "tickets")
    assert not table_exists(db_path, "survey_responses")
