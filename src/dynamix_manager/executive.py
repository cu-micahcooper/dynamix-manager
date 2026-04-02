from __future__ import annotations

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
