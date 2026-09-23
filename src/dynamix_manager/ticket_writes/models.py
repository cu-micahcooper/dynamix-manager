"""Bounded immutable inputs and server-owned normalized change records."""
from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import (AfterValidator, BaseModel, ConfigDict, Field, TypeAdapter, field_validator,
                      model_serializer, model_validator)

PositiveID = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=20000)]
Email = Annotated[str, Field(strict=True, max_length=254, pattern=r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")]


class ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TicketAction(ImmutableModel):
    ticket_id: PositiveID

    @property
    def item(self):
        """The record this action locks and is deduplicated against: (domain, id)."""
        return ("ticket", self.ticket_id)

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        # Omitted edit/assignment fields must remain omitted after encrypted storage.
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


def _iso_date(value):
    from datetime import date, datetime
    try:
        (datetime.fromisoformat(value.replace("Z", "+00:00")) if "T" in value else date.fromisoformat(value))
    except ValueError:
        raise ValueError("Dates must be ISO 8601, for example 2029-06-30.") from None
    return value


class CommentAction(TicketAction):
    kind: Literal["comment"]
    comments: Text
    is_private: Annotated[bool, Field(strict=True)] = True
    notify: Annotated[tuple[Email, ...], Field(max_length=50)] = ()

    @field_validator("comments")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Comment must contain text.")
        return value


class StatusAction(CommentAction):
    kind: Literal["status"]
    status_id: PositiveID


class AssignAction(TicketAction):
    kind: Literal["assign"]
    responsible_uid: UUID | None = None
    responsible_group_id: PositiveID | None = None
    notify_new_responsible: Annotated[bool, Field(strict=True)] = False

    @model_validator(mode="after")
    def explicit_assignment(self):
        fields = self.model_fields_set & {"responsible_uid", "responsible_group_id"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Select an explicit non-null person or group; clearing is unsupported.")
        return self


class EditAction(TicketAction):
    kind: Literal["edit"]
    title: Annotated[str, Field(strict=True, min_length=1, max_length=300)] | None = None
    description: Annotated[str, Field(strict=True, max_length=20000)] | None = None
    priority_id: PositiveID | None = None

    @model_validator(mode="after")
    def explicit_edit(self):
        fields = self.model_fields_set & {"title", "description", "priority_id"}
        if not fields or any(getattr(self, field) is None for field in fields - {"description"}):
            raise ValueError("Select at least one field; only description permits null.")
        if "title" in fields and not self.title.strip():
            raise ValueError("Title must contain text.")
        return self


class TaskAction(TicketAction):
    """Mark one ticket task 100% complete through the task feed, optionally with a comment."""

    kind: Literal["task"]
    task_id: PositiveID
    comments: Text | None = None
    is_private: Annotated[bool, Field(strict=True)] = True
    notify: Annotated[tuple[Email, ...], Field(max_length=50)] = ()

    @field_validator("comments")
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Comment must contain text.")
        return value


class CreateAction(ImmutableModel):
    """Create a ticket; omitted status/priority/form fall back to the tenant's defaults."""

    kind: Literal["create"]
    title: Annotated[str, Field(strict=True, min_length=1, max_length=300)]
    description: Annotated[str, Field(strict=True, max_length=20000)] | None = None
    type_id: PositiveID
    account_id: PositiveID
    requestor_uid: UUID
    form_id: PositiveID | None = None
    status_id: PositiveID | None = None
    priority_id: PositiveID | None = None
    service_id: PositiveID | None = None
    source_id: PositiveID | None = None
    responsible_uid: UUID | None = None
    responsible_group_id: PositiveID | None = None
    notify_requestor: Annotated[bool, Field(strict=True)] = False

    @property
    def ticket_id(self):
        """No ticket exists until TeamDynamix creates it."""
        return 0

    @property
    def item(self):
        return ("create", None)

    @field_validator("title")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Title must contain text.")
        return value

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class AssetAction(ImmutableModel):
    """Base for actions on an asset in the tenant's asset application."""

    asset_id: PositiveID

    @property
    def item(self):
        return ("asset", self.asset_id)

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class _AssetOnly(AssetAction):
    """Asset actions that touch no ticket; ``ticket_id`` is 0 for the shared write pipeline."""

    @property
    def ticket_id(self):
        return 0


class AssetCommentAction(_AssetOnly):
    kind: Literal["asset_comment"]
    comments: Text
    is_private: Annotated[bool, Field(strict=True)] = True
    notify: Annotated[tuple[Email, ...], Field(max_length=50)] = ()

    @field_validator("comments")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Comment must contain text.")
        return value


class LinkAssetAction(AssetAction):
    """Associate an asset with a ticket; locks the ticket like other ticket writes."""

    kind: Literal["asset_link"]
    ticket_id: PositiveID

    @property
    def item(self):
        return ("ticket", self.ticket_id)


class EditAssetAction(_AssetOnly):
    kind: Literal["asset_edit"]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=300)] | None = None
    tag: Annotated[str, Field(strict=True, max_length=100)] | None = None
    serial_number: Annotated[str, Field(strict=True, max_length=100)] | None = None
    status_id: PositiveID | None = None
    owner_uid: UUID | None = None
    owning_department_id: PositiveID | None = None
    location_id: PositiveID | None = None
    location_room_id: PositiveID | None = None
    external_id: Annotated[str, Field(strict=True, max_length=100)] | None = None
    expected_replacement_date: Annotated[str, Field(strict=True, min_length=10, max_length=35),
                                         AfterValidator(_iso_date)] | None = None

    EDITABLE: ClassVar[tuple[str, ...]] = ("name", "tag", "serial_number", "status_id", "owner_uid",
                                             "owning_department_id", "location_id", "location_room_id",
                                             "external_id", "expected_replacement_date")

    @model_validator(mode="after")
    def explicit_edit(self):
        fields = self.model_fields_set & set(self.EDITABLE)
        if not fields:
            raise ValueError("Select at least one asset field to change.")
        if any(getattr(self, field) is None for field in fields - {"external_id"}):
            raise ValueError("Only external_id may be cleared with null.")
        if "name" in fields and not self.name.strip():
            raise ValueError("Name must contain text.")
        return self


class TicketRelationAction(TicketAction):
    """Marker base for ticket writes that add or remove relations or settings on an existing ticket."""


class TicketContactAction(TicketRelationAction):
    kind: Literal["ticket_contact"]
    contact_uid: UUID
    remove: Annotated[bool, Field(strict=True)] = False


Tag = Annotated[str, Field(strict=True, min_length=1, max_length=100)]


class TicketTagsAction(TicketRelationAction):
    kind: Literal["ticket_tags"]
    tags: Annotated[tuple[Tag, ...], Field(min_length=1, max_length=50)]
    remove: Annotated[bool, Field(strict=True)] = False

    @field_validator("tags")
    @classmethod
    def nonblank_tags(cls, value):
        if any(not tag.strip() for tag in value):
            raise ValueError("Tags must contain text.")
        return value


class ChildTicketsAction(TicketRelationAction):
    kind: Literal["ticket_children"]
    child_ticket_ids: Annotated[tuple[PositiveID, ...], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def distinct_children(self):
        if len(set(self.child_ticket_ids)) != len(self.child_ticket_ids) or self.ticket_id in self.child_ticket_ids:
            raise ValueError("Child ticket IDs must be distinct and different from the parent.")
        return self


SLA_START = Literal["now", "created"]
SLA_START_IDS = {"now": 0, "created": 1}


class TicketSlaAction(TicketRelationAction):
    """Assign an SLA (sla_id) or remove the current one (sla_id null)."""

    kind: Literal["ticket_sla"]
    sla_id: PositiveID | None
    comments: Annotated[str, Field(strict=True, max_length=20000)] = ""
    notify: Annotated[tuple[Email, ...], Field(max_length=50)] = ()
    cascade: Annotated[bool, Field(strict=True)] = False
    start_basis: SLA_START = "now"


CLASSIFICATION = Literal["incident", "problem", "change", "release", "service_request", "major_incident"]
CLASSIFICATION_IDS = {"incident": 32, "problem": 33, "change": 34, "release": 35, "service_request": 46, "major_incident": 77}
CLASSIFICATION_NAMES = {32: "Incident", 33: "Problem", 34: "Change", 35: "Release", 46: "Service Request", 77: "Major Incident"}


class ReclassifyAction(TicketRelationAction):
    kind: Literal["reclassify"]
    classification: CLASSIFICATION

    @property
    def classification_id(self):
        return CLASSIFICATION_IDS[self.classification]


ARTICLE_STATUS = Literal["not_submitted", "submitted", "approved", "rejected", "archived"]
ARTICLE_STATUS_IDS = {"not_submitted": 1, "submitted": 2, "approved": 3, "rejected": 4, "archived": 5}
ArticleTitle = Annotated[str, Field(strict=True, min_length=1, max_length=300)]
ArticleBody = Annotated[str, Field(strict=True, min_length=1, max_length=200000)]
Tags = Annotated[tuple[Annotated[str, Field(strict=True, min_length=1, max_length=100)], ...], Field(max_length=50)]
IsoDate = Annotated[str, Field(strict=True, min_length=10, max_length=35), AfterValidator(_iso_date)]


def _nonblank(value):
    if value is not None and not value.strip():
        raise ValueError("Text fields must contain text.")
    return value


class _NoTicket(ImmutableModel):
    """Knowledge base actions touch no ticket; ``ticket_id`` is 0 for the shared write pipeline."""

    @property
    def ticket_id(self):
        return 0

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class ArticleAction(_NoTicket):
    """Base for actions on an existing knowledge base article."""

    article_id: PositiveID

    @property
    def item(self):
        return ("article", self.article_id)


class _ArticleFields(ImmutableModel):
    subject: ArticleTitle | None = None
    summary: Annotated[str, Field(strict=True, max_length=2000)] | None = None
    body: ArticleBody | None = None
    tags: Tags | None = None
    category_id: PositiveID | None = None
    owner_uid: UUID | None = None
    owning_group_id: PositiveID | None = None
    review_date: IsoDate | None = None
    # IsPublished / IsPublic are accepted but ignored by the tenant API (observed live 2026-09-22), so they are not offered.
    status: ARTICLE_STATUS | None = None
    notify_owner: Annotated[bool, Field(strict=True)] | None = None
    notify_owner_of_review_date: Annotated[bool, Field(strict=True)] | None = None
    order: Annotated[float, Field(strict=True, ge=0)] | None = None

    @field_validator("subject", "body", "summary")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)


class ArticleEditAction(ArticleAction, _ArticleFields):
    kind: Literal["article_edit"]
    EDITABLE: ClassVar[tuple[str, ...]] = ("subject", "summary", "body", "tags", "category_id", "owner_uid", "owning_group_id",
                                           "review_date", "status", "notify_owner", "notify_owner_of_review_date", "order")

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not (self.model_fields_set & set(self.EDITABLE)):
            raise ValueError("Select at least one article field to change.")
        return self


class _ArticleTarget(ArticleAction):
    asset_id: PositiveID | None = None
    related_article_id: PositiveID | None = None

    @model_validator(mode="after")
    def exactly_one_target(self):
        if (self.asset_id is None) == (self.related_article_id is None):
            raise ValueError("Give exactly one of asset_id or related_article_id.")
        if self.related_article_id == self.article_id:
            raise ValueError("An article cannot be related to itself.")
        return self


class ArticleLinkAction(_ArticleTarget):
    kind: Literal["article_link"]


class ArticleUnlinkAction(_ArticleTarget):
    kind: Literal["article_unlink"]


class ArticleCreateAction(_NoTicket, _ArticleFields):
    kind: Literal["article_create"]
    subject: ArticleTitle
    body: ArticleBody
    category_id: PositiveID
    status: ARTICLE_STATUS = "not_submitted"

    @model_validator(mode="after")
    def exactly_one_owner(self):
        # TeamDynamix: "Exactly one of OwningGroupID or OwnerUID must be provided." (observed live 2026-09-22)
        if (self.owner_uid is None) == (self.owning_group_id is None):
            raise ValueError("Give exactly one of owner_uid or owning_group_id.")
        return self

    @property
    def item(self):
        return ("create", None)


class CategoryAction(_NoTicket):
    category_id: PositiveID

    @property
    def item(self):
        return ("category", self.category_id)


class _CategoryFields(ImmutableModel):
    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(strict=True, max_length=2000)] | None = None
    parent_id: Annotated[int, Field(strict=True, ge=0)] | None = None  # 0 = top level
    order: Annotated[float, Field(strict=True, ge=0)] | None = None
    is_public: Annotated[bool, Field(strict=True)] | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)


class CategoryEditAction(CategoryAction, _CategoryFields):
    kind: Literal["category_edit"]
    EDITABLE: ClassVar[tuple[str, ...]] = ("name", "description", "parent_id", "order", "is_public")

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not (self.model_fields_set & set(self.EDITABLE)):
            raise ValueError("Select at least one category field to change.")
        return self


class CategoryCreateAction(_NoTicket, _CategoryFields):
    kind: Literal["category_create"]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    is_public: Annotated[bool, Field(strict=True)] = False
    inherit_permissions: Annotated[bool, Field(strict=True)] = False

    @property
    def item(self):
        return ("create", None)


Action = Annotated[CommentAction | StatusAction | AssignAction | EditAction | TaskAction | CreateAction
                   | AssetCommentAction | LinkAssetAction | EditAssetAction
                   | ArticleCreateAction | ArticleEditAction | ArticleLinkAction | ArticleUnlinkAction
                   | CategoryCreateAction | CategoryEditAction
                   | TicketContactAction | TicketTagsAction | ChildTicketsAction | TicketSlaAction | ReclassifyAction,
                   Field(discriminator="kind")]
_actions = TypeAdapter(Action)


def parse_action(value) -> Action:
    return _actions.validate_python(value)


class PreviewField(ImmutableModel):
    name: str
    before: str | None
    after: str | None


class ChangePreview(ImmutableModel):
    application: str
    ticket_id: Annotated[int, Field(strict=True, ge=0)]  # 0 while a ticket is being created
    ticket_title: str
    action: str
    fields: tuple[PreviewField, ...]
    visibility: str | None = None
    recipients: tuple[str, ...] = ()
    notices: tuple[str, ...] = ()


class PreparedChange(ImmutableModel):
    action: Action
    base_url: str
    app_id: PositiveID
    asset_app_id: PositiveID | None = None
    portal_app_id: PositiveID | None = None
    baseline_json: str
    payload_json: str
    preview: ChangePreview


class WriteResult(ImmutableModel):
    outcome: Literal["applied", "rejected", "unknown"]
    message: str
    status_code: int | None = None
    detail: dict | None = None  # safe projection of what the tenant applied (e.g. created ticket ID)
