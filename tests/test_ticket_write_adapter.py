import json
from types import SimpleNamespace

import pytest
import requests

from dynamix_manager.ticket_writes.models import parse_action

UID = "11111111-1111-4111-8111-111111111111"
TICKET = dict(ID=1001, AppID=42, Title="Before", Description="old", PriorityID=2,
              StatusID=1, ResponsibleUid=None, ResponsibleGroupID=9, ModifiedDate="version")


def setup_adapter(**overrides):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    records = {
        "/api/42/tickets/1001": TICKET.copy(),
        "/api/42/tickets/statuses": [dict(ID=5, Name="Ordered", IsActive=True,
                                          StatusClass=5, RequireGoesOffHold=False)],
        "/api/42/tickets/priorities": [dict(ID=7, Name="High", IsActive=True)],
        f"/api/people/{UID}": dict(UID=UID, IsActive=True, FullName="Person", PrimaryEmail="person@example.invalid", Applications=["TDPeople"], OrgApplications=[dict(ID=42, IsActive=True)]),
        "/api/groups/4": dict(ID=4, Name="Team", IsActive=True),
        "/api/groups/4/applications": [dict(AppID=42, GroupID=4)],
    }
    records.update(overrides)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: TICKET.copy())

    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret",
                           request=request, read=lambda path: records[path])
    return adapter, calls, records


def test_comment_explicit_wire_and_feed_success_without_json_schema():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or SimpleNamespace(status_code=200))
    assert adapter.apply_once(prepared).outcome == "applied"
    args, kwargs = calls[0]
    assert args == ("POST", "https://tenant.example/TDWebApi/api/42/tickets/1001/feed")
    assert kwargs["json"] == dict(Comments="Hello", IsPrivate=True, IsRichHtml=False, Notify=[], NewStatusID=0, CascadeStatus=False)
    assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 30)


def test_status_requires_verified_flag_but_not_all_hold_classes_rejected():
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="status", ticket_id=1001, comments="Ordered", status_id=5))
    assert json.loads(adapter.validate(action).payload_json)["NewStatusID"] == 5
    for replacement in (True, None):
        records["/api/42/tickets/statuses"][0]["RequireGoesOffHold"] = replacement
        with pytest.raises(ValueError):
            adapter.validate(action)


def test_edit_partial_patch_preserves_description_null():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, title="After", description=None, priority_id=7)))
    assert adapter.apply_once(prepared).outcome == "applied"
    method, url, kwargs = calls[0]
    assert method == "PATCH" and url.endswith("/api/42/tickets/1001?notifyNewResponsible=false")
    assert kwargs["json"] == [dict(op="replace", path="/Title", value="After"), dict(op="replace", path="/Description", value=None), dict(op="replace", path="/PriorityID", value=7)]
    assert prepared.preview.fields[-1].after == "High"


def test_assignment_preserves_unselected_field_and_names_preview():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="assign", ticket_id=1001, responsible_uid=UID)))
    adapter.apply_once(prepared)
    assert calls[0][2]["json"] == [dict(op="replace", path="/ResponsibleUid", value=UID)]
    assert prepared.preview.fields[0].after == "Person"
    assert prepared.preview.fields[1].before == prepared.preview.fields[1].after


@pytest.mark.parametrize("code,outcome", [(401,"rejected"),(403,"rejected"),(429,"rejected"),(500,"unknown"),(503,"unknown"),(302,"unknown"),(201,"unknown")])
def test_one_attempt_and_classified_outcomes(code, outcome):
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    adapter.request = lambda *args, **kwargs: (calls.append(1) or SimpleNamespace(status_code=code))
    assert adapter.apply_once(prepared).outcome == outcome
    assert len(calls) == 1


def test_timeout_is_unknown_and_not_retried():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("contains secret")
    adapter.request = timeout
    result = adapter.apply_once(prepared)
    assert result.outcome == "unknown" and "secret" not in result.message
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("AppID",99),("AppID",None),("ModifiedDate",None)])
def test_missing_or_wrong_baseline_rejected(field, value):
    adapter, _, records = setup_adapter()
    records["/api/42/tickets/1001"][field] = value
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))


def test_missing_inactive_or_wrong_app_assignee_rejected():
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="assign", ticket_id=1001, responsible_uid=UID))
    user = records[f"/api/people/{UID}"]
    for field, value in [("IsActive",False),("OrgApplications",[]),("OrgApplications",None), ("OrgApplications",[dict(ID=42, IsActive=False)])]:
        before = user[field]
        user[field] = value
        with pytest.raises(ValueError):
            adapter.validate(action)
        user[field] = before


def test_group_notification_true_blocked_without_exact_recipient_contract():
    adapter, _, _ = setup_adapter()
    action = parse_action(dict(kind="assign", ticket_id=1001, responsible_group_id=4, notify_new_responsible=True))
    with pytest.raises(ValueError, match="recipient"):
        adapter.validate(action)


def test_unavailable_metadata_fails_closed_without_error_disclosure():
    adapter, _, _ = setup_adapter()
    def denied(path):
        raise RuntimeError("secret denied")
    adapter.read = denied
    with pytest.raises(ValueError, match="unavailable") as error:
        adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, priority_id=7)))
    assert "secret" not in str(error.value)


def test_connection_preserves_client_header_separate_from_ticket_app_id():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    connection = SimpleNamespace(base_url="https://tenant.example/TDWebApi", app_id=42,
                                 header_app_id="8", token="secret", auth_mode="user", ready=lambda: None)
    adapter = WriteAdapter.from_connection(connection)
    assert adapter.app_id == 42 and adapter._headers["X-TDClient-ID"] == "8"
    connection.auth_mode = "admin"
    with pytest.raises(ValueError):
        WriteAdapter.from_connection(connection)


def test_prepared_change_roundtrip_and_explicit_recipients():
    from dynamix_manager.ticket_writes.models import PreparedChange
    adapter, _, _ = setup_adapter()
    for value in [dict(kind="assign", ticket_id=1001, responsible_group_id=4),
                  dict(kind="edit", ticket_id=1001, description=None),
                  dict(kind="comment", ticket_id=1001, comments="Hello", is_private=False, notify=["person@example.invalid"])]:
        prepared = adapter.validate(parse_action(value))
        restored = PreparedChange.model_validate_json(prepared.model_dump_json())
        assert restored == prepared
        if value["kind"] == "comment":
            assert restored.preview.recipients == ("person@example.invalid",)
            assert restored.preview.visibility == "public"


def test_partial_edit_missing_or_unrecognized_success_is_unknown():
    adapter, _, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, title="After")))
    adapter.request = lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {"ID": 2, "AppID":42})
    assert adapter.apply_once(prepared).outcome == "unknown"


@pytest.mark.parametrize("collection", ["statuses", "priorities"])
def test_inactive_or_missing_metadata_rejected(collection):
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="status", ticket_id=1001, comments="Done", status_id=5) if collection == "statuses" else dict(kind="edit", ticket_id=1001, priority_id=7))
    records[f"/api/42/tickets/{collection}"][0]["IsActive"] = False
    with pytest.raises(ValueError):
        adapter.validate(action)
    records[f"/api/42/tickets/{collection}"] = []
    with pytest.raises(ValueError):
        adapter.validate(action)


@pytest.mark.parametrize("change", [dict(kind="assign", responsible_group_id=4), dict(kind="status", status_id=5, comments="Done")])
def test_converted_project_task_changes_rejected_before_dispatch(change):
    adapter, calls, records = setup_adapter()
    records["/api/42/tickets/1001"]["IsConvertedToTask"] = True
    with pytest.raises(ValueError, match="converted"):
        adapter.validate(parse_action(dict(ticket_id=1001, **change)))
    assert calls == []


def test_prepared_change_cannot_dispatch_to_different_tenant_with_same_app_id():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    from dynamix_manager.ticket_writes.models import PreparedChange
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    restored = PreparedChange.model_validate_json(prepared.model_dump_json())
    other = WriteAdapter("https://other.example/TDWebApi", 42, "another-secret", request=adapter.request)
    assert other.apply_once(restored).outcome == "rejected"
    assert calls == []
    assert restored.base_url == "https://tenant.example/TDWebApi"
