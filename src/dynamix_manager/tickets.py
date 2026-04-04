from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


def build_ticket_search_filters(modified_from: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"StatusIDs": []}
    if modified_from:
        payload["ModifiedDateFrom"] = modified_from
    return payload


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
                "created_at": row.get("CreatedDate"),
                "modified_at": row.get("ModifiedDate"),
                "responded_at": row.get("RespondedDate"),
                "resolved_at": row.get("CompletedDate"),
                "completed_by_name": row.get("CompletedFullName"),
                "sla_name": row.get("SlaName"),
                "sla_begin_at": row.get("SlaBeginDate"),
                "respond_by_at": row.get("RespondByDate"),
                "resolve_by_at": row.get("ResolveByDate"),
                "is_sla_violated": row.get("IsSlaViolated"),
                "is_sla_respond_by_violated": row.get("IsSlaRespondByViolated"),
                "is_sla_resolve_by_violated": row.get("IsSlaResolveByViolated"),
            }
        )

    return pd.DataFrame(normalized)
