# Personal TeamDynamix ticket workbench

Date: 2026-09-17
Status: Design and written specification approved in conversation; implementation exists on codex/personal-ticket-workbench. Live integration gates are recorded in the implementation plan.

## Purpose and approved scope

Build a single-user local web app that presents open TeamDynamix tickets assigned directly to the authenticated user, one at a time. OpenAI suggests next steps and communication using only the current ticket, its conversation history, and instructions the user supplies for that ticket. The user edits and reviews each submission before explicitly sending it under their TeamDynamix identity.

The user selected layout A: original ticket and history on the left, AI assessment and editable communication on the right. The user can use either institutional SSO or a TeamDynamix username/password. API authentication capability must be verified; browser SSO availability alone does not establish API access.

Excluded from version one: knowledge-base retrieval, other tickets as AI context, attachment contents, autonomous sends, bulk changes, group-assigned queues, reassignment, time entry, and public hosting. Existing analysis notebooks and outputs remain intact.

## Existing foundation

Project relocated by user request to `/Users/micahcooper/dynamix-manager` on branch `codex/personal-ticket-workbench`, based on commit `75e0c3b`. This repository already contains `src/dynamix_manager/tdx_client.py`, configuration, CLI, ticket processing, and DuckDB analytics. Implementation must inspect and extend these existing components rather than importing the starter client or replacing existing analytics. The approved user workflow remains unchanged. The following starter-workspace observations are historical and must be reverified in this repository.

The original `/Users/micahcooper/Documents/codexamix` workspace contains `teamdynamix_client.py`, a requests-based client supporting password authentication, bearer tokens, account lookup, ticket search, and individual ticket retrieval. Mocked client tests exist. There is currently no Git repository in this directory. Existing `.env` configuration may be reused by name without displaying secrets. Do not use the client's administrator login path for the personal workbench.

Extend the client with history, status/metadata, and submission operations only after verifying the target tenant's documented API contracts. Do not assume the existing ticket search returns all results or that a browser password is API-enabled.

## Architecture and data flow

Use a Python HTTP backend with a small browser frontend, served from the same origin and bound to loopback. Prefer FastAPI with ordinary HTML/CSS/JavaScript for this small app; retain the existing requests client. Use SQLite for local drafts, skipped-ticket state, and submission receipts. No separate hosted frontend or shared database is needed.

Keep responsibilities separate: TeamDynamix adapter, queue service, OpenAI suggestion service, draft/submission service, and UI. The adapters normalize external responses; UI code does not depend on vendor payload shapes.

Flow: authenticate -> confirm account identity and ticketing application -> load queue -> fetch current ticket and history -> optionally request AI suggestions -> edit -> review exact proposed update -> refresh ticket/history -> submit -> confirm and record receipt -> advance.

Credentials and the OpenAI API key remain in backend configuration or server memory. Do not put them in browser storage, SQLite, URLs, or logs. Do not persist passwords entered through login. Keep tokens in server memory and clear on logout. Enforce same-origin requests, CSRF protection on mutations, allowed Host checks, and a local authenticated session; loopback binding alone is insufficient. Sanitize vendor HTML before display and treat ticket content as untrusted input, including within AI prompts.

## Authentication and discovery

First support user-scoped API password authentication or a user-scoped bearer token if the tenant permits it. Confirm identity through the account endpoint, display that identity, and scope every queue and write to it. An unavailable API login must produce an actionable error; do not silently fall back to administrator access or automate SSO to extract credentials. If both supported API paths are unavailable, report live integration as blocked and keep demo mode usable.

Verify tenant base URL, ticketing app ID, identity identifier, status classifications, history visibility semantics, recipient eligibility, submission API shape, and any search limits in read-only discovery. These are implementation discovery tasks, not facts already established by this design.

## Queue and ticket context

Include tickets directly assigned to the signed-in user whose tenant status classification is active, excluding resolved, closed, and cancelled terminal states. Include on-hold tickets if classified as active. Use tenant metadata rather than guessing from display names. Handle all pages or supported search partitions; if completeness cannot be established, show an explicit incomplete-queue warning.

Order overdue tickets first, then priority, then oldest unanswered requester activity, with ticket ID as a stable tie breaker. Where no unanswered requester message exists, use last activity time. Derive unanswered activity from the most recent requester message after the most recent public staff response; internal notes do not count as a requester response. Normalize timestamps and display local time. Missing due dates are not overdue.

Show title, ID, requester, status, priority, due date, description, and chronological history with author, time, and public/internal labels. Provide the original TeamDynamix link. Fetch complete relevant history; label incomplete history and disable AI generation until the app can accurately describe the available context or recover it. Do not represent a partial history as complete.

Previous/next and skip preserve drafts. Skip moves the ticket out of the current pass with an explicit skipped view or return action. Refresh reconciles resolved or reassigned tickets without discarding saved drafts. Advancing after a successful update means reviewed for this pass; it does not imply the ticket was closed. Local state is keyed by tenant, app, user, and ticket.

## AI suggestions

Generation is user-triggered. Send only the current ticket fields and conversation entries needed for the task, plus the user's added context. Exclude credentials, unrelated tickets, attachments, and unnecessary contact details. Explain in the app that selected ticket text is sent to the configured OpenAI Platform project. Use the Responses API with schema-validated structured output; select a currently supported model during implementation, configurable in backend settings. Request non-storage where supported and do not imply that this guarantees zero provider retention.

Output fields: short summary, unresolved issues, suggested next steps, missing information/questions, draft text, and supporting entry references. Validate source references against supplied ticket/history IDs. Separate recorded facts, user-supplied observations, and proposed actions. Never assert that an action has already happened unless the supplied context establishes it. Show incomplete/refused/invalid AI responses as recoverable generation errors while preserving user edits.

Public drafts use a separate public-safe generation context containing public ticket content and user instructions intended for that reply; do not feed private history or private AI assessments into the public drafting call. Internal assessments and internal-note drafts may use the authorized internal history. The UI reminds the user to review public wording. Users can edit either draft; switching visibility never silently transfers an internal draft into a public composer. Regeneration offers a replacement for review and does not overwrite unsaved edits automatically.

Long histories must be handled within the configured model context limit. Do not silently truncate: show an explanatory error and leave manual composition available if full supported context cannot fit in version one.

## Review and submit

Allow one public reply or internal note per submission, with an optional allowed status change. Display and validate exact notification recipients separately from note visibility; a public entry must not be assumed to notify anyone automatically. Defaults are public reply to the eligible requester, or internal note without external recipients. Actual visibility and notification behavior must be confirmed against the tenant API before enabling live submission.

The review screen shows exact message text, recipients, visibility, current/new status, and ticket identity. Only its explicit Submit and next button authorizes a write. AI generation never submits. Validate all fields again in the backend and confirm the ticket remains assigned to the user and active.

Refresh ticket and history immediately before submission and compare a revision token where available, otherwise a stable fingerprint including status, assignment, and history. If changed, preserve the draft and require a new preview against the refreshed context. Use conditional writes if the API supports them. Without conditional writes, disclose the remaining race between freshness check and remote write rather than claiming atomic protection.

Disable repeated UI clicks and create a persisted local submission intent with a unique identifier before sending. On confirmed success, save the remote receipt and advance. On definite rejection, preserve the draft and show the error. On timeout or ambiguous transport failure, mark the intent uncertain, stay on the ticket, block resubmission, and reconcile against remote history/status. Never automatically retry a potentially accepted write. If reconciliation cannot identify the outcome conclusively, provide a link to the remote ticket and a user acknowledgement workflow before allowing a fresh submission. On restart, reconcile pending intents as uncertain.

Prefer a single supported remote operation for note and status. If separate operations are necessary, represent partial success explicitly, record each confirmed operation, and never resend the note to retry only a failed status change. Verify operation order and API capabilities during discovery before choosing the implementation.

## Local persistence and failure handling

Persist draft text, user instructions, visibility, selected recipients/status, source revision, queue-pass state, and minimal submission receipts in a gitignored local data directory. Ticket/history cache stays in memory for version one. SQLite and its directory use owner-only permissions where supported; drafts remain sensitive local data. Logout clears in-memory credentials and context while saved drafts remain isolated by account for the next login. Do not expose drafts from a different account.

Expired TeamDynamix authentication prompts reauthentication without losing drafts. Rate limits honor documented retry timing for reads. Network/AI failure leaves manual drafting and navigation available when sufficient ticket context is loaded. Writes follow the uncertainty rules above. Logs redact credentials and avoid raw ticket text and vendor error bodies containing private data.

## Validation and acceptance

Planning clarifications: perform read-only tenant API discovery before implementing live submission. Treat fields or entries with uncertain visibility as internal; make user instructions intended for a public reply explicit. Reconciliation must use remote identifiers or sufficiently reliable author, time, recipient, visibility, and operation evidence; matching message text alone does not confirm success.

Original codexamix environment observation on 2026-09-17 (not yet verified in dynamix-manager): the existing `.venv/bin/python` points to an unavailable Python 3.13 framework installation, and the available system Python lacks pytest. Baseline tests could not execute. Establish a working isolated environment before implementation and rerun the baseline; no test-pass claim is made by this specification.

Run existing tests and new targeted tests for identity/assignment filtering; status classification; full queue/history traversal; queue ordering; account-isolated draft persistence; source-reference validation; separation of public and internal AI contexts; invalid AI output; auth expiry; recipient validation; stale tickets; repeated clicks; ambiguous writes; restart reconciliation; and partial update failure.

Use fictional tickets in a demo mode with no external credentials or network writes. Browser-test login/setup states, queue load, reading history, generation success/failure, editing, regeneration preservation, visibility switching, review, stale-ticket interruption, successful submit/advance, skip/return, and draft restoration. Test normal laptop and narrow layouts, keyboard access, and absence of exposed secrets or unsafe rendered HTML. Fix observed failures before asking the user to test.

Live validation begins with read-only identity, queue, ticket/history, and metadata checks. OpenAI smoke testing can use synthetic context first. Do not send a real ticket update merely to validate integration; a live write requires the user's explicit approval of its exact preview or a designated authorized test ticket. Report mocked/browser validation and live integration validation separately.

Done means the tested local app starts with documented instructions, works through the full demo review-and-submit workflow, and has verified live read integration when credentials are available. Do not label live submissions verified until an authorized real/test-ticket write and receipt have been checked. Unavailable credentials or tenant API limitations must remain explicit outstanding integration gates.
