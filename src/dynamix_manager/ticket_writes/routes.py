"""Retired browser review routes. Old links stay inert; nothing here reads a request body."""

from starlette.responses import PlainTextResponse
from starlette.routing import Route


def create_retired_ticket_write_routes():
    """Keep old links inert; no request parsing, service access, or credentials."""
    class RetiredEndpoint:
        async def __call__(self, scope, receive, send):
            response = PlainTextResponse(
                "This review page has been retired and cannot submit changes. "
                "Return to ChatGPT and explicitly request the ticket update there.",
                status_code=410,
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
            )
            await response(scope, receive, send)

    return [Route(path, RetiredEndpoint()) for path in (
        "/writes/review", "/writes/open", "/writes/save",
    )]
