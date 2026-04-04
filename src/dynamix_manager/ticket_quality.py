from __future__ import annotations

from collections.abc import Iterable, Mapping

import numpy as np
import pandas as pd


def _names_match(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return left.strip().casefold() == right.strip().casefold()


def _actor_type(
    created_uid: str | None,
    created_full_name: str | None,
    requestor_uid: str | None,
    requestor_name: str | None,
) -> str:
    if created_uid and requestor_uid and created_uid == requestor_uid:
        return "client"
    if _names_match(created_full_name, requestor_name):
        return "client"
    return "it"


def normalize_ticket_quality_ticket_rows(rows: Iterable[dict]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "ticket_id": row["ID"],
                "ticket_title": row.get("Title"),
                "status_name": row.get("StatusName"),
                "status_class": row.get("StatusClass"),
                "service_name": row.get("ServiceName"),
                "team_name": row.get("ResponsibleGroupName"),
                "assignee_name": row.get("RespondingFullName"),
                "requestor_name": row.get("RequestorName"),
                "requestor_uid": row.get("RequestorUid"),
                "created_at": row.get("CreatedDate"),
                "modified_at": row.get("ModifiedDate"),
                "resolved_at": row.get("CompletedDate"),
            }
        )
    return pd.DataFrame(normalized)


def normalize_ticket_quality_feed_rows(
    ticket_id: int,
    requestor_uid: str | None,
    requestor_name: str | None,
    feed_entries: Iterable[dict],
    detailed_feed_entries: Mapping[int, dict] | None = None,
) -> pd.DataFrame:
    detailed_feed_entries = detailed_feed_entries or {}
    normalized: list[dict[str, object]] = []

    for entry in feed_entries:
        created_uid = entry.get("CreatedUid")
        created_full_name = entry.get("CreatedFullName")
        normalized.append(
            {
                "ticket_id": ticket_id,
                "interaction_id": entry.get("ID"),
                "parent_interaction_id": None,
                "created_at": entry.get("CreatedDate"),
                "created_uid": created_uid,
                "created_full_name": created_full_name,
                "body": entry.get("Body"),
                "is_private": entry.get("IsPrivate"),
                "is_communication": entry.get("IsCommunication"),
                "update_type": entry.get("UpdateType"),
                "actor_type": _actor_type(
                    created_uid=created_uid,
                    created_full_name=created_full_name,
                    requestor_uid=requestor_uid,
                    requestor_name=requestor_name,
                ),
                "interaction_source": "entry",
            }
        )

        replies = entry.get("Replies") or detailed_feed_entries.get(entry.get("ID"), {}).get("Replies") or []
        for reply in replies:
            reply_uid = reply.get("CreatedUid")
            reply_name = reply.get("CreatedFullName")
            normalized.append(
                {
                    "ticket_id": ticket_id,
                    "interaction_id": reply.get("ID"),
                    "parent_interaction_id": entry.get("ID"),
                    "created_at": reply.get("CreatedDate"),
                    "created_uid": reply_uid,
                    "created_full_name": reply_name,
                    "body": reply.get("Body"),
                    "is_private": entry.get("IsPrivate", False),
                    "is_communication": entry.get("IsCommunication", True),
                    "update_type": entry.get("UpdateType"),
                    "actor_type": _actor_type(
                        created_uid=reply_uid,
                        created_full_name=reply_name,
                        requestor_uid=requestor_uid,
                        requestor_name=requestor_name,
                    ),
                    "interaction_source": "reply",
                }
            )

    return pd.DataFrame(normalized)


def _meaningful_interactions(interactions: pd.DataFrame) -> pd.DataFrame:
    if interactions.empty:
        return interactions

    frame = interactions.copy()
    frame["created_at"] = pd.to_datetime(frame["created_at"], utc=True, errors="coerce")
    frame["body"] = frame["body"].astype("string").fillna("")
    frame["is_private"] = frame["is_private"].fillna(False)
    frame["is_communication"] = frame["is_communication"].fillna(False)

    return frame.loc[
        (frame["created_at"].notna())
        & (~frame["is_private"])
        & (frame["body"].str.strip() != "")
        & (
            frame["interaction_source"].eq("reply")
            | frame["is_communication"]
            | frame["update_type"].eq(1)
        )
    ].sort_values(["ticket_id", "created_at", "interaction_id"])


def _normalized_interactions(interactions: pd.DataFrame) -> pd.DataFrame:
    if interactions.empty:
        return interactions
    frame = interactions.copy()
    frame["created_at"] = pd.to_datetime(frame["created_at"], utc=True, errors="coerce")
    frame["body"] = frame["body"].astype("string").fillna("")
    frame["is_private"] = frame["is_private"].fillna(False)
    frame["is_communication"] = frame["is_communication"].fillna(False)
    return frame.sort_values(["ticket_id", "created_at", "interaction_id"])


def _days_off_dates(days_off: pd.DataFrame | None) -> set[str]:
    if days_off is None or days_off.empty or "holiday_date" not in days_off.columns:
        return set()
    holiday_dates = pd.to_datetime(days_off["holiday_date"], utc=True, errors="coerce").dropna()
    return set(holiday_dates.dt.strftime("%Y-%m-%d"))


def _business_days_since(
    start: pd.Timestamp | str | None,
    as_of: pd.Timestamp,
    holiday_dates: set[str],
) -> int | None:
    timestamp = pd.to_datetime(start, utc=True, errors="coerce")
    if pd.isna(timestamp):
        return None
    start_date = np.datetime64(timestamp.strftime("%Y-%m-%d"))
    end_date = np.datetime64(as_of.strftime("%Y-%m-%d"))
    holidays = np.array(sorted(holiday_dates), dtype="datetime64[D]") if holiday_dates else np.array([], dtype="datetime64[D]")
    if holidays.size:
        return int(np.busday_count(start_date, end_date, holidays=holidays))
    return int(np.busday_count(start_date, end_date))


def _it_follow_up_streak(interactions: pd.DataFrame) -> int:
    streak = 0
    for actor_type in reversed(interactions["actor_type"].tolist()):
        if actor_type == "client":
            break
        if actor_type == "it":
            streak += 1
    return streak


def build_ticket_quality_flags(
    tickets: pd.DataFrame,
    interactions: pd.DataFrame,
    follow_up_limit: int = 3,
    stale_public_update_days: int = 3,
    days_off: pd.DataFrame | None = None,
    as_of: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    as_of_ts = pd.Timestamp.now("UTC") if as_of is None else pd.to_datetime(as_of, utc=True, errors="coerce")
    holiday_dates = _days_off_dates(days_off)
    flag_columns = [
        *list(tickets.columns),
        "last_public_interaction_at",
        "last_private_interaction_at",
        "last_private_interaction_by",
        "last_public_interaction_actor_type",
        "last_public_interaction_by",
        "client_last_interaction_flag",
        "it_follow_up_streak",
        "it_follow_up_without_client_response_flag",
        "interaction_count",
        "stale_public_update_business_days",
        "stale_public_update_flag",
        "private_activity_since_last_public_flag",
    ]
    relevant = _meaningful_interactions(interactions)
    normalized = _normalized_interactions(interactions)
    rows: list[dict[str, object]] = []

    for ticket in tickets.to_dict(orient="records"):
        ticket_id = ticket["ticket_id"]
        ticket_interactions = relevant.loc[relevant["ticket_id"] == ticket_id]
        private_interactions = normalized.loc[
            (normalized["ticket_id"] == ticket_id)
            & (normalized["created_at"].notna())
            & (normalized["is_private"])
            & (normalized["body"].str.strip() != "")
        ]
        last = ticket_interactions.iloc[-1] if not ticket_interactions.empty else None
        last_private = private_interactions.iloc[-1] if not private_interactions.empty else None
        follow_up_streak = _it_follow_up_streak(ticket_interactions) if not ticket_interactions.empty else 0
        public_reference = last["created_at"] if last is not None else ticket.get("created_at")
        stale_public_update_business_days = _business_days_since(public_reference, as_of_ts, holiday_dates)
        private_activity_since_last_public = bool(
            last_private is not None
            and (
                last is None
                or last_private["created_at"] > last["created_at"]
            )
        )
        rows.append(
            {
                **ticket,
                "last_public_interaction_at": last["created_at"] if last is not None else None,
                "last_private_interaction_at": last_private["created_at"] if last_private is not None else None,
                "last_private_interaction_by": last_private["created_full_name"] if last_private is not None else None,
                "last_public_interaction_actor_type": last["actor_type"] if last is not None else None,
                "last_public_interaction_by": last["created_full_name"] if last is not None else None,
                "client_last_interaction_flag": bool(last is not None and last["actor_type"] == "client"),
                "it_follow_up_streak": int(follow_up_streak),
                "it_follow_up_without_client_response_flag": bool(follow_up_streak > follow_up_limit),
                "interaction_count": int(len(ticket_interactions)),
                "stale_public_update_business_days": stale_public_update_business_days,
                "stale_public_update_flag": bool(
                    stale_public_update_business_days is not None
                    and stale_public_update_business_days >= stale_public_update_days
                ),
                "private_activity_since_last_public_flag": private_activity_since_last_public,
            }
        )

    return pd.DataFrame(rows, columns=flag_columns)
