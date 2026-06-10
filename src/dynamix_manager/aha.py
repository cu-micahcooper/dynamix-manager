from __future__ import annotations

import copy
import re
from collections.abc import Iterable
from typing import Callable

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


def fetch_aha_initiative_details(
    base_url: str,
    api_key: str,
    reference_num: str,
) -> dict:
    """Fetch the date/progress details for one Aha initiative."""
    response = requests.get(
        f"{base_url.rstrip('/')}/api/v1/initiatives/{reference_num}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params={
            "fields": (
                "id,reference_num,name,start_date,end_date,progress,progress_source,"
                "workflow_status,url"
            )
        },
        timeout=20,
    )
    response.raise_for_status()
    return response.json().get("initiative") or {}


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
                "reference_num": _initiative_reference_num(cell),
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


def enrich_aha_roadmap_details(
    roadmap: dict[str, object],
    base_url: str,
    api_key: str,
    *,
    fetcher: Callable[[str, str, str], dict] | None = None,
) -> dict[str, object]:
    """Add expected end dates, progress, status, and URLs to parsed initiatives."""
    enriched = copy.deepcopy(roadmap)
    detail_fetcher = fetcher or fetch_aha_initiative_details
    details_by_ref: dict[str, dict] = {}

    for workspace in enriched.get("workspaces") or []:
        for goal in workspace.get("goals") or []:
            for initiative in goal.get("initiatives") or []:
                reference_num = str(initiative.get("reference_num") or "").strip()
                if not reference_num:
                    continue
                if reference_num not in details_by_ref:
                    details_by_ref[reference_num] = detail_fetcher(base_url, api_key, reference_num)
                details = details_by_ref[reference_num]
                status = details.get("workflow_status") or {}
                initiative.update(
                    {
                        "end_date": details.get("end_date") or initiative.get("end_date"),
                        "progress": details.get("progress", initiative.get("progress")),
                        "url": details.get("url") or initiative.get("url"),
                        "status": status.get("name") or initiative.get("status"),
                        "status_color": status.get("color") or initiative.get("status_color"),
                    }
                )

    return enriched


def _initiative_reference_num(cell: dict) -> str:
    for value in (cell.get("url"), cell.get("html_value"), cell.get("html")):
        if not value:
            continue
        match = re.search(r"/initiatives/([^\"'<>\s]+)", str(value))
        if match:
            return match.group(1)
    return ""


def _iter_cell_records(value: object) -> Iterable[dict]:
    if isinstance(value, dict):
        if "row_ref" in value and "plain_value" in value:
            yield value
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_cell_records(item)
