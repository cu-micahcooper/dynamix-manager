"""Hosted MCP tools for preparing—not applying—reviewable ticket changes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any, Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.fastmcp.utilities.func_metadata import FuncMetadata
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adapter import WriteAdapter
from .models import AssignAction, ChangePreview, CommentAction, EditAction, StatusAction
from .service import WriteAuthorizationRequired, WritesDisabled


WRITE_SCHEMES = [{"type": "oauth2", "scopes": ["tdx.read", "tdx.write"]}]
READ_SCHEMES = [{"type": "oauth2", "scopes": ["tdx.read"]}]
NOT_SAVED = "NOT SAVED. Review the immutable preview and use the review link to approve Save."
OPERATION_ID = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{32}$")]
METADATA_SEARCH = Annotated[str, Field(strict=True, min_length=2, max_length=100)]
METADATA_LIMIT = Annotated[int, Field(strict=True, ge=1, le=10)]
MetadataKind = Literal["statuses", "priorities", "people", "groups"]


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PrepareOutput(_Output):
    not_saved: Literal[True]
    message: Literal[
        "NOT SAVED. Review the immutable preview and use the review link to approve Save."
    ]
    operation_id: OPERATION_ID
    preview: ChangePreview
    review_url: str
    expires_at: float


class ResultOutput(_Output):
    operation_id: OPERATION_ID
    outcome: Literal["pending", "applied", "rejected", "unknown", "conflict", "expired"]
    message: str
    ticket_id: int
    ticket_url: str
    status_code: int | None = None


class MetadataOutput(_Output):
    kind: MetadataKind
    results: list[dict[str, Any]]
    returned: int
    complete: Literal[False]


class SafeFuncMetadata(FuncMetadata):
    """Sanitize only these tools' pre-call validation and preserve error metadata."""

    def pre_parse_json(self, data):
        # These hosted write tools require the JSON types declared by their
        # schemas. Do not apply FastMCP's legacy JSON-inside-string coercion.
        return data.copy()

    async def call_fn_with_arg_validation(
        self,
        fn,
        fn_is_async,
        arguments_to_validate,
        arguments_to_pass_directly,
    ):
        try:
            arguments = self.pre_parse_json(arguments_to_validate)
            parsed = self.arg_model.model_validate(arguments)
        except (ValidationError, TypeError, ValueError, AttributeError):
            raise ValueError("Invalid tool arguments.") from None
        values = parsed.model_dump_one_level()
        values |= arguments_to_pass_directly or {}
        if fn_is_async:
            return await fn(**values)
        return fn(**values)

    def convert_result(self, result):
        # Typed success responses still receive outputSchema validation. Error
        # CallToolResults intentionally have no structuredContent to validate.
        if isinstance(result, CallToolResult) and result.isError:
            return result
        return super().convert_result(result)


def _error(message, *, challenge=None):
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        isError=True,
        **({"_meta": {"mcp/www_authenticate": [challenge]}} if challenge else {}),
    )


def _write_challenge(public_url):
    return (
        'Bearer error="insufficient_scope", '
        'error_description="Additional authorization is required for ticket write review.", '
        'scope="tdx.read tdx.write", '
        f'resource_metadata="{public_url}/.well-known/oauth-protected-resource/mcp"'
    )


def _prepare(service, action):
    try:
        prepared = service.prepare(get_access_token(), action)
    except WriteAuthorizationRequired:
        return _error(
            "Additional authorization is required to prepare this ticket change.",
            challenge=_write_challenge(service.settings.public_url),
        )
    except WritesDisabled:
        return _error("Hosted ticket write preparation is disabled.")
    except Exception:
        return _error("The ticket change could not be safely prepared.")
    try:
        return PrepareOutput(
            not_saved=True,
            message=NOT_SAVED,
            operation_id=prepared.operation_id,
            preview=prepared.preview,
            review_url=prepared.review_url,
            expires_at=prepared.expires_at,
        )
    except Exception:
        return _error("The ticket change could not be safely prepared.")


def _result(service, operation_id):
    try:
        status = service.result(get_access_token(), operation_id)
    except WriteAuthorizationRequired:
        return _error(
            "Additional authorization is required to read this ticket-write result.",
            challenge=_write_challenge(service.settings.public_url),
        )
    except Exception:
        return _error("The ticket-write result is unavailable.")
    try:
        return ResultOutput.model_validate(asdict(status))
    except Exception:
        return _error("The ticket-write result is unavailable.")


def _harden_tool(server, name):
    tool = server._tool_manager.get_tool(name)
    argument_model = tool.fn_metadata.arg_model
    argument_model.model_config = {
        **argument_model.model_config,
        "extra": "forbid",
        "hide_input_in_errors": True,
    }
    argument_model.model_rebuild(force=True)
    tool.parameters = argument_model.model_json_schema(by_alias=True)
    metadata = tool.fn_metadata
    tool.fn_metadata = SafeFuncMetadata(
        arg_model=argument_model,
        output_schema=metadata.output_schema,
        output_model=metadata.output_model,
        wrap_output=metadata.wrap_output,
    )


def register_ticket_write_tools(server, tool, service, connection_provider):
    """Register the hosted-only preparation and bounded discovery surface."""
    prepare = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=True,
    )
    read = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=True,
    )
    write_meta = {"securitySchemes": WRITE_SCHEMES}
    read_meta = {"securitySchemes": READ_SCHEMES}

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def prepare_ticket_comment(action: CommentAction) -> PrepareOutput:
        """Prepare a comment preview and review link. This does not save the comment."""
        return _prepare(service, action)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def prepare_ticket_status(action: StatusAction) -> PrepareOutput:
        """Prepare a status preview and review link. This does not save the status."""
        return _prepare(service, action)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def prepare_ticket_assignment(action: AssignAction) -> PrepareOutput:
        """Prepare an assignment preview and review link. This does not save it."""
        return _prepare(service, action)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def prepare_ticket_edit(action: EditAction) -> PrepareOutput:
        """Prepare a field-edit preview and review link. This does not save it."""
        return _prepare(service, action)

    @tool(annotations=read, meta=read_meta, structured_output=True)
    def ticket_write_metadata(
        kind: MetadataKind,
        search: METADATA_SEARCH | None = None,
        limit: METADATA_LIMIT = 10,
    ) -> MetadataOutput:
        """Read a bounded set of active options used to prepare ticket changes."""
        try:
            adapter = WriteAdapter.from_connection(connection_provider())
            return MetadataOutput.model_validate(
                adapter.discover_metadata(kind, search=search, limit=limit)
            )
        except Exception:
            return _error("Ticket write metadata is unavailable.")

    @tool(annotations=read, meta=write_meta, structured_output=True)
    def ticket_write_result(operation_id: OPERATION_ID) -> ResultOutput:
        """Read the safe result for an operation owned by the current write grant."""
        return _result(service, operation_id)

    names = (
        "prepare_ticket_comment",
        "prepare_ticket_status",
        "prepare_ticket_assignment",
        "prepare_ticket_edit",
        "ticket_write_metadata",
        "ticket_write_result",
    )
    for name in names:
        _harden_tool(server, name)
