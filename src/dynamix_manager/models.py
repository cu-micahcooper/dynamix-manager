from __future__ import annotations

import pandas as pd


def _hours_between(start: pd.Series, end: pd.Series) -> pd.Series:
    start_ts = pd.to_datetime(start, utc=True, errors="coerce")
    end_ts = pd.to_datetime(end, utc=True, errors="coerce")
    return (end_ts - start_ts).dt.total_seconds() / 3600


def build_ticket_linked_survey_model(
    surveys: pd.DataFrame,
    tickets: pd.DataFrame,
) -> pd.DataFrame:
    model = surveys.merge(tickets, how="left", on="ticket_id")
    model["ticket_linked"] = model["service_name"].notna()
    model["response_time_hours"] = _hours_between(model["created_at"], model["responded_at"])
    model["resolution_time_hours"] = _hours_between(model["created_at"], model["resolved_at"])
    if "ticket_app_name" in model.columns:
        model = model.loc[model["ticket_app_name"].eq("InfoTech Tickets")]
    else:
        model = model.loc[model["ticket_linked"]]
    return model.reset_index(drop=True)
