"""Fictional data; this adapter never contacts an external service."""

from copy import deepcopy
from datetime import UTC, datetime
from typing import ClassVar


class DemoAdapter:
    base_url = "demo://fictional"
    app_id = "demo"
    can_submit = True
    capabilities: ClassVar[dict] = {
        "live_write_verified": False,
        "conditional_write": False,
        "demo": True,
    }

    def __init__(self):
        self.tickets = {}
        examples = [
            (
                101,
                "VPN connection drops during remote work",
                "Avery Morgan",
                "requester-1",
                4,
            ),
            (
                102,
                "Access to the shared project folder",
                "Jordan Lee",
                "requester-2",
                3,
            ),
            (
                103,
                "Classroom projector has a blue tint",
                "Taylor Chen",
                "requester-3",
                2,
            ),
        ]
        for identity, title, name, requester, priority in examples:
            person = {
                "id": requester,
                "name": name,
                "email": f"{requester}@example.invalid",
            }
            self.tickets[identity] = {
                "id": identity,
                "title": title,
                "description": "This is a fictional demonstration ticket. Please help investigate the reported issue.",
                "status_id": 1,
                "status": "New",
                "status_class": 1,
                "priority": priority,
                "due": "2026-09-15T17:00:00Z" if identity == 101 else None,
                "modified": "2026-09-16T15:00:00Z",
                "assigned_id": "demo-user",
                "requester": person,
                "history": [
                    {
                        "id": f"{identity}-1",
                        "author": name,
                        "author_id": requester,
                        "created": "2026-09-16T14:00:00Z",
                        "text": "The issue started yesterday. What information would help?",
                        "private": False,
                        "requester": True,
                    },
                    {
                        "id": f"{identity}-2",
                        "author": "Demo Analyst",
                        "author_id": "demo-user",
                        "created": "2026-09-16T15:00:00Z",
                        "text": "Internal: investigate recent changes before proposing a fix.",
                        "private": True,
                        "requester": False,
                    },
                ],
                "history_complete": True,
                "url": f"https://example.invalid/tickets/{identity}",
                "recipients": [person],
                "statuses": [
                    {"id": 1, "name": "New", "status_class": "new"},
                    {"id": 2, "name": "In progress", "status_class": "in_process"},
                    {"id": 3, "name": "Resolved", "status_class": "completed"},
                ],
            }

    def login(self):
        return {
            "id": "demo-user",
            "name": "Demo Analyst",
            "email": "analyst@example.invalid",
        }

    def queue(self):
        return {
            "tickets": deepcopy(list(self.tickets.values())),
            "complete": True,
            "warning": "",
        }

    def ticket(self, identity):
        return deepcopy(self.tickets[int(identity)])

    def submit(self, identity, payload):
        ticket = self.tickets[int(identity)]
        receipt_id = f"demo-{identity}-{len(ticket['history']) + 1}"
        ticket["history"].append(
            {
                "id": receipt_id,
                "author": "Demo Analyst",
                "author_id": "demo-user",
                "created": datetime.now(UTC).isoformat(),
                "text": payload["text"],
                "private": payload["visibility"] == "internal",
                "requester": False,
            }
        )
        if payload.get("status_id") is not None:
            ticket["status_id"] = payload["status_id"]
            ticket["status"] = next(
                s["name"]
                for s in ticket["statuses"]
                if str(s["id"]) == str(payload["status_id"])
            )
            ticket["status_class"] = 3 if str(payload["status_id"]) == "3" else 2
        return {"id": receipt_id, "ticket_id": identity, "simulated": True}


def suggestion(ticket, visibility, instructions):
    return {
        "summary": "Simulated assessment: the requester reports an unresolved issue.",
        "unresolved": ["Cause has not yet been established."],
        "next_steps": [
            "Ask for the exact error and the most recent successful attempt."
        ],
        "questions": ["When did this last work as expected?"],
        "draft": "Thanks for reporting this. Could you share the exact error message and when this last worked? I will use those details to guide the investigation.",
        "references": [str(ticket["history"][0]["id"])],
        "simulated": True,
    }
