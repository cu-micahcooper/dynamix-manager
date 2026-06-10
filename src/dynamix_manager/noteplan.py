from __future__ import annotations

import re
from pathlib import Path

import pandas as pd


DEFAULT_CFO_NOTES_DIR = (
    Path.home()
    / "Library/Containers/co.noteplan.NotePlan-setapp/Data/Library/Application Support"
    / "co.noteplan.NotePlan-setapp/Notes/Chris"
)
_EVENT_RE = re.compile(r"\*\*Event:\*\*.*?\((\d{4}-\d{2}-\d{2})(?:[ T](\d{2}:\d{2}))?")
_BULLET_RE = re.compile(r"^([ \t]*)([-*+])\s*(.*)$")


def parse_event_timestamp(text: str) -> pd.Timestamp | None:
    match = _EVENT_RE.search(text)
    if not match:
        return None
    date_part = match.group(1)
    time_part = match.group(2) or "00:00"
    ts = pd.Timestamp(f"{date_part} {time_part}")
    return ts.tz_localize("America/New_York").tz_convert("UTC")


def extract_agenda_items(text: str) -> list[str]:
    lines = text.splitlines()
    in_agenda = False
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if in_agenda:
                break
            in_agenda = stripped == "## Agenda"
            continue
        if not in_agenda:
            continue
        match = _BULLET_RE.match(line)
        if not match:
            continue
        indent, _marker, item = match.groups()
        if indent:
            continue
        clean = _clean_bullet_text(item)
        if clean:
            items.append(clean)
    return items


def extract_open_tasks(text: str) -> list[str]:
    tasks: list[str] = []
    for line in text.splitlines():
        match = re.match(r"^[ \t]*\*(?!\*)\s+(.*)$", line)
        if not match:
            continue
        clean = _clean_task_text(match.group(1))
        if clean:
            tasks.append(clean)
    return tasks


def build_cfo_discussion_items(
    notes_dir: str | Path,
    *,
    as_of: pd.Timestamp,
) -> list[dict[str, str]]:
    root = Path(notes_dir)
    if not root.exists():
        return []
    as_of = _as_utc(as_of)
    notes = []
    for path in sorted(root.glob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt", ".markdown"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        event_at = parse_event_timestamp(text)
        if event_at is None:
            continue
        notes.append({"path": path, "text": text, "event_at": event_at})

    future_notes = sorted(
        (note for note in notes if note["event_at"] > as_of),
        key=lambda note: note["event_at"],
    )
    past_notes = sorted(
        (note for note in notes if note["event_at"] < as_of),
        key=lambda note: note["event_at"],
        reverse=True,
    )

    items: list[dict[str, str]] = []
    if future_notes:
        for text in extract_agenda_items(str(future_notes[0]["text"])):
            items.append({"source": "Next agenda", "text": text})

    if past_notes:
        seen_tasks: set[str] = set()
        for task in extract_open_tasks(str(past_notes[0]["text"])):
            key = " ".join(task.lower().split())
            if key in seen_tasks:
                continue
            seen_tasks.add(key)
            items.append({"source": "Open action", "text": task})

    return items


def _as_utc(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def _clean_bullet_text(value: str) -> str:
    clean = value.strip()
    if not clean or clean.startswith("#"):
        return ""
    return clean


def _clean_task_text(value: str) -> str:
    clean = value.strip()
    if not clean or clean.startswith("#"):
        return ""
    if re.match(r"\[[xX-]\]", clean) or "@done(" in clean:
        return ""
    clean = re.sub(r"^\[\s\]\s*", "", clean).strip()
    return clean
