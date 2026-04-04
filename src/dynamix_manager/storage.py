from pathlib import Path

import duckdb
import pandas as pd


def ensure_analytics_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)):
        pass


def replace_table(db_path: Path, table_name: str, frame: pd.DataFrame) -> None:
    ensure_analytics_db(db_path)
    with duckdb.connect(str(db_path)) as connection:
        connection.register("frame", frame)
        connection.execute(f"create or replace table {table_name} as select * from frame")


def read_table(db_path: Path, table_name: str) -> pd.DataFrame:
    with duckdb.connect(str(db_path), read_only=True) as connection:
        return connection.execute(f"select * from {table_name}").fetchdf()


def table_exists(db_path: Path, table_name: str) -> bool:
    if not db_path.exists():
        return False
    with duckdb.connect(str(db_path)) as connection:
        result = connection.execute(
            """
            select count(*)
            from information_schema.tables
            where table_schema = 'main' and table_name = ?
            """,
            [table_name],
        ).fetchone()
    return bool(result and result[0])
