# Confirmed Ticket Writes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicitly confirmed personal ticket writes to the hosted ChatGPT pilot without granting existing read sessions write access.

**Architecture:** Typed preparation tools persist encrypted immutable proposed operations. A short-lived browser capability presents the exact change and permits one CSRF-protected Save; no MCP tool commits writes. A durable state machine, independent scope checks and an upstream adapter prevent blind retries and unintended edits.

**Tech Stack:** Existing Python, FastMCP, Starlette, Pydantic, requests, encrypted SQLite/Fernet, pytest and Node test runner.

---

## Execution rules and current context

- Staged rollout approved 2026-09-18: implement only comment/status/assign/edit.
  Reject creation inputs and do not register a creation preparation tool. Creation
  references in the original tasks below are deferred acceptance criteria, not
  current implementation requirements. Evidence gate passed for these four actions.

- Repository: `/Users/micahcooper/dynamix-manager`; approved spec: `docs/superpowers/specs/2026-09-18-ticket-writes-design.md` (commit `c6fd4be`). All paths below are relative to that repository.
- The working tree contains uncommitted hosted connector source and unrelated user work. Do not create a clean-HEAD worktree that omits the working connector. Stay in the current checkout for this increment, record initial status, preserve unrelated edits, and stage explicit owned paths only. Do not commit unrelated pre-existing changes as part of these tasks.
- Use `.venv/bin/python3.14 -m pytest` (the older venv aliases are stale). Use `node --test tests/frontend/*.test.cjs` for frontend regression. Confirm executables before running.
- Apply @superpowers:test-driven-development to every implementation task, @superpowers:systematic-debugging for failures, and @superpowers:verification-before-completion before success claims.
- No production mutation during research/tests. Deployment is initially write-disabled. Actual scope consent and exact live ticket changes require the user's action/approval.
- Each implementation task follows red test → observed failure → minimal code → green tests → focused diff review → scoped commit. Existing untracked source must be reviewed before inclusion in a commit.

## File boundaries

Planning checkpoint: independent plan review approved on 2026-09-18; advisory
concurrency tests incorporated. Fresh baseline: 344 Python tests and 14 frontend
tests passed. The initial sandboxed run blocked three notebook kernel socket binds;
the approved unsandboxed rerun passed all 344. No write implementation, deployment,
API support verification, or live ticket mutation has occurred at this checkpoint.

Create `src/dynamix_manager/ticket_writes/` with `__init__.py`, `models.py` (typed inputs), `adapter.py` (verified TDX contracts), `store.py` (encrypted lifecycle and audit), `service.py` (prepare/commit orchestration), `routes.py` (review/Save), and `tools.py` (hosted-only MCP registration). Keep HTML rendering escaped and server-side inside routes.py initially; no new frontend framework.

Modify `hosted.py`, `personal_auth.py`, and `personal_auth_store.py` only where needed for write scope/grant validation and feature-flag wiring. Do not alter the default local plugin tool set. Update `scripts/stage_hosted_connector.py` to explicitly include each new module; add no broad directory upload.

Create `tests/test_ticket_write_models.py`, `tests/test_ticket_write_adapter.py`, `tests/test_ticket_write_store.py`, `tests/test_ticket_write_service.py`, `tests/test_ticket_write_routes.py`, and `tests/test_ticket_write_tools.py`. Extend existing auth, hosted and bundle tests. Create `docs/tdx-write-api-contracts.md` and update `docs/hosted-connector.md`.

### Task 1: Verify API contracts and baseline (blocking gate)

**Files:** Read `src/dynamix_manager/tdx_client.py`, `plugin.py`, `hosted.py`, `personal_auth.py`; create `docs/tdx-write-api-contracts.md`.

Execution checkpoint 2026-09-18: baseline rerun passed 344 Python/14 frontend
tests. Official API evidence is recorded in `docs/tdx-write-api-contracts.md`.
The full five-action evidence gate has not passed: arbitrary form creation lacks
the field/rule/default metadata needed for the approved pre-save validation.
Comments, ordinary status changes, explicit-field assignment and partial field
edits have documented contracts suitable for synthetic implementation. No product
code, deployment, OAuth permission or production ticket has been changed. Obtain
direction on staged delivery or an authoritative selected-form contract before
changing the approved full-scope implementation path. The user then approved
staged delivery; the evidence gate is complete for the four current operations,
with creation explicitly deferred rather than silently treated as verified.

- [x] Run `git status --short`, inspect relevant source and local AGENTS instructions, then run `.venv/bin/python3.14 -m pytest -q` and `node --test tests/frontend/*.test.cjs`. Record actual baseline results, not historical counts.
- [x] Read official Cedarville/TeamDynamix API documentation using read-only requests/browsing. Record source URLs, retrieval date, method/path, request/response fields, permissions, notification effects, custom-field requirements, partial-update semantics, and conditional-write support for each of the five actions.
- [x] Verify metadata sources for requester, active assignee/group, priority, type/form, statuses and required creation attributes. Record whether permission to create/edit can be read in advance or only authoritatively checked by mutation responses. Creation gap documented and deferred by approval.
- [x] Record sanitized minimal request/response fixtures in the contract document; use synthetic names and IDs in tests. Never record auth headers, tokens or actual private ticket text.
- [x] Build a support matrix: comment visibility/recipient behavior; status closure requirements; assignment user/group behavior; title/description/priority partial edits; creation validation. If any required contract is unsupported or unclear, stop that capability and report the evidence gap before implementation. Do not replace partial edits with full-object updates or silently reduce scope.
- [x] Verify current official OpenAI MCP OAuth/per-tool scope metadata behavior needed for step-up consent; preserve read-only clients. This is documentation research, not a ChatGPT permission change. Verified 2026-09-18: https://developers.openai.com/plugins/build/auth requires per-tool scopes, resource metadata and runtime `mcp/www_authenticate` errors. Add an insufficient-scope error result test; metadata alone does not trigger step-up UI.
- [x] Review/commit only the new API evidence document. Do not guess executable mutation payloads in advance of this gate. Evidence committed in `99afcb1`.

### Task 2: Typed models and single-attempt adapter

**Files:** Create package `__init__.py`, `models.py`, `adapter.py` and associated model/adapter tests.

Completed for the approved four-action stage: 45 focused tests pass; independent
spec and quality reviews approved. Prepared records bind tenant URL and app ID;
serialization retains omitted fields. Assignment notifications remain fail-closed
when exact recipients cannot be verified; converted-ticket status/assignment and
date-required statuses are explicitly rejected. Live read-only checks confirmed
priority metadata and the personal user's `OrgApplications` shape; no live writes.

- [ ] Write parametrized failing tests for the five action models with `extra='forbid'`, positive ticket IDs, bounded strings/collections, explicit nullable-versus-omitted values, allowed editable fields, and rejection of deletion/bulk/admin/other-app requests.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_models.py -q`; confirm failure is missing implementation, not fixture/import infrastructure.
- [ ] Implement discriminated action models: `comment`, `status`, `assign`, `edit`, `create`. Comment defaults private/no recipients. Creation accepts only typed attributes validated against metadata; edits allow title, description, priority only. Define immutable normalized payload and display-preview structures.
- [ ] Write adapter tests asserting exact URL/method/body from Task 1 contracts; enforce tenant/application binding, no redirects, a bounded timeout, and exactly one upstream call on timeouts/429/5xx. Test permission errors and metadata lookup validation.
- [ ] Implement adapter methods `metadata`, `snapshot`, `validate`, and `apply_once`. Return typed outcomes `applied`, `rejected`, or `unknown`; classify uncertain transport/server responses as unknown unless the contract proves rejection. Never route mutations through any retry wrapper.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_models.py tests/test_ticket_write_adapter.py -q`; require all green; review and commit owned package/test files.

### Task 3: Durable encrypted operation lifecycle

**Files:** Create `store.py`, `tests/test_ticket_write_store.py`; reuse vault encryption and protected DB access without changing existing OAuth expiry cleanup.

Completed 2026-09-19: independent spec and quality reviews approved; 28 store
tests and 73 combined model/adapter/store tests pass. Review fixes cover nested
payload repr redaction, mandatory CSRF for claim/check, and abandoned full-pool
expiry recovery. The conservative 128-record cap reserves a slot for every
retained operation throughout its lifecycle, including unresolved operations;
the separate 1,000-marker cap is defense in depth and may not be reached before
the total-record limit. Known outcomes release locks/markers, never capacity
reservations needed to record results. No live writes or deployment occurred.

- [ ] Write failing lifecycle tests with injected clock: prepare, expire, bind browser, atomically claim, finish, duplicate Save, parallel claim, crash/reopen, capacity overflow and encryption-at-rest inspection.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_store.py -q` and observe red.
- [ ] Implement separate encrypted tables for operations, unresolved markers and audit. Use random 256-bit capabilities stored hashed, expiry=300 seconds, encrypted immutable payload, owner/client/resource/grant binding, and payload digest. Bound previews at 128, unresolved markers at 1,000, audit at 10,000/30 days; capacity fails closed.
- [ ] Implement states `pending → sending → applied|rejected|unknown`, plus `expired|conflict` before dispatch. Atomically claim with a compare-and-set in `BEGIN IMMEDIATE`, commit before network I/O, and never hold the DB transaction during HTTP. Returning an existing outcome must not dispatch again.
- [ ] Allocate durable unresolved marker before dispatch keyed by actor/application/target/normalized-payload digest. Treat crash-left `sending` as unknown. Preview cleanup must not remove markers. Block equivalent preparation until authoritative reconciliation; never infer rejection from absent bounded feed data.
- [ ] Add tests for markers surviving expiry/cleanup/restart, concurrent equivalent preparations, resolution requiring evidence, and capacity denial without marker eviction. Also prepare two equivalent previews before either dispatches, then Save simultaneously using separate store connections/processes: an atomic unique marker constraint must permit only one dispatch. Keep reconciliation an internal/read-only operator path, not a force-retry or write tool.
- [ ] Run store tests green; review and commit only these files.

### Task 4: Explicit OAuth write scope and grant checks

**Files:** Modify `personal_auth.py`, `personal_auth_store.py`, `hosted.py`; extend `tests/test_personal_auth.py`, `tests/test_hosted_connector.py`.

Completed 2026-09-19: independent spec and security/quality reviews approved.
61 focused auth/hosted tests and the full 439-test Python suite pass. The helper
`write_grant_binding(access_token)` reloads server-side access authority;
`validate_write_grant(binding)` rechecks immutable durable consent, current
configuration, expiry and revocation. `app.write_runtime.require_enabled()` is
the dynamic, fail-closed gate for the forthcoming prepare/Save service. Discovery
advertises both personal scopes while transport still requires only read.
Existing DCR client capabilities can request explicit new write consent without
elevating existing grants. The environment flag accepts exactly `true`/`false`
and defaults false. No deployment or production consent change has occurred.

- [ ] Add failing tests for old read grant refresh attempting elevation, unknown scopes, expired/revoked grant approval, client/resource/subject mismatch, and refresh-token rotation preserving only authorized scopes.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_personal_auth.py tests/test_hosted_connector.py -q` and record expected new failures.
- [ ] Support only scope sets `{tdx.read}` and `{tdx.read,tdx.write}` with explicit login consent text. Discovery may advertise both; defaults stay read. Existing family records missing write authority fail write checks. Installed FastMCP derives protected-resource `scopes_supported` from transport `required_scopes`; explicitly separate advertised support from required read scope rather than requiring write for all existing reads.
- [ ] Store immutable grant family binding (subject, client, resource, original scopes, upstream expiry) at consent/code exchange. Preserve binding on revocation/replay updates. Expose an internal `validate_write_grant(binding)` helper that independently checks stored authorization and current revocation/expiry; do not rely on unvalidated tool arguments or cached claims.
- [ ] Keep read transport authorization unchanged. Require personal auth for this write increment; reject write enablement under the external-IdP mode until equivalent grant-family semantics are designed. Carry validated family context to hosted write preparation without returning tokens to clients.
- [ ] Add `TDX_HOSTED_WRITES_ENABLED` default false and an injectable runtime flag provider checked both at prepare and Save. Environment rollback/restart must block persisted pending approvals; existing reads remain usable.
- [ ] Run auth/hosted tests green, review backwards compatibility and commit explicit paths after reviewing their existing untracked changes.

### Task 5: Preparation and commit orchestration

**Files:** Create `service.py`, `tests/test_ticket_write_service.py`. If needed,
add a narrow owner-bound operation-ID lookup to `store.py` with store tests for
the later read-only result tool; never return a capability or dispatch through
that lookup.

Browser Save has no MCP bearer context. Its service connection must come only
from the stored, freshly validated grant binding, then the encrypted personal
vault, with the configured UID verified before any ticket operation. Use a
scoped connection context that closes the upstream session on every exit,
including identity failure. Never fall back to the local `.env` or admin login.
The review URL must use the fragment transport described in Task 6.

Completed 2026-09-19: independent spec and quality reviews approved; 100 combined
model/adapter/store/service tests and the full 466-test Python suite pass. The
`TicketWriteService` provides prepare, open_review, commit and exact-owner result;
the production factory uses only the validated grant's personal vault token.
Review fixes sanitize preparation failures and recheck flag/consent after live
reads before issuing any capability. Claim precedes preflight, and accepted
outcomes persist before optional readback. No hosted routes/tools are wired yet,
and no live writes or deployment occurred. Public schema confirms `DaysOld` is
integer days; full-snapshot comparison remains deliberately conservative.

- [ ] Write failing tests: no upstream write at prepare; metadata resolves IDs to names; unknown notification effects block; missing required fields block; immutable payload; complete before/after preview; scope/flag/identity validation; changed baseline prevents save.
- [ ] Run service tests red, then implement `prepare(principal, action)` producing operation ID, preview and review URL. Snapshot affected fields, validate application and metadata, include public/private setting and explicit/implicit notifications. Never create a savable preview for unsupported forms.
- [ ] Implement `commit(capability, browser_binding)` using stored payload only: flag/grant/expiry checks, verified TDX identity and refreshed metadata/baseline, then atomic claim, one upstream apply, durable outcome. Where supported, use conditional writes; otherwise send partial intended fields and document the remaining last-moment race.
- [ ] Acquire cross-worker same-ticket serialization before refreshing/comparing the baseline and retain it through dispatch/outcome recording; do not hold a SQLite transaction across HTTP. Use a durable ticket claim that cannot be automatically reclaimed after a crash without reconciliation. Test two distinct edits as well as duplicate operations. Upstream conditional validation remains authoritative. Grant revocation is checked immediately before dispatch; do not claim it can cancel a request already dispatched.
- [ ] Separate save outcome from read-back outcome: successful mutation plus failed read-back remains applied, not unknown/retryable. Ambiguous save remains unknown and blocked. Return only safe result text, ticket ID and validated TDX link, never upstream raw error bodies.
- [ ] Add simultaneous Save, duplicate retry, restart, conflict, expired token, revoked grant, disabled flag, missing upstream permission, notification and read-back failure tests; assert adapter call counts (zero or one as appropriate).
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_service.py tests/test_ticket_write_store.py -q`; green, focused review, commit.

### Task 6: Human confirmation routes

**Files:** Create `routes.py`, `tests/test_ticket_write_routes.py`; wire routes through `hosted.py`.

Logging-safe transport detail (2026-09-19): Railway documents HTTP path logs
(`https://docs.railway.com/observability/logs`); do not assume all edge logging
can be disabled. Use `/writes/review#<capability>` so the capability never reaches
the server in a URL. A small CSP-hashed same-origin script removes the fragment
and submits a bounded POST to open the preview, not save it. Server-render the
actual escaped review and explicit Save form. Opening/binding cannot dispatch a
TDX mutation. Preserve a secure HttpOnly browser cookie across previews; derive
stable per-capability CSRF from that secret browser binding, never from public
operation ID alone. Neither capability nor CSRF appears in any request URL.
Reject unexpected query parameters, keep bodies/headers out of app logs, and
browser-test fragment removal, strict Origin, repeat opening and actual Save.

UI direction from the existing `.impeccable.md`: restrained editorial briefing,
clear/accountable/sober for this consequential action (no jokes or ornaments).
Use an open, left-aligned layout, explicit Before/After labels, complete wrapping
text, separate visibility/recipient sections, and one primary Save button. Stack
comparisons on narrow screens; keep visible keyboard focus and 44px controls.
Do not optimistically report success. Locally available Palatino/Palatino Linotype
headings with Trebuchet MS body text and serif/sans-serif fallbacks avoid external
font requests. Use navy-tinted neutrals, restrained contrast, no card grids or
decorative animation. Include a fixed safe return-to-ChatGPT link and validated
read-only ticket link; never accept arbitrary redirect URLs.

- [ ] Write failing HTTP tests for GET being read-only, escaped malicious ticket content, exact preview, five-minute expiry, browser binding, strict origin, CSRF, content-type/body limits, absent external assets and security headers. Save must accept no replacement payload.
- [ ] Implement GET review with single-browser capability binding, `__Host-` secure HttpOnly SameSite cookie and per-operation CSRF; POST Save validates everything server-side. Use no-store, no cross-origin referrer disclosure, frame denial and restrictive CSP. Reuse `Referrer-Policy: same-origin` from the corrected login flow: a blanket `no-referrer` on native forms can produce `Origin: null` and conflict with exact-origin checks. Browser-test the actual headers. No cross-site post-login redirect is required; show outcome on the same origin.
- [ ] Add tests for link theft after binding, repeated GET behavior, parallel tabs, duplicate POST result, guessed capabilities, error-page leakage and grant expiry/revocation between preview and Save. Rate-limit review endpoints with bounded state, fail closed, and keep access/query logging disabled at app and hosting layers.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_routes.py tests/test_hosted_connector.py -q` green.
- [ ] Browser-test synthetic data only via a test-only local harness with fake adapter (never shipped or routed in production). Verify keyboard operation, visible changed fields/recipients, no save before click, duplicate click causes one call, and conflict/unknown states are clear. Do not use production credentials in this harness.
- [ ] Review/commit route tests and source; record browser evidence without capability URLs or ticket secrets.

### Task 7: Hosted MCP tools and source-only packaging

**Files:** Create `tools.py`, `tests/test_ticket_write_tools.py`; modify `hosted.py`, `scripts/stage_hosted_connector.py`, `tests/test_hosted_bundle.py`.

Test validation through actual MCP calls, not only direct Python functions.
FastMCP builds an outer argument model and can render validation exceptions
before the function runs; configure strict extra-field rejection and suppress
input values in validation errors there as well as in nested action models.
Preserve omitted versus explicit-null edit fields through the tool boundary.

- [ ] Test hosted tools `prepare_ticket_comment`, `prepare_ticket_status`, `prepare_ticket_assignment`, `prepare_ticket_edit`, `prepare_ticket_creation`, plus bounded read-only `ticket_write_metadata` and owner-bound `ticket_write_result`. Result lookup cannot commit or clear an unresolved operation. Verify no commit tool and no new local stdio tools.
- [ ] Implement typed tools delegating to service. Preparation metadata: `readOnlyHint=false`, `idempotentHint=false`, conservative destructive/open-world hints and required `tdx.read`+`tdx.write`. Existing read tools retain `tdx.read`. Correct the existing hosted loop that currently overwrites every tool's security scheme.
- [ ] Update hosted-only server instructions and `connection_status` capability reporting: the shared plugin currently hardcodes no updates/read-only. Preserve local defaults; report write availability only when the hosted flag and the caller's validated grant permit it, not merely because write tools exist.
- [ ] Return ordinary review links plus explicit text “not saved”; do not modify the ticket widget to auto-submit, and do not mark Save as widget-accessible. Limit metadata searches to the selected ticket application and bounded results.
- [ ] Write failing bundle tests for each new module being included, secrets/test harness excluded, and local read-only plugin unaffected. Add each new module to the source allowlist, not a directory glob.
- [ ] Run `.venv/bin/python3.14 -m pytest tests/test_ticket_write_tools.py tests/test_hosted_bundle.py tests/test_plugin.py tests/test_hosted_connector.py -q` and `node --test tests/frontend/*.test.cjs`; require green, then scoped review/commit.

### Task 8: Full verification, review and controlled rollout

**Files:** Update `docs/hosted-connector.md` and this plan's checkboxes; no production ticket fixture files.

- [ ] Run `.venv/bin/python3.14 -m pytest -q`, `node --test tests/frontend/*.test.cjs`, focused Ruff on changed Python files, and `git diff --check`. Resolve failures; record exact counts and gaps.
- [ ] Request independent code/security review with @superpowers:requesting-code-review, prioritizing authorization, token/grant binding, capability leakage, unknown outcome retention and payload immutability. Resolve actionable findings and rerun tests.
- [ ] Document verified API limitations, human review flow, OAuth step-up, flags/rollback, no blind retries, retention/capacity, read-only reconciliation and explicit live-test gates.
- [ ] Stage source with `.venv/bin/python3.14 scripts/stage_hosted_connector.py`, inspect allowlist output, deploy to the existing Railway service write-disabled. Verify health, unauthenticated MCP denial, read-only compatibility and no secret logging. Do not rotate vault keys or upload the dirty working tree.
- [ ] Enable the write pilot only after local tests/review and verified production read health. Refresh ChatGPT tool definitions; have the user explicitly approve new write consent. Verify old read grants cannot prepare writes. No actual ticket write is authorized by consent alone.
- [ ] Prepare one user-selected action and review its actual preview. Request exact approval of ticket/content/visibility/recipients; let the user perform Save. Then inspect read-back and record outcome without private content. Repeat approved tests for remaining operation classes or explicitly mark them live-unverified.
- [ ] Verify disabling writes blocks already-issued review links while reads work; re-enable only within the approved pilot rollout. Hand off with test evidence, deployment ID, supported operations, and any unverified live gates. No claim of full live coverage without those checks.
