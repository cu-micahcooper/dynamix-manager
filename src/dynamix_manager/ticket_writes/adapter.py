"""Tenant-bound, single-attempt writes using verified TDX wire contracts.

The caller must durably claim the operation and compare the baseline before calling
apply_once. This adapter deliberately provides neither retries nor approval logic.
"""
import json
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import requests

from .models import (AssignAction, ChangePreview, CommentAction, EditAction, PreparedChange,
                     PreviewField, StatusAction, TaskAction, WriteResult, parse_action)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class WriteAdapter:
    def __init__(self, base_url, app_id, token, *, request=None, read=None,
                 application_name="InfoTech Tickets", header_app_id=None):
        base_url = base_url.rstrip("/")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.lower() != "/tdwebapi"):
            raise ValueError("Expected an HTTPS tenant API URL.")
        if type(app_id) is not int or app_id <= 0 or not token:
            raise ValueError("An application and personal token are required.")
        self.base_url, self.app_id, self.application_name = base_url, app_id, application_name
        self._headers = {"Authorization": f"Bearer {token}", "X-TDClient-ID": str(header_app_id or app_id),
                         "Accept": "application/json"}
        # requests.request creates a fresh Session with zero retry adapters. Do not use
        # the shared read client's retry/error-collapsing transport for mutations.
        self.request = request or requests.request
        self.read = read or self._read

    @classmethod
    def from_connection(cls, connection):
        connection.ready()
        if connection.auth_mode != "user":
            raise ValueError("Ticket writes require a personal connection.")
        return cls(connection.base_url, connection.app_id, connection.token,
                   header_app_id=connection.header_app_id)

    def _read(self, path):
        if not path.startswith("/api/"):
            raise ValueError("Metadata path is outside this tenant.")
        response = self.request("GET", self.base_url + path, headers=self._headers,
                                timeout=(5, 30), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Required ticket metadata is unavailable.")
        return response.json()

    def _get(self, path):
        try:
            return self.read(path)
        except Exception:
            raise ValueError("Required ticket metadata is unavailable.") from None

    def _metadata_request(self, method, path, *, payload=None):
        """Make one bounded read-only metadata request and collapse all failures."""
        if method not in {"GET", "POST"} or not path.startswith("/api/"):
            raise ValueError("Ticket write metadata is unavailable.")
        options = {
            "headers": self._headers,
            "timeout": (5, 30),
            "allow_redirects": False,
        }
        if method == "POST":
            options["json"] = payload
        try:
            response = self.request(method, self.base_url + path, **options)
            if response.status_code != 200:
                raise ValueError
            result = response.json()
        except Exception:
            raise ValueError("Ticket write metadata is unavailable.") from None
        if not isinstance(result, list):
            raise ValueError("Ticket write metadata is unavailable.")
        return result

    @staticmethod
    def _valid_option(item):
        return (
            isinstance(item, dict)
            and type(item.get("ID")) is int
            and item["ID"] > 0
            and isinstance(item.get("Name"), str)
            and bool(item["Name"].strip())
            and item.get("IsActive") is True
        )

    def discover_metadata(self, kind, *, search=None, limit=10):
        """Return a minimal, bounded projection of write-supporting metadata."""
        if kind not in {"statuses", "priorities", "people", "groups"}:
            raise ValueError("Unsupported ticket write metadata kind.")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Metadata limit must be between 1 and 10.")
        if kind in {"statuses", "priorities"}:
            if search is not None:
                raise ValueError("Search is unsupported for this metadata kind.")
            rows = self._metadata_request(
                "GET", f"/api/{self.app_id}/tickets/{kind}"
            )
            results = []
            for item in rows:
                if not self._valid_option(item):
                    continue
                if kind == "statuses":
                    if (
                        type(item.get("StatusClass")) is not int
                        or item["StatusClass"] not in range(1, 7)
                        or item.get("RequireGoesOffHold") is not False
                    ):
                        continue
                    results.append({
                        "ID": item["ID"],
                        "Name": item["Name"],
                        "StatusClass": item["StatusClass"],
                        "RequireGoesOffHold": item["RequireGoesOffHold"],
                    })
                else:
                    results.append({"ID": item["ID"], "Name": item["Name"]})
                if len(results) == limit:
                    break
            return self._metadata_result(kind, results)

        if not isinstance(search, str):
            raise ValueError("Person and group searches require 2 to 100 characters.")
        search = search.strip()
        if not 2 <= len(search) <= 100:
            raise ValueError("Person and group searches require 2 to 100 characters.")
        if kind == "people":
            return self._discover_people(search, limit)
        return self._discover_groups(search, limit)

    @staticmethod
    def _metadata_result(kind, results):
        return {
            "kind": kind,
            "results": results,
            "returned": len(results),
            "complete": False,
        }

    PEOPLE_CANDIDATE_WINDOW = 50

    def _discover_people(self, search, limit):
        # The lookup mixes customers and technicians and carries no application data, so
        # scan a bounded window and verify each candidate until ``limit`` are eligible.
        query = urlencode({"searchText": search, "maxResults": self.PEOPLE_CANDIDATE_WINDOW})
        candidates = self._metadata_request("GET", f"/api/people/lookup?{query}")
        results, seen = [], set()
        for candidate in candidates[:self.PEOPLE_CANDIDATE_WINDOW]:
            if len(results) == limit:
                break
            if not isinstance(candidate, dict):
                continue
            try:
                uid = str(UUID(str(candidate.get("UID"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if uid in seen:
                continue
            seen.add(uid)
            details = self._metadata_object("GET", f"/api/people/{uid}")
            name = details.get("FullName")
            try:
                details_uid = str(UUID(str(details.get("UID"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if (
                details_uid != uid
                or details.get("IsActive") is not True
                or not isinstance(name, str)
                or not name.strip()
                or not self._app_eligible(details.get("OrgApplications"), person=True)
            ):
                continue
            results.append({"ID": uid, "Name": name, "AppID": self.app_id})
        return self._metadata_result("people", results)

    def _discover_groups(self, search, limit):
        candidates = self._metadata_request(
            "POST",
            "/api/groups/search",
            payload={"NameLike": search, "IsActive": True, "HasAppID": self.app_id},
        )
        results, seen = [], set()
        for candidate in candidates[:limit]:
            if (
                not isinstance(candidate, dict)
                or type(candidate.get("ID")) is not int
                or candidate["ID"] <= 0
                or candidate["ID"] in seen
            ):
                continue
            group_id = candidate["ID"]
            seen.add(group_id)
            group = self._metadata_object("GET", f"/api/groups/{group_id}")
            applications = self._metadata_request(
                "GET", f"/api/groups/{group_id}/applications"
            )
            if (
                not self._valid_option(group)
                or group["ID"] != group_id
                or not self._app_eligible(applications, group_id=group_id)
            ):
                continue
            results.append({"ID": group_id, "Name": group["Name"], "AppID": self.app_id})
        return self._metadata_result("groups", results)

    def _metadata_object(self, method, path):
        if method != "GET" or not path.startswith("/api/"):
            raise ValueError("Ticket write metadata is unavailable.")
        try:
            response = self.request(
                method,
                self.base_url + path,
                headers=self._headers,
                timeout=(5, 30),
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError
            result = response.json()
        except Exception:
            raise ValueError("Ticket write metadata is unavailable.") from None
        if not isinstance(result, dict):
            raise ValueError("Ticket write metadata is unavailable.")
        return result

    def snapshot(self, ticket_id):
        if type(ticket_id) is not int or ticket_id <= 0:
            raise ValueError("Ticket ID must be positive.")
        ticket = self._get(f"/api/{self.app_id}/tickets/{ticket_id}")
        if (not isinstance(ticket, dict) or ticket.get("AppID") != self.app_id
                or ticket.get("ID") != ticket_id or not ticket.get("ModifiedDate")
                or not isinstance(ticket.get("Title"), str)):
            raise ValueError("A matching application-bound ticket baseline is required.")
        return ticket

    TASK_KEYS = ("ID", "Title", "IsActive", "PercentComplete", "CompletedDate",
                 "ResponsibleFullName", "ResponsibleGroupName", "TypeID")

    @staticmethod
    def _completed_date(task):
        """TDX serializes an unset completion date as the .NET minimum date, not null."""
        value = task.get("CompletedDate")
        if not isinstance(value, str) or not value or value.startswith("0001-01-01"):
            return None
        return value

    def list_tasks(self, ticket_id, *, limit=25):
        """Return a bounded, minimal projection of a ticket's tasks (no descriptions or emails)."""
        if type(ticket_id) is not int or ticket_id <= 0:
            raise ValueError("Ticket ID must be positive.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Task limit must be between 1 and 100.")
        rows = self._metadata_request("GET", f"/api/{self.app_id}/tickets/{ticket_id}/tasks")
        valid = [row for row in rows if isinstance(row, dict) and type(row.get("ID")) is int
                 and row["ID"] > 0 and isinstance(row.get("Title"), str)]
        tasks = [{**{key: row.get(key) for key in self.TASK_KEYS}, "CompletedDate": self._completed_date(row)}
                 for row in valid[:limit]]
        return {"ticket_id": ticket_id, "tasks": tasks, "returned": len(tasks),
                "complete": len(valid) <= limit}

    def _task_snapshot(self, action):
        task = self._get(f"/api/{self.app_id}/tickets/{action.ticket_id}/tasks/{action.task_id}")
        percent = task.get("PercentComplete") if isinstance(task, dict) else None
        if (not isinstance(task, dict) or task.get("ID") != action.task_id
                or task.get("TicketID") != action.ticket_id or task.get("IsActive") is not True
                or type(percent) is not int or not 0 <= percent < 100 or self._completed_date(task)
                or not isinstance(task.get("Title"), str) or not task.get("ModifiedDate")):
            raise ValueError("The task is not an open, incomplete task on this ticket.")
        return task

    def _task_feed(self, action, task):
        payload = dict(IsPrivate=action.is_private, IsRichHtml=False, Notify=list(action.notify),
                       PercentComplete=100)
        fields = [PreviewField(name="Task", before=task["Title"], after=task["Title"]),
                  PreviewField(name="PercentComplete", before=str(task["PercentComplete"]), after="100")]
        if action.comments is not None:
            payload["Comments"] = action.comments
            fields.append(PreviewField(name="Comment", before=None, after=action.comments))
        return payload, fields

    def metadata(self, action):
        if isinstance(action, StatusAction):
            return self._active_option("statuses", action.status_id)
        if isinstance(action, EditAction) and "priority_id" in action.model_fields_set:
            return self._active_option("priorities", action.priority_id)
        if isinstance(action, AssignAction):
            return self._assignment_metadata(action)
        return {}

    def _active_option(self, collection, identifier):
        options = self._get(f"/api/{self.app_id}/tickets/{collection}")
        matches = [item for item in options if isinstance(item, dict) and item.get("ID") == identifier] if isinstance(options, list) else []
        if len(matches) != 1 or matches[0].get("IsActive") is not True or not matches[0].get("Name"):
            raise ValueError("Selected active metadata could not be verified.")
        return matches[0]

    def _app_eligible(self, applications, *, person=False, group_id=None):
        return isinstance(applications, list) and any(
            isinstance(app, dict) and app.get("ID" if person else "AppID") == self.app_id
            and (app.get("IsActive") is True if person else app.get("GroupID") == group_id)
            for app in applications)

    def _assignment_metadata(self, action):
        selected = {}
        if "responsible_uid" in action.model_fields_set:
            user = self._get(f"/api/people/{action.responsible_uid}")
            if (not isinstance(user, dict) or str(user.get("UID", "")).lower() != str(action.responsible_uid)
                    or user.get("IsActive") is not True or not self._app_eligible(user.get("OrgApplications"), person=True)
                    or not user.get("FullName")):
                raise ValueError("Selected person is not verified active and application eligible.")
            selected["ResponsibleUid"] = user["FullName"]
        if "responsible_group_id" in action.model_fields_set:
            group = self._get(f"/api/groups/{action.responsible_group_id}")
            apps = self._get(f"/api/groups/{action.responsible_group_id}/applications")
            if (not isinstance(group, dict) or group.get("ID") != action.responsible_group_id
                    or group.get("IsActive") is not True or not self._app_eligible(apps, group_id=action.responsible_group_id) or not group.get("Name")):
                raise ValueError("Selected group is not verified active and application eligible.")
            selected["ResponsibleGroupID"] = group["Name"]
        if action.notify_new_responsible:
            raise ValueError("Exact assignment notification recipients are not verified; disable notifications.")
        return selected

    def validate(self, action):
        action = parse_action(action)
        ticket, metadata = self.snapshot(action.ticket_id), self.metadata(action)
        if ticket.get("IsConvertedToTask") is True and isinstance(action, (AssignAction, StatusAction)):
            raise ValueError("Assignment and status changes on converted project tasks are unsupported.")
        if isinstance(action, StatusAction):
            if metadata.get("RequireGoesOffHold") is not False or metadata.get("StatusClass") not in range(1, 7):
                raise ValueError("Status requirements are unsupported or unavailable.")
        baseline = ticket
        if isinstance(action, TaskAction):
            task = self._task_snapshot(action)
            payload, fields = self._task_feed(action, task)
            baseline = {"ticket": ticket, "task": task}
        elif isinstance(action, CommentAction):
            payload, fields = self._feed(action, ticket, metadata)
        else:
            payload, fields = self._patch(action, ticket, metadata)
        feed_like = isinstance(action, (CommentAction, TaskAction))
        preview = ChangePreview(application=self.application_name, ticket_id=action.ticket_id,
                                ticket_title=ticket["Title"], action=action.kind, fields=tuple(fields),
                                visibility=("private" if action.is_private else "public") if feed_like else None,
                                recipients=action.notify if feed_like else (),
                                notices=self._notices(action))
        return PreparedChange(action=action, base_url=self.base_url, app_id=self.app_id, baseline_json=canonical_json(baseline),
                              payload_json=canonical_json(payload), preview=preview)

    @staticmethod
    def _notices(action):
        common = ("Read access does not guarantee write permission; TeamDynamix authorizes the save.",
                  "Preflight comparison cannot prevent a last-moment external edit.",
                  "Tenant automation rules have not been audited.")
        if isinstance(action, CommentAction):
            return common + ("Only the listed email recipients are requested through Notify. Private visibility does not prevent disclosure to email recipients.",)
        if isinstance(action, TaskAction):
            return common + ("TeamDynamix accepting the update is not proof the task shows as completed; confirm CompletedDate with list_ticket_tasks.",)
        return common + ("New-responsible notification is disabled.",)

    def _feed(self, action, ticket, metadata):
        status = action.status_id if isinstance(action, StatusAction) else 0
        payload = dict(Comments=action.comments, IsPrivate=action.is_private, IsRichHtml=False,
                       Notify=list(action.notify), NewStatusID=status, CascadeStatus=False)
        fields = [PreviewField(name="Comment", before=None, after=action.comments)]
        if status:
            if "StatusID" not in ticket:
                raise ValueError("Status baseline is unavailable.")
            fields.append(PreviewField(name="Status", before=str(ticket.get("StatusName") or ticket["StatusID"]), after=metadata["Name"]))
        return payload, fields

    def _patch(self, action, ticket, metadata):
        mapping = (("responsible_uid", "ResponsibleUid"), ("responsible_group_id", "ResponsibleGroupID")) if isinstance(action, AssignAction) else (
            ("title", "Title"), ("description", "Description"), ("priority_id", "PriorityID"))
        payload, fields = [], []
        labels = {"ResponsibleUid": "ResponsibleFullName", "ResponsibleGroupID": "ResponsibleGroupName", "PriorityID": "PriorityName"}
        for name, wire in mapping:
            if name not in action.model_fields_set and not isinstance(action, AssignAction):
                continue
            if wire not in ticket:
                raise ValueError("Relevant ticket baseline is unavailable.")
            before = ticket.get(labels.get(wire)) or ticket[wire]
            value = getattr(action, name) if name in action.model_fields_set else ticket[wire]
            if name == "responsible_uid" and value is not None:
                value = str(value)
            after = metadata.get(wire, value) if isinstance(action, AssignAction) else metadata.get("Name", value) if wire == "PriorityID" else value
            if name not in action.model_fields_set:
                after = before
            else:
                payload.append(dict(op="replace", path=f"/{wire}", value=value))
            fields.append(PreviewField(name=wire, before=None if before is None else str(before), after=None if after is None else str(after)))
        return payload, fields

    def apply_once(self, prepared):
        if prepared.base_url != self.base_url or prepared.app_id != self.app_id:
            return WriteResult(outcome="rejected", message="Tenant or application binding does not match.")
        action = prepared.action
        path = f"/api/{self.app_id}/tickets/{action.ticket_id}"
        feed = isinstance(action, (CommentAction, TaskAction))
        if isinstance(action, TaskAction):
            path += f"/tasks/{action.task_id}/feed"
        else:
            path += "/feed" if feed else "?notifyNewResponsible=false"
        try:
            response = self.request("POST" if feed else "PATCH", self.base_url + path,
                                    headers=self._headers, json=json.loads(prepared.payload_json),
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        # The published feed contract lists 200; Cedarville also returns 201
        # for a created feed entry (verified against persisted dispatch status
        # and authoritative feed read-back). Do not broaden PATCH or accept 202.
        if status == 200 or (feed and status == 201):
            if not feed:
                try:
                    ticket = response.json()
                    if not isinstance(ticket, dict) or ticket.get("ID") != action.ticket_id or ticket.get("AppID") != self.app_id:
                        raise ValueError("Unrecognized ticket response.")
                except Exception:
                    return WriteResult(outcome="unknown", message="The response did not confirm the target ticket; do not retry.", status_code=status)
            return WriteResult(outcome="applied", message="TeamDynamix accepted the change.", status_code=status)
        outcome = "rejected" if 400 <= status < 500 and status != 408 else "unknown"
        return WriteResult(outcome=outcome, message="TeamDynamix rejected the change." if outcome == "rejected" else "The upstream outcome is unknown; do not retry.", status_code=status)
