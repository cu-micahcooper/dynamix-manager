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


ASSET_UID = "aaaaaaaa-0000-4000-8000-000000000001"


def test_asset_actions_are_typed_bounded_and_carry_an_item_identity():
    from dynamix_manager.ticket_writes.models import (AssetCommentAction, CommentAction, CreateAction,
                                                       EditAssetAction, LinkAssetAction)
    comment = parse(dict(kind="asset_comment", asset_id=1973209, comments="Racked in SSC 101"))
    assert isinstance(comment, AssetCommentAction) and comment.is_private is True and comment.notify == ()
    assert comment.item == ("asset", 1973209) and comment.ticket_id == 0
    link = parse(dict(kind="asset_link", asset_id=1973209, ticket_id=30605254))
    assert isinstance(link, LinkAssetAction) and link.item == ("ticket", 30605254)
    edit = parse(dict(kind="asset_edit", asset_id=1973209, status_id=1447, expected_replacement_date="2029-06-30", tag="CU-1"))
    assert isinstance(edit, EditAssetAction) and edit.item == ("asset", 1973209)
    assert edit.model_dump(mode="json") == {"kind": "asset_edit", "asset_id": 1973209, "status_id": 1447,
                                            "expected_replacement_date": "2029-06-30", "tag": "CU-1"}
    assert CommentAction(kind="comment", ticket_id=5, comments="x").item == ("ticket", 5)
    assert CreateAction(kind="create", title="T", type_id=1, account_id=2, requestor_uid=ASSET_UID).item[0] == "create"
    for bad in (dict(kind="asset_edit", asset_id=1973209), dict(kind="asset_edit", asset_id=1973209, name="  "),
                dict(kind="asset_edit", asset_id=1973209, expected_replacement_date="soon"),
                dict(kind="asset_edit", asset_id=1973209, owner_uid="nope"),
                dict(kind="asset_comment", asset_id=0, comments="x"), dict(kind="asset_link", asset_id=1)):
        with pytest.raises(ValidationError):
            parse(bad)


def test_article_actions_are_typed_bounded_and_carry_an_item_identity():
    from dynamix_manager.ticket_writes.models import (ArticleCreateAction, ArticleEditAction, ArticleLinkAction,
                                                       ArticleUnlinkAction, CategoryCreateAction, CategoryEditAction)
    create = parse(dict(kind="article_create", subject="Reset MFA", body="Step one", category_id=9212, owner_uid=ASSET_UID))
    assert isinstance(create, ArticleCreateAction) and create.item == ("create", None) and create.ticket_id == 0
    assert create.status == "not_submitted" and create.is_published is False and create.is_public is False
    assert create.model_dump(mode="json") == {"kind": "article_create", "subject": "Reset MFA", "body": "Step one", "category_id": 9212,
                                              "owner_uid": ASSET_UID}
    assert parse(dict(kind="article_create", subject="s", body="x", category_id=1, owning_group_id=77)).owner_uid is None
    edit = parse(dict(kind="article_edit", article_id=95821, status="archived", is_published=False, tags=["macos"]))
    assert isinstance(edit, ArticleEditAction) and edit.item == ("article", 95821) and edit.ticket_id == 0
    assert edit.model_dump(mode="json") == {"kind": "article_edit", "article_id": 95821, "status": "archived", "is_published": False, "tags": ["macos"]}
    link = parse(dict(kind="article_link", article_id=95821, asset_id=1973209))
    assert isinstance(link, ArticleLinkAction) and link.item == ("article", 95821)
    unlink = parse(dict(kind="article_unlink", article_id=95821, related_article_id=84764))
    assert isinstance(unlink, ArticleUnlinkAction) and unlink.related_article_id == 84764
    category = parse(dict(kind="category_create", name="Scratch", parent_id=10548))
    assert isinstance(category, CategoryCreateAction) and category.item == ("create", None) and category.is_public is False
    cat_edit = parse(dict(kind="category_edit", category_id=9107, name="Adobe apps"))
    assert isinstance(cat_edit, CategoryEditAction) and cat_edit.item == ("category", 9107) and cat_edit.ticket_id == 0
    for bad in (dict(kind="article_create", subject=" ", body="x", category_id=1, owner_uid=ASSET_UID),
                dict(kind="article_create", subject="s", body="", category_id=1, owner_uid=ASSET_UID),
                dict(kind="article_create", subject="s", body="x", category_id=1, owner_uid=ASSET_UID, status="published"),
                dict(kind="article_create", subject="s", body="x", category_id=1, owner_uid=ASSET_UID, tags=["a" * 101]),
                dict(kind="article_create", subject="s", body="x", category_id=1),  # TDX: exactly one of owner or group
                dict(kind="article_create", subject="s", body="x", category_id=1, owner_uid=ASSET_UID, owning_group_id=77),
                dict(kind="article_edit", article_id=95821),
                dict(kind="article_edit", article_id=95821, review_date="soon"),
                dict(kind="article_link", article_id=95821),
                dict(kind="article_link", article_id=95821, asset_id=1, related_article_id=2),
                dict(kind="article_link", article_id=95821, related_article_id=95821),
                dict(kind="category_edit", category_id=9107),
                dict(kind="category_create", name="  ")):
        with pytest.raises(ValidationError):
            parse(bad)
