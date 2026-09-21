# Consolidate Ticket-Survey Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four overlapping ticket/survey ingestion paths with a single `sync_all()` function that always produces a complete `tickets` table before surveys are linked to it.

**Architecture:** A new public function `sync_all(config, client)` handles the full ingest cycle — one auth call, one survey fetch, one bulk ticket search (with modified-from watermark), individual fetches for any survey-referenced tickets still missing, then materialization of the join and survey health artifacts. `generate_executive_report` and `generate_executive_email` both become wrappers that call `sync_all()` first, so every report command is self-contained. The public functions `sync_tickets`, `cache_survey_report`, `discover_ticket_app`, `cache_ticket_context`, `backfill_ticket_links`, and `refresh_survey_slice` are removed entirely. The CLI `backfill-tickets` command is removed; `generate-email` gains a client argument.

**Tech Stack:** Python 3.13, pandas, DuckDB, pytest, ruff

---

## File Map

| File | Action | What changes |
|---|---|---|
| `src/dynamix_manager/pipeline.py` | Modify | Add `sync_all()`; update `generate_executive_report` and `generate_executive_email` signatures; remove 6 public functions |
| `src/dynamix_manager/cli.py` | Modify | Remove `backfill-tickets` subparser/handler; pass `client` to `generate_executive_email` |
| `tests/test_pipeline.py` | Modify | Update imports; remove 11 obsolete tests; add 5 new tests; update 1 existing test |

Internal helpers `_cache_ticket_context_from_rows`, `_discover_ticket_app_from_rows`, `_write_survey_rows`, `_survey_ticket_ids`, `_artifact_root`, `_open_tickets_only`, and `_write_days_off_rows` are **kept unchanged**.

---

### Task 1: Write failing tests for `sync_all()`

**Files:**
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Add `sync_all` to imports at the top of `tests/test_pipeline.py`**

Replace the existing import block:

```python
from dynamix_manager.pipeline import (
    backfill_ticket_links,
    cache_days_off,
    cache_ticket_quality_slice,
    cache_survey_report,
    cache_ticket_context,
    discover_ticket_app,
    generate_executive_report,
    materialize_ticket_linked_surveys,
    refresh_survey_slice,
    sync_tickets,
)
```

With:

```python
from dynamix_manager.pipeline import (
    cache_days_off,
    cache_ticket_quality_slice,
    generate_executive_report,
    generate_executive_email,
    materialize_ticket_linked_surveys,
    sync_all,
)
```

- [ ] **Step 2: Append the four new test functions to `tests/test_pipeline.py`**

```python
def test_sync_all_fetches_surveys_bulk_syncs_and_materializes(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "ServiceName": "Desktop Support",
            "ResponsibleGroupName": "Client Services",
            "RespondingFullName": "Analyst One",
            "CreatedDate": "2026-03-01T08:00:00Z",
            "ModifiedDate": "2026-03-01T09:00:00Z",
            "CompletedDate": "2026-03-01T09:00:00Z",
        },
    ]

    summary = sync_all(config=config, client=client)

    surveys = read_table(config.db_path, "survey_responses")
    tickets = read_table(config.db_path, "tickets")
    linked = read_table(config.db_path, "ticket_linked_surveys")
    assert summary["survey_rows"] == 1
    assert summary["ticket_rows"] == 1
    assert summary["linked_rows"] == 1
    assert len(surveys) == 1
    assert len(tickets) == 1
    assert len(linked) == 1
    assert config.report_output_path.exists()
    assert config.notebook_output_path.exists()
    assert client.auth_calls == 1


def test_sync_all_fetches_missing_survey_tickets_individually(tmp_path):
    """Ticket 99 is in the survey but absent from the bulk search result — must be fetched via GET."""
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([
        {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
        {"ResponseID": 2, "TicketID": 99, "48398": "Satisfied"},
    ])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "CreatedDate": "2026-03-01T08:00:00Z",
            "ModifiedDate": "2026-03-01T09:00:00Z",
        },
    ]
    client.ticket_payloads[(634, 99)] = {
        "ID": 99,
        "Title": "Wi-Fi issue",
        "StatusName": "Open",
        "CreatedDate": "2026-03-01T10:00:00Z",
        "ModifiedDate": "2026-03-01T10:00:00Z",
    }

    summary = sync_all(config=config, client=client)

    tickets = read_table(config.db_path, "tickets")
    assert set(tickets["ticket_id"]) == {42, 99}
    assert summary["ticket_rows"] == 2
    assert client.ticket_calls == [(99, "token", 634)]


def test_sync_all_bulk_sync_uses_modified_from_watermark(tmp_path):
    """The search payload must include ModifiedDateFrom set to the max existing modified_at."""
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([
        {"ticket_id": 1, "ticket_title": "Old", "modified_at": "2026-03-20T10:00:00Z"},
    ]))

    captured_payloads = []

    class CapturingClient(StubClient):
        def search_tickets(self, token, payload, ticket_app_id=None):
            captured_payloads.append(payload)
            return []

    client = CapturingClient([{"ResponseID": 1, "TicketID": 1, "48398": "Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]

    sync_all(config=config, client=client)

    assert len(captured_payloads) == 1
    assert "ModifiedDateFrom" in captured_payloads[0]
    assert "2026-03-20" in captured_payloads[0]["ModifiedDateFrom"]


def test_sync_all_skips_individual_fetch_when_bulk_sync_covers_all_survey_tickets(tmp_path):
    """If every survey ticket_id is present after bulk sync, no individual GETs are issued."""
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "CreatedDate": "2026-03-01T08:00:00Z",
            "ModifiedDate": "2026-03-01T09:00:00Z",
        },
    ]

    sync_all(config=config, client=client)

    assert client.ticket_calls == []
```

- [ ] **Step 3: Run the four new tests — confirm they all fail with `ImportError` or `NameError`**

```bash
python -m pytest tests/test_pipeline.py::test_sync_all_fetches_surveys_bulk_syncs_and_materializes tests/test_pipeline.py::test_sync_all_fetches_missing_survey_tickets_individually tests/test_pipeline.py::test_sync_all_bulk_sync_uses_modified_from_watermark tests/test_pipeline.py::test_sync_all_skips_individual_fetch_when_bulk_sync_covers_all_survey_tickets -v
```

Expected: 4 FAILED (ImportError: cannot import name 'sync_all')

---

### Task 2: Implement `sync_all()` in `pipeline.py`

**Files:**
- Modify: `src/dynamix_manager/pipeline.py`

- [ ] **Step 1: Add `sync_all` after `materialize_ticket_linked_surveys` (around line 328)**

Insert this function immediately after the `materialize_ticket_linked_surveys` function and before `sync_tickets`:

```python
def sync_all(
    config: RuntimeConfig,
    client: TeamDynamixClient,
) -> dict[str, object]:
    """Single entry point: sync surveys, bulk-sync tickets, fill any missing, materialize join."""
    token = client.authenticate()

    # 1. Fetch and persist surveys
    survey_rows = client.fetch_report(survey_report_id(), token, with_data=True)
    survey_frame = _write_survey_rows(config, survey_rows)

    # 2. Discover ticket app (prefers "InfoTech Tickets" by name)
    ticket_app = _discover_ticket_app_from_rows(client, token, survey_rows)
    ticket_app_id = int(ticket_app["AppID"])
    ticket_app_name = ticket_app.get("Name")

    # 3. Bulk sync via search API using modified_from watermark
    existing = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    modified_from: str | None = None
    if not existing.empty and "modified_at" in existing.columns:
        max_mod = pd.to_datetime(existing["modified_at"], utc=True, errors="coerce").dropna().max()
        if pd.notna(max_mod):
            modified_from = max_mod.isoformat()

    payload = build_ticket_search_filters(modified_from=modified_from)
    bulk_rows = client.search_tickets(token, payload, ticket_app_id=ticket_app_id)
    bulk_frame = normalize_ticket_rows(bulk_rows)
    if not bulk_frame.empty:
        bulk_frame["ticket_app_id"] = ticket_app_id
        bulk_frame["ticket_app_name"] = ticket_app_name

    if existing.empty:
        combined = bulk_frame
    elif bulk_frame.empty:
        combined = existing
    else:
        combined = pd.concat([existing, bulk_frame], ignore_index=True).drop_duplicates(
            subset=["ticket_id"], keep="last"
        )
    replace_table(config.db_path, "tickets", combined)

    # 4. Fill any survey-referenced tickets still missing after bulk sync
    combined, _ = _cache_ticket_context_from_rows(
        config=config,
        client=client,
        token=token,
        ticket_app_id=ticket_app_id,
        ticket_app_name=ticket_app_name,
        survey_rows=survey_rows,
        existing_tickets=combined if not combined.empty else None,
        limit=None,
        max_attempts=2,
    )

    # 5. Materialize join and write survey health artifacts
    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    notebook_path = write_survey_health_notebook(
        db_path=config.db_path,
        output_path=config.notebook_output_path,
    )

    return {
        "survey_rows": len(survey_frame),
        "ticket_app_id": ticket_app_id,
        "ticket_rows": len(combined),
        "linked_rows": int(model["ticket_linked"].sum()),
        "notebook_written": int(notebook_path.exists()),
    }
```

- [ ] **Step 2: Run the four new tests — confirm they all pass**

```bash
python -m pytest tests/test_pipeline.py::test_sync_all_fetches_surveys_bulk_syncs_and_materializes tests/test_pipeline.py::test_sync_all_fetches_missing_survey_tickets_individually tests/test_pipeline.py::test_sync_all_bulk_sync_uses_modified_from_watermark tests/test_pipeline.py::test_sync_all_skips_individual_fetch_when_bulk_sync_covers_all_survey_tickets -v
```

Expected: 4 PASSED

- [ ] **Step 3: Commit**

```bash
git add src/dynamix_manager/pipeline.py tests/test_pipeline.py
git commit -m "feat: add sync_all() as single entry point for ticket-survey ingest"
```

---

### Task 3: Update `generate_executive_report` and `generate_executive_email`

**Files:**
- Modify: `src/dynamix_manager/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write a failing test for the new `generate_executive_report` signature**

Replace `test_generate_executive_report_writes_html_and_returns_summary` in `tests/test_pipeline.py` with:

```python
def test_generate_executive_report_syncs_and_writes_html(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "ServiceName": "Desktop Support",
            "CreatedDate": "2026-03-30T10:00:00Z",
            "ModifiedDate": "2026-03-31T10:00:00Z",
        },
    ]

    result = generate_executive_report(config, client)

    report_path = tmp_path / "reports" / "executive_report.html"
    assert report_path.exists()
    assert "IT Executive Report" in report_path.read_text()
    assert result["report_written"] == 1
    assert "new_tickets_this_week" in result
```

- [ ] **Step 2: Add a failing test for `generate_executive_email` with client**

Append to `tests/test_pipeline.py`:

```python
def test_generate_executive_email_syncs_and_writes_html(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "ServiceName": "Desktop Support",
            "CreatedDate": "2026-03-30T10:00:00Z",
            "ModifiedDate": "2026-03-31T10:00:00Z",
        },
    ]

    result = generate_executive_email(config, client)

    email_path = tmp_path / "reports" / "executive_email.html"
    assert email_path.exists()
    assert result["email_written"] == 1
    assert "new_tickets_this_week" in result
```

- [ ] **Step 3: Run both new tests — confirm they fail**

```bash
python -m pytest tests/test_pipeline.py::test_generate_executive_report_syncs_and_writes_html tests/test_pipeline.py::test_generate_executive_email_syncs_and_writes_html -v
```

Expected: 2 FAILED (TypeError or old function not matching signature)

- [ ] **Step 4: Replace `generate_executive_report` in `pipeline.py`**

Replace the entire `generate_executive_report` function (lines ~366-400) with:

```python
def generate_executive_report(
    config: RuntimeConfig,
    client: TeamDynamixClient,
) -> dict[str, object]:
    """Sync all data then render the executive report."""
    sync_all(config=config, client=client)

    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )
    days_off = (
        read_table(config.db_path, "days_off")
        if table_exists(config.db_path, "days_off")
        else pd.DataFrame(columns=["holiday_date"])
    )

    snapshot = summarize_executive_snapshot(
        tickets, surveys, days_off=days_off, tdx_base_url=config.base_url
    )

    artifact_root = _artifact_root(config)
    report_path = artifact_root / "reports" / "executive_report.html"
    write_executive_report(snapshot, report_path)

    return {
        "report_written": int(report_path.exists()),
        "new_tickets_this_week": snapshot["new_tickets_this_week"],
        "avg_weekly_tickets": snapshot["avg_weekly_tickets_created"],
        "stale_open_count": snapshot["stale_open_count"],
    }
```

- [ ] **Step 5: Replace `generate_executive_email` in `pipeline.py`**

Replace the entire `generate_executive_email` function (lines ~403-432) with:

```python
def generate_executive_email(
    config: RuntimeConfig,
    client: TeamDynamixClient,
) -> dict[str, object]:
    """Sync all data then render the executive email HTML."""
    sync_all(config=config, client=client)

    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )
    days_off = (
        read_table(config.db_path, "days_off")
        if table_exists(config.db_path, "days_off")
        else pd.DataFrame(columns=["holiday_date"])
    )

    snapshot = summarize_executive_snapshot(
        tickets, surveys, days_off=days_off, tdx_base_url=config.base_url
    )

    artifact_root = _artifact_root(config)
    email_path = artifact_root / "reports" / "executive_email.html"
    write_executive_email(snapshot, email_path)

    return {
        "email_written": int(email_path.exists()),
        "email_path": str(email_path),
        "new_tickets_this_week": snapshot["new_tickets_this_week"],
        "stale_open_count": snapshot["stale_open_count"],
    }
```

- [ ] **Step 6: Run both tests — confirm they pass**

```bash
python -m pytest tests/test_pipeline.py::test_generate_executive_report_syncs_and_writes_html tests/test_pipeline.py::test_generate_executive_email_syncs_and_writes_html -v
```

Expected: 2 PASSED

- [ ] **Step 7: Commit**

```bash
git add src/dynamix_manager/pipeline.py tests/test_pipeline.py
git commit -m "feat: generate-report and generate-email always sync before rendering"
```

---

### Task 4: Remove dead public functions from `pipeline.py`

**Files:**
- Modify: `src/dynamix_manager/pipeline.py`

The six public functions being removed are: `sync_tickets`, `cache_survey_report`, `discover_ticket_app`, `cache_ticket_context`, `refresh_survey_slice`, `backfill_ticket_links`.

- [ ] **Step 1: Delete `sync_tickets` (lines ~331-363)**

Remove the entire function:

```python
def sync_tickets(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    ticket_app_id: int,
) -> dict[str, int]:
    ...
```

- [ ] **Step 2: Delete `cache_survey_report`, `discover_ticket_app`, `cache_ticket_context` (lines ~176-227)**

Remove all three functions:

```python
def cache_survey_report(config, client, report_id): ...
def discover_ticket_app(config, client, report_id): ...
def cache_ticket_context(config, client, ticket_app_id, ...): ...
```

`cache_days_off` (lines ~186-193) lives in this same region — **do not remove it**. Only remove the three functions listed above.

- [ ] **Step 3: Delete `refresh_survey_slice` and `backfill_ticket_links` (lines ~435-522)**

Remove both functions in their entirety.

- [ ] **Step 4: Commit**

```bash
git add src/dynamix_manager/pipeline.py
git commit -m "refactor: remove sync_tickets, cache_survey_report, discover_ticket_app, cache_ticket_context, refresh_survey_slice, backfill_ticket_links"
```

---

### Task 5: Remove obsolete tests and update imports

**Files:**
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Remove all tests that reference deleted functions**

Delete these eleven test functions from `tests/test_pipeline.py`:

- `test_cache_survey_report_persists_normalized_rows`
- `test_discover_ticket_app_finds_ticket_application_from_report_rows`
- `test_discover_ticket_app_prefers_infotech_tickets_by_name`
- `test_cache_ticket_context_persists_ticket_details_for_survey_rows`
- `test_cache_ticket_context_honors_limit`
- `test_cache_ticket_context_reuses_existing_ticket_rows`
- `test_cache_ticket_context_keeps_partial_progress_on_rate_limit`
- `test_sync_tickets_upserts_modified_tickets`
- `test_refresh_survey_slice_runs_end_to_end_and_writes_report`
- `test_backfill_ticket_links_runs_multiple_batches_until_no_progress`
- `test_backfill_ticket_links_materializes_outputs_once_after_batching`

- [ ] **Step 2: Run full test suite — all tests must pass**

```bash
python -m pytest -v
```

Expected: all tests PASS (the exact count will be lower than before — 11 tests removed, 5 added, net reduction of ~6)

- [ ] **Step 3: Commit**

```bash
git add tests/test_pipeline.py
git commit -m "test: remove obsolete pipeline tests for deleted functions"
```

---

### Task 6: Update the CLI

**Files:**
- Modify: `src/dynamix_manager/cli.py`

- [ ] **Step 1: Remove the `backfill-tickets` subparser from `build_parser()`**

In `cli.py`, delete these lines entirely:

```python
    # backfill-tickets
    backfill = subparsers.add_parser(
        "backfill-tickets",
        help="Backfill ticket context for all surveyed tickets in batches",
    )
    backfill.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of tickets to fetch per batch (default: 50)",
    )
    backfill.add_argument(
        "--max-batches",
        type=int,
        default=5,
        help="Maximum number of batches to run (default: 5)",
    )
```

- [ ] **Step 2: Update the `refresh-surveys` help text in `build_parser()`**

Replace:

```python
    subparsers.add_parser(
        "refresh-surveys",
        help="Fetch latest survey report and refresh linked-ticket model",
    )
```

With:

```python
    subparsers.add_parser(
        "refresh-surveys",
        help="Sync surveys and tickets, fill missing ticket context, materialize join",
    )
```

- [ ] **Step 3: Remove the `backfill-tickets` handler from `main()` and update `refresh-surveys` and `generate-email`**

In `main()`, replace the `refresh-surveys` handler:

```python
    if args.command == "refresh-surveys":
        result = pipeline.refresh_survey_slice(
            config=config,
            client=client,
            report_id=survey_report_id(),
        )
        print(json.dumps(result, indent=2))
```

With:

```python
    if args.command == "refresh-surveys":
        result = pipeline.sync_all(config=config, client=client)
        print(json.dumps(result, indent=2))
```

Delete the entire `backfill-tickets` handler block:

```python
    elif args.command == "backfill-tickets":
        result = pipeline.backfill_ticket_links(
            config=config,
            client=client,
            report_id=survey_report_id(),
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
        print(json.dumps(result, indent=2))
```

Update the `generate-email` handler to pass `client`:

```python
    elif args.command == "generate-email":
        result = pipeline.generate_executive_email(config=config, client=client)
        print(json.dumps(result, indent=2))
```

Also remove the now-unused import at the top of `cli.py`:

```python
from dynamix_manager.config import load_runtime_config, survey_report_id
```

Replace with:

```python
from dynamix_manager.config import load_runtime_config
```

- [ ] **Step 4: Verify `build_parser` test still passes (CLI tests import `build_parser`)**

```bash
python -m pytest tests/test_cli.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/cli.py
git commit -m "refactor: remove backfill-tickets CLI command; wire generate-email through sync_all"
```

---

### Task 7: Full verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS, no failures

- [ ] **Step 2: Run ruff**

```bash
python -m ruff check src/ tests/
```

Expected: no output (clean)

- [ ] **Step 3: Smoke-check the CLAUDE.md CLI reference is accurate**

Verify CLAUDE.md still lists only valid commands. The `backfill-tickets` line should be removed:

In `CLAUDE.md`, delete:

```
dynamix-manager backfill-tickets [--batch-size N] [--max-batches N]
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: remove backfill-tickets from CLI reference in CLAUDE.md"
```
