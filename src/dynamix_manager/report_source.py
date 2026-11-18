"""Read TeamDynamix reporting rows into memory for the duration of a report."""

import pandas as pd

from .config import load_runtime_config
from .tdx_client import TeamDynamixClient
from .tickets import normalize_ticket_rows


def reporting_client(config):
    return TeamDynamixClient(
        config.base_url, config.app_id, config.username, config.password
    )


def date_rows(client, token, app, field, start, end):
    """Split capped searches, including busy single days; never accept truncation."""
    limit = 2000
    payload = {
        field + "From": start.isoformat(),
        field + "To": end.isoformat(),
        "MaxResults": limit,
    }
    rows = client.search_tickets(token, payload, ticket_app_id=app)
    if len(rows) < limit:
        return rows
    if end - start < pd.Timedelta(seconds=2):
        raise RuntimeError("Reporting window exceeds the TeamDynamix search limit.")
    mid = start + (end - start) / 2
    return date_rows(client, token, app, field, start, mid) + date_rows(
        client, token, app, field, mid, end
    )


def frame_from_rows(rows):
    return normalize_ticket_rows({row["ID"]: row for row in rows}.values())


def live_history(config=None, client=None, app=634):
    config = config or load_runtime_config()
    client = client or reporting_client(config)
    token = client.authenticate()
    # Query contiguous quarters so each request has a bounded result and duration.
    end = pd.Timestamp.now("UTC")
    start = pd.Timestamp("2000-01-01", tz="UTC")
    batches = []
    while start < end:
        stop = min(start + pd.DateOffset(months=3), end)
        batch = frame_from_rows(
            date_rows(client, token, app, "CreatedDate", start, stop)
        )
        if not batch.empty:
            batches.append(batch)
        start = stop
    return (
        pd.concat(batches, ignore_index=True).drop_duplicates("ticket_id")
        if batches
        else pd.DataFrame()
    )
