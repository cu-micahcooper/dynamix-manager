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
