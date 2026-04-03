from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


_SATISFACTION_VALUES = frozenset({"Very Satisfied", "Satisfied", "Unsatisfied", "Very Unsatisfied"})
_EFFORT_VALUES = frozenset({"Very Easy", "Easy", "Difficult", "Very Difficult"})
_SKIP_KEYS = frozenset({
    "ResponseID", "TicketID", "SurveyRequestedDate", "SurveyCompletedDate",
    "SurveyCompletedFullName", "ItemCompletedFullName", "AccountName", "ItemTitle",
})


def infer_survey_columns(rows: Iterable[dict]) -> dict[str, str | None]:
    satisfaction_key: str | None = None
    effort_key: str | None = None
    comment_key: str | None = None

    for row in rows:
        for k in row:
            if k in _SKIP_KEYS:
                continue
            v = str(row.get(k) or "")
            if not v:
                continue
            if satisfaction_key is None and v in _SATISFACTION_VALUES:
                satisfaction_key = k
            elif effort_key is None and v in _EFFORT_VALUES:
                effort_key = k
            elif comment_key is None and k not in {satisfaction_key, effort_key}:
                comment_key = k

        if (satisfaction_key or effort_key) and comment_key:
            break

    return {
        "satisfaction_key": satisfaction_key,
        "effort_key": effort_key,
        "comment_key": comment_key,
    }


def normalize_survey_rows(rows: list[dict]) -> pd.DataFrame:
    columns = infer_survey_columns(rows)
    satisfaction_key = columns["satisfaction_key"]
    effort_key = columns["effort_key"]
    comment_key = columns["comment_key"]

    normalized = []
    for row in rows:
        normalized.append(
            {
                "response_id": row["ResponseID"],
                "ticket_id": row["TicketID"],
                "survey_requested_at": row.get("SurveyRequestedDate"),
                "survey_completed_at": row.get("SurveyCompletedDate"),
                "satisfaction_label": row.get(satisfaction_key) if satisfaction_key else None,
                "customer_effort_label": row.get(effort_key) if effort_key else None,
                "comment_text": row.get(comment_key) if comment_key else None,
            }
        )

    return pd.DataFrame(normalized)
