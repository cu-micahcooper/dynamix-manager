"""Tenant-bound, single-attempt writes using verified TDX wire contracts.

The caller must durably claim the operation and compare the baseline before calling
apply_once. This adapter deliberately provides neither retries nor approval logic.
"""
import json
from urllib.parse import urlsplit

import requests

from .models import (AssignAction, ChangePreview, CommentAction, EditAction,
                     PreparedChange, PreviewField, StatusAction, WriteResult, parse_action)


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

    def snapshot(self, ticket_id):
        if type(ticket_id) is not int or ticket_id <= 0:
            raise ValueError("Ticket ID must be positive.")
        ticket = self._get(f"/api/{self.app_id}/tickets/{ticket_id}")
        if (not isinstance(ticket, dict) or ticket.get("AppID") != self.app_id
                or ticket.get("ID") != ticket_id or not ticket.get("ModifiedDate")
                or not isinstance(ticket.get("Title"), str)):
            raise ValueError("A matching application-bound ticket baseline is required.")
        return ticket

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
        if isinstance(action, CommentAction):
            payload, fields = self._feed(action, ticket, metadata)
        else:
            payload, fields = self._patch(action, ticket, metadata)
        preview = ChangePreview(application=self.application_name, ticket_id=action.ticket_id,
                                ticket_title=ticket["Title"], action=action.kind, fields=tuple(fields),
                                visibility=("private" if action.is_private else "public") if isinstance(action, CommentAction) else None,
                                recipients=action.notify if isinstance(action, CommentAction) else (),
                                notices=self._notices(action))
        return PreparedChange(action=action, base_url=self.base_url, app_id=self.app_id, baseline_json=canonical_json(ticket),
                              payload_json=canonical_json(payload), preview=preview)

    @staticmethod
    def _notices(action):
        common = ("Read access does not guarantee write permission; TeamDynamix authorizes the save.",
                  "Preflight comparison cannot prevent a last-moment external edit.",
                  "Tenant automation rules have not been audited.")
        if isinstance(action, CommentAction):
            return common + ("Only the listed email recipients are requested through Notify. Private visibility does not prevent disclosure to email recipients.",)
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
        feed = isinstance(action, CommentAction)
        path += "/feed" if feed else "?notifyNewResponsible=false"
        try:
            response = self.request("POST" if feed else "PATCH", self.base_url + path,
                                    headers=self._headers, json=json.loads(prepared.payload_json),
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        if status == 200:
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
