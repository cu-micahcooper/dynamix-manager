import pytest
from pydantic import ValidationError


def parse(value):
    from dynamix_manager.ticket_writes.models import parse_action
    return parse_action(value)


def test_private_immutable_comment_defaults():
    action = parse(dict(kind="comment", ticket_id=1, comments="Hello"))
    assert action.is_private is True and action.notify == ()
    with pytest.raises(ValidationError):
        action.comments = "changed"


@pytest.mark.parametrize("value", [
    dict(kind="create", ticket_id=1),
    dict(kind="comment", ticket_id=True, comments="hello"),
    dict(kind="comment", ticket_id=0, comments="hello"),
    dict(kind="comment", ticket_id=1, comments=" "),
    dict(kind="comment", ticket_id=1, comments="x", notify=["not-email"]),
    dict(kind="comment", ticket_id=1, comments="x", extra="oops"),
    dict(kind="assign", ticket_id=1),
    dict(kind="assign", ticket_id=1, responsible_uid=None),
    dict(kind="edit", ticket_id=1),
    dict(kind="edit", ticket_id=1, title=None),
    dict(kind="edit", ticket_id=1, priority_id=None),
    dict(kind="edit", ticket_id=1, title="x" * 301),
])
def test_rejects_unsupported_or_unsafe_actions(value):
    with pytest.raises(ValidationError):
        parse(value)


def test_description_null_is_explicit_and_assignment_omission_preserved():
    edit = parse(dict(kind="edit", ticket_id=1, description=None))
    assert edit.model_fields_set == {"kind", "ticket_id", "description"}
    assign = parse(dict(kind="assign", ticket_id=1, responsible_group_id=4))
    assert "responsible_uid" not in assign.model_fields_set
    assert assign.notify_new_responsible is False


@pytest.mark.parametrize("value", [dict(kind="assign", ticket_id=1, responsible_group_id=4), dict(kind="edit", ticket_id=1, description=None)])
def test_json_round_trip_preserves_explicit_fields(value):
    import json
    action = parse(value)
    restored = parse(json.loads(action.model_dump_json()))
    assert restored.model_fields_set == action.model_fields_set


@pytest.mark.parametrize("change", [dict(notify=["a@example.invalid"] * 51), dict(comments="x" * 20001), dict(is_private="false")])
def test_comment_resource_bounds_and_strict_visibility(change):
    with pytest.raises(ValidationError):
        parse({"kind": "comment", "ticket_id": 1, "comments": "Hello", **change})


def test_task_completion_action_is_private_by_default_and_requires_task_id():
    from dynamix_manager.ticket_writes.models import TaskAction

    action = parse(dict(kind="task", ticket_id=1001, task_id=77))
    assert isinstance(action, TaskAction)
    assert action.comments is None and action.is_private is True and action.notify == ()
    with_comment = parse(dict(kind="task", ticket_id=1001, task_id=77, comments="Done",
                                     is_private=False, notify=["ap@example.invalid"]))
    assert with_comment.comments == "Done" and with_comment.notify == ("ap@example.invalid",)
    for bad in (dict(kind="task", ticket_id=1001), dict(kind="task", ticket_id=1001, task_id=0),
                dict(kind="task", ticket_id=1001, task_id=77, comments="   "),
                dict(kind="task", ticket_id=1001, task_id=77, percent_complete=50)):
        with pytest.raises(ValidationError):
            parse(bad)


REQUESTOR = "aaaaaaaa-0000-4000-8000-000000000001"


def test_create_action_requires_core_fields_and_has_no_ticket_yet():
    from dynamix_manager.ticket_writes.models import CreateAction

    action = parse(dict(kind="create", title="Replace projector", type_id=3, account_id=11, requestor_uid=REQUESTOR))
    assert isinstance(action, CreateAction) and action.ticket_id == 0
    assert action.description is None and action.notify_requestor is False
    assert action.model_dump(mode="json") == {"kind": "create", "title": "Replace projector", "type_id": 3,
                                              "account_id": 11, "requestor_uid": REQUESTOR}
    full = parse(dict(kind="create", title="T", description="Body", type_id=3, account_id=11, requestor_uid=REQUESTOR,
                      form_id=2, status_id=30793, priority_id=7, service_id=9, source_id=4,
                      responsible_uid=REQUESTOR, responsible_group_id=14405, notify_requestor=True))
    assert full.responsible_group_id == 14405 and full.notify_requestor is True
    assert type(full).model_validate_json(full.model_dump_json()) == full
    for bad in (dict(kind="create", title="T", type_id=3, account_id=11),
                dict(kind="create", title="   ", type_id=3, account_id=11, requestor_uid=REQUESTOR),
                dict(kind="create", title="T", type_id=0, account_id=11, requestor_uid=REQUESTOR),
                dict(kind="create", title="T", type_id=3, account_id=11, requestor_uid="not-a-uid"),
                dict(kind="create", title="T", type_id=3, account_id=11, requestor_uid=REQUESTOR, ticket_id=5),
                dict(kind="create", title="x" * 301, type_id=3, account_id=11, requestor_uid=REQUESTOR)):
        with pytest.raises(ValidationError):
            parse(bad)
