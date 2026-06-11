from __future__ import annotations

import pandas as pd

_YOUTRACK_DONE_STAGES = {
    "done",
    "closed",
    "resolved",
    "fixed",
    "completed",
    "cancelled",
    "canceled",
}
_SURVEY_SATISFACTION_ORDER = [
    "Great!",
    "Very Satisfied",
    "Satisfied",
    "Could have been better",
    "Unsatisfied",
    "Very Unsatisfied",
]
_HEADER_BURST_TAGLINES = [
    "THE HOME STRETCH HITS HARD",
    "IT'S THE FINAL COUNTDOWN",
    "Board of Trustee Edition",
    "THE CURTAIN CALL IS COMING",
    "ONE LAST ENCORE",
    "THE LAST TRACK IS SPINNING",
]


def _ordered_satisfaction_counts(
    surveys: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, int]:
    if surveys.empty or "satisfaction_label" not in surveys.columns or "survey_completed_at" not in surveys.columns:
        return {}

    survey_completed = _parse_dates(surveys["survey_completed_at"])
    satisfaction_mask = (
        survey_completed.notna()
        & (survey_completed >= start)
        & (survey_completed <= end)
        & surveys["satisfaction_label"].notna()
    )
    raw_counts = surveys.loc[satisfaction_mask, "satisfaction_label"].value_counts().to_dict()
    ordered_counts: dict[str, int] = {}
    for label in _SURVEY_SATISFACTION_ORDER:
        count = int(raw_counts.pop(label, 0))
        if count:
            ordered_counts[label] = count
    for label in sorted(raw_counts):
        ordered_counts[str(label)] = int(raw_counts[label])
    return ordered_counts


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime("%b %-d")


def _fmt_full_date(ts: pd.Timestamp) -> str:
    return ts.strftime("%b %-d, %Y")


def _survey_effective_start_label(
    surveys: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> str | None:
    if surveys.empty or "satisfaction_label" not in surveys.columns or "survey_completed_at" not in surveys.columns:
        return None
    completed = _parse_dates(surveys["survey_completed_at"])
    mask = (
        completed.notna()
        & (completed >= start)
        & (completed <= end)
        & surveys["satisfaction_label"].notna()
    )
    if not mask.any():
        return None
    earliest = pd.Timestamp(completed.loc[mask].min())
    if earliest <= start:
        return None
    return _fmt_full_date(earliest)


def _header_burst_tagline(as_of: pd.Timestamp) -> str:
    iso_week = int(as_of.isocalendar().week)
    return _HEADER_BURST_TAGLINES[(iso_week + 2) % len(_HEADER_BURST_TAGLINES)]


def _delta_pct(current: int, prior: int) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / prior * 100.0


def _build_7day_windows(
    as_of: pd.Timestamp, n: int = 8
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """Return n consecutive 7-day (start, end) pairs, oldest first, newest last.

    The final window is always (as_of - 7d, as_of).
    """
    windows = []
    for i in range(n, 0, -1):
        w_end = as_of - pd.Timedelta(days=7 * (i - 1))
        w_start = w_end - pd.Timedelta(days=7)
        windows.append((w_start, w_end))
    return windows


def _weekly_series(dates: pd.Series, windows: list[tuple]) -> list[int]:
    return [int(((dates >= s) & (dates <= e)).sum()) for s, e in windows]


_SENTINEL_THRESHOLD = pd.Timestamp("2000-01-01", tz="UTC")


def _count_open_at(
    created: pd.Series,
    resolved: pd.Series,
    snapshot: pd.Timestamp,
    *,
    is_currently_open: "pd.Series | None" = None,
) -> int:
    """Return the number of tickets open at a specific point in time.

    For tickets with a valid resolved_at (>= 2000), resolved_at is the sole
    historical signal: open iff resolved_at > snapshot.

    For tickets without a valid resolved_at (NaT or sentinel), we fall back to
    current status_class via `is_currently_open`. TDX sometimes closes tickets
    by changing status_class without setting resolved_at, so without this
    fallback those tickets would be counted as "open forever" inflating the
    sparkline by thousands.

    If `is_currently_open` is not provided, falls back to treating NaT
    resolved_at as "not yet resolved" (original behaviour, safe for tests).
    """
    was_created = created.notna() & (created <= snapshot)

    if is_currently_open is not None:
        # Tickets with a valid resolved_at → tracked by that date alone.
        # Tickets without one (NaT / sentinel) → use current status_class.
        has_valid_resolution = resolved.notna()
        not_yet_resolved = (
            (~has_valid_resolution & is_currently_open)            # no date → open now?
            | (has_valid_resolution & (resolved > snapshot))       # has date → not yet resolved?
        )
    else:
        not_yet_resolved = resolved.isna() | (resolved > snapshot)

    return int((was_created & not_yet_resolved).sum())


def _open_weekly_series(
    tickets: pd.DataFrame, windows: list[tuple]
) -> list[int]:
    """Return open-ticket count at the end of each (start, end) window."""
    n = len(tickets)
    created = (
        _parse_dates(tickets["created_at"])
        if "created_at" in tickets.columns
        else pd.Series([pd.NaT] * n, dtype="datetime64[ns, UTC]")
    )
    resolved = (
        _parse_dates(tickets["resolved_at"])
        if "resolved_at" in tickets.columns
        else pd.Series([pd.NaT] * n, dtype="datetime64[ns, UTC]")
    )
    # TDX uses C# DateTime.MinValue ("0001-01-01") as a sentinel for "no date".
    # Convert any resolved_at before year 2000 to NaT so it's treated as missing.
    resolved = resolved.where(resolved.isna() | (resolved >= _SENTINEL_THRESHOLD), other=pd.NaT)

    # Derive current open/closed from status_class so tickets that were closed
    # without a resolved_at date don't inflate every historical window.
    sc_col = (
        pd.to_numeric(tickets["status_class"], errors="coerce")
        if "status_class" in tickets.columns
        else pd.Series([float("nan")] * n)
    )
    is_currently_open = ~sc_col.isin({3, 4})

    return [_count_open_at(created, resolved, end, is_currently_open=is_currently_open) for _, end in windows]


def _parse_dates(col: pd.Series) -> pd.Series:
    return pd.to_datetime(col, utc=True, errors="coerce", format="mixed")


def _recent_survey_comments(
    surveys: pd.DataFrame,
    tickets: pd.DataFrame | None = None,
    *,
    period_start: pd.Timestamp,
    as_of: pd.Timestamp,
    limit: int | None = None,
) -> list[dict[str, str]]:
    if surveys.empty or "survey_completed_at" not in surveys.columns or "comment_text" not in surveys.columns:
        return []

    comments = surveys.copy()
    comments["survey_completed_at"] = _parse_dates(comments["survey_completed_at"])
    comments["comment_text"] = comments["comment_text"].astype("string").fillna("")
    mask = (
        comments["survey_completed_at"].notna()
        & (comments["survey_completed_at"] >= period_start)
        & (comments["survey_completed_at"] <= as_of)
        & comments["comment_text"].str.strip().ne("")
    )
    comments = comments.loc[mask].sort_values("survey_completed_at", ascending=False)
    if comments.empty:
        return []

    if (
        tickets is not None
        and not tickets.empty
        and "ticket_id" in comments.columns
        and "ticket_id" in tickets.columns
        and "ticket_title" in tickets.columns
    ):
        ticket_titles = tickets.loc[:, ["ticket_id", "ticket_title"]].drop_duplicates(
            subset=["ticket_id"], keep="last"
        )
        comments = comments.merge(ticket_titles, how="left", on="ticket_id")

    columns = [
        col
        for col in [
            "survey_completed_at",
            "satisfaction_label",
            "commenter_name",
            "ticket_title",
            "team_name",
            "comment_text",
        ]
        if col in comments.columns
    ]
    rows: list[dict[str, str]] = []
    selected = comments.loc[:, columns]
    if limit is not None:
        selected = selected.head(limit)
    for row in selected.to_dict(orient="records"):
        normalized: dict[str, str] = {}
        for key, value in row.items():
            if key == "survey_completed_at":
                normalized[key] = pd.Timestamp(value).isoformat()
            elif pd.isna(value):
                normalized[key] = ""
            else:
                normalized[key] = str(value)
        rows.append(normalized)
    return rows


def summarize_cfo_snapshot(
    tickets: pd.DataFrame,
    surveys: pd.DataFrame,
    youtrack_projects: list[dict] | dict[str, object] | None = None,
    as_of: pd.Timestamp | None = None,
    period_start: pd.Timestamp | None = None,
) -> dict[str, object]:
    """Aggregate data for the CFO Update email.

    All time periods use rolling 7-day windows relative to as_of:
      - current period: (as_of - 7d, as_of)
      - prior period:   (as_of - 14d, as_of - 7d)
      - year-ago period: same window shifted back 52 weeks

    YouTrack projects are passed in pre-fetched.
    """
    if as_of is None:
        as_of = pd.Timestamp.now("UTC")
    if as_of.tzinfo is None:
        as_of = as_of.tz_localize("UTC")

    if period_start is None:
        period_start = as_of - pd.Timedelta(days=7)
    else:
        period_start = pd.Timestamp(period_start)
        if period_start.tzinfo is None:
            period_start = period_start.tz_localize("UTC")
        else:
            period_start = period_start.tz_convert("UTC")
        if period_start >= as_of:
            period_start = as_of - pd.Timedelta(days=7)

    period_delta = as_of - period_start
    prior_end = period_start
    prior_start = prior_end - period_delta
    year_end = as_of - pd.Timedelta(weeks=52)
    year_start = year_end - period_delta
    trailing_year_start = as_of - pd.Timedelta(days=365)
    period_days = max(1, round(period_delta.total_seconds() / 86400))
    volume_period_label = "last 7 days" if period_days == 7 else f"last {period_days} days"
    prior_volume_period_label = (
        "prior 7 days" if period_days == 7 else f"prior {period_days} days"
    )

    result: dict[str, object] = {
        "period_label": f"{_fmt(period_start)} – {_fmt(as_of)}",
        "week_range_label": f"{_fmt(period_start)} – {_fmt(as_of)}",
        "prior_week_range_label": f"{_fmt(prior_start)} – {_fmt(prior_end)}",
        "year_ago_range_label": f"{_fmt(year_start)} – {_fmt(year_end)}",
        "volume_period_label": volume_period_label,
        "prior_volume_period_label": prior_volume_period_label,
        "report_generated_at": as_of.isoformat(),
        "as_of_label": _fmt(as_of),
        "header_burst_tagline": _header_burst_tagline(as_of),
    }

    # ── Ticket volume ──────────────────────────────────────────────────────────
    if not tickets.empty and "created_at" in tickets.columns:
        created = _parse_dates(tickets["created_at"])
        tw_created = int(((created >= period_start) & (created <= as_of)).sum())
        pw_created = int(((created >= prior_start) & (created <= prior_end)).sum())
        ya_created = int(((created >= year_start) & (created <= year_end)).sum())
    else:
        tw_created = pw_created = ya_created = 0

    if not tickets.empty and "resolved_at" in tickets.columns:
        resolved = _parse_dates(tickets["resolved_at"])
        tw_closed = int((resolved.notna() & (resolved >= period_start) & (resolved <= as_of)).sum())
        pw_closed = int((resolved.notna() & (resolved >= prior_start) & (resolved <= prior_end)).sum())
        ya_closed = int((resolved.notna() & (resolved >= year_start) & (resolved <= year_end)).sum())
    else:
        tw_closed = pw_closed = ya_closed = 0

    result.update(
        {
            "tickets_created_this_week": tw_created,
            "tickets_created_prior_week": pw_created,
            "tickets_created_year_ago": ya_created,
            "tickets_created_ww_delta_pct": _delta_pct(tw_created, pw_created),
            "tickets_created_yy_delta_pct": _delta_pct(tw_created, ya_created),
            "tickets_closed_this_week": tw_closed,
            "tickets_closed_prior_week": pw_closed,
            "tickets_closed_year_ago": ya_closed,
            "tickets_closed_ww_delta_pct": _delta_pct(tw_closed, pw_closed),
            "tickets_closed_yy_delta_pct": _delta_pct(tw_closed, ya_closed),
        }
    )

    # ── Total open tickets ─────────────────────────────────────────────────────
    # ── 8-period trends (rolling 7-day windows) ────────────────────────────────
    windows = _build_7day_windows(as_of, n=8)
    period_labels = [_fmt(s) for s, _ in windows]

    if not tickets.empty and "created_at" in tickets.columns:
        created_series = _parse_dates(tickets["created_at"])
        result["created_weekly"] = [
            {"week": lbl, "count": c}
            for lbl, c in zip(period_labels, _weekly_series(created_series, windows))
        ]
    else:
        result["created_weekly"] = [{"week": lbl, "count": 0} for lbl in period_labels]

    if not tickets.empty and "resolved_at" in tickets.columns:
        resolved_series = _parse_dates(tickets["resolved_at"])
        result["closed_weekly"] = [
            {"week": lbl, "count": c}
            for lbl, c in zip(period_labels, _weekly_series(resolved_series, windows))
        ]
    else:
        result["closed_weekly"] = [{"week": lbl, "count": 0} for lbl in period_labels]

    if not tickets.empty:
        open_counts = _open_weekly_series(tickets, windows)
        result["open_weekly"] = [
            {"week": lbl, "count": c}
            for lbl, c in zip(period_labels, open_counts)
        ]
        result["total_open_tickets"] = int(open_counts[-1])
        result["total_open_tickets_prior_week"] = int(open_counts[-2]) if len(open_counts) > 1 else 0
    else:
        result["open_weekly"] = [{"week": lbl, "count": 0} for lbl in period_labels]
        result["total_open_tickets"] = 0
        result["total_open_tickets_prior_week"] = 0

    result["total_open_tickets_ww_delta"] = (
        result["total_open_tickets"] - result["total_open_tickets_prior_week"]
    )

    # ── Survey: last 7 days ────────────────────────────────────────────────────
    _EASY = {"Very Easy", "Easy"}

    if not surveys.empty and "customer_effort_label" in surveys.columns and "survey_completed_at" in surveys.columns:
        completed = _parse_dates(surveys["survey_completed_at"])
        mask = (completed >= period_start) & (completed <= as_of) & surveys["customer_effort_label"].notna()
        effort_counts: dict = surveys.loc[mask, "customer_effort_label"].value_counts().to_dict()
        effort_total = sum(effort_counts.values())
        easy_rate: float | None = (
            sum(effort_counts.get(k, 0) for k in _EASY) / effort_total
            if effort_total > 0 else None
        )
    else:
        effort_counts = {}
        effort_total = 0
        easy_rate = None

    result["survey_effort_counts"] = effort_counts
    result["survey_effort_total"] = effort_total
    result["survey_easy_rate"] = easy_rate
    result["survey_period_label"] = f"{_fmt(period_start)} – {_fmt(as_of)}"
    ordered_counts = _ordered_satisfaction_counts(surveys, start=period_start, end=as_of)
    trailing_year_counts = _ordered_satisfaction_counts(
        surveys,
        start=trailing_year_start,
        end=as_of,
    )

    result["survey_satisfaction_counts"] = ordered_counts
    result["survey_satisfaction_total"] = int(sum(ordered_counts.values()))
    result["survey_trailing_year_label"] = f"{_fmt(trailing_year_start)} – {_fmt(as_of)}"
    result["survey_trailing_year_effective_start_label"] = _survey_effective_start_label(
        surveys,
        start=trailing_year_start,
        end=as_of,
    )
    result["survey_satisfaction_counts_trailing_year"] = trailing_year_counts
    result["survey_satisfaction_total_trailing_year"] = int(sum(trailing_year_counts.values()))
    result["survey_comments"] = _recent_survey_comments(
        surveys,
        tickets,
        period_start=period_start,
        as_of=as_of,
    )

    # ── YouTrack projects ──────────────────────────────────────────────────────
    project_rows: list[dict] = []
    sprint_issues: list[dict] = []
    if isinstance(youtrack_projects, dict):
        project_rows = list(youtrack_projects.get("projects") or [])
        sprint_issues = list(youtrack_projects.get("sprint_issues") or [])
    else:
        project_rows = list(youtrack_projects or [])

    project_movement = {
        "in_progress_count": len(project_rows),
        "new_this_week": 0,
        "completed_this_week": 0,
    }
    if sprint_issues:
        sprint_frame = pd.DataFrame(sprint_issues)
        created = (
            _parse_dates(sprint_frame["created_at"])
            if "created_at" in sprint_frame.columns
            else pd.Series([pd.NaT] * len(sprint_frame), dtype="datetime64[ns, UTC]")
        )
        resolved = (
            _parse_dates(sprint_frame["resolved_at"])
            if "resolved_at" in sprint_frame.columns
            else pd.Series([pd.NaT] * len(sprint_frame), dtype="datetime64[ns, UTC]")
        )
        updated = (
            _parse_dates(sprint_frame["updated_at"])
            if "updated_at" in sprint_frame.columns
            else pd.Series([pd.NaT] * len(sprint_frame), dtype="datetime64[ns, UTC]")
        )
        stage = (
            sprint_frame["stage"].astype("string").str.strip().str.lower()
            if "stage" in sprint_frame.columns
            else pd.Series([""] * len(sprint_frame), dtype="string")
        )
        project_movement["new_this_week"] = int(
            (created.notna() & (created >= period_start) & (created <= as_of)).sum()
        )
        new_issue_ids = set(
            sprint_frame.loc[
                created.notna() & (created >= period_start) & (created <= as_of),
                "id",
            ].astype("string")
        )
        completed_mask = (
            resolved.notna() & (resolved >= period_start) & (resolved <= as_of)
        ) | (
            resolved.isna()
            & stage.isin(_YOUTRACK_DONE_STAGES)
            & updated.notna()
            & (updated >= period_start)
            & (updated <= as_of)
        )
        project_movement["completed_this_week"] = int(completed_mask.sum())
        project_rows = [
            {
                **project,
                "is_new_this_week": str(project.get("id") or "") in new_issue_ids,
            }
            for project in project_rows
        ]
    result["youtrack_projects"] = project_rows
    result["youtrack_project_movement"] = project_movement

    return result
