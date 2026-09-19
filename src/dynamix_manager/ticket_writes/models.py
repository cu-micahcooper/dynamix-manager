"""Bounded immutable inputs and server-owned normalized change records."""
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_serializer, model_validator

PositiveID = Annotated[int, Field(strict=True, gt=0)]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=20000)]
Email = Annotated[str, Field(strict=True, max_length=254, pattern=r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+$")]


class ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TicketAction(ImmutableModel):
    ticket_id: PositiveID

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        # Omitted edit/assignment fields must remain omitted after encrypted storage.
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


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


Action = Annotated[CommentAction | StatusAction | AssignAction | EditAction, Field(discriminator="kind")]
_actions = TypeAdapter(Action)


def parse_action(value) -> Action:
    return _actions.validate_python(value)


class PreviewField(ImmutableModel):
    name: str
    before: str | None
    after: str | None


class ChangePreview(ImmutableModel):
    application: str
    ticket_id: PositiveID
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
    baseline_json: str
    payload_json: str
    preview: ChangePreview


class WriteResult(ImmutableModel):
    outcome: Literal["applied", "rejected", "unknown"]
    message: str
    status_code: int | None = None
