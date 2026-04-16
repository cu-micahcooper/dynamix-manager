from __future__ import annotations

from collections.abc import Iterable

import pandas as pd


_SATISFACTION_VALUES = frozenset(
    {
        "Very Satisfied",
        "Satisfied",
        "Unsatisfied",
        "Very Unsatisfied",
        "Great!",
        "Could have been better",
    }
)
_EFFORT_VALUES = frozenset({"Very Easy", "Easy", "Difficult", "Very Difficult"})
_SKIP_KEYS = frozenset({
    "ResponseID", "TicketID", "SurveyRequestedDate", "SurveyCompletedDate",
    "SurveyCompletedFullName", "ItemCompletedFullName", "AccountName", "ItemTitle",
})


def infer_survey_columns(rows: Iterable[dict]) -> dict[str, str | None]:
    satisfaction_key: str | None = None
    effort_key: str | None = None
    comment_keys: list[str] = []

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
            elif k not in {satisfaction_key, effort_key} and k not in comment_keys:
                comment_keys.append(k)

    return {
        "satisfaction_key": satisfaction_key,
        "effort_key": effort_key,
        "comment_key": comment_keys[0] if comment_keys else None,
        "comment_keys": comment_keys,
    }


def normalize_survey_rows(rows: list[dict]) -> pd.DataFrame:
    columns = infer_survey_columns(rows)
    satisfaction_key = columns["satisfaction_key"]
    effort_key = columns["effort_key"]
    comment_keys = columns.get("comment_keys") or ([] if columns["comment_key"] is None else [columns["comment_key"]])

    normalized = []
    for row in rows:
        comments: list[str] = []
        for key in comment_keys:
            value = str(row.get(key) or "").strip()
            if value and value not in comments:
                comments.append(value)
        normalized.append(
            {
                "response_id": row["ResponseID"],
                "ticket_id": row["TicketID"],
                "survey_requested_at": row.get("SurveyRequestedDate"),
                "survey_completed_at": row.get("SurveyCompletedDate"),
                "commenter_name": row.get("SurveyCompletedFullName") or None,
                "satisfaction_label": row.get(satisfaction_key) if satisfaction_key else None,
                "customer_effort_label": row.get(effort_key) if effort_key else None,
                "comment_text": "\n\n".join(comments) if comments else None,
            }
        )

    return pd.DataFrame(normalized)
