# Confirmed ticket writes in the hosted ChatGPT pilot

## Approved intent and boundaries

Extend the working personal Railway connector from read-only ticket access to
comments, status changes, reassignment, ticket creation, and ticket-field edits.
Every mutation uses preview, explicit human confirmation, then save. Keep the
verified personal UID restriction and upstream TDX permissions. No deletion,
bulk operations, attachments, administrative impersonation, or multi-user rollout.
The local Codex connector stays read-only in this increment.

## Approach

Use a server-owned pending-change record plus a human-operated confirmation page.
Immediate mutation tools are rejected because their arguments alone do not prove
human approval. Chat-only instructions to ask for confirmation are also insufficient
as the only enforcement. A hosted confirmation page adds one browser step but
allows the server to bind approval to an exact immutable change.

Tools prepare changes and return a short-lived review link; they cannot commit
changes. The review link contains an opaque, unguessable, single-use capability,
not ticket content or credentials. Opening it is read-only. Only the explicit
Save form submission commits. Use a five-minute expiry, browser-bound cookie,
CSRF protection, exact-origin validation, no external assets, no-store headers,
no referrer disclosure, and no query/access logging for these routes. Treat the
link as sensitive; possession permits viewing the proposed change and confirming
it. Do not expose a callable MCP commit tool or let the widget auto-submit.

## Supported operations

- Add a comment with explicitly selected public/private visibility and explicit
  notification recipients. Default to private and no notifications; never silently
  substitute visibility or recipients unsupported by the verified upstream API.
- Change status, with any required closure/update text shown in the preview.
- Reassign to a resolved active user/group supported by the ticket application.
- Edit title, description, and priority. Other fields, custom attributes, and
  workflows are out of scope for edits in this increment.
- Create tickets using the application's required fields and validated metadata:
  title, description, requester, type/form, status, priority, and any required
  application-specific fields. Required custom fields may be supplied for creation
  only through verified metadata and typed validation. Unsupported required forms
  must fail before presenting a savable preview, with a specific explanation.

Only InfoTech Tickets is in scope. Resolve IDs to names and validate against live
metadata/permissions; do not guess IDs. Display the action, application, ticket ID
and title (existing tickets), before/after values, visibility, recipients, and
any documented implicit notifications before confirmation. If notification effects
cannot be determined reliably for an operation, block it pending clarification.
Treat all upstream ticket text as untrusted content and escape it on review pages.

## Authorization and isolation

Introduce an independently enforced `tdx.write` scope in addition to `tdx.read`.
Existing read grants must not gain writes through refresh or server deployment.
Require a new explicit consent flow for writes, update discovery and per-tool
security metadata, and preserve the existing read-only paths. Server-side scope
checks apply both when preparing and committing changes. Bind pending records to
the authenticated subject, OAuth client/resource and grant family; the confirmation
capability is delegated only to that immutable record. Before saving, revalidate
that grant family is active, its original write permission remains valid, and the
upstream identity/token and ticket permissions remain valid. Revocation or expiry
blocks saving. No admin credentials or fallback identities.

## Storage and commit semantics

Store pending changes encrypted alongside the existing protected SQLite vault,
with bounded records and expiry cleanup. Include immutable normalized payload,
payload hash, owner/grant binding, baseline ticket version or relevant field
snapshot, and lifecycle state. Never record passwords, tokens or ticket bodies
in application logs. Audit only action, actor identifier, ticket ID, timestamps,
operation ID and outcome; keep audit storage private with a bounded retention policy
of 30 days and a hard cap of 10,000 records.

Atomically claim a pending operation before sending one upstream request. Duplicate
submissions return the recorded result and never issue another request. Restart or
timeout after dispatch is an unknown outcome, not permission to retry. Do not use
the client's read retry behavior for mutations. Preserve durable sending/unknown
records until reconciled; never transition them automatically to pending. The
five-minute expiry applies only to approval capabilities and unsubmitted previews.
Unresolved dispatched operations retain a durable marker keyed by actor,
application, target and normalized payload hash, blocking equivalent preparations
even after preview cleanup. Read-only reconciliation may mark success only with
authoritative evidence of the specific operation, or failure only with authoritative
evidence that it was not applied. Otherwise keep it unknown and blocked; an absence
in a bounded activity feed is not evidence of failure. Unresolved markers are not
subject to the audit retention limit; cap them at 1,000 and fail closed on new
preparations when full rather than evicting them.
Successful saves re-read the ticket where feasible and show its ID and result.
If the upstream response is ambiguous, show unknown outcome and a read-only ticket
link; require reconciliation before offering another equivalent write. Do not
claim exactly-once delivery where the upstream API cannot guarantee it.

Immediately before editing/status/reassignment, re-read and compare the saved
baseline. Changed relevant fields or unavailable baseline means conflict: save
nothing and require a new preview. Use upstream conditional writes if supported.
If unsupported, document that preflight detection cannot close the final race;
send only intended fields through a verified partial-update API. Do not fall back
to full-object replacement that can overwrite unrelated concurrent edits.

## Implementation boundaries and API verification

Keep schema/validation, pending-operation store, upstream mutation adapter, and
review routes separate. Integrate tool registration with existing plugin.py and
authorization with hosted.py/personal_auth.py without broad refactors.
Before coding mutation calls, verify official Cedarville/TeamDynamix API contracts
for each supported operation, metadata, notifications, permission errors and
concurrency support. Endpoint or semantics uncertainty is a blocking evidence gap,
not license to invent a request or perform a live exploratory write. Plan this
verification as the first implementation task; surface unsupported capabilities
before changing this scope.

Mark mutation preparation tools accurately as non-read-only and non-idempotent
where they allocate pending records, with appropriate security schemes. Existing
read tools remain read-only. UI explains that preparing is not saving. Deployment
uses a write feature flag, default off, checked at preparation and again at Save,
and preserves Railway/Azure portability. Disabling it blocks outstanding review
links from committing without deleting diagnostic or reconciliation records.

## Tests and release gates

Use test-first implementation with mocked upstream APIs. Cover every operation,
validation, scope elevation denial, revoked/expired grants, cross-user/client
access, CSRF/origin/cookie failures, malformed capabilities, expiry, stored-XSS,
conflicts, notification rendering, duplicate/concurrent saves, restarts, ambiguous
timeouts and upstream permission/rate-limit/server errors without write retries.
Exercise review pages in a browser against synthetic data, and rerun all existing
Python/frontend suites. Verify published tool metadata and read-only regression.

Live checks before ticket-write approval are limited to authentication, metadata,
tool discovery, and reads. A real save requires the user to approve the exact
ticket, change, recipients and visibility (or disposable ticket creation contents).
Do not treat approval to build as approval to mutate a production ticket. The
pilot is not fully write-verified until an explicitly approved live mutation and
read-back succeed. Scope-expansion consent and deployment/rollback instructions
must be documented; disable the write flag to roll back without breaking reads.
