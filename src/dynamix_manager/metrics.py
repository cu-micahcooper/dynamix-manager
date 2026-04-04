from __future__ import annotations

import numpy as np
import pandas as pd


SATISFACTION_SCORES = {
    "Very Dissatisfied": 1,
    "Dissatisfied": 2,
    "Satisfied": 4,
    "Very Satisfied": 5,
}
NEGATIVE_LABELS = {"Very Dissatisfied", "Dissatisfied"}


def _days_off_dates(days_off: pd.DataFrame | None) -> set[str]:
    if days_off is None or days_off.empty or "holiday_date" not in days_off.columns:
        return set()
    holiday_dates = pd.to_datetime(days_off["holiday_date"], utc=True, errors="coerce").dropna()
    return set(holiday_dates.dt.strftime("%Y-%m-%d"))


def _completion_per_day(
    frame: pd.DataFrame,
    group_columns: list[str],
    label_defaults: dict[str, str],
    days_off: pd.DataFrame | None = None,
) -> list[dict[str, str | int]]:
    if "resolved_at" not in frame.columns:
        return []

    resolved = frame.loc[frame["resolved_at"].notna()].copy()
    if resolved.empty:
        return []

    resolved["resolved_at"] = pd.to_datetime(resolved["resolved_at"], utc=True, errors="coerce")
    resolved = resolved.loc[resolved["resolved_at"].notna()]
    if resolved.empty:
        return []

    resolved["resolved_date"] = resolved["resolved_at"].dt.strftime("%Y-%m-%d")
    resolved = resolved.loc[resolved["resolved_at"].dt.weekday < 5]
    if resolved.empty:
        return []

    holiday_dates = _days_off_dates(days_off)
    if holiday_dates:
        resolved = resolved.loc[~resolved["resolved_date"].isin(holiday_dates)]
    if resolved.empty:
        return []

    for column, default in label_defaults.items():
        if column not in resolved.columns:
            resolved[column] = default
        resolved[column] = resolved[column].astype("string").fillna(default).replace("", default)

    grouped = (
        resolved.groupby(["resolved_date", *group_columns], dropna=False)
        .size()
        .reset_index(name="completed_tickets")
        .sort_values(["resolved_date", *group_columns])
    )
    return grouped.to_dict(orient="records")


def _top_counts(frame: pd.DataFrame, column: str, limit: int = 5) -> list[tuple[str, int]]:
    if column not in frame.columns:
        return []
    series = frame[column].dropna()
    if series.empty:
        return []
    counts = series.value_counts().head(limit)
    return [(str(label), int(count)) for label, count in counts.items()]


def _text_column(
    frame: pd.DataFrame,
    column: str,
    default: str,
) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="string")
    return frame[column].astype("string").fillna(default).replace("", default)


def _bool_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(False, index=frame.index, dtype="bool")
    return frame[column].fillna(False).astype("bool")


def _datetime_column(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns, UTC]")
    return pd.to_datetime(frame[column], utc=True, errors="coerce")


def _percentiles(series: pd.Series) -> dict[str, float | None]:
    values = series.dropna()
    if values.empty:
        return {"p50": None, "p75": None, "p90": None}
    return {
        "p50": float(values.quantile(0.50)),
        "p75": float(values.quantile(0.75)),
        "p90": float(values.quantile(0.90)),
    }


def _business_days_since(
    start: pd.Series,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
) -> pd.Series:
    timestamps = pd.to_datetime(start, utc=True, errors="coerce")
    end_date = np.datetime64(as_of.strftime("%Y-%m-%d"))
    holidays = np.array(sorted(holiday_dates), dtype="datetime64[D]") if holiday_dates else np.array([], dtype="datetime64[D]")

    def count_days(value: pd.Timestamp | pd.NaT) -> float:
        if pd.isna(value):
            return np.nan
        start_date = np.datetime64(value.strftime("%Y-%m-%d"))
        if holidays.size:
            return float(np.busday_count(start_date, end_date, holidays=holidays))
        return float(np.busday_count(start_date, end_date))

    return timestamps.apply(count_days)


def _business_days_until(
    end: pd.Series,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
) -> pd.Series:
    timestamps = pd.to_datetime(end, utc=True, errors="coerce")
    start_date = np.datetime64(as_of.strftime("%Y-%m-%d"))
    holidays = np.array(sorted(holiday_dates), dtype="datetime64[D]") if holiday_dates else np.array([], dtype="datetime64[D]")

    def count_days(value: pd.Timestamp | pd.NaT) -> float:
        if pd.isna(value):
            return np.nan
        end_date = np.datetime64(value.strftime("%Y-%m-%d"))
        if holidays.size:
            return float(np.busday_count(start_date, end_date, holidays=holidays))
        return float(np.busday_count(start_date, end_date))

    return timestamps.apply(count_days)


def _backlog_age_buckets(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
) -> list[dict[str, int]]:
    open_tickets = frame.loc[frame["resolved_at"].isna()].copy() if "resolved_at" in frame.columns else frame.iloc[0:0].copy()
    if open_tickets.empty or "created_at" not in open_tickets.columns:
        return []

    open_tickets["business_age_days"] = _business_days_since(open_tickets["created_at"], as_of, holiday_dates)
    buckets = [
        ("0-1 business days", 0, 1),
        ("2 business days", 2, 2),
        ("3-5 business days", 3, 5),
        ("6-10 business days", 6, 10),
        ("11+ business days", 11, None),
    ]
    results: list[dict[str, int]] = []
    for label, lower, upper in buckets:
        if upper is None:
            count = int((open_tickets["business_age_days"] >= lower).sum())
        else:
            count = int(open_tickets["business_age_days"].between(lower, upper).sum())
        if count:
            results.append({"bucket": label, "tickets": count})
    return results


def _public_interaction_counts(interactions: pd.DataFrame) -> pd.DataFrame:
    if interactions is None or interactions.empty:
        return pd.DataFrame(
            columns=["ticket_id", "touch_count", "it_touch_count", "client_touch_count"]
        )

    frame = interactions.copy()
    frame["created_at"] = pd.to_datetime(frame["created_at"], utc=True, errors="coerce")
    frame["body"] = frame["body"].astype("string").fillna("")
    frame["is_private"] = frame["is_private"].fillna(False)
    frame["is_communication"] = frame["is_communication"].fillna(False)
    public_interactions = frame.loc[
        (frame["created_at"].notna())
        & (~frame["is_private"])
        & (frame["body"].str.strip() != "")
        & (
            frame["interaction_source"].eq("reply")
            | frame["is_communication"]
            | frame["update_type"].eq(1)
        )
    ]
    if public_interactions.empty:
        return pd.DataFrame(
            columns=["ticket_id", "touch_count", "it_touch_count", "client_touch_count"]
        )

    grouped = (
        public_interactions.groupby("ticket_id", dropna=False)
        .agg(
            touch_count=("interaction_id", "count"),
            it_touch_count=("actor_type", lambda s: int((s == "it").sum())),
            client_touch_count=("actor_type", lambda s: int((s == "client").sum())),
        )
        .reset_index()
    )
    return grouped


def _high_touch_tickets(
    tickets: pd.DataFrame,
    interactions: pd.DataFrame | None,
    limit: int = 10,
) -> list[dict[str, object]]:
    counts = _public_interaction_counts(interactions)
    if counts.empty:
        return []
    merged = tickets.merge(counts, how="left", on="ticket_id")
    merged[["touch_count", "it_touch_count", "client_touch_count"]] = merged[
        ["touch_count", "it_touch_count", "client_touch_count"]
    ].fillna(0)
    for column, default in {
        "ticket_title": "",
        "team_name": "Unassigned team",
        "service_name": "Unassigned service",
    }.items():
        if column not in merged.columns:
            merged[column] = default
        merged[column] = merged[column].astype("string").fillna(default).replace("", default)
    merged = merged.sort_values(
        ["touch_count", "it_touch_count", "ticket_id"],
        ascending=[False, False, True],
    )
    return merged.loc[
        :,
        [
            "ticket_id",
            "ticket_title",
            "team_name",
            "service_name",
            "touch_count",
            "it_touch_count",
            "client_touch_count",
        ],
    ].head(limit).to_dict(orient="records")


def _team_quality_hotspots(quality_flags: pd.DataFrame | None, limit: int = 10) -> list[dict[str, object]]:
    if quality_flags is None or quality_flags.empty or "team_name" not in quality_flags.columns:
        return []
    frame = quality_flags.copy()
    for column in [
        "client_last_interaction_flag",
        "it_follow_up_without_client_response_flag",
        "stale_public_update_flag",
        "private_activity_since_last_public_flag",
    ]:
        if column not in frame.columns:
            frame[column] = False
        frame[column] = frame[column].fillna(False)
    grouped = (
        frame.groupby("team_name", dropna=False)
        .agg(
            tickets=("ticket_id", "count"),
            client_last_interaction=("client_last_interaction_flag", lambda s: int(s.fillna(False).sum())),
            repeated_it_followup=("it_follow_up_without_client_response_flag", lambda s: int(s.fillna(False).sum())),
            stale_public_updates=("stale_public_update_flag", lambda s: int(s.fillna(False).sum())),
            private_activity_since_last_public=("private_activity_since_last_public_flag", lambda s: int(s.fillna(False).sum())),
            average_interaction_count=("interaction_count", "mean"),
        )
        .reset_index()
        .sort_values(
            ["repeated_it_followup", "stale_public_updates", "client_last_interaction", "tickets"],
            ascending=[False, False, False, False],
        )
    )
    return grouped.head(limit).to_dict(orient="records")


def _sla_summary(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
) -> dict[str, object]:
    if frame.empty:
        return {
            "covered_tickets": 0,
            "violated_tickets": 0,
            "respond_breached": 0,
            "resolve_breached": 0,
            "open_near_respond_due": 0,
            "open_near_resolve_due": 0,
            "coverage_rate": 0.0,
            "violation_rate": 0.0,
        }

    sla_name = _text_column(frame, "sla_name", "")
    has_sla = (
        sla_name.ne("")
        | _datetime_column(frame, "respond_by_at").notna()
        | _datetime_column(frame, "resolve_by_at").notna()
        | _bool_column(frame, "is_sla_violated")
        | _bool_column(frame, "is_sla_respond_by_violated")
        | _bool_column(frame, "is_sla_resolve_by_violated")
    )
    covered = frame.loc[has_sla].copy()
    if covered.empty:
        return {
            "covered_tickets": 0,
            "violated_tickets": 0,
            "respond_breached": 0,
            "resolve_breached": 0,
            "open_near_respond_due": 0,
            "open_near_resolve_due": 0,
            "coverage_rate": 0.0,
            "violation_rate": 0.0,
        }

    resolved_mask = (
        pd.to_datetime(covered["resolved_at"], utc=True, errors="coerce").notna()
        if "resolved_at" in covered.columns
        else pd.Series(False, index=covered.index)
    )
    respond_due_days = _business_days_until(_datetime_column(covered, "respond_by_at"), as_of, holiday_dates)
    resolve_due_days = _business_days_until(_datetime_column(covered, "resolve_by_at"), as_of, holiday_dates)
    violated = _bool_column(covered, "is_sla_violated")
    respond_violated = _bool_column(covered, "is_sla_respond_by_violated")
    resolve_violated = _bool_column(covered, "is_sla_resolve_by_violated")
    open_near_respond_due = (~resolved_mask) & (~respond_violated) & respond_due_days.between(0, 1, inclusive="both")
    open_near_resolve_due = (~resolved_mask) & (~resolve_violated) & resolve_due_days.between(0, 1, inclusive="both")

    return {
        "covered_tickets": int(len(covered)),
        "violated_tickets": int(violated.sum()),
        "respond_breached": int(respond_violated.sum()),
        "resolve_breached": int(resolve_violated.sum()),
        "open_near_respond_due": int(open_near_respond_due.sum()),
        "open_near_resolve_due": int(open_near_resolve_due.sum()),
        "coverage_rate": float(len(covered) / len(frame)) if len(frame) else 0.0,
        "violation_rate": float(violated.sum() / len(covered)) if len(covered) else 0.0,
    }


def _sla_hotspots(frame: pd.DataFrame, limit: int = 10) -> list[dict[str, object]]:
    if frame.empty or "team_name" not in frame.columns:
        return []
    covered = frame.loc[
        _text_column(frame, "sla_name", "").ne("")
        | _bool_column(frame, "is_sla_violated")
        | _bool_column(frame, "is_sla_respond_by_violated")
        | _bool_column(frame, "is_sla_resolve_by_violated")
    ].copy()
    if covered.empty:
        return []
    covered["team_name"] = _text_column(covered, "team_name", "Unassigned team")
    grouped = (
        covered.groupby("team_name", dropna=False)
        .agg(
            covered_tickets=("ticket_id", "count"),
            violated_tickets=("is_sla_violated", lambda s: int(s.fillna(False).sum())),
            respond_breached=("is_sla_respond_by_violated", lambda s: int(s.fillna(False).sum())),
            resolve_breached=("is_sla_resolve_by_violated", lambda s: int(s.fillna(False).sum())),
        )
        .reset_index()
        .sort_values(["violated_tickets", "resolve_breached", "covered_tickets"], ascending=[False, False, False])
    )
    return grouped.head(limit).to_dict(orient="records")


def _hygiene_summary(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty:
        return {
            "missing_title": 0,
            "missing_service": 0,
            "missing_team": 0,
            "open_unassigned": 0,
            "missing_priority": 0,
        }
    resolved = (
        pd.to_datetime(frame["resolved_at"], utc=True, errors="coerce").notna()
        if "resolved_at" in frame.columns
        else pd.Series(False, index=frame.index)
    )
    return {
        "missing_title": int(_text_column(frame, "ticket_title", "").eq("").sum()),
        "missing_service": int(_text_column(frame, "service_name", "").eq("").sum()),
        "missing_team": int(_text_column(frame, "team_name", "").eq("").sum()),
        "open_unassigned": int((~resolved & _text_column(frame, "assignee_name", "").eq("")).sum()),
        "missing_priority": int(_text_column(frame, "priority_name", "").eq("").sum()),
    }


def _hygiene_tickets(frame: pd.DataFrame, limit: int = 10) -> list[dict[str, object]]:
    if frame.empty:
        return []
    tickets = frame.copy()
    tickets["ticket_title"] = _text_column(tickets, "ticket_title", "")
    tickets["team_name"] = _text_column(tickets, "team_name", "Unassigned team")
    tickets["service_name"] = _text_column(tickets, "service_name", "Unassigned service")
    tickets["assignee_name"] = _text_column(tickets, "assignee_name", "Unassigned")
    tickets["priority_name"] = _text_column(tickets, "priority_name", "")
    resolved = (
        pd.to_datetime(tickets["resolved_at"], utc=True, errors="coerce").notna()
        if "resolved_at" in tickets.columns
        else pd.Series(False, index=tickets.index)
    )
    tickets["issues"] = (
        tickets["ticket_title"].eq("").astype(int)
        + tickets["service_name"].eq("Unassigned service").astype(int)
        + tickets["team_name"].eq("Unassigned team").astype(int)
        + ((~resolved) & tickets["assignee_name"].eq("Unassigned")).astype(int)
        + tickets["priority_name"].eq("").astype(int)
    )
    flagged = tickets.loc[tickets["issues"] > 0].sort_values(["issues", "ticket_id"], ascending=[False, True])
    return flagged.loc[:, ["ticket_id", "ticket_title", "team_name", "service_name", "assignee_name", "issues"]].head(limit).to_dict(orient="records")


def _quality_adjusted_sla(
    frame: pd.DataFrame,
    quality_flags: pd.DataFrame | None,
    interactions: pd.DataFrame | None,
) -> dict[str, int]:
    if frame.empty:
        return {
            "breached_and_high_touch": 0,
            "breached_and_client_waiting": 0,
            "breached_and_repeated_it_followup": 0,
        }
    breached_ids = set(
        frame.loc[_bool_column(frame, "is_sla_violated"), "ticket_id"].dropna().astype(int).tolist()
    )
    if not breached_ids:
        return {
            "breached_and_high_touch": 0,
            "breached_and_client_waiting": 0,
            "breached_and_repeated_it_followup": 0,
        }
    touch_counts = _public_interaction_counts(interactions)
    high_touch_ids = set(
        touch_counts.loc[touch_counts["touch_count"] >= 4, "ticket_id"].dropna().astype(int).tolist()
    )
    client_waiting_ids: set[int] = set()
    repeated_followup_ids: set[int] = set()
    if quality_flags is not None and not quality_flags.empty:
        if "client_last_interaction_flag" in quality_flags.columns:
            client_waiting_ids = set(
                quality_flags.loc[
                    quality_flags["client_last_interaction_flag"].fillna(False),
                    "ticket_id",
                ].dropna().astype(int).tolist()
            )
        if "it_follow_up_without_client_response_flag" in quality_flags.columns:
            repeated_followup_ids = set(
                quality_flags.loc[
                    quality_flags["it_follow_up_without_client_response_flag"].fillna(False),
                    "ticket_id",
                ].dropna().astype(int).tolist()
            )
    return {
        "breached_and_high_touch": len(breached_ids & high_touch_ids),
        "breached_and_client_waiting": len(breached_ids & client_waiting_ids),
        "breached_and_repeated_it_followup": len(breached_ids & repeated_followup_ids),
    }


def _member_backlog_hotspots(
    frame: pd.DataFrame,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
    limit: int = 10,
) -> list[dict[str, object]]:
    if frame.empty:
        return []
    open_tickets = frame.copy()
    if "resolved_at" in open_tickets.columns:
        open_tickets = open_tickets.loc[pd.to_datetime(open_tickets["resolved_at"], utc=True, errors="coerce").isna()]
    if open_tickets.empty or "created_at" not in open_tickets.columns:
        return []
    open_tickets["team_name"] = _text_column(open_tickets, "team_name", "Unassigned team")
    open_tickets["assignee_name"] = _text_column(open_tickets, "assignee_name", "Unassigned")
    open_tickets["business_age_days"] = _business_days_since(open_tickets["created_at"], as_of, holiday_dates)
    grouped = (
        open_tickets.groupby(["team_name", "assignee_name"], dropna=False)
        .agg(
            backlog_tickets=("ticket_id", "count"),
            average_backlog_age=("business_age_days", "mean"),
        )
        .reset_index()
        .sort_values(["backlog_tickets", "average_backlog_age", "team_name", "assignee_name"], ascending=[False, False, True, True])
    )
    return grouped.head(limit).to_dict(orient="records")


def _top_recurrent_titles(frame: pd.DataFrame, limit: int = 10) -> list[dict[str, object]]:
    if frame.empty or "ticket_title" not in frame.columns:
        return []
    titles = frame.copy()
    titles["ticket_title"] = titles["ticket_title"].astype("string").fillna("").str.strip()
    if "service_name" not in titles.columns:
        titles["service_name"] = "Unassigned service"
    titles["service_name"] = titles["service_name"].astype("string").fillna("Unassigned service").replace("", "Unassigned service")
    titles = titles.loc[titles["ticket_title"] != ""]
    if titles.empty:
        return []
    titles["normalized_title"] = titles["ticket_title"].str.casefold().str.replace(r"\s+", " ", regex=True)
    grouped = (
        titles.groupby(["service_name", "normalized_title"], dropna=False)
        .agg(
            tickets=("ticket_id", "count"),
            ticket_title=("ticket_title", "first"),
        )
        .reset_index()
        .sort_values(["tickets", "ticket_title"], ascending=[False, True])
    )
    grouped = grouped.loc[grouped["tickets"] > 1]
    return grouped.loc[:, ["ticket_title", "service_name", "tickets"]].head(limit).to_dict(orient="records")


def _stale_open_tickets(
    tickets: pd.DataFrame,
    quality_flags: pd.DataFrame | None,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
    threshold_business_days: int = 3,
    limit: int = 10,
) -> list[dict[str, object]]:
    if "status_class" in tickets.columns:
        open_tickets = tickets.loc[~pd.to_numeric(tickets["status_class"], errors="coerce").isin({3, 4})].copy()
    elif "resolved_at" in tickets.columns:
        open_tickets = tickets.loc[pd.to_datetime(tickets["resolved_at"], utc=True, errors="coerce").isna()].copy()
    else:
        open_tickets = tickets.iloc[0:0].copy()
    if open_tickets.empty:
        return []
    for column, default in {
        "ticket_title": "",
        "team_name": "Unassigned team",
        "service_name": "Unassigned service",
    }.items():
        if column not in open_tickets.columns:
            open_tickets[column] = default
        open_tickets[column] = open_tickets[column].astype("string").fillna(default).replace("", default)

    if quality_flags is not None and not quality_flags.empty:
        open_tickets = open_tickets.merge(
            quality_flags.loc[:, [col for col in ["ticket_id", "last_public_interaction_at"] if col in quality_flags.columns]],
            how="left",
            on="ticket_id",
        )
    else:
        open_tickets["last_public_interaction_at"] = pd.NA

    reference = open_tickets["last_public_interaction_at"].fillna(
        _datetime_column(open_tickets, "modified_at")
    ).fillna(
        _datetime_column(open_tickets, "created_at")
    )
    open_tickets["stale_business_days"] = _business_days_since(reference, as_of, holiday_dates)
    stale = open_tickets.loc[open_tickets["stale_business_days"] > threshold_business_days].sort_values(
        ["stale_business_days", "ticket_id"],
        ascending=[False, True],
    )
    columns = ["ticket_id", "ticket_title", "team_name", "service_name", "stale_business_days"]
    if "ticket_app_id" in stale.columns:
        columns.append("ticket_app_id")
    return stale.loc[:, columns].head(limit).to_dict(orient="records")


def _recent_comments(frame: pd.DataFrame, limit: int = 5) -> list[dict[str, str | None]]:
    if "comment_text" not in frame.columns:
        return []
    comments = frame.copy()
    comments["comment_text"] = comments["comment_text"].astype("string").fillna("")
    comments = comments.loc[comments["comment_text"].str.strip() != ""]
    if comments.empty:
        return []
    if "survey_completed_at" in comments.columns:
        comments = comments.sort_values("survey_completed_at", ascending=False)
    return comments.loc[:, [col for col in ["survey_completed_at", "satisfaction_label", "team_name", "comment_text"] if col in comments.columns]].head(limit).to_dict(orient="records")


def summarize_survey_health(frame: pd.DataFrame) -> dict[str, object]:
    total_responses = len(frame)
    linked_responses = int(frame["ticket_linked"].fillna(False).sum()) if "ticket_linked" in frame.columns else 0
    comment_count = 0
    if "comment_text" in frame.columns:
        comment_series = frame["comment_text"].astype("string")
        comment_count = int(comment_series.fillna("").str.strip().ne("").sum())

    scores = (
        frame["satisfaction_label"].map(SATISFACTION_SCORES).dropna()
        if "satisfaction_label" in frame.columns
        else pd.Series(dtype="float64")
    )
    negative_rate = 0.0
    if total_responses and "satisfaction_label" in frame.columns:
        negative_rate = (
            frame["satisfaction_label"].isin(NEGATIVE_LABELS).fillna(False).sum() / total_responses
        )

    return {
        "total_responses": total_responses,
        "linked_responses": linked_responses,
        "comment_count": comment_count,
        "comment_rate": (comment_count / total_responses) if total_responses else 0.0,
        "average_score": float(scores.mean()) if not scores.empty else None,
        "negative_response_rate": float(negative_rate),
        "satisfaction_counts": _top_counts(frame, "satisfaction_label"),
        "top_teams": _top_counts(
            frame.loc[frame["ticket_linked"].fillna(False)] if "ticket_linked" in frame.columns else frame,
            "team_name",
        ),
        "recent_comments": _recent_comments(frame),
    }


def summarize_ticket_health(
    frame: pd.DataFrame,
    days_off: pd.DataFrame | None = None,
    quality_flags: pd.DataFrame | None = None,
    interactions: pd.DataFrame | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> dict[str, object]:
    as_of_ts = pd.Timestamp.now("UTC") if as_of is None else pd.to_datetime(as_of, utc=True, errors="coerce")
    holiday_dates = _days_off_dates(days_off)
    total_tickets = len(frame)
    resolved_mask = frame["resolved_at"].notna() if "resolved_at" in frame.columns else pd.Series(False, index=frame.index)
    resolved_tickets = int(resolved_mask.sum())
    backlog_tickets = total_tickets - resolved_tickets

    response_hours = frame["response_time_hours"].dropna() if "response_time_hours" in frame.columns else pd.Series(dtype="float64")
    resolution_hours = frame["resolution_time_hours"].dropna() if "resolution_time_hours" in frame.columns else pd.Series(dtype="float64")
    stale_open_tickets = _stale_open_tickets(frame, quality_flags, as_of_ts, holiday_dates)
    touch_counts = _public_interaction_counts(interactions)
    average_touches = float(touch_counts["touch_count"].mean()) if not touch_counts.empty else None
    quality_counts = {
        "client_last_interaction": int(quality_flags["client_last_interaction_flag"].fillna(False).sum())
        if quality_flags is not None and "client_last_interaction_flag" in quality_flags.columns
        else 0,
        "repeated_it_followup": int(quality_flags["it_follow_up_without_client_response_flag"].fillna(False).sum())
        if quality_flags is not None and "it_follow_up_without_client_response_flag" in quality_flags.columns
        else 0,
        "stale_open_tickets": len(stale_open_tickets),
        "stale_public_updates": int(quality_flags["stale_public_update_flag"].fillna(False).sum())
        if quality_flags is not None and "stale_public_update_flag" in quality_flags.columns
        else 0,
        "private_activity_since_last_public": int(quality_flags["private_activity_since_last_public_flag"].fillna(False).sum())
        if quality_flags is not None and "private_activity_since_last_public_flag" in quality_flags.columns
        else 0,
    }
    sla_summary = _sla_summary(frame, as_of_ts, holiday_dates)
    hygiene_counts = _hygiene_summary(frame)

    return {
        "total_tickets": total_tickets,
        "resolved_tickets": resolved_tickets,
        "backlog_tickets": backlog_tickets,
        "average_response_hours": float(response_hours.mean()) if not response_hours.empty else None,
        "average_resolution_hours": float(resolution_hours.mean()) if not resolution_hours.empty else None,
        "response_percentiles_hours": _percentiles(response_hours),
        "resolution_percentiles_hours": _percentiles(resolution_hours),
        "top_teams": _top_counts(frame, "team_name"),
        "top_services": _top_counts(frame, "service_name"),
        "average_touches_per_ticket": average_touches,
        "quality_counts": quality_counts,
        "sla_summary": sla_summary,
        "sla_hotspots": _sla_hotspots(frame),
        "hygiene_counts": hygiene_counts,
        "hygiene_tickets": _hygiene_tickets(frame),
        "quality_adjusted_sla": _quality_adjusted_sla(frame, quality_flags, interactions),
        "backlog_age_buckets": _backlog_age_buckets(frame, as_of_ts, holiday_dates),
        "stale_open_tickets": stale_open_tickets,
        "high_touch_tickets": _high_touch_tickets(frame, interactions),
        "team_quality_hotspots": _team_quality_hotspots(quality_flags),
        "member_backlog_hotspots": _member_backlog_hotspots(frame, as_of_ts, holiday_dates),
        "top_recurrent_titles": _top_recurrent_titles(frame),
        "daily_team_completion": _completion_per_day(
            frame,
            group_columns=["team_name"],
            label_defaults={"team_name": "Unassigned team"},
            days_off=days_off,
        ),
        "daily_member_completion": _completion_per_day(
            frame,
            group_columns=["team_name", "assignee_name"],
            label_defaults={
                "team_name": "Unassigned team",
                "assignee_name": "Unassigned",
            },
            days_off=days_off,
        ),
    }
