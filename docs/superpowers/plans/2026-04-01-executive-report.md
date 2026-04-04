# Executive Snapshot Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone HTML executive report that surfaces ticket volume trends, satisfaction summary, completion-time distribution, and key operational indicators for a senior non-IT administrator.

**Architecture:** New `executive.py` module owns all snapshot-metric logic. `reporting.py` gains `render_executive_report_html` / `write_executive_report`. Pipeline gets a `generate_executive_report` function (no API calls — reads cached DB tables). CLI gains a `generate-report` subcommand. The box-and-whisker chart is rendered client-side via Plotly.js CDN with data embedded as a JSON blob, matching the existing survey health report pattern.

**Tech Stack:** Python 3.13, pandas, numpy (busday_count), DuckDB, Plotly.js CDN (3.4.0), Tailwind CSS CDN, pytest

**Scope note:** "Working hours" approximation = `numpy.busday_count(created_date, resolved_date) × 8`. Partial first/last business days are not split.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/dynamix_manager/executive.py` | All executive-snapshot metric logic |
| Create | `tests/test_executive.py` | Unit tests for executive metrics |
| Modify | `src/dynamix_manager/reporting.py` | Add `render_executive_report_html`, `write_executive_report` |
| Create | `tests/test_executive_reporting.py` | HTML rendering tests |
| Modify | `src/dynamix_manager/pipeline.py` | Add `generate_executive_report` |
| Modify | `src/dynamix_manager/cli.py` | Add `generate-report` subcommand |
| Modify | `tests/test_pipeline.py` | Pipeline integration test |
| Modify | `tests/test_cli.py` | CLI parser test |

---

### Task 1: `executive.py` — ticket volume metrics

**Files:**
- Create: `src/dynamix_manager/executive.py`
- Create: `tests/test_executive.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_executive.py
import pandas as pd
import pytest

from dynamix_manager.executive import summarize_executive_snapshot


def test_new_tickets_this_week_counts_tickets_created_since_monday():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # Monday — in week
        {"ticket_id": 2, "created_at": "2026-03-31T10:00:00Z"},  # Tuesday — in week
        {"ticket_id": 3, "created_at": "2026-03-29T23:59:59Z"},  # Sunday before — out
        {"ticket_id": 4, "created_at": "2026-03-23T10:00:00Z"},  # prior week — out
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["new_tickets_this_week"] == 2


def test_avg_weekly_tickets_created_divides_total_by_weeks_elapsed():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-16T10:00:00Z"},  # week 12
        {"ticket_id": 2, "created_at": "2026-03-23T10:00:00Z"},  # week 13
        {"ticket_id": 3, "created_at": "2026-03-30T10:00:00Z"},  # week 14
        {"ticket_id": 4, "created_at": "2026-03-31T10:00:00Z"},  # week 14
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    # oldest = week 12 (starts 2026-03-16), as_of = week 14 (starts 2026-03-30) → 3 weeks
    assert snapshot["avg_weekly_tickets_created"] == pytest.approx(4 / 3, rel=1e-3)


def test_week_over_week_delta_pct_compares_current_to_prior_week():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week
        {"ticket_id": 2, "created_at": "2026-03-31T10:00:00Z"},  # this week
        {"ticket_id": 3, "created_at": "2026-03-23T10:00:00Z"},  # prior week
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    # this_week=2, prior_week=1 → (2-1)/1 * 100 = 100.0
    assert snapshot["week_over_week_delta_pct"] == pytest.approx(100.0)


def test_week_over_week_delta_pct_is_none_when_prior_week_empty():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"},  # this week only
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_over_week_delta_pct"] is None


def test_week_label_formats_monday_to_sunday():
    tickets = pd.DataFrame([{"ticket_id": 1, "created_at": "2026-03-30T10:00:00Z"}])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["week_label"] == "Mar 30 – Apr 5"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_executive.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dynamix_manager.executive'`

- [ ] **Step 3: Write the implementation**

```python
# src/dynamix_manager/executive.py
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd


def _week_start(ts: pd.Timestamp) -> pd.Timestamp:
    """Return the Monday midnight UTC of the ISO week containing ts."""
    d = ts.normalize().tz_convert("UTC") if ts.tzinfo else ts.normalize().tz_localize("UTC")
    return d - pd.Timedelta(days=d.weekday())


def _week_label(week_start: pd.Timestamp) -> str:
    """Format a week as 'Mon D – Mon D', e.g. 'Mar 30 – Apr 5'."""
    week_end = week_start + pd.Timedelta(days=6)
    if week_start.month == week_end.month:
        return f"{week_start.strftime('%b %-d')} – {week_end.strftime('%-d')}"
    return f"{week_start.strftime('%b %-d')} – {week_end.strftime('%b %-d')}"


def _parse_created_at(tickets: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(tickets["created_at"], utc=True, errors="coerce")


def summarize_executive_snapshot(
    tickets: pd.DataFrame,
    surveys: pd.DataFrame,
    days_off: pd.DataFrame | None = None,
    as_of: pd.Timestamp | None = None,
) -> dict[str, object]:
    if as_of is None:
        as_of = pd.Timestamp.now("UTC")
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    ws = _week_start(as_of)
    prior_ws = ws - pd.Timedelta(days=7)

    result: dict[str, object] = {
        "week_label": _week_label(ws),
        "report_generated_at": as_of.isoformat(),
    }

    # --- ticket volume ---
    if tickets.empty or "created_at" not in tickets.columns:
        result["new_tickets_this_week"] = 0
        result["avg_weekly_tickets_created"] = 0.0
        result["week_over_week_delta_pct"] = None
    else:
        created = _parse_created_at(tickets)
        this_week_mask = (created >= ws) & (created <= as_of)
        prior_week_mask = (created >= prior_ws) & (created < ws)
        this_week_count = int(this_week_mask.sum())
        prior_week_count = int(prior_week_mask.sum())

        oldest = created.dropna().min()
        if pd.isna(oldest):
            weeks_elapsed = 1
        else:
            oldest_ws = _week_start(oldest)
            weeks_elapsed = max(1, int((ws - oldest_ws).days / 7) + 1)

        result["new_tickets_this_week"] = this_week_count
        result["avg_weekly_tickets_created"] = len(tickets) / weeks_elapsed
        result["week_over_week_delta_pct"] = (
            (this_week_count - prior_week_count) / prior_week_count * 100.0
            if prior_week_count > 0
            else None
        )

    # remaining keys — populated in later tasks
    result.setdefault("completion_hours_this_week", [])
    result.setdefault("completion_hours_all_time", [])
    result.setdefault("satisfaction_counts", {})
    result.setdefault("satisfaction_trend", [])
    result.setdefault("sla_compliance_rate", None)
    result.setdefault("stale_open_count", 0)
    result.setdefault("top_services", [])
    result.setdefault("median_first_response_hours", None)
    result.setdefault("unassigned_count", 0)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_executive.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/dynamix_manager/executive.py tests/test_executive.py
git commit -m "feat: add executive snapshot ticket volume metrics"
```

---

### Task 2: `executive.py` — completion hours

**Files:**
- Modify: `src/dynamix_manager/executive.py`
- Modify: `tests/test_executive.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_executive.py`:

```python
from dynamix_manager.executive import _business_hours_between, summarize_executive_snapshot


def test_business_hours_between_returns_8_for_one_business_day():
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-03-31 08:00:00", tz="UTC")
    assert _business_hours_between(start, end, set()) == 8.0


def test_business_hours_between_excludes_holiday():
    # Mon→Wed = 2 business days normally, but Tuesday is a holiday → 1 day = 8h
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-04-01 08:00:00", tz="UTC")
    assert _business_hours_between(start, end, {"2026-03-31"}) == 8.0


def test_business_hours_between_returns_zero_for_same_day():
    start = pd.Timestamp("2026-03-30 08:00:00", tz="UTC")
    end = pd.Timestamp("2026-03-30 16:00:00", tz="UTC")
    assert _business_hours_between(start, end, set()) == 0.0


def test_completion_hours_this_week_lists_resolved_tickets():
    tickets = pd.DataFrame([
        {
            "ticket_id": 1,
            "created_at": "2026-03-30T08:00:00Z",   # Mon this week
            "resolved_at": "2026-03-31T08:00:00Z",   # Tue this week → 1 biz day = 8h
            "status_class": 3,
        },
        {
            "ticket_id": 2,
            "created_at": "2026-01-05T08:00:00Z",
            "resolved_at": "2026-01-07T08:00:00Z",   # 2 biz days = 16h; resolved last quarter
            "status_class": 3,
        },
        {
            "ticket_id": 3,
            "created_at": "2026-03-30T08:00:00Z",
            "resolved_at": None,                      # still open — excluded
            "status_class": 1,
        },
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")

    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)

    assert snapshot["completion_hours_this_week"] == [8.0]
    assert set(snapshot["completion_hours_all_time"]) == {8.0, 16.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_executive.py::test_business_hours_between_returns_8_for_one_business_day tests/test_executive.py::test_completion_hours_this_week_lists_resolved_tickets -v`
Expected: FAIL with `ImportError` (function not yet defined)

- [ ] **Step 3: Implement `_business_hours_between` and extend `summarize_executive_snapshot`**

Add to `src/dynamix_manager/executive.py` (before `summarize_executive_snapshot`):

```python
def _business_hours_between(
    start: pd.Timestamp,
    end: pd.Timestamp,
    holidays: set[str],
) -> float:
    """Return approximate business hours between start and end (8h per business day)."""
    if pd.isna(start) or pd.isna(end) or end <= start:
        return 0.0
    holiday_array = (
        np.array(sorted(holidays), dtype="datetime64[D]") if holidays else np.array([], dtype="datetime64[D]")
    )
    days = int(np.busday_count(start.date(), end.date(), holidays=holiday_array))
    return float(max(days, 0)) * 8.0
```

Replace the `# remaining keys — populated in later tasks` block in `summarize_executive_snapshot` with:

```python
    # --- completion hours ---
    holiday_dates: set[str] = set()
    if days_off is not None and "holiday_date" in days_off.columns:
        holiday_dates = set(days_off["holiday_date"].dropna().astype(str))

    if not tickets.empty and "resolved_at" in tickets.columns and "created_at" in tickets.columns:
        resolved_at = pd.to_datetime(tickets["resolved_at"], utc=True, errors="coerce")
        created_at_col = _parse_created_at(tickets)
        resolved_mask = resolved_at.notna()

        this_week_resolved = resolved_mask & (resolved_at >= ws) & (resolved_at <= as_of)
        all_resolved = resolved_mask

        def _hours(row_created, row_resolved):
            return _business_hours_between(row_created, row_resolved, holiday_dates)

        all_time_hours = [
            _hours(c, r)
            for c, r in zip(
                created_at_col[all_resolved],
                resolved_at[all_resolved],
            )
        ]
        this_week_hours = [
            _hours(c, r)
            for c, r in zip(
                created_at_col[this_week_resolved],
                resolved_at[this_week_resolved],
            )
        ]
        result["completion_hours_this_week"] = this_week_hours
        result["completion_hours_all_time"] = all_time_hours
    else:
        result["completion_hours_this_week"] = []
        result["completion_hours_all_time"] = []

    result.setdefault("satisfaction_counts", {})
    result.setdefault("satisfaction_trend", [])
    result.setdefault("sla_compliance_rate", None)
    result.setdefault("stale_open_count", 0)
    result.setdefault("top_services", [])
    result.setdefault("median_first_response_hours", None)
    result.setdefault("unassigned_count", 0)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_executive.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/executive.py tests/test_executive.py
git commit -m "feat: add executive snapshot completion hours with business-day approximation"
```

---

### Task 3: `executive.py` — survey and operational stats

**Files:**
- Modify: `src/dynamix_manager/executive.py`
- Modify: `tests/test_executive.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_executive.py`:

```python
def test_satisfaction_counts_groups_by_label():
    surveys = pd.DataFrame([
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-01T12:00:00Z"},
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-02T12:00:00Z"},
        {"satisfaction_label": "Satisfied",      "survey_completed_at": "2026-03-03T12:00:00Z"},
        {"satisfaction_label": "Dissatisfied",   "survey_completed_at": "2026-03-04T12:00:00Z"},
    ])
    snapshot = summarize_executive_snapshot(
        pd.DataFrame(), surveys, as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["satisfaction_counts"] == {
        "Very Satisfied": 2,
        "Satisfied": 1,
        "Dissatisfied": 1,
    }


def test_satisfaction_trend_includes_last_six_months():
    surveys = pd.DataFrame([
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2026-03-15T12:00:00Z"},
        {"satisfaction_label": "Dissatisfied",   "survey_completed_at": "2026-03-20T12:00:00Z"},
        {"satisfaction_label": "Very Satisfied", "survey_completed_at": "2025-09-01T12:00:00Z"},  # >6 months ago
    ])
    as_of = pd.Timestamp("2026-04-01", tz="UTC")
    snapshot = summarize_executive_snapshot(pd.DataFrame(), surveys, as_of=as_of)

    months = [m["month"] for m in snapshot["satisfaction_trend"]]
    assert "2026-03" in months
    assert "2025-09" not in months

    march = next(m for m in snapshot["satisfaction_trend"] if m["month"] == "2026-03")
    assert march["total"] == 2
    assert march["positive_rate"] == pytest.approx(0.5)


def test_sla_compliance_rate_excludes_tickets_without_sla():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "is_sla_violated": False},
        {"ticket_id": 2, "is_sla_violated": False},
        {"ticket_id": 3, "is_sla_violated": True},
        {"ticket_id": 4, "is_sla_violated": None},   # no SLA — excluded
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["sla_compliance_rate"] == pytest.approx(2 / 3)


def test_stale_open_count_excludes_resolved_and_recent_tickets():
    tickets = pd.DataFrame([
        {
            "ticket_id": 1,
            "created_at": "2026-03-01T08:00:00Z",   # ~22 biz days old — stale
            "resolved_at": None,
            "status_class": 1,
        },
        {
            "ticket_id": 2,
            "created_at": "2026-03-30T08:00:00Z",   # 2 biz days old — not stale
            "resolved_at": None,
            "status_class": 1,
        },
        {
            "ticket_id": 3,
            "created_at": "2026-01-01T08:00:00Z",
            "resolved_at": "2026-03-01T08:00:00Z",  # resolved — excluded
            "status_class": 3,
        },
    ])
    as_of = pd.Timestamp("2026-04-01 12:00:00", tz="UTC")
    snapshot = summarize_executive_snapshot(tickets, pd.DataFrame(), as_of=as_of)
    assert snapshot["stale_open_count"] == 1


def test_top_services_returns_top_5_by_count():
    rows = [{"ticket_id": i, "service_name": "Printing"} for i in range(5)]
    rows += [{"ticket_id": i + 100, "service_name": "Network"} for i in range(3)]
    rows += [{"ticket_id": i + 200, "service_name": "Software"} for i in range(2)]
    tickets = pd.DataFrame(rows)
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["top_services"][0] == {"service_name": "Printing", "count": 5}
    assert snapshot["top_services"][1] == {"service_name": "Network", "count": 3}


def test_median_first_response_hours_uses_response_time_column():
    tickets = pd.DataFrame([
        {"response_time_hours": 2.0},
        {"response_time_hours": 4.0},
        {"response_time_hours": 6.0},
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["median_first_response_hours"] == pytest.approx(4.0)


def test_unassigned_count_only_counts_open_tickets():
    tickets = pd.DataFrame([
        {"ticket_id": 1, "assignee_name": None,   "resolved_at": None,                   "status_class": 1},  # open, unassigned
        {"ticket_id": 2, "assignee_name": "Alex", "resolved_at": None,                   "status_class": 1},  # open, assigned
        {"ticket_id": 3, "assignee_name": None,   "resolved_at": "2026-03-01T10:00:00Z", "status_class": 3},  # resolved, unassigned
    ])
    snapshot = summarize_executive_snapshot(
        tickets, pd.DataFrame(), as_of=pd.Timestamp("2026-04-01", tz="UTC")
    )
    assert snapshot["unassigned_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_executive.py -k "satisfaction or sla or stale or top_service or response_hours or unassigned" -v`
Expected: FAIL — keys return default empty/None values

- [ ] **Step 3: Implement survey and operational stats**

Replace the `result.setdefault(...)` block at the end of `summarize_executive_snapshot` with the full implementation:

```python
    # --- survey stats ---
    _POSITIVE = {"Very Satisfied", "Satisfied"}
    if not surveys.empty and "satisfaction_label" in surveys.columns:
        result["satisfaction_counts"] = (
            surveys["satisfaction_label"]
            .value_counts()
            .to_dict()
        )
        if "survey_completed_at" in surveys.columns:
            sc = surveys.copy()
            sc["survey_completed_at"] = pd.to_datetime(
                sc["survey_completed_at"], utc=True, errors="coerce"
            )
            cutoff_6m = as_of - pd.DateOffset(months=6)
            recent = sc.loc[sc["survey_completed_at"] >= cutoff_6m].copy()
            if not recent.empty:
                recent["month"] = recent["survey_completed_at"].dt.to_period("M")
                trend = (
                    recent.groupby("month")["satisfaction_label"]
                    .agg(
                        total="count",
                        positive=lambda s: s.isin(_POSITIVE).sum(),
                    )
                    .reset_index()
                )
                result["satisfaction_trend"] = [
                    {
                        "month": str(row["month"]),
                        "total": int(row["total"]),
                        "positive_rate": float(row["positive"] / row["total"]) if row["total"] else 0.0,
                    }
                    for _, row in trend.sort_values("month").iterrows()
                ]
            else:
                result["satisfaction_trend"] = []
        else:
            result["satisfaction_trend"] = []
    else:
        result["satisfaction_counts"] = {}
        result["satisfaction_trend"] = []

    # --- SLA compliance ---
    if not tickets.empty and "is_sla_violated" in tickets.columns:
        sla_tracked = tickets["is_sla_violated"].notna()
        sla_count = int(sla_tracked.sum())
        if sla_count > 0:
            compliant = int((tickets.loc[sla_tracked, "is_sla_violated"] == False).sum())  # noqa: E712
            result["sla_compliance_rate"] = compliant / sla_count
        else:
            result["sla_compliance_rate"] = None
    else:
        result["sla_compliance_rate"] = None

    # --- stale open count (> 5 business days) ---
    if not tickets.empty and "created_at" in tickets.columns:
        created_col = _parse_created_at(tickets)
        resolved_col = (
            pd.to_datetime(tickets["resolved_at"], utc=True, errors="coerce")
            if "resolved_at" in tickets.columns
            else pd.Series([pd.NaT] * len(tickets))
        )
        sc_col = (
            pd.to_numeric(tickets["status_class"], errors="coerce")
            if "status_class" in tickets.columns
            else pd.Series([float("nan")] * len(tickets))
        )
        open_mask = ~sc_col.isin({3, 4}) & resolved_col.isna()
        holiday_array = (
            np.array(sorted(holiday_dates), dtype="datetime64[D]")
            if holiday_dates
            else np.array([], dtype="datetime64[D]")
        )
        open_tickets = tickets.loc[open_mask].copy()
        open_created = created_col[open_mask]
        stale_count = 0
        for c in open_created:
            if pd.isna(c):
                continue
            days = int(np.busday_count(c.date(), as_of.date(), holidays=holiday_array))
            if days > 5:
                stale_count += 1
        result["stale_open_count"] = stale_count
    else:
        result["stale_open_count"] = 0

    # --- top services ---
    if not tickets.empty and "service_name" in tickets.columns:
        top = (
            tickets["service_name"]
            .dropna()
            .value_counts()
            .head(5)
            .reset_index()
        )
        top.columns = ["service_name", "count"]
        result["top_services"] = top.to_dict(orient="records")
    else:
        result["top_services"] = []

    # --- median first response ---
    if not tickets.empty and "response_time_hours" in tickets.columns:
        median = tickets["response_time_hours"].dropna().median()
        result["median_first_response_hours"] = float(median) if not pd.isna(median) else None
    else:
        result["median_first_response_hours"] = None

    # --- unassigned open count ---
    if not tickets.empty:
        resolved_col2 = (
            pd.to_datetime(tickets["resolved_at"], utc=True, errors="coerce")
            if "resolved_at" in tickets.columns
            else pd.Series([pd.NaT] * len(tickets))
        )
        sc_col2 = (
            pd.to_numeric(tickets["status_class"], errors="coerce")
            if "status_class" in tickets.columns
            else pd.Series([float("nan")] * len(tickets))
        )
        open_mask2 = ~sc_col2.isin({3, 4}) & resolved_col2.isna()
        assignee = (
            tickets["assignee_name"].astype("string").str.strip()
            if "assignee_name" in tickets.columns
            else pd.array([""] * len(tickets), dtype="string")
        )
        unassigned_mask = open_mask2 & (assignee.isna() | assignee.eq(""))
        result["unassigned_count"] = int(unassigned_mask.sum())
    else:
        result["unassigned_count"] = 0

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_executive.py -v`
Expected: all PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/dynamix_manager/executive.py tests/test_executive.py
git commit -m "feat: add executive snapshot survey and operational stats"
```

---

### Task 4: Executive HTML report renderer

**Files:**
- Modify: `src/dynamix_manager/reporting.py`
- Create: `tests/test_executive_reporting.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_executive_reporting.py
from pathlib import Path

from dynamix_manager.reporting import render_executive_report_html, write_executive_report


def _sample_snapshot():
    return {
        "new_tickets_this_week": 12,
        "avg_weekly_tickets_created": 10.5,
        "week_over_week_delta_pct": 20.0,
        "satisfaction_counts": {"Very Satisfied": 100, "Satisfied": 50, "Dissatisfied": 8},
        "completion_hours_this_week": [8.0, 16.0, 24.0],
        "completion_hours_all_time": [8.0, 16.0, 40.0, 80.0],
        "sla_compliance_rate": 0.87,
        "stale_open_count": 3,
        "top_services": [
            {"service_name": "Desktop Support", "count": 25},
            {"service_name": "Network", "count": 12},
        ],
        "satisfaction_trend": [
            {"month": "2026-03", "positive_rate": 0.85, "total": 50},
        ],
        "median_first_response_hours": 2.5,
        "unassigned_count": 7,
        "week_label": "Mar 30 – Apr 5",
        "report_generated_at": "2026-04-01T12:00:00+00:00",
    }


def test_render_executive_report_html_includes_kpi_values():
    html = render_executive_report_html(_sample_snapshot())
    assert "12" in html                    # new tickets this week
    assert "10.5" in html                  # avg weekly
    assert "87" in html                    # SLA compliance (87%)
    assert "3" in html                     # stale open
    assert "7" in html                     # unassigned
    assert "2.5" in html                   # median response


def test_render_executive_report_html_includes_satisfaction_table():
    html = render_executive_report_html(_sample_snapshot())
    assert "Very Satisfied" in html
    assert "100" in html
    assert "Satisfied" in html


def test_render_executive_report_html_embeds_plotly_and_box_data():
    html = render_executive_report_html(_sample_snapshot())
    assert "plotly" in html.lower()
    assert "completion_hours" in html.lower() or "box" in html.lower()
    assert "This Week" in html
    assert "All Time" in html


def test_render_executive_report_html_includes_top_services():
    html = render_executive_report_html(_sample_snapshot())
    assert "Desktop Support" in html
    assert "Network" in html


def test_render_executive_report_html_shows_week_label():
    html = render_executive_report_html(_sample_snapshot())
    assert "Mar 30" in html


def test_write_executive_report_creates_file(tmp_path: Path):
    output_path = tmp_path / "executive_report.html"
    write_executive_report(_sample_snapshot(), output_path)
    assert output_path.exists()
    assert "IT Executive Report" in output_path.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_executive_reporting.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement the renderer**

Add to the bottom of `src/dynamix_manager/reporting.py` (after the existing `write_ticket_health_report` and before `render_ticket_quality_html`):

```python
def _executive_kpi_card(label: str, value: str, sub: str = "") -> str:
    sub_html = f'<p class="text-sm text-stone-500 mt-1">{escape(sub)}</p>' if sub else ""
    return (
        f'<div class="bg-white rounded-lg border border-stone-200 px-6 py-5">'
        f'<p class="text-sm font-medium text-stone-500">{escape(label)}</p>'
        f'<p class="text-3xl font-bold text-stone-900 mt-1">{escape(value)}</p>'
        f"{sub_html}"
        f"</div>"
    )


def render_executive_report_html(snapshot: dict) -> str:
    import json

    week_label = escape(str(snapshot.get("week_label", "")))
    generated_at = escape(str(snapshot.get("report_generated_at", "")))

    new_tickets = snapshot.get("new_tickets_this_week", 0)
    avg_weekly = snapshot.get("avg_weekly_tickets_created", 0.0)
    ww_delta = snapshot.get("week_over_week_delta_pct")
    sla_rate = snapshot.get("sla_compliance_rate")
    stale = snapshot.get("stale_open_count", 0)
    unassigned = snapshot.get("unassigned_count", 0)
    median_response = snapshot.get("median_first_response_hours")

    ww_str = f"{ww_delta:+.1f}% vs prior week" if ww_delta is not None else "no prior week data"
    sla_str = f"{sla_rate * 100:.0f}%" if sla_rate is not None else "N/A"
    response_str = f"{median_response:.1f} hrs" if median_response is not None else "N/A"

    kpi_cards = "\n".join([
        _executive_kpi_card("New Tickets This Week", str(new_tickets), ww_str),
        _executive_kpi_card("Avg Weekly Tickets", f"{avg_weekly:.1f}"),
        _executive_kpi_card("SLA Compliance", sla_str),
        _executive_kpi_card("Stale Open (>5 days)", str(stale)),
        _executive_kpi_card("Unassigned Open", str(unassigned)),
        _executive_kpi_card("Median First Response", response_str),
    ])

    # satisfaction counts table
    sat_counts = snapshot.get("satisfaction_counts", {})
    label_order = ["Very Satisfied", "Satisfied", "Dissatisfied", "Very Dissatisfied"]
    total_sat = sum(sat_counts.values()) or 1
    sat_rows = ""
    for lbl in label_order:
        count = sat_counts.get(lbl, 0)
        pct = count / total_sat * 100
        bar_w = int(pct)
        sat_rows += (
            f'<tr class="border-b border-stone-100">'
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(lbl)}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(str(count))}</td>'
            f'<td class="px-4 py-3">'
            f'  <div class="h-2 bg-stone-100 rounded">'
            f'    <div class="h-2 bg-blue-500 rounded" style="width:{bar_w}%"></div>'
            f'  </div>'
            f'</td>'
            f'</tr>'
        )
    if not sat_rows:
        sat_rows = '<tr><td class="px-4 py-3 text-stone-500" colspan="3">No survey data</td></tr>'

    # satisfaction trend table
    trend_rows = ""
    for entry in snapshot.get("satisfaction_trend", []):
        pct = f'{entry["positive_rate"] * 100:.0f}%'
        trend_rows += (
            f'<tr class="border-b border-stone-100">'
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(entry["month"]))}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(str(entry["total"]))}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(pct)}</td>'
            f'</tr>'
        )
    if not trend_rows:
        trend_rows = '<tr><td class="px-4 py-3 text-stone-500" colspan="3">No trend data</td></tr>'

    # top services table
    service_rows = ""
    for svc in snapshot.get("top_services", []):
        service_rows += (
            f'<tr class="border-b border-stone-100">'
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(svc.get("service_name") or ""))}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(str(svc.get("count") or 0))}</td>'
            f'</tr>'
        )
    if not service_rows:
        service_rows = '<tr><td class="px-4 py-3 text-stone-500" colspan="2">No service data</td></tr>'

    # plotly data — escape </script> injection
    box_data_json = json.dumps({
        "this_week": snapshot.get("completion_hours_this_week", []),
        "all_time": snapshot.get("completion_hours_all_time", []),
    }).replace("</", r"<\/")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>IT Executive Report</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.plot.ly/plotly-3.4.0.min.js"></script>
</head>
<body class="bg-stone-50 font-sans text-stone-900">
  <header class="bg-white border-b border-stone-200 px-8 py-6">
    <h1 class="text-2xl font-bold">IT Executive Report</h1>
    <p class="text-stone-500 text-sm mt-1">Week of {week_label} &nbsp;·&nbsp; Generated {generated_at}</p>
  </header>
  <main class="max-w-5xl mx-auto px-8 py-8 space-y-10">

    <!-- KPI Cards -->
    <section>
      <h2 class="text-lg font-semibold text-stone-700 mb-4">At a Glance</h2>
      <div class="grid grid-cols-2 md:grid-cols-3 gap-4">
        {kpi_cards}
      </div>
    </section>

    <!-- Completion Time Box/Whisker -->
    <section>
      <h2 class="text-lg font-semibold text-stone-700 mb-4">Ticket Completion Time (Business Hours)</h2>
      <div class="bg-white rounded-lg border border-stone-200 p-4">
        <div id="completion-chart" style="height:360px"></div>
      </div>
    </section>

    <!-- Survey Satisfaction -->
    <section>
      <h2 class="text-lg font-semibold text-stone-700 mb-4">Survey Satisfaction</h2>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div class="bg-white rounded-lg border border-stone-200 overflow-hidden">
          <table class="w-full">
            <thead class="bg-stone-50 border-b border-stone-200">
              <tr>
                <th class="px-4 py-3 text-left text-xs font-semibold text-stone-500 uppercase">Response</th>
                <th class="px-4 py-3 text-right text-xs font-semibold text-stone-500 uppercase">Count</th>
                <th class="px-4 py-3 text-left text-xs font-semibold text-stone-500 uppercase">Share</th>
              </tr>
            </thead>
            <tbody>{sat_rows}</tbody>
          </table>
        </div>
        <div class="bg-white rounded-lg border border-stone-200 overflow-hidden">
          <table class="w-full">
            <thead class="bg-stone-50 border-b border-stone-200">
              <tr>
                <th class="px-4 py-3 text-left text-xs font-semibold text-stone-500 uppercase">Month</th>
                <th class="px-4 py-3 text-right text-xs font-semibold text-stone-500 uppercase">Surveys</th>
                <th class="px-4 py-3 text-right text-xs font-semibold text-stone-500 uppercase">% Positive</th>
              </tr>
            </thead>
            <tbody>{trend_rows}</tbody>
          </table>
        </div>
      </div>
    </section>

    <!-- Top Services -->
    <section>
      <h2 class="text-lg font-semibold text-stone-700 mb-4">Top Request Categories</h2>
      <div class="bg-white rounded-lg border border-stone-200 overflow-hidden max-w-sm">
        <table class="w-full">
          <thead class="bg-stone-50 border-b border-stone-200">
            <tr>
              <th class="px-4 py-3 text-left text-xs font-semibold text-stone-500 uppercase">Service</th>
              <th class="px-4 py-3 text-right text-xs font-semibold text-stone-500 uppercase">Tickets</th>
            </tr>
          </thead>
          <tbody>{service_rows}</tbody>
        </table>
      </div>
    </section>

  </main>

  <script>
    window.EXEC_BOX_DATA = {box_data_json};
    (function () {{
      var d = window.EXEC_BOX_DATA;
      var traces = [
        {{
          type: 'box',
          y: d.all_time,
          name: 'All Time',
          marker: {{ color: '#94a3b8' }},
          boxpoints: false,
        }},
        {{
          type: 'box',
          y: d.this_week,
          name: 'This Week',
          marker: {{ color: '#3b82f6' }},
          boxpoints: false,
        }},
      ];
      var layout = {{
        margin: {{ t: 20, r: 20, b: 50, l: 60 }},
        yaxis: {{ title: 'Business Hours', zeroline: false }},
        legend: {{ orientation: 'h', y: -0.15 }},
        paper_bgcolor: 'white',
        plot_bgcolor: 'white',
      }};
      Plotly.newPlot('completion-chart', traces, layout, {{ responsive: true }});
    }})();
  </script>
</body>
</html>
"""


def write_executive_report(snapshot: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_executive_report_html(snapshot))
    return output_path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_executive_reporting.py -v`
Expected: all PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/dynamix_manager/reporting.py tests/test_executive_reporting.py
git commit -m "feat: add executive report HTML renderer with plotly box/whisker"
```

---

### Task 5: Pipeline `generate_executive_report`

**Files:**
- Modify: `src/dynamix_manager/pipeline.py`
- Modify: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_pipeline.py`:

```python
from dynamix_manager.pipeline import generate_executive_report


def test_generate_executive_report_writes_html_and_returns_summary(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame([
            {
                "ticket_id": 1,
                "created_at": "2026-03-30T10:00:00Z",
                "resolved_at": None,
                "service_name": "Printing",
            }
        ]),
    )
    replace_table(
        config.db_path,
        "survey_responses",
        pd.DataFrame([
            {
                "response_id": 1,
                "satisfaction_label": "Very Satisfied",
                "survey_completed_at": "2026-03-31T12:00:00Z",
            }
        ]),
    )

    result = generate_executive_report(config)

    report_path = tmp_path / "reports" / "executive_report.html"
    assert report_path.exists()
    assert "IT Executive Report" in report_path.read_text()
    assert result["report_written"] == 1
    assert "new_tickets_this_week" in result
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_generate_executive_report_writes_html_and_returns_summary -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `generate_executive_report`**

Add to `src/dynamix_manager/pipeline.py`, importing at the top:

```python
from dynamix_manager.executive import summarize_executive_snapshot
from dynamix_manager.reporting import (
    write_executive_report,
    write_survey_health_report,
    write_ticket_health_report,
    write_ticket_quality_report,
)
```

Add the function after `materialize_ticket_linked_surveys`:

```python
def generate_executive_report(config: RuntimeConfig) -> dict[str, object]:
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

    snapshot = summarize_executive_snapshot(tickets, surveys, days_off=days_off)

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

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pipeline.py::test_generate_executive_report_writes_html_and_returns_summary -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add src/dynamix_manager/pipeline.py tests/test_pipeline.py
git commit -m "feat: add generate_executive_report pipeline function"
```

---

### Task 6: CLI `generate-report` command

**Files:**
- Modify: `src/dynamix_manager/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py` (or create it):

```python
from dynamix_manager.cli import build_parser


def test_build_parser_supports_generate_report_command():
    parser = build_parser()
    parsed = parser.parse_args(["generate-report"])
    assert parsed.command == "generate-report"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py::test_build_parser_supports_generate_report_command -v`
Expected: FAIL — `SystemExit` or assertion error (subcommand not recognized)

- [ ] **Step 3: Add the subcommand and handler**

In `src/dynamix_manager/cli.py`, add inside `build_parser()` before `return parser`:

```python
    # generate-report
    subparsers.add_parser(
        "generate-report",
        help="Generate the executive snapshot report from cached data",
    )
```

In `main()`, add the handler after the `cache-days-off` block:

```python
    elif args.command == "generate-report":
        result = pipeline.generate_executive_report(config=config)
        print(json.dumps(result, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass

- [ ] **Step 6: Ruff check**

Run: `.venv/bin/python -m ruff check src/dynamix_manager/executive.py src/dynamix_manager/reporting.py src/dynamix_manager/pipeline.py src/dynamix_manager/cli.py`
Expected: `All checks passed!`

- [ ] **Step 7: Commit**

```bash
git add src/dynamix_manager/cli.py tests/test_cli.py
git commit -m "feat: add generate-report CLI subcommand for executive snapshot"
```

---

## Self-Review

**Spec coverage check:**
- ✅ New tickets created this week → `new_tickets_this_week` (Task 1)
- ✅ Average weekly tickets created → `avg_weekly_tickets_created` (Task 1)
- ✅ Survey summary stats (count at each satisfaction level) → `satisfaction_counts` (Task 3)
- ✅ Box and whisker of ticket completion time (working hours) — this week vs all time → `completion_hours_this_week` / `completion_hours_all_time` + Plotly renderer (Tasks 2, 4)
- ✅ Week-over-week delta → `week_over_week_delta_pct` (Task 1)
- ✅ SLA compliance rate → `sla_compliance_rate` (Task 3)
- ✅ Backlog age / stale open → `stale_open_count` (Task 3)
- ✅ Top service categories → `top_services` (Task 3)
- ✅ Satisfaction trend → `satisfaction_trend` (Task 3)
- ✅ Median first response → `median_first_response_hours` (Task 3)
- ✅ Unassigned open count → `unassigned_count` (Task 3)
- ✅ HTML report written to `reports/executive_report.html` (Tasks 4, 5)
- ✅ CLI `generate-report` subcommand (Task 6)

**Placeholder scan:** No TODOs, no "similar to Task N" references, no vague steps — all steps contain complete code.

**Type consistency:** `summarize_executive_snapshot(tickets, surveys, days_off, as_of)` used consistently in Tasks 1–3, 5. `render_executive_report_html(snapshot)` and `write_executive_report(snapshot, output_path)` consistent across Tasks 4, 5. `generate_executive_report(config)` consistent in Tasks 5, 6.
