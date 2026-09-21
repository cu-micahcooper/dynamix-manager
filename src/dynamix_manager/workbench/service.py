"""Immutable preview and durable submission state machine."""

import json
import secrets
import threading
from copy import deepcopy

from fastapi import HTTPException

from .domain import active_assigned, revision


class WorkbenchService:
    def __init__(self, store):
        self.store = store
        self.previews = {}
        self.lock = threading.RLock()

    def ticket(self, session, identity):
        try:
            ticket = session["adapter"].ticket(identity)
        except Exception as exc:
            raise HTTPException(
                502,
                "Ticket could not be refreshed. Check your connection and API authentication.",
            ) from exc
        if not active_assigned(ticket, session["user"]["id"]):
            raise HTTPException(
                409,
                "Ticket is no longer active and directly assigned to you. Your draft is preserved.",
            )
        return ticket

    def read_ticket(self, session, identity):
        intent = self.store.blocking(session["scope"], identity)
        if not intent:
            return self.ticket(session, identity)
        try:
            ticket = session["adapter"].ticket(identity)
        except Exception:  # noqa: BLE001 - preserve recovery UI when remote ticket is unavailable
            context = json.loads(intent["data"]).get("_ticket", {})
            ticket = {
                "id": int(identity),
                "title": context.get(
                    "title", "Uncertain submission requires inspection"
                ),
                "url": context.get("url", ""),
                "description": "Remote ticket is unavailable. Inspect its submission outcome before acknowledging.",
                "requester": {"id": "", "name": "Unavailable"},
                "history": [],
                "history_complete": False,
                "recipients": [],
                "statuses": [],
                "status": "Unavailable",
                "status_id": None,
                "status_class": None,
                "assigned_id": None,
                "priority": 0,
                "due": None,
            }
        return {
            **ticket,
            "uncertain": True,
            "submission_state": intent["status"],
            "write_blocked": True,
        }

    def validate(self, ticket, body):
        visibility = body.get("visibility", "public")
        if visibility not in ("public", "internal"):
            raise HTTPException(422, "Choose public reply or internal note.")
        text = body.get("text", "")
        if not isinstance(text, str) or not text.strip() or len(text) > 50000:
            raise HTTPException(422, "Message must contain 1–50,000 characters.")
        recipients = body.get("recipients", [])
        eligible = {str(r["id"]): r for r in ticket.get("recipients", [])}
        if not isinstance(recipients, list) or any(
            str(r) not in eligible for r in recipients
        ):
            raise HTTPException(
                422, "Notification recipient is not eligible for this ticket."
            )
        if visibility == "internal" and recipients:
            raise HTTPException(
                422, "Version one internal notes do not notify external recipients."
            )
        status = body.get("status_id")
        if status is not None and str(status) not in {
            str(s["id"]) for s in ticket.get("statuses", [])
        }:
            raise HTTPException(422, "Selected status is not allowed.")
        return {
            "text": text,
            "visibility": visibility,
            "recipients": list(dict.fromkeys(str(r) for r in recipients)),
            "status_id": status,
        }

    def preview(self, session, identity, body):
        if self.store.blocking(session["scope"], identity):
            raise HTTPException(
                409,
                "An uncertain submission requires reconciliation or acknowledgement before another preview.",
            )
        ticket = self.ticket(session, identity)
        if body.get("source_revision") and body["source_revision"] != revision(ticket):
            raise HTTPException(
                409,
                "Ticket changed while composing. Your draft is preserved; refresh and review again.",
            )
        payload = self.validate(ticket, body)
        self.store.save_draft(
            session["scope"],
            identity,
            payload["visibility"],
            {
                **payload,
                "instructions": body.get("instructions", ""),
                "source_revision": revision(ticket),
            },
        )
        if not session["adapter"].can_submit:
            raise HTTPException(
                409,
                "Live submission is disabled until tenant visibility, recipients, and update semantics are verified. Your draft is saved.",
            )
        identity_token = secrets.token_urlsafe(32)
        snapshot = {
            "scope": session["scope"],
            "nonce": session["nonce"],
            "ticket_id": str(identity),
            "revision": revision(ticket),
            "payload": deepcopy(payload),
        }
        with self.lock:
            self.previews[identity_token] = snapshot
        return dict(
            preview_id=identity_token,
            ticket_id=identity,
            title=ticket["title"],
            **{
                **payload,
                "recipients": [
                    r
                    for r in ticket.get("recipients", [])
                    if str(r["id"]) in payload["recipients"]
                ],
            },
            current_status=ticket.get("status"),
            new_status=next(
                (
                    s["name"]
                    for s in ticket.get("statuses", [])
                    if str(s["id"]) == str(payload["status_id"])
                ),
                ticket.get("status"),
            ),
            warning="Freshness is checked immediately before submission; the remote service does not guarantee an atomic conditional write.",
        )

    def submit(self, session, identity, preview_id):
        with self.lock:
            snapshot = self.previews.get(preview_id)
            if (
                not snapshot
                or snapshot["scope"] != session["scope"]
                or snapshot["nonce"] != session["nonce"]
                or snapshot["ticket_id"] != str(identity)
            ):
                raise HTTPException(
                    409,
                    "Preview is missing, already used, or belongs to another session. Review again.",
                )
            ticket = self.ticket(session, identity)
            if revision(ticket) != snapshot["revision"]:
                del self.previews[preview_id]
                raise HTTPException(
                    409,
                    "Ticket changed since preview. Your draft is preserved; refresh and review again.",
                )
            self.validate(ticket, snapshot["payload"])
            try:
                intent = self.store.begin(
                    session["scope"],
                    identity,
                    preview_id,
                    {
                        **snapshot["payload"],
                        "_ticket": {
                            "title": ticket.get("title"),
                            "url": ticket.get("url"),
                        },
                    },
                )
            except ValueError as exc:
                raise HTTPException(
                    409, "Submission already started; do not retry."
                ) from exc
            del self.previews[preview_id]
        try:
            outgoing = deepcopy(snapshot["payload"])
            if session["mode"] != "demo":
                eligible = {
                    str(r["id"]): r["email"] for r in ticket.get("recipients", [])
                }
                outgoing["recipients"] = [
                    eligible[identity] for identity in outgoing["recipients"]
                ]
            receipt = session["adapter"].submit(identity, outgoing)
            if not isinstance(receipt, dict) or not receipt.get("id"):
                raise RuntimeError("Unconfirmed receipt")
        except ValueError:
            self.store.finish(intent, "rejected", {})
            return {
                "status": "rejected",
                "intent_id": intent,
                "message": "The remote service rejected this update. Your draft is preserved.",
            }
        except Exception:  # noqa: BLE001 - any unclassified write failure is uncertain
            self.store.finish(intent, "uncertain", {})
            return {
                "status": "uncertain",
                "intent_id": intent,
                "message": "The update may have been accepted. Do not resend. Inspect the remote ticket, then acknowledge the outcome.",
            }
        self.store.finish(intent, "confirmed", receipt)
        self.store.mark(session["scope"], identity, "reviewed")
        return {"status": "confirmed", "intent_id": intent, "receipt": receipt}

    def clear_session(self, scope):
        with self.lock:
            self.previews = {
                k: v for k, v in self.previews.items() if v["scope"] != scope
            }
