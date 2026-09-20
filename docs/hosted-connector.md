# Hosted connector: implementation and launch gates

The resource server now supports an account-restricted TDX-backed personal login,
alongside the original external-issuer mode. The user verified the personal
ChatGPT ticket drilldown. This remains a single-user pilot, not an institutional
SSO or multi-user product. This hosted connector is the only MCP surface; the
earlier local stdio plugin was removed, and `dynamix_manager.plugin` now holds
only the shared tool core that the hosted server imports.

## What runs

The hosted entry point serves stateless MCP HTTP at `/mcp`, OAuth protected-resource
metadata at `/.well-known/oauth-protected-resource/mcp`, and a public `/healthz`.
It reuses ticket search, details/activity and the embedded ticket interface.

In external-issuer mode, every MCP request requires an externally issued RS256 access token with a known
JWKS signing key, exact issuer, exact scalar audience equal to the public `/mcp`
URL, nonempty `sub`, `client_id` (or `azp`), `exp`, `iat`, and `tdx.read` in a
space-separated `scope` (or `scp`). Missing, invalid or insufficient claims deny
access. It never accepts a ChatGPT session cookie or passes the connector token
to TDX. Configure the provider to issue access tokens, not ID tokens, for this
resource. Authorization-code/PKCE, consent, client registration, refresh and
revocation are the external provider's responsibility; compatibility with
ChatGPT must be tested before deployment. Access-token revocation takes effect
on expiry; choose short token lifetimes. Local TDX mappings can be revoked
immediately for subsequent tool calls (already-running requests may finish).

Each tool call looks up an encrypted TDX bearer token using `(issuer, subject)`,
checks its configured expiry, calls TDX `getuser` to verify its UID, and uses only
that token. TDX remains the authority for API permissions. Upstream sessions close
at request completion. No shared account fallback or user-selected tenant URL
exists. Reads are not cached; write previews retain encrypted ticket baselines
and intended changes under the operation retention rules below. UID checking
adds calls against TDX's rate limits; size a pilot
accordingly. API permissions must be compared with intended ticket UI permissions
using two real users before release.

## Personal-pilot configuration

Set `TDX_HOSTED_AUTH_MODE=personal`, `TDX_HOSTED_PUBLIC_URL`,
`TDX_HOSTED_VAULT_PATH`, `TDX_HOSTED_VAULT_KEY`, `TDX_HOSTED_ALLOWED_UID` and
`TDX_HOSTED_REDIRECT_URIS` (JSON array of exact approved ChatGPT callback URLs).
An empty callback array permits the process to run but denies all client
registration and authorization. Obtain the callback from ChatGPT configuration;
never substitute a guessed callback or wildcard.

The login page asks for personal TDX credentials and explicit consent to the
requested read-only or read-and-write scopes. Read-only remains the default. Credentials
are sent only to the connector over HTTPS and then Cedarville's TDX API. The
returned identity must match the configured UID. The password is not persisted;
the expiring TDX token is encrypted in the vault. Connector access tokens expire
within ten minutes, with single-use rotating refresh tokens bounded by TDX expiry.
Replaying a used refresh token revokes its entire grant family. Expired TDX access
requires signing in again. This is an OAuth bridge, not native TDX OAuth or SSO.

The pilot intentionally allows only one person. Its public login endpoints have
global rate limits suitable for a small pilot; do not treat this as a production
multi-user identity service. Broad rollout should return to an established
institutional identity provider. Railway logs must never capture bodies or
Authorization headers. Opaque connector tokens and client state are encrypted
in SQLite using hashed indexes; the same external key protects the personal vault.

## External-issuer host configuration

Inject these through the hosting platform's secret/configuration facility; no
project `.env` is read by the hosted entry point:

- `TDX_HOSTED_PUBLIC_URL`: HTTPS origin, no path or trailing slash.
- `TDX_HOSTED_ISSUER`: exact OAuth issuer for the approved identity provider.
- `TDX_HOSTED_JWKS_URL`: fixed HTTPS signing-key endpoint for that issuer.
- `TDX_HOSTED_VAULT_PATH`: SQLite path in a private persistent directory.
- `TDX_HOSTED_VAULT_KEY`: Fernet key supplied externally, never committed or logged.

The production factory fixes the TDX tenant to Cedarville and client header ID to
2045; it discovers InfoTech Tickets separately. The database is mode 0600; its
directory must be mode 0700, owned by the runtime user, and not writable by other
users. Store the vault encryption key separately from backups. Back up encrypted
data and protect backups equivalently. Key rotation/migration needs an operational
procedure before broad rollout. SQLite is a single-host pilot storage choice;
do not place it on a shared network filesystem or run replicas with independent
vaults. JWKS keys are cached for five minutes. A token carrying an unknown `kid`
triggers one immediate refetch (throttled to once per 30 seconds), so rotated
keys work without waiting for the cache to expire. If a refresh fails, the last
good key set keeps serving until the provider recovers; only a failure before the
first successful fetch denies access.

Install and run behind a TLS reverse proxy (private backend network only):

```sh
python -m pip install '.[hosted]'
uvicorn dynamix_manager.hosted:from_environment --factory --host 127.0.0.1 --port 8000 --no-access-log --no-proxy-headers
```

Container recipe, run from repository root:

```sh
docker build -f deploy/hosted.Dockerfile -t teamdynamix-connector .
```

The Dockerfile-specific ignore file excludes `.env`, tickets, reports, notebooks,
credentials from the build context. The
container's narrow startup script initializes `/data/private`, then clears groups
and drops to UID/GID 10001 before importing/starting the application. It rejects
symlinked or untrusted storage paths and never recursively changes ownership.
Use a persistent `/data` mount and platform-injected secrets.
The backend port must not be publicly reachable except through HTTPS termination.
Preserve the configured public Host header. Set request/rate/concurrency limits at
the proxy, disable request/body/auth-header logging, and log only privacy-safe
operational outcomes. Health is process liveness, not TDX/auth readiness.

## Railway pilot deployment

Railway is the selected initial host. `railway.toml` selects the existing Docker
recipe, `/healthz`, one replica and bounded restart retries. The same settings
are explicitly configured on the service: the initial CLI deployments selected
Railpack despite the config file and failed before building the container.

Pilot provisioned 2026-09-18 in the user-approved personal Railway account:

- Project `teamdynamix-connector-pilot`: `cfba9747-5b49-4852-b798-4b8a75a5af1a`.
- Service `connector`: `cd3c8a3d-412e-4506-affe-d3b37e72fd87`.
- Environment `production`: `6ff3107f-b5e7-4370-a01f-03d40030a3fb` (Railway default name;
  this is still a pilot, not approved production).
- Volume `connector-volume`: `4de5f03a-3fac-43fb-9eb4-496181fa7b14`, mounted at `/data`.
- HTTPS origin: `https://connector-production-3f83.up.railway.app`.
- Personal mode, allowed personal UID, `PORT=8000`, private vault path and an
  externally generated encryption key are configured. Secret values are not
  recorded here. No TDX password/token or ticket data was included in the upload.
- Callback verified in ChatGPT's setup form and allowlisted exactly:
  `https://chatgpt.com/connector/oauth/xHJ0eMh1C0-4`.
- Initial deployment (superseded): `b8da231b-41f3-400c-bc8d-da9fff282a04`.
- Verified HTTPS health 200, both OAuth discovery documents 200,
  unauthenticated MCP 401, and unapproved callback registration 400.
- Restart completed successfully with the same volume mounted; health and MCP
  denial checks passed again. Direct runtime UID/file-mode inspection was not
  available because Railway has no registered SSH key. Startup enforces these
  properties, but durable grant persistence still needs a live authorized flow.
- Callback-enabled deployment `822c0a81-dc1a-43dc-833b-4f5a579f43ab` succeeded.
  ChatGPT created `asdk_app_6aadc664665481919afb82215b68f924` (TeamDynamix Personal
  Pilot). Live DCR returned 201, authorization redirected successfully, and the
  browser reached the personal login/consent page. Developer mode and CSP
  enforcement were enabled with user approval. Personal login/consent,
  reconnect/revoke and in-chat ticket drilldown were pending at this checkpoint;
  see subsequent test and rollout evidence below.
- Railway now deprecates `railway.toml`; migrate to its current infrastructure
  configuration before 2026-12-01 if still hosted there.

Later Azure migration should reuse the container and OAuth resource-server code,
move the encrypted vault and separately protected encryption key, and update the
public resource URL, OAuth audience/client configuration and ChatGPT connection.
The runtime does not depend on Railway APIs.

- Set `PORT=8000` to match the container's listener and Railway's health probe.
- Attach a persistent volume at `/data`; set `TDX_HOSTED_VAULT_PATH` to
  `/data/private/credentials.sqlite`.
- Railway mounts volumes root-owned. The startup script handles only the private
  child directory, then drops privileges before serving requests. The mount must
  be root-owned and not group/world-writable; startup fails closed otherwise.
- Set the variables for the selected auth mode. Personal mode does not need an
  external issuer or JWKS. External mode requires real approved provider endpoints.
- Generate an HTTPS domain for the service and set `TDX_HOSTED_PUBLIC_URL` to that
  exact origin. Authentication must remain mandatory on `/mcp`.
- Upload only an explicitly prepared source bundle. Docker ignore rules restrict
  image build context; they are not a guarantee that a CLI uploader won't transmit
  other local files. Never upload the project `.env`, reports, vault or tickets.
- Validate health, unauthenticated 401, OAuth discovery and persistence after a
  restart before linking any personal TDX credentials. The SSO/linking gates below
  still apply; a green Railway deployment alone does not complete the app.

References: [Railway configuration](https://docs.railway.com/config-as-code/reference),
[volume ownership](https://docs.railway.com/volumes),
[health checks and PORT](https://docs.railway.com/deployments/healthchecks).

## Institutional account linking remains a separate gate

Cedarville's live OpenAPI schema documents `/api/auth/loginsso`, which returns a
bearer token for the current SSO session. It does not document a third-party OAuth
redirect/token exchange contract. Do not scrape session cookies, assume SSO tokens
are interchangeable, or ask users to paste tokens into ChatGPT.

`CredentialVault.put(issuer, subject, uid, token, expires_at)` is an operator-only
Python API for a controlled test mapping, **not a public enrollment endpoint**.
Before using it, an authorized operator must independently verify that the
external OAuth subject and TDX UID belong to the same person and that the token
is that person's credential (not an admin/service-account token). Verify token
expiry and ownership against TDX; the vault does not establish ownership. Never
put credentials in command-line arguments, source files, prompts or logs.
`revoke(issuer, subject)` removes a mapping. An expired token requires relinking;
password storage and automatic TDX renewal are deliberately not implemented.

The personal pilot uses the approved TDX-backed login instead of an SSO handoff.
For a usable institutional multi-user release we still need:

1. Approved host/domain and private storage/secrets configuration.
2. Cedarville OAuth application registration compatible with ChatGPT's MCP flow.
3. A supported TDX user linking and renewal mechanism, confirmed with TDX or
   authoritative tenant documentation, then implemented and tested.
4. Two-user permission/denial testing against live TDX, including feed visibility.
5. ChatGPT app registration, consent, reconnect/revoke and embedded UI validation.

Institutional identity registrations and production rollout remain pending;
the personal pilot does not satisfy those gates.

## Tests

Browser-login correction (2026-09-18): the original `no-referrer` policy
conflicted with strict Origin validation because native HTML form submissions
can send `Origin: null` under that policy. OAuth responses now use `same-origin`,
preserving the native form Origin while suppressing cross-origin referrers.
Cookie, CSRF and exact-Origin checks remain unchanged. The reported live failure
occurred 70 seconds after form load, ruling out the five-minute timeout; its
specific rejected field was not logged. Full suite after correction: 344 passed.
Correction deployed as `8ba69c02-f1bc-4b3b-beff-b6f718f9f4d4`. Live HTTP smoke
test using the configured personal account passed registration (201), login form
(200), TDX authentication (302), PKCE exchange (200), authenticated MCP discovery
(200, eight tools), grant revocation (200) and revoked-token denial (401). No
ticket content was requested. The smoke client registration remains stored;
its issued grant was revoked. Browser login and ChatGPT drilldown still need
end-to-end verification; the HTTP smoke test supplies an explicit Origin.

The next browser attempt opened at 23:24:35 UTC and submitted at 23:37:41 UTC,
after the five-minute transaction deadline. This attempt therefore cannot
establish whether the browser-policy correction worked. Added fixed diagnostic
labels for session, origin, browser-cookie, CSRF, consent and upstream failures;
no request values or exception contents are logged. Full suite: 344 passed.

The following browser attempt authenticated successfully at 23:46:40 UTC (302),
then re-submitted the consumed form at 23:46:57 (400, session label). The failure
is now after TDX authentication, not credential validation. Since browsers may
apply `form-action 'self'` to cross-origin POST redirects, successful login now
returns a same-origin success document with an escaped, allowlisted GET link
labelled "Continue to ChatGPT", rather than a cross-origin form redirect. CSP
remains unchanged. The page explicitly states the two-minute code lifetime;
the login form states its five-minute deadline. This resolves the protocol's
form-redirect compatibility risk without permitting cross-origin credential
posts. Full Python suite: 344 passed; focused Ruff and diff checks passed.

```sh
.venv/bin/python3.14 -m pytest tests/test_hosted_connector.py tests/test_plugin.py -q
node --test tests/frontend/*.test.cjs
```

Pre-write local verification: 344 Python tests and 14 frontend tests passed, focused
Ruff checks and `git diff --check` passed. Independent auth reviews found no
remaining release blockers. The personal TDX login adapter was checked against
the real account without fetching tickets. These checks and the public endpoint
checks above do not prove production SSO or real ChatGPT integration.

## Ticket-write increment

Direct-write rollout verified 2026-09-20: source commit `e877166`, Railway
deployment `25ab57df-4912-4a0d-b12d-82029c0911c8` reported SUCCESS. Live health
returned 200/ok, the retired review endpoint returned 410, and unauthenticated
MCP returned 401. ChatGPT's Refresh action replaced all four prepare tools with
the four direct-write tools and their required request IDs/write scopes. No
live ticket mutation was made to verify this deployment. Verification: 601
Python tests, 14 frontend tests, focused Ruff and diff checks; independent
spec and security reviews passed. A discovered pending-conflict retry issue was
fixed with a failing-then-passing regression before release.

The user subsequently completed the ChatGPT connection and reported that ticket
drilldown worked. The connected pilot also returned a successful authenticated
read-only connection status during write-development checks. This establishes
the personal read path, not institutional SSO or multi-user readiness.

The approved write increment covers comments, ordinary status changes,
explicit-field assignment, and title/description/priority edits. Ticket creation
remains disabled: the available form metadata does not establish all required
fields and conditional rules. See `tdx-write-api-contracts.md` for the verified
contracts and limits. Assignment notifications whose exact recipients cannot be
verified and statuses requiring an off-hold date fail closed.
Status changes do not cascade to child tasks; task completion is not part of
this increment.

The approved 2026-09-20 direct-write flow replaces browser review: an explicit
user request is sufficient to submit a supported change. Authentication, existing
write grants, and TDX permissions remain mandatory. Development and deployment
checks must not generate live ticket changes. The earlier review-page deployment
history below is retained for incident context, not as the current UX contract.

### Authorization and rollback contract

Incident correction, 2026-09-19: the first user-saved comment was posted and TDX
recorded the requested recipient in its notified list, but the connector recorded
unknown because POST feed returned HTTP 201 instead of the documented 200. The
stored operation result independently confirmed 201. The adapter now accepts
200/201 for feed writes only; PATCH identity verification and all uncertain-result
no-retry protections remain unchanged. Regression tests first failed for comment
and status 201 responses, then passed; full verification: 539 Python tests,
14 frontend tests, focused Ruff and diff checks. No additional live write was
performed. Source-only correction deployment: `4b5de239-75f9-4a03-9002-6e9acd605b83`.
The historical unknown operation was subsequently reconciled through the existing
internal authoritative-evidence hook with explicit operator approval. Fresh TDX
activity readback and exact stored-action checks confirmed the applied comment.
Runtime verification found zero remaining locks and markers for that operation;
the hosted result tool independently returned `applied`. No TDX write was resent.
The temporary Railway SSH key was revoked, its agent identity removed, and its
local key files deleted; Railway then listed no registered keys. Do not resend
the already-confirmed comment or infer email delivery from TDX's notified list.

The implementation accepts exactly `TDX_HOSTED_WRITES_ENABLED=true` or
`false`. In personal mode, omission means **true**: the connector is read/write
by default and `false` is the explicit off switch. In external-issuer mode
omission means false and `true` is rejected. The
runtime gate is rechecked by the write service rather than inferred from tool
discovery. Redeploy/restart with false is the rollback mechanism for pending
approvals; it must leave existing read tools usable. Service/route integration
tests pass; the deployed pending-preview rollback test remains unverified.

### Controlled rollout evidence — 2026-09-19

- 535 Python tests and 14 frontend tests passed; focused Ruff and diff checks
  passed. Independent spec and security review found no remaining blocking
  findings. Synthetic HTTPS browser tests verified explicit Save, duplicate
  dispatch prevention, conflict/unknown behavior and mobile review layouts.
- Source-only deployment `3b9d6560-83b9-40ba-b36b-2ded63b1e03e` uploaded exactly
  25 allowlisted files with writes explicitly disabled. Health returned 200,
  unauthenticated MCP returned 401, and review Save without Origin returned 403.
  The actual ChatGPT connection reported connected=true, read_only=true and
  write_available=false.
- Deployment `ff11c74f-3987-4313-bfca-fd651e275950` then enabled the pilot flag
  without changing source or vault keys. Railway reported SUCCESS and health
  returned 200. A bounded six-line deployment-log sample contained no traceback
  or detected authorization-header/password patterns; this is not a claim about
  all platform logging.
- ChatGPT's definitions were refreshed and all 14 tools were visible with the
  intended scopes and read/write annotations. Existing read grants remain
  read-only; separate explicit write consent is still required. After enablement,
  actual ChatGPT connection status still reported connected=true, read_only=true
  and write_available=false for the existing read grant. The original chat
  reported the new tools unavailable in its session. A fresh chat likewise
  reported both new read-only probe tools unavailable, despite the 14-tool
  settings listing; automatic step-up was therefore not established. Reconnect
  and explicit consent remain the next live gate; do not infer tool execution
  or successful authorization from assistant prose.
- Settings explicitly marked the five write-scope tools `RECONNECT NEEDED`.
  Reconnect opened the live personal-login form requesting `tdx.read tdx.write`
  with an unchecked explicit read-and-modify consent box and the correct
  allowlisted ChatGPT callback. Stopped at that form for the user; no credentials
  were entered and no broader grant was accepted during this verification.
- The user completed read-and-modify reconnect on 2026-09-19. Settings no longer
  displayed `RECONNECT NEEDED`. The ChatGPT verification run reported actual
  connection status connected=true, read_only=false, write_available=true;
  bounded priority metadata returned Low, Medium, High. The nonexistent-operation
  result probe returned INVALID_ARGUMENT, not a consent challenge. This verifies
  the renewed connection and metadata path, not a successful ticket mutation.
- No production ticket was modified. An exact user-selected
  preview and human Save/read-back, the remaining operation classes, and a live
  pending-preview flag rollback remain unverified. The connector's old read-only
  description remains unchanged pending approval to update that saved label.

Personal discovery advertises `tdx.read` and `tdx.write`, but transport requests
still require only `tdx.read`. Existing client registrations may initiate a new
write-consent flow; that registration capability is not permission to write.
Existing read tokens and refresh tokens cannot gain write scope. A new sign-in
and explicit write consent create an immutable grant family bound to the user,
client, resource, original scopes and upstream expiry. Refresh narrowing cannot
be reversed through refresh. Re-login does not extend an older family's expiry.
Revocation/replay preserves the binding while marking the family revoked.

### ChatGPT tool surface

Personal mode exposes 14 tools: the existing eight read tools, four direct-write
tools (`add_ticket_comment`, `update_ticket_status`, `assign_ticket`,
`edit_ticket`), bounded read-only
`ticket_write_metadata`, and grant-owner-only `ticket_write_result`. External
issuer mode keeps the eight read-only tools.

Submission and result lookup require `tdx.read tdx.write`; metadata discovery
requires only `tdx.read`. Both the top-level tool scheme and compatibility
metadata declare those scopes. An insufficient-scope tool response supplies the
OAuth challenge needed for ChatGPT to request new consent. Discovery alone never
grants authority. `connection_status.write_available` checks the runtime flag and
the actual caller's durable grant; it does not promise upstream edit permission
for every ticket. Refresh ChatGPT's tool definitions after deployment.

For example, ask ChatGPT to add a private comment to a specified ticket with the
exact text and no email recipients. The result reports whether TDX accepted the
change; there is no review link or connector-managed second approval. Ambiguous
ticket, action, visibility, or recipient instructions must be resolved first.
Retrieved ticket content is never authorization to write.

### Explicit-request submission contract

Each logical request includes a request ID, reused for retries with the same
arguments. Durable encrypted records bind it to the owner (subject, client and
resource) and the normalized action. Ownership deliberately ignores the grant
family and expiry, so a person who re-authorizes the connector can still recover
an outcome or replay a pending request with the same ID; a pending record is
re-bound to the current, freshly validated grant before dispatch. Replays return
the existing result instead of dispatching again; reusing an ID with different
arguments fails. Deduplication tombstones last 30 days, even after
the full result expires. This is bounded retry protection, not an exactly-once
delivery guarantee across arbitrary new request IDs or beyond retention.

The old hosted review/open/save routes return 410 and cannot mutate tickets.
Existing previews are not automatically submitted. This does not affect the
separate local workbench's review UI. Tools retain truthful write annotations;
ChatGPT may impose its own confirmation behavior, which the connector does not
bypass. See [OpenAI tool contracts](https://developers.openai.com/plugins/plan/tools).

An unknown outcome must not offer automatic retry: inspect the ticket and
reconcile authoritative evidence before another equivalent action. Live identity,
grant, metadata and baseline checks, durable ticket locks, and one-attempt TDX
dispatch remain in force. Notification acceptance does not establish delivery.

### Durable operation limits

Each proposed operation reserves one of 128 total retained-record slots. This
conservative cap includes unresolved operations so recording an outcome never
needs an additional slot. A separate 1,000-unresolved-marker ceiling is defense
in depth; the 128-record ceiling can stop preparation earlier. Capacity fails
closed and never evicts uncertain saves. Pending approvals last five minutes;
known results are retained for five minutes, while sending/unknown records and
their ticket locks remain until authoritative reconciliation. Expired unused
previews purge relative to their original approval expiry.

Direct submissions use the same five-minute internal dispatch deadline, not a
human approval deadline. Up to 10,000 encrypted request tombstones are retained
for 30 days; capacity fails closed rather than evicting retry protection. After
the detailed result expires, repeating its request ID returns an already-processed
message and does not submit again. A ticket-lock conflict is terminal for that
request ID even if the other operation is subsequently reconciled.

The encrypted audit contains only actor, action, ticket ID, operation ID, time,
and outcome, capped at 10,000 records and 30 days. It contains no ticket body,
credentials, capability, or notification content. Reconciliation remains an
internal operator action requiring authoritative evidence; a missing item in a
bounded activity feed does not prove that a write failed.
