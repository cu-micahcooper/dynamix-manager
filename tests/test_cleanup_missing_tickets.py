import runpy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

cleanup = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "scripts/cleanup_missing_tickets.py")
)


def test_exhausted_rate_limit_recorded_separately_from_404(tmp_path, monkeypatch):
    main = cleanup["main"]
    config = SimpleNamespace(
        db_path=tmp_path / "data.db", username="", password="", app_id=1, base_url=""
    )
    client = Mock()
    client.session.get.side_effect = [
        Mock(status_code=429, headers={}) for _ in range(4)
    ] + [Mock(status_code=404)]
    for name, value in {
        "load_runtime_config": lambda: config,
        "TeamDynamixClient": lambda **kw: client,
        "missing_ticket_ids": lambda path: [123, 456],
        "time": Mock(),
    }.items():
        monkeypatch.setitem(main.__globals__, name, value)
    assert main() == 1
    assert client.session.get.call_count == 5
    assert (
        config.db_path.with_suffix(".unresolved-ticket-ids.txt").read_text() == "123\n"
    )


def test_checkpoint_replay_preserves_rows_and_handles_legacy_schema(tmp_path):
    import duckdb

    db = tmp_path / "tickets.db"
    with duckdb.connect(str(db)) as conn:
        conn.execute(
            "create table tickets(ticket_id bigint, ticket_title varchar, assignee_name integer, legacy varchar)"
        )
        conn.execute("insert into tickets values (1, 'stored', null, 'keep')")
    rows = [
        {"ID": 1, "Title": "overwrite"},
        {"ID": 2, "Title": "first", "RespondingFullName": "Jane"},
        {"ID": 2, "Title": "duplicate"},
    ]
    for _ in range(2):
        cleanup["save_checkpoint"](db, rows)
    with duckdb.connect(str(db)) as conn:
        assert conn.execute(
            "select ticket_id,ticket_title,assignee_name,legacy from tickets order by ticket_id"
        ).fetchall() == [(1, "stored", None, "keep"), (2, "first", "Jane", None)]
