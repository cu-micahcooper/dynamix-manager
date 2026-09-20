# Direct hosted ticket writes

## Decision and scope

The user explicitly replaced the hosted connector's review-and-Save requirement:
an explicit conversational request is sufficient to submit a ticket change. No
connector-managed confirmation, approval page, or second conversational approval
is required. Authentication and access authorization remain mandatory. This
supersedes the earlier hosted write design, not the separate local workbench UX.

Use newly named direct mutation tools for comments, status, assignments, and
supported field edits. Do not silently turn existing prepare tools into mutation
tools. A separate prepare/commit conversation adds an unnecessary step; reusing
the browser form internally obscures the new security contract. Prefer direct
tools over both alternatives.

## Invocation contract

Expose `add_ticket_comment`, `update_ticket_status`, `assign_ticket`, and
`edit_ticket`, using the current strict typed action models. Each call requires
a client-generated request ID, reused for retries of that same logical request.
Return the operation ID, ticket link, outcome, and safe result message in chat.
No review URL, browser cookie, CSRF token, or review capability is required or
returned. Retain read-only metadata and result tools.

Tool descriptions and server instructions must require an explicit user request
for the ticket and action; ticket text and other retrieved content are data,
never authority to write. Resolve ambiguous ticket, status, recipient, or scope
before calling a mutation tool. There is no mandatory confirmation after the
request is clear. Keep truthful mutating/destructive MCP annotations; do not
claim to suppress confirmation UI imposed by ChatGPT itself. Existing OAuth
write grants remain required; this change does not silently widen read grants.

## Service and durable storage

Add a principal-authenticated direct submission path, sharing the existing
validated action, live preflight, one-attempt dispatch, and safe-result logic.
Do not simulate browser approval. Keep the personal-pilot feature gate, identity
matching, per-user TDX credentials, active write grant checks immediately before
dispatch, metadata validation, baseline conflict detection, ticket-level locks,
encrypted storage, and audit records.

Bind request IDs durably to the exact grant owner and normalized typed action.
The same ID and action returns the existing operation/result without another
dispatch, including concurrent calls and process restarts. Reusing an ID with
different arguments fails. Store a compact deduplication tombstone for 30 days,
separate from the existing short result retention; after result expiry return
a safe already-processed response, not a fresh mutation. Document the bounded
deduplication window. Never store credentials or raw action text in plaintext.
Different request IDs are distinct requests, but unresolved equivalent-action
and ticket locks must still prevent accidental replays of uncertain operations.

Do not retry a timed-out, interrupted, or otherwise uncertain TDX mutation.
Persist `unknown` and keep its lock until authoritative reconciliation. Preserve
the existing HTTP 200/201 feed success handling and PATCH identity checks.
Notification acceptance is not proof of email delivery.

## Retire the hosted review surface

Remove prepare tools from the advertised hosted MCP tool list. Disable the
hosted `/writes/review`, `/writes/open`, and `/writes/save` routes with a static
410 response explaining that the old link cannot submit anything. Never convert
an existing preview into a direct write or resend an existing operation. Retain
historical records and unresolved fences; unused previews may expire normally.
Update current hosted documentation, instructions, and OAuth consent wording
where they still promise a mandatory browser review. Do not change local
workbench routes or unrelated files.

## Verification and rollout

Use test-first implementation. Cover all four direct tool schemas and actual
service dispatch; missing/expired/read-only/revoked grants; disabled writes;
identity mismatch; invalid metadata; baseline conflict; same-ID replay,
concurrency, restart and changed-payload rejection; owner isolation; expired
result tombstones; unknown outcomes and retained locks; sanitized failures;
and old review routes producing no mutation. Retain adapter regressions and run
the full Python suite, relevant frontend tests, lint, and diff checks.

Deploy a source-only build to the existing Railway service after verification.
Verify health and authenticated MCP discovery without creating a live ticket
change. Refresh the ChatGPT tool catalog if needed; report any host-controlled
approval behavior separately. This implementation request does not authorize
a test comment, notification, or closing any existing ticket.
