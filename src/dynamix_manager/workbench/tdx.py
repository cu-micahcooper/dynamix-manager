"""Personal read adapter; live writes are gated until tenant semantics are verified.

Contracts: official TDWebApi OpenAPI, 2026-09-17. Search has MaxResults but
no total/page cursor; feed top=0 requests all parents and Uri loads replies.
"""

from __future__ import annotations

from urllib.parse import urljoin, urlsplit
from uuid import UUID

import requests

from dynamix_manager.tdx_client import (
    TeamDynamixClient,
    build_auth_headers,
    parse_auth_token,
    uses_admin_auth,
)

ACTIVE_CLASSES = {1, 2, 5, 6}
CLASS_NAMES = {
    1: "new",
    2: "in_process",
    3: "completed",
    4: "cancelled",
    5: "on_hold",
    6: "requested",
}


class TDXAdapter:
    can_submit = False

    def __init__(
        self,
        base_url,
        app_id,
        username="",
        password="",
        token="",
        verified_submission=False,
    ):
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/").lower() != "/tdwebapi"
        ):
            raise ValueError(
                "Use an HTTPS tenant URL ending in /TDWebApi without credentials or query parameters."
            )
        if not str(app_id).isdigit() or int(app_id) <= 0:
            raise ValueError("A positive ticket application ID is required.")
        self.base_url, self.app_id = base_url.rstrip("/"), str(app_id)
        self.client = TeamDynamixClient(self.base_url, self.app_id, username, password)
        self.capabilities = {
            "live_write_verified": False,
            "submission_contract_verified": bool(verified_submission),
            "conditional_write": False,
        }
        self.can_submit = bool(verified_submission)
        self.token = token
        self.identity = None
        self.statuses = None

    def _request(self, method, path, **kwargs):
        # Never follow redirects with an authenticated request. No automatic write retries.
        try:
            r = self.client.session.request(
                method,
                self.base_url + path,
                headers=build_auth_headers(self.token, self.app_id),
                timeout=30,
                allow_redirects=False,
                **kwargs,
            )
        except requests.RequestException:
            raise RuntimeError(
                "TeamDynamix is unavailable; your draft is preserved."
            ) from None
        if r.status_code in (401, 403):
            raise RuntimeError(
                "TeamDynamix authentication expired or access is denied. Sign in again with a personal API account."
            )
        if r.status_code == 429:
            raise RuntimeError(
                "TeamDynamix rate limit reached. Wait before refreshing."
            )
        if not 200 <= r.status_code < 300:
            raise RuntimeError(
                "TeamDynamix rejected the request. Check API access and application ID."
            )
        if (
            path == "/api/auth/login"
            and "application/json" not in r.headers.get("Content-Type", "").lower()
        ):
            return r.text.strip()
        try:
            return r.json()
        except (ValueError, TypeError):
            raise RuntimeError(
                "TeamDynamix returned an unsupported response."
            ) from None

    def login(self):
        if not self.token:
            if uses_admin_auth(self.client.username, self.client.password):
                raise ValueError(
                    "Use personal user credentials; administrator credentials are not supported."
                )
            if not self.client.username or not self.client.password:
                raise ValueError(
                    "Enter personal API credentials or a user-scoped bearer token."
                )
            self.token = parse_auth_token(
                self._request(
                    "POST",
                    "/api/auth/login",
                    json={
                        "UserName": self.client.username,
                        "Password": self.client.password,
                    },
                )
            )
            self.client.password = ""
        user = self._request("GET", "/api/auth/getuser")
        if not isinstance(user, dict) or not user.get("UID"):
            raise RuntimeError(
                "TeamDynamix did not confirm a personal account identity."
            )
        self.identity = {
            "id": str(user["UID"]),
            "name": user.get("FullName") or user.get("UserName") or "",
            "email": user.get("Email") or user.get("PrimaryEmail") or "",
        }
        return self.identity

    def _statuses(self):
        if self.statuses is None:
            self.statuses = self._request("GET", f"/api/{self.app_id}/tickets/statuses")
            if not isinstance(self.statuses, list):
                self.statuses = None
                raise RuntimeError("Ticket status metadata could not be verified.")
        return self.statuses

    def queue(self):
        if not self.identity:
            raise ValueError("Sign in before loading tickets.")
        active = [
            s["ID"]
            for s in self._statuses()
            if s.get("StatusClass") in ACTIVE_CLASSES and s.get("IsActive") is True
        ]
        rows = (
            self._request(
                "POST",
                f"/api/{self.app_id}/tickets/search",
                json={
                    "PrimaryResponsibilityUids": [self.identity["id"]],
                    "StatusIDs": active,
                    "MaxResults": 1000,
                },
            )
            if active
            else []
        )
        if not isinstance(rows, list):
            raise RuntimeError("Unsupported ticket search response.")  # noqa: TRY004 - safe adapter boundary error
        tickets = [
            self._normalize(t)
            for t in rows
            if t.get("ResponsibleUid") == self.identity["id"]
            and t.get("StatusID") in active
        ]
        return {
            "tickets": tickets,
            "complete": False,
            "warning": "Queue completeness is unverified: the tenant search has no total or paging cursor (maximum 1000 results).",
        }

    def _normalize(self, t):
        statuses = self.statuses or []
        status = next((s for s in statuses if s.get("ID") == t.get("StatusID")), {})
        requester = {
            "id": str(t.get("RequestorUid") or ""),
            "name": t.get("RequestorName") or "",
            "email": t.get("RequestorEmail") or "",
        }
        return {
            "id": t["ID"],
            "title": t.get("Title") or "",
            "description": t.get("Description") or "",
            "status_id": t.get("StatusID"),
            "status": status.get("Name") or t.get("StatusName") or "",
            "status_class": CLASS_NAMES.get(status.get("StatusClass"), "unknown"),
            "priority": t.get("PriorityOrder") or 0,
            "due": t.get("EndDate"),
            "modified": t.get("ModifiedDate"),
            "assigned_id": str(t.get("ResponsibleUid") or ""),
            "requester": requester,
            "history": [],
            "history_complete": False,
            "attachments": [
                {
                    "id": str(a["ID"]),
                    "name": a["Name"],
                    "size": a.get("Size"),
                    "private": a.get("IsPrivate") is not False,
                }
                for a in (t.get("Attachments") or [])
                if isinstance(a, dict)
                and a.get("ID")
                and str(a.get("Name", "")).lower().endswith(".pdf")
            ],
            "url": self.base_url.rsplit("/", 1)[0]
            + f"/TDNext/Apps/{self.app_id}/Tickets/TicketDet.aspx?TicketID={t['ID']}",
            "recipients": [requester] if requester["email"] else [],
            "statuses": [
                {
                    "id": s["ID"],
                    "name": s["Name"],
                    "status_class": CLASS_NAMES.get(s.get("StatusClass"), "unknown"),
                }
                for s in statuses
                if s.get("IsActive") and not s.get("RequireGoesOffHold")
            ],
        }

    def ticket(self, ticket_id):
        ticket_id = int(ticket_id)
        self._statuses()
        result = self._normalize(
            self._request("GET", f"/api/{self.app_id}/tickets/{ticket_id}")
        )
        feed = self._request(
            "GET", f"/api/{self.app_id}/tickets/{ticket_id}/feed", params={"top": 0}
        )
        if not isinstance(feed, list):
            return result
        complete = len(feed) <= 1000
        history = []
        for entry in feed[:1000]:
            private = entry.get("IsPrivate") is not False
            replies = entry.get("Replies") or []
            if entry.get("RepliesCount", 0) > len(replies):
                uri = urljoin(self.base_url + "/", entry.get("Uri") or "")
                if not uri.startswith(self.base_url + "/api/"):
                    complete = False
                else:
                    try:
                        detail = self._request("GET", uri[len(self.base_url) :])
                        replies = detail.get("Replies") or []
                        if detail.get("IsPrivate") is not False:
                            private = True
                    except RuntimeError:
                        complete = False
            if len(replies) != entry.get("RepliesCount", len(replies)):
                complete = False
            for row, entry_id in [(entry, str(entry.get("ID", "")))] + [
                (r, f"{entry.get('ID')}:reply:{r.get('ID')}") for r in replies
            ]:
                history.append(
                    {
                        "id": entry_id,
                        "author": row.get("CreatedFullName") or "",
                        "author_id": str(row.get("CreatedUid") or ""),
                        "created": row.get("CreatedDate") or "",
                        "text": row.get("Body") or "",
                        "private": private,
                        "requester": bool(result["requester"]["id"])
                        and row.get("CreatedUid") == result["requester"]["id"],
                    }
                )
        result["history"] = sorted(history, key=lambda h: (h["created"], h["id"]))
        result["history_complete"] = complete
        return result

    def pdf_attachment(self, identity):
        identity = str(UUID(str(identity)))
        response = None
        try:
            response = self.client.session.request(
                "GET",
                f"{self.base_url}/api/attachments/{identity}/content",
                headers=build_auth_headers(self.token, self.app_id),
                timeout=30,
                allow_redirects=False,
                stream=True,
            )
            if response.status_code != 200:
                raise RuntimeError(
                    "PDF could not be retrieved. Check your TeamDynamix access."
                )
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                data.extend(chunk)
                if len(data) > 30 * 1024 * 1024:
                    raise ValueError(
                        "PDF exceeds the 30 MB viewing limit. Open it in TeamDynamix."
                    )
            if not data.startswith(b"%PDF-"):
                raise ValueError("The attachment did not contain a valid PDF.")
            return bytes(data)
        except requests.RequestException:
            raise RuntimeError(
                "PDF download failed. Try again or open it in TeamDynamix."
            ) from None
        finally:
            if response is not None:
                response.close()

    def submit(self, ticket_id, payload):
        if not self.can_submit:
            raise ValueError(
                "Live submission is disabled until tenant visibility, notification and update semantics are verified."
            )
        if (
            payload.get("visibility") not in ("public", "internal")
            or not str(payload.get("text", "")).strip()
        ):
            raise ValueError("A message and valid visibility are required.")
        recipients = payload.get("recipients", [])
        if not isinstance(recipients, list) or any(
            not isinstance(x, str) or "@" not in x or "\n" in x or "\r" in x
            for x in recipients
        ):
            raise ValueError("Notification recipients must be email addresses.")
        body = {
            "Comments": payload["text"],
            "IsPrivate": payload["visibility"] == "internal",
            "IsRichHtml": False,
            "Notify": recipients,
            "NewStatusID": payload.get("status_id") or 0,
        }
        try:
            response = self.client.session.request(
                "POST",
                self.base_url + f"/api/{self.app_id}/tickets/{int(ticket_id)}/feed",
                headers=build_auth_headers(self.token, self.app_id),
                json=body,
                timeout=30,
                allow_redirects=False,
            )
        except requests.RequestException:
            raise RuntimeError(
                "Submission outcome is uncertain. Inspect the remote ticket before resubmitting."
            ) from None
        if 400 <= response.status_code < 500 and response.status_code not in (408, 409):
            raise ValueError(
                "TeamDynamix rejected the update; your draft is preserved."
            )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(
                "Submission outcome is uncertain. Inspect the remote ticket before resubmitting."
            )
        try:
            receipt = response.json()
            if not isinstance(receipt, dict) or not receipt.get("ID"):
                raise ValueError()
        except (TypeError, ValueError):
            raise RuntimeError(
                "Submission outcome is uncertain: no remote receipt was returned."
            ) from None
        return {
            "id": str(receipt["ID"]),
            "ticket_id": int(ticket_id),
            "created": receipt.get("CreatedDate", ""),
        }
