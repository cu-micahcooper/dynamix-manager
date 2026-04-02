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


_HOUR_BUCKETS: list[tuple[float, float, str]] = [
    (0, 8, "0–8h"),
    (8, 24, "8–24h"),
    (24, 40, "24–40h"),
    (40, 80, "40–80h"),
    (80, 160, "80–160h"),
    (160, 320, "160–320h"),
    (320, float("inf"), "320–1000h"),
]


def _bucket_completion_hours(hours: list[float], cap: float = 1000.0) -> list[dict]:
    """Bucket completion hours into fixed ranges, capping outliers at `cap`."""
    counts = {label: 0 for _, _, label in _HOUR_BUCKETS}
    for h in hours:
        h = min(h, cap)
        for lo, hi, label in _HOUR_BUCKETS:
            if lo <= h < hi:
                counts[label] += 1
                break
    return [{"label": label, "count": counts[label]} for _, _, label in _HOUR_BUCKETS]


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
    tdx_base_url: str | None = None,
) -> dict[str, object]:
    if as_of is None:
        as_of = pd.Timestamp.now("UTC")
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    ws = _week_start(as_of)
    prior_ws = ws - pd.Timedelta(days=7)
    prior_ws_as_of = as_of - pd.Timedelta(days=7)

    def _fmt(ts: pd.Timestamp) -> str:
        return ts.strftime("%b %-d")

    result: dict[str, object] = {
        "week_label": _week_label(ws),
        "report_generated_at": as_of.isoformat(),
        "as_of_label": _fmt(as_of),
        "week_range_label": f"{_fmt(ws)} – {_fmt(as_of)}",
        "prior_week_range_label": f"{_fmt(prior_ws)} – {_fmt(prior_ws_as_of)}",
        "tdx_base_url": tdx_base_url,
    }

    # --- ticket volume ---
    if tickets.empty or "created_at" not in tickets.columns:
        result["new_tickets_this_week"] = 0
        result["avg_weekly_tickets_created"] = 0.0
        result["week_over_week_delta_pct"] = None
    else:
        created = _parse_created_at(tickets)
        this_week_mask = (created >= ws) & (created <= as_of)
        prior_week_mask = (created >= prior_ws) & (created <= prior_ws_as_of)
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
        result["completion_hours_this_week"] = _bucket_completion_hours(this_week_hours)
        result["completion_hours_all_time"] = _bucket_completion_hours(all_time_hours)
    else:
        result["completion_hours_this_week"] = []
        result["completion_hours_all_time"] = []

    # --- survey stats ---
    _POSITIVE = {"Very Satisfied", "Satisfied"}
    if not surveys.empty and "satisfaction_label" in surveys.columns:
        if "survey_completed_at" in surveys.columns:
            _completed = pd.to_datetime(surveys["survey_completed_at"], utc=True, errors="coerce")
            _cutoff_30d = as_of - pd.Timedelta(days=30)
            _rated_mask = (_completed >= _cutoff_30d) & (_completed <= as_of) & surveys["satisfaction_label"].notna()
            result["satisfaction_counts"] = (
                surveys.loc[_rated_mask, "satisfaction_label"]
                .value_counts()
                .to_dict()
            )
            result["satisfaction_period_label"] = f"{_fmt(_cutoff_30d)} – {_fmt(as_of)}"
        else:
            result["satisfaction_counts"] = (
                surveys["satisfaction_label"].dropna().value_counts().to_dict()
            )
            result["satisfaction_period_label"] = "all time"
        if "survey_completed_at" in surveys.columns:
            sc = surveys.copy()
            sc["survey_completed_at"] = pd.to_datetime(
                sc["survey_completed_at"], utc=True, errors="coerce"
            )
            cutoff_6m = as_of - pd.DateOffset(months=6)
            recent = sc.loc[sc["survey_completed_at"] >= cutoff_6m].copy()
            if not recent.empty:
                recent["month"] = recent["survey_completed_at"].dt.tz_convert(None).dt.to_period("M")
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
        result["satisfaction_period_label"] = ""

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

    # --- detail columns available for drill-down ---
    _DETAIL_COLS = ["ticket_id", "ticket_title", "service_name", "team_name", "assignee_name", "created_at", "ticket_app_id"]

    def _ticket_detail(df: pd.DataFrame, limit: int = 200) -> list[dict]:
        cols = [c for c in _DETAIL_COLS if c in df.columns]
        return df[cols].head(limit).to_dict(orient="records")

    # new tickets this week detail
    if not tickets.empty and "created_at" in tickets.columns and this_week_mask.any():
        result["new_tickets_detail"] = _ticket_detail(
            tickets.loc[this_week_mask].sort_values("created_at", ascending=False)
        )
    else:
        result["new_tickets_detail"] = []

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
        open_created = created_col[open_mask]
        stale_idx = []
        for idx, c in open_created.items():
            if pd.isna(c):
                continue
            days = int(np.busday_count(c.date(), as_of.date(), holidays=holiday_array))
            if days > 5:
                stale_idx.append(idx)
        result["stale_open_count"] = len(stale_idx)
        result["stale_tickets_detail"] = _ticket_detail(
            tickets.loc[stale_idx].assign(
                _days=lambda df: [
                    int(np.busday_count(
                        pd.to_datetime(r, utc=True).date(), as_of.date(),
                        holidays=holiday_array
                    ))
                    for r in df["created_at"]
                ]
            ).sort_values("_days", ascending=False).drop(columns=["_days"])
        )
    else:
        result["stale_open_count"] = 0
        result["stale_tickets_detail"] = []

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

    # --- median first response (calendar hours) ---
    if (
        not tickets.empty
        and "created_at" in tickets.columns
        and "responded_at" in tickets.columns
    ):
        _created = pd.to_datetime(tickets["created_at"], utc=True, errors="coerce")
        _responded = pd.to_datetime(tickets["responded_at"], utc=True, errors="coerce")
        _mask = _responded.notna() & _created.notna() & (_responded > _created)
        if _mask.any():
            _hours = (_responded[_mask] - _created[_mask]).dt.total_seconds() / 3600
            result["median_first_response_hours"] = float(_hours.median())
        else:
            result["median_first_response_hours"] = None
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
        unassigned_mask = open_mask2 & (assignee.isna() | (assignee == ""))
        result["unassigned_count"] = int(unassigned_mask.sum())
        _unassigned_df = tickets.loc[unassigned_mask]
        if "created_at" in _unassigned_df.columns:
            _unassigned_df = _unassigned_df.sort_values("created_at", ascending=False)
        result["unassigned_tickets_detail"] = _ticket_detail(_unassigned_df)
    else:
        result["unassigned_count"] = 0
        result["unassigned_tickets_detail"] = []

    return result
