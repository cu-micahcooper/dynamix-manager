"""Conservative ticket eligibility and deterministic context fingerprints."""

import hashlib
import json
from datetime import UTC, datetime


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).timestamp()
    except (ValueError, TypeError):
        return 0.0


def active_assigned(ticket, user_id):
    return str(ticket.get("assigned_id")) == str(user_id) and ticket.get(
        "status_class"
    ) in (1, 2, 5, 6, "new", "in_process", "on_hold", "requested")


def revision(ticket):
    # Include all received context even when an upstream revision token exists.
    return hashlib.sha256(
        json.dumps(ticket, sort_keys=True, default=str).encode()
    ).hexdigest()


def queue_sort(tickets):
    now = datetime.now(UTC).timestamp()

    def key(t):
        history = t.get("history", [])
        last_reply = max(
            (
                timestamp(h.get("created"))
                for h in history
                if not h.get("private", True) and not h.get("requester")
            ),
            default=0,
        )
        unanswered = [
            timestamp(h.get("created"))
            for h in history
            if h.get("requester") and timestamp(h.get("created")) > last_reply
        ]
        activity = min(unanswered) if unanswered else timestamp(t.get("modified"))
        due = timestamp(t.get("due"))
        return (
            not (due and due < now),
            -int(t.get("priority") or 0),
            activity,
            str(t["id"]),
        )

    return sorted(tickets, key=key)
