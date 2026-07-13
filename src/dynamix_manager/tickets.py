from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

_CANCELLED_STATUS_NAMES = {"cancelled", "canceled"}


def build_ticket_search_filters(
    modified_from: str | None = None,
    modified_to: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    closed_from: str | None = None,
    closed_to: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"StatusIDs": []}
    if modified_from:
        payload["ModifiedDateFrom"] = modified_from
    if modified_to:
        payload["ModifiedDateTo"] = modified_to
    if created_from:
        payload["CreatedDateFrom"] = created_from
    if created_to:
        payload["CreatedDateTo"] = created_to
    if closed_from:
        payload["ClosedDateFrom"] = closed_from
    if closed_to:
        payload["ClosedDateTo"] = closed_to
    return payload


def _cancelled_status_name(value: object) -> bool:
    return str(value or "").strip().casefold() in _CANCELLED_STATUS_NAMES


def ticket_row_is_cancelled(
    row: dict,
    *,
    status_name_key: str = "StatusName",
    status_class_key: str = "StatusClass",
) -> bool:
    if _cancelled_status_name(row.get(status_name_key)):
        return True
    try:
        return int(row.get(status_class_key, 0) or 0) == 4
    except (TypeError, ValueError):
        return False


def exclude_cancelled_tickets(
    tickets: pd.DataFrame,
    *,
    status_name_col: str = "status_name",
    status_class_col: str = "status_class",
) -> pd.DataFrame:
    if tickets.empty:
        return tickets.copy()

    mask = pd.Series(False, index=tickets.index)
    if status_name_col in tickets.columns:
        names = tickets[status_name_col].fillna("").astype("string").str.strip().str.casefold()
        mask = mask | names.isin(_CANCELLED_STATUS_NAMES)
    if status_class_col in tickets.columns:
        classes = pd.to_numeric(tickets[status_class_col], errors="coerce")
        mask = mask | classes.eq(4)
    return tickets.loc[~mask].copy()


def _clean_date(value: object) -> object:
    """Return None for C# DateTime.MinValue (0001-01-01) sentinels; pass other values through."""
    if isinstance(value, str) and value.startswith("0001-"):
        return None
    return value


def normalize_ticket_rows(rows: Iterable[dict]) -> pd.DataFrame:
    normalized = []
    for row in rows:
        normalized.append(
            {
                "ticket_id": row["ID"],
                "ticket_title": row.get("Title"),
                "status_name": row.get("StatusName"),
                "status_class": row.get("StatusClass"),
                "type_name": row.get("TypeName"),
                "priority_name": row.get("PriorityName"),
                "service_name": row.get("ServiceName"),
                "team_name": row.get("ResponsibleGroupName"),
                "assignee_name": row.get("RespondingFullName"),
                "assignee_uid": row.get("RespondedUid") or row.get("ResponsibleUid"),
                "requestor_name": row.get("RequestorName"),
                "requestor_uid": row.get("RequestorUid"),
                "created_at": _clean_date(row.get("CreatedDate")),
                "modified_at": _clean_date(row.get("ModifiedDate")),
                "responded_at": _clean_date(row.get("RespondedDate")),
                "resolved_at": _clean_date(row.get("CompletedDate")),
                "completed_by_name": row.get("CompletedFullName"),
                "sla_name": row.get("SlaName"),
                "sla_begin_at": _clean_date(row.get("SlaBeginDate")),
                "respond_by_at": _clean_date(row.get("RespondByDate")),
                "resolve_by_at": _clean_date(row.get("ResolveByDate")),
                "is_sla_violated": row.get("IsSlaViolated"),
                "is_sla_respond_by_violated": row.get("IsSlaRespondByViolated"),
                "is_sla_resolve_by_violated": row.get("IsSlaResolveByViolated"),
            }
        )

    return pd.DataFrame(normalized)
