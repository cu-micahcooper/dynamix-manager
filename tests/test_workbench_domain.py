from dynamix_manager.workbench.domain import active_assigned, queue_sort, revision


def test_assignment_and_terminal_classes():
    assert active_assigned({"assigned_id": "u", "status_class": 2}, "u")
    assert not active_assigned({"assigned_id": "other", "status_class": 2}, "u")
    assert not active_assigned({"assigned_id": "u", "status_class": 3}, "u")


def test_revision_includes_history_and_assignment():
    a = {"id": 1, "assigned_id": "u", "history": []}
    assert revision(a) == revision(dict(a))
    assert revision(a) != revision({**a, "history": [{"text": "changed"}]})


def test_overdue_before_priority():
    tickets = [
        {"id": 2, "priority": 5},
        {"id": 1, "priority": 1, "due": "2000-01-01T00:00:00Z"},
    ]
    assert [t["id"] for t in queue_sort(tickets)] == [1, 2]


def test_requested_is_active():
    assert active_assigned({"assigned_id": "u", "status_class": 6}, "u")
