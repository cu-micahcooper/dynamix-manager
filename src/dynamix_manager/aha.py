from __future__ import annotations

from collections.abc import Iterable

import requests


DEFAULT_AHA_BASE_URL = "https://cedarville-university.aha.io"
STRATEGIC_TECH_ROADMAP_REPORT_ID = "7623493057900138289"


def fetch_aha_roadmap_pivot(
    base_url: str,
    api_key: str,
    *,
    report_id: str = STRATEGIC_TECH_ROADMAP_REPORT_ID,
) -> dict:
    """Fetch the saved Aha pivot report used for strategic technology projects."""
    response = requests.get(
        f"{base_url.rstrip('/')}/api/v1/bookmarks/custom_pivots/{report_id}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params={"view": "pivot"},
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def parse_aha_roadmap_pivot(payload: dict) -> dict[str, object]:
    rows = {int(key): value for key, value in (payload.get("rows") or {}).items()}
    initiatives_by_row: dict[int, list[dict[str, str]]] = {}
    for cell in _iter_cell_records(payload.get("cells") or []):
        row_ref = cell.get("row_ref")
        if row_ref is None:
            continue
        name = str(cell.get("plain_value") or "").strip()
        if not name:
            continue
        initiatives_by_row.setdefault(int(row_ref), []).append(
            {
                "id": str(cell.get("id") or ""),
                "name": name,
            }
        )

    workspaces = []
    for workspace_ref in payload.get("top_level_rows") or []:
        workspace = rows.get(int(workspace_ref))
        if not workspace:
            continue
        goals = []
        for goal_ref in workspace.get("child_refs") or []:
            goal = rows.get(int(goal_ref))
            if not goal:
                continue
            initiatives = initiatives_by_row.get(int(goal_ref), [])
            goals.append(
                {
                    "id": str(goal.get("id") or ""),
                    "name": str(goal.get("plain_value") or "").strip(),
                    "initiatives": initiatives,
                }
            )
        workspaces.append(
            {
                "id": str(workspace.get("id") or ""),
                "name": str(workspace.get("plain_value") or "").strip(),
                "goals": goals,
            }
        )

    goal_count = sum(len(workspace["goals"]) for workspace in workspaces)
    initiative_count = sum(
        len(goal["initiatives"])
        for workspace in workspaces
        for goal in workspace["goals"]
    )
    return {
        "workspace_count": len(workspaces),
        "goal_count": goal_count,
        "initiative_count": initiative_count,
        "workspaces": workspaces,
    }


def _iter_cell_records(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        if "row_ref" in value and "plain_value" in value:
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_cell_records(item)
