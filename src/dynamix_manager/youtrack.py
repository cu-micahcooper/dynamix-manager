from __future__ import annotations

import requests


_PROJECTS_FIELDS = (
    "id,name,shortName,description,archived,iconUrl,"
    "leader(id,login,name),team(name)"
)
_SPRINT_ISSUE_FIELDS = (
    "idReadable,summary,project(shortName),"
    "customFields(name,value(name,login))"
)
_DONE_STAGES = {"done", "closed", "resolved", "fixed", "completed", "cancelled", "canceled"}


def _get_custom_field(issue: dict, field_name: str) -> str:
    for f in issue.get("customFields") or []:
        if f.get("name") != field_name:
            continue
        val = f.get("value")
        if val is None:
            return ""
        if isinstance(val, list):
            return ", ".join(v.get("name") or v.get("login") or "" for v in val)
        if isinstance(val, dict):
            return val.get("name") or val.get("login") or ""
        return str(val)
    return ""


def fetch_youtrack_projects(
    base_url: str,
    token: str,
    *,
    top: int = 100,
) -> list[dict]:
    """Return active YouTrack projects sorted by name."""
    url = f"{base_url.rstrip('/')}/api/admin/projects"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"fields": _PROJECTS_FIELDS, "archived": "false", "orderBy": "name", "$top": str(top)}
    response = requests.get(url, headers=headers, params=params, timeout=15)
    response.raise_for_status()
    raw: list[dict] = response.json()
    results = []
    for p in raw:
        leader = p.get("leader") or {}
        team = p.get("team") or {}
        results.append({
            "id": p.get("id", ""),
            "name": p.get("name", ""),
            "short_name": p.get("shortName", ""),
            "description": p.get("description") or "",
            "leader_name": leader.get("name") or leader.get("login") or "",
            "icon_url": p.get("iconUrl") or "",
            "team_name": team.get("name") or "",
        })
    return results


def fetch_youtrack_inprogress_projects(
    base_url: str,
    token: str,
    *,
    board_id: str | None = None,
    top: int = 500,
) -> list[dict]:
    """Return current-sprint issues with Stage = 'In Progress' from the agile board.

    Uses /api/agiles/{board_id}/sprints/current to get the board's current sprint,
    then filters to issues whose 'Stage' custom field is 'In Progress'.

    Each returned dict has: id, summary, it_team, assignee, project_short.
    The 'it_team' field comes from the 'IT Team' custom field on the issue.
    """
    if not board_id:
        return []

    base = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    response = requests.get(
        f"{base}/api/agiles/{board_id}/sprints/current",
        headers=headers,
        params={"fields": f"id,name,issues({_SPRINT_ISSUE_FIELDS})"},
        timeout=15,
    )
    response.raise_for_status()
    sprint = response.json()
    all_issues: list[dict] = sprint.get("issues") or []

    results = []
    for issue in all_issues:
        stage = _get_custom_field(issue, "Stage").strip()
        if stage != "In Progress":
            continue
        results.append({
            "id": issue.get("idReadable", ""),
            "summary": issue.get("summary", ""),
            "it_team": _get_custom_field(issue, "IT Team"),
            "assignee": _get_custom_field(issue, "Assignee"),
            "project_short": (issue.get("project") or {}).get("shortName", ""),
        })

    return sorted(results, key=lambda i: (i["it_team"].lower(), i["summary"].lower()))
