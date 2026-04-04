# Survey-First Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first survey-focused TeamDynamix analytics slice: API/report ingestion into DuckDB, ticket-linked survey modeling, and an initial notebook plus HTML artifact.

**Architecture:** Add a small Python ingestion stack under `src/dynamix_manager/` with isolated modules for configuration, TeamDynamix auth/HTTP access, DuckDB storage, survey report ingestion, ticket ingestion, and ticket-linked survey modeling. Keep presentation separate from ingestion so the same modeled tables drive both a Jupyter notebook and a generated HTML report.

**Tech Stack:** Python 3.13, DuckDB, requests, python-dotenv, pandas, JupyterLab, pytest, Ruff

**Scope note:** This plan only delivers the survey-first slice. Weekly prior-month leadership reporting and broader balanced-scorecard rollups remain deferred until after this first survey ingestion/modeling/report path is stable.

**Generated artifact note:** `data/analytics.duckdb` and `reports/survey_health.html` are generated local artifacts for this slice and should not be committed.

---

### Task 1: Add Survey-First Dependencies and Storage Settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/dynamix_manager/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing config test**

```python
from dynamix_manager.config import analytics_db_path, survey_report_id


def test_default_survey_storage_settings():
    assert analytics_db_path().name == "analytics.duckdb"
    assert survey_report_id() == 100482
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_default_survey_storage_settings -v`
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path


def analytics_db_path() -> Path:
    return Path("data") / "analytics.duckdb"


def survey_report_id() -> int:
    return 100482
```

Also add runtime dependencies for `duckdb` and `nbformat`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_default_survey_storage_settings -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml src/dynamix_manager/config.py tests/test_config.py
git commit -m "feat: add survey storage configuration"
```

### Task 2: Add DuckDB Storage Bootstrap

**Files:**
- Create: `src/dynamix_manager/storage.py`
- Create: `tests/test_storage.py`

- [ ] **Step 1: Write the failing storage test**

```python
from pathlib import Path

from dynamix_manager.storage import ensure_analytics_db


def test_ensure_analytics_db_creates_parent_directory(tmp_path: Path):
    db_path = tmp_path / "nested" / "analytics.duckdb"
    ensure_analytics_db(db_path)
    assert db_path.parent.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_storage.py::test_ensure_analytics_db_creates_parent_directory -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
from pathlib import Path
import duckdb


def ensure_analytics_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)):
        pass
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_storage.py::test_ensure_analytics_db_creates_parent_directory -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/storage.py tests/test_storage.py
git commit -m "feat: add duckdb storage bootstrap"
```

### Task 3: Add TeamDynamix Client Primitives

**Files:**
- Create: `src/dynamix_manager/tdx_client.py`
- Create: `tests/test_tdx_client.py`

- [ ] **Step 1: Write the failing auth header test**

```python
from dynamix_manager.tdx_client import build_auth_headers


def test_build_auth_headers_includes_bearer_and_app_id():
    headers = build_auth_headers("token", "1234")
    assert headers["Authorization"] == "Bearer token"
    assert headers["X-TDClient-ID"] == "1234"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tdx_client.py::test_build_auth_headers_includes_bearer_and_app_id -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def build_auth_headers(token: str, app_id: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-TDClient-ID": app_id,
        "Accept": "application/json",
    }
```

Also add a small `TeamDynamixClient` class with isolated methods for authentication,
ticket search, and report fetch.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tdx_client.py::test_build_auth_headers_includes_bearer_and_app_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/tdx_client.py tests/test_tdx_client.py
git commit -m "feat: add teamdynamix client primitives"
```

### Task 4: Add Runtime Config Loading and Validation

**Files:**
- Modify: `src/dynamix_manager/config.py`
- Create: `tests/test_config_runtime.py`

- [ ] **Step 1: Write the failing runtime config test**

```python
from dynamix_manager.config import load_runtime_config


def test_load_runtime_config_reads_required_env(monkeypatch):
    monkeypatch.setenv("TDX_BASE_URL", "https://example.test")
    monkeypatch.setenv("TDX_APP_ID", "1234")
    monkeypatch.setenv("TDX_USERNAME", "user")
    monkeypatch.setenv("TDX_PASSWORD", "pass")
    config = load_runtime_config()
    assert config.base_url == "https://example.test"
    assert config.app_id == "1234"
    assert config.db_path.name == "analytics.duckdb"
    assert config.report_output_path.name == "survey_health.html"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config_runtime.py::test_load_runtime_config_reads_required_env -v`
Expected: FAIL with `ImportError` or `AttributeError`

- [ ] **Step 3: Write minimal implementation**

```python
@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    app_id: str
    username: str
    password: str
    db_path: Path
    report_output_path: Path


def load_runtime_config() -> RuntimeConfig:
    ...
```

This module owns `.env` loading, required-value validation, and generated-artifact locations for the rest of the pipeline.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_config_runtime.py::test_load_runtime_config_reads_required_env -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/config.py tests/test_config_runtime.py
git commit -m "feat: add runtime config loading"
```

### Task 5: Add Survey Report Ingestion

**Files:**
- Create: `src/dynamix_manager/surveys.py`
- Modify: `src/dynamix_manager/storage.py`
- Create: `tests/test_surveys.py`

- [ ] **Step 1: Write the failing survey normalization test**

```python
import pandas as pd

from dynamix_manager.surveys import infer_survey_columns, normalize_survey_rows


def test_normalize_survey_rows_preserves_ticket_link():
    rows = [{
        "ResponseID": 1,
        "TicketID": 42,
        "SurveyRequestedDate": "2026-03-01T11:00:00Z",
        "SurveyCompletedDate": "2026-03-01T12:00:00Z",
        "48398": "Very Satisfied",
        "48399": "Helpful support",
    }]
    df = normalize_survey_rows(rows)
    assert df.loc[0, "ticket_id"] == 42
    assert df.loc[0, "response_id"] == 1
    assert df.loc[0, "survey_requested_at"] == "2026-03-01T11:00:00Z"
    assert df.loc[0, "survey_completed_at"] == "2026-03-01T12:00:00Z"
    assert df.loc[0, "satisfaction_label"] == "Very Satisfied"
    assert df.loc[0, "comment_text"] == "Helpful support"


def test_infer_survey_columns_finds_satisfaction_and_comment_keys():
    row = {
        "ResponseID": 1,
        "TicketID": 42,
        "48398": "Very Satisfied",
        "48399": "Helpful support",
    }
    columns = infer_survey_columns([row])
    assert columns["satisfaction_key"] == "48398"
    assert columns["comment_key"] == "48399"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_surveys.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def infer_survey_columns(rows: list[dict]) -> dict[str, str | None]:
    ...


def normalize_survey_rows(rows: list[dict]) -> pd.DataFrame:
    ...
```

Normalize at least these v1 columns from report `100482`:

- `response_id`
- `ticket_id`
- `survey_requested_at`
- `survey_completed_at`
- `satisfaction_label`

If free-text comments are present, also normalize `comment_text`. Preserve the raw rows separately, and persist both a raw survey table and a normalized survey table in DuckDB.
Do not hardcode `48398` or `48399` as the only valid live keys. The implementation must infer or validate the satisfaction field from the report rows and should map a comment field when present. Missing satisfaction semantics should fail clearly; missing comment text should not.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_surveys.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/surveys.py src/dynamix_manager/storage.py tests/test_surveys.py
git commit -m "feat: add survey report ingestion"
```

### Task 6: Add Ticket Ingestion for Survey Joins

**Files:**
- Create: `src/dynamix_manager/tickets.py`
- Modify: `src/dynamix_manager/storage.py`
- Create: `tests/test_tickets.py`

- [ ] **Step 1: Write the failing ticket normalization test**

```python
from dynamix_manager.tickets import normalize_ticket_rows


def test_normalize_ticket_rows_preserves_ticket_identity():
    rows = [{
        "ID": 42,
        "Title": "Printer issue",
        "CreatedDate": "2026-03-01T10:00:00Z",
        "CompletedFullName": "Analyst One",
    }]
    df = normalize_ticket_rows(rows)
    assert df.loc[0, "ticket_id"] == 42
    assert df.loc[0, "title"] == "Printer issue"


def test_build_ticket_search_filters_uses_watermark_for_incremental_refresh():
    filters = build_ticket_search_filters("2026-03-01T00:00:00Z")
    assert filters["ModifiedDateFrom"] == "2026-03-01T00:00:00Z"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tickets.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def normalize_ticket_rows(rows: list[dict]) -> pd.DataFrame:
    ...


def build_ticket_search_filters(modified_from: str | None) -> dict:
    ...
```

Persist a raw ticket table and normalized ticket dimension table suitable for survey joins.
Default behavior should support incremental refresh using a ticket watermark such as `ModifiedDateFrom`, while still allowing an initial full-history pull when no watermark exists.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tickets.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/tickets.py src/dynamix_manager/storage.py tests/test_tickets.py
git commit -m "feat: add ticket ingestion for survey joins"
```

### Task 7: Add Ticket-Linked Survey Model

**Files:**
- Create: `src/dynamix_manager/models.py`
- Modify: `src/dynamix_manager/storage.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing join model test**

```python
import pandas as pd

from dynamix_manager.models import build_ticket_linked_surveys


def test_build_ticket_linked_surveys_joins_on_ticket_id():
    surveys = pd.DataFrame([{"response_id": 1, "ticket_id": 42, "satisfaction_label": "Very Satisfied"}])
    tickets = pd.DataFrame([{"ticket_id": 42, "title": "Printer issue", "completed_full_name": "Analyst One"}])
    modeled = build_ticket_linked_surveys(surveys, tickets)
    assert modeled.loc[0, "title"] == "Printer issue"
    assert modeled.loc[0, "completed_full_name"] == "Analyst One"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py::test_build_ticket_linked_surveys_joins_on_ticket_id -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def build_ticket_linked_surveys(surveys: pd.DataFrame, tickets: pd.DataFrame) -> pd.DataFrame:
    return surveys.merge(tickets, on="ticket_id", how="inner")
```

Persist the resulting modeled table in DuckDB.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_models.py::test_build_ticket_linked_surveys_joins_on_ticket_id -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/models.py src/dynamix_manager/storage.py tests/test_models.py
git commit -m "feat: add ticket-linked survey model"
```

### Task 8: Add Survey Summary Metrics and Comment Themes

**Files:**
- Create: `src/dynamix_manager/metrics.py`
- Create: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing summary metrics test**

```python
import pandas as pd

from dynamix_manager.metrics import summarize_survey_health


def test_summarize_survey_health_counts_responses():
    df = pd.DataFrame([
        {"response_id": 1, "satisfaction_label": "Very Satisfied", "comment_text": "Helpful"},
        {"response_id": 2, "satisfaction_label": "Satisfied", "comment_text": "Quick response"},
    ])
    summary = summarize_survey_health(df)
    assert summary["response_count"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_metrics.py::test_summarize_survey_health_counts_responses -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def summarize_survey_health(df: pd.DataFrame) -> dict:
    ...
```

Include lightweight comment-theme extraction based on simple token frequency or grouped phrase counts, not heavy NLP.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_metrics.py::test_summarize_survey_health_counts_responses -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/metrics.py tests/test_metrics.py
git commit -m "feat: add survey summary metrics"
```

### Task 9: Add Survey Refresh CLI

**Files:**
- Create: `src/dynamix_manager/cli.py`
- Modify: `src/dynamix_manager/__init__.py`
- Modify: `pyproject.toml`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI wiring test**

```python
from dynamix_manager.cli import build_parser


def test_build_parser_supports_refresh_surveys_command():
    parser = build_parser()
    parsed = parser.parse_args(["refresh-surveys"])
    assert parsed.command == "refresh-surveys"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_build_parser_supports_refresh_surveys_command -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def build_parser() -> argparse.ArgumentParser:
    ...
```

Expose an entry point such as `dynamix-manager`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_build_parser_supports_refresh_surveys_command -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/cli.py src/dynamix_manager/__init__.py pyproject.toml tests/test_cli.py
git commit -m "feat: add survey refresh cli"
```

### Task 10: Add Survey Refresh Orchestration

**Files:**
- Create: `src/dynamix_manager/pipeline.py`
- Modify: `src/dynamix_manager/cli.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing orchestration test**

```python
import pytest

from dynamix_manager.pipeline import refresh_surveys


def test_refresh_surveys_requires_ticket_and_survey_steps(monkeypatch, tmp_path):
    calls = []

    def record(name):
        def _inner(*args, **kwargs):
            calls.append(name)
            if name == "config":
                class Config:
                    base_url = "https://example.test"
                    app_id = "1234"
                    username = "user"
                    password = "pass"
                    db_path = tmp_path / "analytics.duckdb"
                    report_output_path = tmp_path / "survey_health.html"
                return Config()
            if name in {"survey", "tickets"}:
                return []
            return None
        return _inner

    monkeypatch.setattr("dynamix_manager.pipeline.load_runtime_config", record("config"))
    monkeypatch.setattr("dynamix_manager.pipeline.authenticate", record("auth"))
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_survey_report_rows", record("survey"))
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_ticket_rows", record("tickets"))
    monkeypatch.setattr("dynamix_manager.pipeline.persist_raw_tables", record("raw"))
    monkeypatch.setattr("dynamix_manager.pipeline.persist_modeled_surveys", record("persist"))
    monkeypatch.setattr("dynamix_manager.pipeline.write_survey_report_html", record("report"))

    refresh_surveys()

    assert calls == ["config", "auth", "survey", "tickets", "raw", "persist", "report"]


def test_refresh_surveys_surfaces_auth_failure(monkeypatch):
    monkeypatch.setattr("dynamix_manager.pipeline.load_runtime_config", lambda: object())

    def fail_auth(*args, **kwargs):
        raise RuntimeError("auth failed")

    monkeypatch.setattr("dynamix_manager.pipeline.authenticate", fail_auth)

    with pytest.raises(RuntimeError, match="auth failed"):
        refresh_surveys()


def test_refresh_surveys_preserves_last_successful_cache_on_failure(monkeypatch, tmp_path):
    existing_db = tmp_path / "analytics.duckdb"
    existing_db.write_text("sentinel")

    class Config:
        base_url = "https://example.test"
        app_id = "1234"
        username = "user"
        password = "pass"
        db_path = existing_db
        report_output_path = tmp_path / "survey_health.html"

    monkeypatch.setattr("dynamix_manager.pipeline.load_runtime_config", lambda: Config())
    monkeypatch.setattr("dynamix_manager.pipeline.authenticate", lambda *args, **kwargs: "token")

    def fail_report(*args, **kwargs):
        raise RuntimeError("report failed")

    monkeypatch.setattr("dynamix_manager.pipeline.fetch_survey_report_rows", fail_report)

    with pytest.raises(RuntimeError, match="report failed"):
        refresh_surveys()

    assert existing_db.read_text() == "sentinel"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def refresh_surveys() -> None:
    ...
```

This task must explicitly cover:

- loading `.env`-backed runtime config
- authenticating to TeamDynamix
- fetching report `100482`
- fetching ticket data needed for joins
- using a ticket watermark for incremental refresh when prior refresh metadata exists
- persisting raw survey/ticket tables first
- building and persisting the ticket-linked survey model
- generating `reports/survey_health.html` from modeled survey data
- recording refresh metadata such as refresh timestamp and status
- preserving the last successful cached data unless the replacement refresh succeeds
- surfacing actionable failures if auth, report fetch, or ticket fetch fails

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/pipeline.py src/dynamix_manager/cli.py tests/test_pipeline.py
git commit -m "feat: orchestrate survey refresh pipeline"
```

### Task 11: Add First Notebook and HTML Survey Artifact

**Files:**
- Create: `notebooks/survey_health.ipynb`
- Create: `src/dynamix_manager/reporting.py`
- Create: `reports/.gitkeep`
- Create: `tests/test_reporting.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing report rendering test**

```python
from pathlib import Path

from dynamix_manager.reporting import write_survey_report_html


def test_write_survey_report_html_creates_artifact(tmp_path: Path):
    output_path = tmp_path / "survey_health.html"
    write_survey_report_html(
        {"response_count": 5, "top_themes": ["helpful"]},
        output_path,
    )
    text = output_path.read_text()
    assert "5" in text
    assert "helpful" in text
    assert "tailwindcss" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_reporting.py::test_write_survey_report_html_creates_artifact -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
def write_survey_report_html(summary: dict, output_path: Path) -> Path:
    ...
```

Create an initial notebook that reads the DuckDB modeled table and renders the first survey-first executive views. The reporting task must also write a concrete artifact file such as `reports/survey_health.html` using Tailwind CDN styling.
The rendered HTML must surface the data refresh timestamp clearly so stale output is obvious.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_reporting.py::test_write_survey_report_html_creates_artifact -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add notebooks/survey_health.ipynb src/dynamix_manager/reporting.py reports/.gitkeep tests/test_reporting.py README.md
git commit -m "feat: add initial survey notebook and report renderer"
```

### Task 12: Verify End-to-End Survey Refresh

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: PASS

- [ ] **Step 2: Run Ruff**

Run: `.venv/bin/python -m ruff check .`
Expected: `All checks passed!`

- [ ] **Step 3: Run the survey refresh command**

Run: `.venv/bin/python -m dynamix_manager.cli refresh-surveys`
Expected: raw and modeled survey/ticket tables created in `data/analytics.duckdb`, plus `reports/survey_health.html` written from modeled survey data

- [ ] **Step 4: Smoke-check notebook/report dependencies**

Run: `.venv/bin/jupyter lab --version`
Expected: prints a JupyterLab version string

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "feat: deliver survey-first analytics slice"
```
