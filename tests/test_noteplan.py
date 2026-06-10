from pathlib import Path

import pandas as pd

from dynamix_manager.noteplan import (
    build_cfo_discussion_items,
    extract_agenda_items,
    extract_open_tasks,
    parse_event_timestamp,
)


def test_parse_event_timestamp_reads_noteplan_event_line():
    timestamp = parse_event_timestamp(
        "**Event:**  ![📅](2026-06-11 09:00:::event-id:::NA:::Chris / Micah:::#9FE1E7)"
    )

    assert timestamp == pd.Timestamp("2026-06-11 13:00:00+0000", tz="UTC")


def test_extract_agenda_items_reads_top_level_bullets_until_next_heading():
    text = """# Meeting
**Event:**  ![📅](2026-06-11 09:00:::event-id)

## Agenda
- Kyle Medical Center
\t- two offices
- Compromised accounts
*
## Meeting Minutes
- Ignore this
"""

    assert extract_agenda_items(text) == ["Kyle Medical Center", "Compromised accounts"]


def test_extract_open_tasks_skips_completed_and_blank_tasks():
    text = """# Meeting
* Open follow-up
* [ ] Checkbox action
* [x] Completed action @done(2026-06-01 09:00 AM)
*
## Agenda
* Agenda task
"""

    assert extract_open_tasks(text) == [
        "Open follow-up",
        "Checkbox action",
        "Agenda task",
    ]


def test_build_cfo_discussion_items_uses_next_future_agenda_and_past_open_tasks(tmp_path: Path):
    (tmp_path / "past.md").write_text(
        """# Past
**Event:**  ![📅](2026-06-04 09:00:::past)
* Follow up on GUARD
* [x] Done item @done(2026-06-05 09:00 AM)
## Agenda
* Get quote on rip and replace
"""
    )
    (tmp_path / "older.md").write_text(
        """# Older
**Event:**  ![📅](2026-05-28 09:00:::older)
* Ignore older open task
"""
    )
    (tmp_path / "future.md").write_text(
        """# Future
**Event:**  ![📅](2026-06-11 09:00:::future)
## Agenda
- Kyle Medical Center
- Compromised accounts
"""
    )
    (tmp_path / "later.md").write_text(
        """# Later
**Event:**  ![📅](2026-06-18 09:00:::later)
## Agenda
- Ignore later meeting
"""
    )

    items = build_cfo_discussion_items(
        tmp_path,
        as_of=pd.Timestamp("2026-06-10 12:00:00+0000", tz="UTC"),
    )

    assert items == [
        {"source": "Next agenda", "text": "Kyle Medical Center"},
        {"source": "Next agenda", "text": "Compromised accounts"},
        {"source": "Open action", "text": "Follow up on GUARD"},
        {"source": "Open action", "text": "Get quote on rip and replace"},
    ]


def test_build_cfo_discussion_items_is_empty_when_notes_are_missing(tmp_path: Path):
    assert build_cfo_discussion_items(tmp_path / "missing", as_of=pd.Timestamp("2026-06-10")) == []
