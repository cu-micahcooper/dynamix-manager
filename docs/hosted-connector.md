# Hosted connector: implementation and launch gates

The resource server supports a TDX-backed personal login alongside the original
external-issuer mode. Since 2026-09-21 the personal login runs **multi-user** when
`TDX_HOSTED_ALLOWED_UID` is omitted: anyone who authenticates to TeamDynamix with
their own credentials becomes their own OAuth subject, and TDX's permissions
govern what each person can read or change. Setting `TDX_HOSTED_ALLOWED_UID`
pins the connector to one person, as the original pilot did. This hosted
connector is the only MCP surface; the earlier local stdio plugin was removed,
and `dynamix_manager.plugin` holds only the shared tool core.

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
`TDX_HOSTED_VAULT_PATH`, `TDX_HOSTED_VAULT_KEY` and `TDX_HOSTED_REDIRECT_URIS`
(JSON array of approved callback URLs: exact HTTPS URLs, or an HTTPS path prefix
ending in `/*` that admits exactly one extra path segment with no query or
fragment; production uses `https://chatgpt.com/connector/oauth/*` because ChatGPT
gives every person's copy of the app its own callback). `TDX_HOSTED_ALLOWED_UID`
is optional: set it to restrict the connector to one person, omit it to admit
every authenticated TeamDynamix user.
An empty callback array permits the process to run but denies all client
registration and authorization. Obtain the callback from ChatGPT configuration;
never substitute a guessed callback or wildcard.

The login page asks for personal TDX credentials and explicit consent to the
requested read-only or read-and-write scopes. Read-only remains the default. Credentials
are sent only to the connector over HTTPS and then Cedarville's TDX API. The
returned identity must match the configured UID. Connector access tokens expire
within ten minutes, with single-use rotating refresh tokens. Replaying a used
refresh token revokes its entire grant family. This is an OAuth bridge, not native
TDX OAuth or SSO.

**Two sign-in options.** Option A is the TeamDynamix username and password
(with the optional "Keep me connected"). Option B is Cedarville single sign-on:
the page links to `GET /api/auth/loginsso` on the tenant, which TeamDynamix wires
through its Shibboleth service provider to Cedarville's Entra ID and, after
sign-in, displays the bearer token as plain text on the TeamDynamix origin. The
person copies that token into the login form; the connector verifies it with
`getuser`, links the account with the token's own `exp` as the grant expiry, and
stores no password, so it cannot renew and the person repeats this daily. The two
options are exclusive in one submission. Option B was verified live on
2026-09-22: the owner completed the Entra hop in the browser, pasted the token,
the connector linked the account with no denials logged, and the resulting grant
listed all 19 tools, reported write access available, returned the queue, and
survived a refresh-token rotation. TeamDynamix documents `loginsso` as
intended for its own client-side code: the endpoint sends no CORS headers, takes no
return URL, and its `getuser`/`login` CORS policy is `*` without credentials, so a
third-party server can never receive the token automatically. Copy-and-paste is the
only password-free path.

**Page design.** The sign-in, success and error pages share one inline stylesheet
in Cedarville brand colors (blue `#003963`, gold `#FBB93A`, orange for warnings)
allowed by CSP hash only; there are still no scripts and no external assets. The
sign-in card shows the password and SSO options side by side (stacked on phones)
with the SSO steps numbered; the success page says "You're connected", returns to
ChatGPT automatically after four seconds via a meta refresh with the button as
fallback, and states whether access will renew or expire; the error page lists
the likely causes (expired or reused link, rejected credentials, both options
filled) and points back to ChatGPT.

**Per-user isolation.** Vault records, OAuth grant families, write-grant
bindings, request-ID deduplication and audit rows are all keyed by the person's
TDX UID (the OAuth subject). Tests prove one person's tokens resolve only their
own TDX credential, a result lookup by another subject is refused, and a replay
of another person's request ID becomes a new operation. Rotated refresh tokens
are retained for 24 hours to detect replay rather than for the whole 90-day
grant, and store capacities were raised for campus-scale use.

**Staying connected.** TDX bearer tokens last about 24 hours and cannot be
refreshed, so by default the connector would need a new sign-in every day. The
login form's "Keep me connected" box (unchecked by default in production, so each
person opts in knowingly) stores the username and
password, encrypted in the same vault envelope as the token under the external
Fernet key. When a request finds the stored token within one hour of expiry, the
connector logs in to TDX again, verifies the returned UID still matches, replaces
the token and continues; the person sees no interruption. Remembered logins issue
90-day connector grants so ChatGPT keeps refreshing silently. A failed renewal
keeps using a still-valid token and logs only a fixed label; if the token has
already expired the next request asks for a fresh sign-in. A renewal that
authenticates as a different UID wipes the stored link. Tradeoff: the personal
password is at rest on the host, so anyone holding both the volume and the vault
key could log in as that person rather than for at most 24 hours. To stop storing
it, sign in again with the box unchecked (which overwrites the record with a
token-only link bounded by TDX expiry), or remove the vault record as the
operator.

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

## Production deployment (CU DevOps Playground) — 2026-09-21

The production connector runs in the Cedarville Railway workspace **CU DevOps
Playground**, separate from the personal-account pilot, which stays up until
ChatGPT is cut over.

- Project `teamdynamix-connector`: `7e6cec5a-9dba-4fcf-a528-52d192eca04d`.
- Service `connector`: `39f4ffae-b110-40fb-a9db-95834cf7366e`.
- Environment `production`: `085a1c95-0914-4f82-8b95-a2fde442b6ec`.
- Volume `connector-volume`: `454dda37-ef2b-40f7-b0e1-476dc502f02a`, mounted at `/data`.
- HTTPS origin: `https://connector-production-a492.up.railway.app`.
- Variables: `PORT=8000`, `TDX_HOSTED_AUTH_MODE=personal`, `TDX_HOSTED_PUBLIC_URL`,
  `TDX_HOSTED_VAULT_PATH=/data/private/credentials.sqlite`, a vault key generated
  on the operator's machine and never printed, `TDX_HOSTED_REDIRECT_URIS` with the
  existing ChatGPT callback plus the localhost test callback. **No
  `TDX_HOSTED_ALLOWED_UID`**: this instance is multi-user, and
  `TDX_HOSTED_WRITES_ENABLED` is unset so writes are on by default.
- Service settings had to be set through Railway's GraphQL API because a CLI-created
  service ignores `railway.toml` and its `Builder` enum has no Dockerfile value:
  `dockerfilePath=deploy/hosted.Dockerfile` (which makes Railway build the
  Dockerfile), `healthcheckPath=/healthz`, timeout 120, one replica, restart on
  failure with 3 retries. The first two deployments failed instantly under Railpack.
- Deployment `e4dabca1-617a-4c95-b98b-8ed84bf68441` (commit `0cdbbc5`) succeeded:
  `/healthz` 200, both discovery documents 200 with `tdx.read tdx.write`,
  unauthenticated `/mcp` 401, `/writes/review` 410, and a DCR + authorize round
  trip rendered the multi-user login page with read-and-modify consent and "Keep
  me connected" unchecked.
- **Deploying (2026-09-22 onward).** The CLI's browser login belongs to a different
  Railway account, so production deploys use a project token scoped to
  teamdynamix-connector / production, kept in the git-ignored `.env` as
  `RAILWAY_TOKEN`. Build a clean bundle (`git archive HEAD pyproject.toml README.md
  LICENSE railway.toml deploy/ src/`) into a scratch directory and run
  `railway up --path-as-root <dir> -s connector -e production -d` with that
  variable set. `railway whoami` reports Unauthorized under a project token; that
  is expected. Always confirm `railway status` names this project first: without
  a valid link the CLI silently creates a new project named after the upload
  directory in whichever account is logged in (this happened once, producing a
  failed "bundle" project in the personal account).

Cutover checklist: (1) in ChatGPT, point the TeamDynamix connector at the new
`/mcp` URL (or create a new connector) and note the callback URL it shows;
(2) add that callback to `TDX_HOSTED_REDIRECT_URIS` if it differs; (3) each person
signs in once on the login page; (4) share/publish the connector in the ChatGPT
workspace so other staff can add it; (5) once verified, remove the pilot project
and its volume from the personal Railway account. A custom `cedarville.edu`
domain changes the OAuth issuer and audience, so set it before people connect or
expect everyone to reconnect.

### ChatGPT cutover — 2026-09-22

ChatGPT's settings offer no way to change an installed app's MCP server URL, so
the cutover created a new app, **TeamDynamix**
(`asdk_app_6ab26f9bc67881918e3f49a669cbe9bb`), pointed at
`https://connector-production-a492.up.railway.app/mcp` with OAuth via Dynamic
Client Registration and both `tdx.read` and `tdx.write` as default scopes. The
setup form reveals the app's callback only under the "User-Defined OAuth Client"
registration method: `https://chatgpt.com/connector/oauth/_Eilu8ZXDrY0`, now in
`TDX_HOSTED_REDIRECT_URIS` alongside the pilot's callback and the localhost test
callback. Creation immediately launched the OAuth flow; the owner signed in on the
production login page (no denials logged) and the app shows a connected account
and all 18 tools. End-to-end test from a ChatGPT conversation the same day:
`connection_status` active; "tickets requested by Alan McCain created in August
2026" resolved the person (stated back as mccaina@cedarville.edu, no
confirmation prompt) and returned the three matching tickets with the ticket
widget rendered; a private comment on the closed connector test ticket 30865373
was written and read back from the feed. The pilot app was then deleted from
ChatGPT. The pilot Railway project in the personal account remains until
decommissioned (requires signing the CLI in as that account).

### Sharing the app with colleagues — 2026-09-22

ChatGPT offers no per-person sharing for a developer-mode app and the owner is not
a workspace admin, so each colleague adds the app themselves: Plugins, Create app,
name it, paste `https://connector-production-a492.up.railway.app/mcp`, keep OAuth
(endpoints and both scopes are discovered automatically), tick the risk
acknowledgement, Create, then sign in on the connector's login page with their own
TeamDynamix credentials. Each copy gets its own
`https://chatgpt.com/connector/oauth/<id>` callback, so the production allowlist
now carries the prefix rule (deployment `a5bfad71`, then variable restart
`d360c6c5`); a live registration with an arbitrary callback under that prefix
returned 201 while the bare prefix, a nested path and a foreign host returned 400.
Developer mode must be enabled for the colleague in the workspace. Publishing to
the whole Edu workspace remains a workspace-admin action.

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

### Hosted-only deployment — 2026-09-20

Commit `5d441c7` removed the local stdio plugin and made personal mode
read/write by default. Verified locally first against the real tenant with a
self-signed-TLS harness: full OAuth flow, all eight read tools, metadata
discovery, one private test comment and one status change (New to Closed) on
ticket 30848005, each applied once with replay deduplicated. Railway deployment
`90f78dcf-379e-452d-99c9-4f29f8340432` (clean `git archive` bundle of
`pyproject.toml`, `README.md`, `LICENSE`, `railway.toml`, `deploy/`, `src/`)
succeeded; live checks: `/healthz` 200, both discovery documents 200 with
`tdx.read tdx.write`, unauthenticated `/mcp` 401, `/writes/review` 410,
unapproved callback registration 400. The service keeps its explicit
`TDX_HOSTED_WRITES_ENABLED=true`, which now matches the default. Refresh the
connector's tool definitions in ChatGPT after this deployment.

### Stay-connected deployment — 2026-09-20

Commit `cb9d22c` added remembered logins with automatic TDX token renewal and
90-day connector grants. Railway deployment `dcb9648b-bff0-441e-bd38-3a7f7b71d1bf`
succeeded; live checks: `/healthz` 200, unauthenticated `/mcp` 401,
`/writes/review` 410, and a real DCR + authorize round trip rendered the login
page with "Keep me connected" checked by default. Existing links made before
this deployment hold no password and still expire with their TDX token; sign in
once more through ChatGPT with the box checked to switch to renewal.

### Task completion deployment — 2026-09-20

Commit `26d8577` added `complete_ticket_task` and `list_ticket_tasks`. Railway
deployment `72337f64-b8f2-4e09-a265-9435eeedab03` succeeded; `/healthz` 200 and
unauthenticated `/mcp` 401. Live confirmation on ticket 30605254 the same day:
six task completions each returned 201 and read back with `PercentComplete` 100
and a real `CompletedDate`; each completion activated the next task in the
template chain. This surfaced one bug, fixed in deployment `0189ced2-7e3f-4111-a305-c83824a19b5a` (commit `0e8f518`): TDX
reports an incomplete task's `CompletedDate` as `0001-01-01T00:00:00`, which the
preflight had treated as already complete. Refresh the connector's tool
definitions in ChatGPT to see the new tools.

### Search filters deployment — 2026-09-21

Commit `16c9773` (server-side search filters, people resolution before person
searches, `my_queue` by status class). Railway deployment
`892be1b9-81c5-44ab-981e-4fc30a3b5a58` succeeded; `/healthz` 200, unauthenticated
`/mcp` 401. Exercised live the same day: "Alan McCain",
`mccaina@cedarville.edu` and `mccaina` all resolve to Alan McCain and return
his tickets by UID; "McCain" alone lists seven candidates and runs no search.
Follow-up commit `9c33b56` (deployment `02b41da3-8c8f-49fb-8bf4-a4c62451718e`) matched usernames by
email local part, since live lookups omit `UserName`, and switched
responsibility filters to the ticket's primary responsibility. Refresh the
connector's tool definitions in ChatGPT to pick up the new filters.

### Ticket creation deployment — 2026-09-21

Commit `c89c86e` added `create_ticket` and `ticket_create_metadata`; Railway
deployment `cbbdc9f7-b90e-4fdb-b6cf-07d6d3c1bd39` succeeded and the persistent
test client listed 18 tools. Live read checks: types, forms and sources list
correctly and the account search returns "Information Technology" (56883). Live
creation verified the same day: ticket 30865373 ("Connector creation test
(please ignore)", type Classroom Technology and Support, account Information
Technology, source 4. Web, requestor and responsible resolved from "Micah
Cooper" to micahcooper@cedarville.edu) returned 201. Tenant defaults applied
with `applyDefaults=true`: status New, priority Low, form Generic Form, and the
responsible group CIO alongside the requested responsible person, so the
one-call assignment took and no second step was needed. Replaying the same
request ID returned the identical result without a second creation. The
ticket was then closed through `update_ticket_status`.

### ChatGPT tool surface

Personal mode exposes 43 tools: twenty-three read tools (the original eight,
`show_tickets`, six asset reads: `search_assets`, `get_asset`, `asset_feed`,
`ticket_assets`, `asset_tickets`, `asset_metadata`, six knowledge base
reads: `search_articles`, `get_article`, `article_categories`,
`related_articles`, `article_services`, `asset_articles`, and two report
reads: `list_reports`, `run_report`), fifteen direct-write
tools (`add_ticket_comment`, `update_ticket_status`, `assign_ticket`,
`edit_ticket`, `complete_ticket_task`, `create_ticket`, `add_asset_comment`,
`link_asset_to_ticket`, `edit_asset`, `create_article`, `edit_article`,
`link_article`, `unlink_article`, `create_article_category`,
`edit_article_category`), bounded read-only `ticket_write_metadata`,
`list_ticket_tasks` and `ticket_create_metadata`, and the grant-owner-only
`ticket_write_result` and `resolve_ticket_write`. External issuer mode keeps the
twenty-three read-only tools.

**Assets.** The connector discovers the tenant's asset application by class
(`TDAssets`), preferring "InfoTech Assets/CIs" when several exist (Cedarville has
CTL, InfoTech and Operations asset apps); `connection_status` lists them.
`search_assets` maps the API's `AssetSearch` filters one-to-one and resolves
`owner`/`user` through the people API like ticket searches. `asset_tickets` and
the `asset_id` filter on `search_tickets` go through the asset's
`ConfigurationItemID`, which is what ticket search filters on. Asset writes use
the same one-attempt, request-ID-deduplicated pipeline: comments post to the
asset feed, links post to `tickets/{id}/assets/{assetId}` and lock the ticket,
edits are a JSON Patch on the asset with status, owner and department verified
first and a snapshot compare so concurrent changes conflict. Results carry an
`item` naming the asset and its TDNext URL. Live-verified reads on 2026-09-22:
statuses, model and vendor search, owner-resolved search, asset detail with
attributes, feed, and ticket links. Live-verified writes the same day on the
owner's own asset 1973209: a feed comment returned 201, replayed identically
under the same request ID, and appeared in the feed **as public even though it
was sent private**, so the tool now states that asset feeds ignore the private
flag; a no-op `edit_asset` (external ID set to its current value) returned 200
with the updated asset and left tag and status unchanged. `link_asset_to_ticket`
was exercised the same day on scratch ticket 30879870: the link returned 200 and
appeared on both `ticket_assets` and `asset_tickets`, the replay under the same
request ID returned the stored result, and a second link under a new request ID
returned 204 (already linked), which the adapter now records as applied. Before
that fix the 204 was classified unknown, which left the ticket write-locked
(an unknown outcome holds its ticket lock until reconciled with authoritative
evidence; see the submission contract below). The connector exposes no unlink,
so a link is permanent from the connector's point of view.

**Knowledge base.** The Client Portal application is discovered by class
(`TDClient`; Cedarville has one, 2045) and reported by `connection_status`.
Reads map `ArticleSearch` one-to-one, resolve `author` through the people API
(reflected back in `resolved_people`), and return bodies as plain-text
snippets of 400 characters; `get_article` gives the full text, or the raw HTML
on request. Text search ranks archived articles with approved ones, so the
search tool tells the model to filter by status and published flag for
"what do we tell users" questions. Writes use the same pipeline:
`create_article` makes a Not Submitted draft owned by the signed-in user unless
an owner or owning group is named (TeamDynamix requires exactly one);
`edit_article` is a JSON Patch with a revision-and-modified-date baseline and a
preview notice when a change archives; `link_article` and `unlink_article`
relate an article to an asset or to another article and read the current link
state first, so a repeat is a no-op instead of the 400 or 500 the tenant
returns; categories can be created and edited, never deleted. Article deletion
is not exposed: archive instead. **Publishing is not possible through the API**:
live on 2026-09-22 both PATCH and PUT returned 200 and left `IsPublished` and
`IsPublic` unchanged, so the tools do not offer those flags and every preview
says to publish in the portal. Bodies are sanitised before saving (script,
iframe, object and embed elements and `on*` attributes are removed, and the
preview says so); plain text is wrapped in paragraphs. Live-verified the same
day on scratch category 28469 and article 173058: category create and edit,
article create, field edits, status to Approved and Archived, asset and
related-article link and unlink with read-back on both sides. Design:
`docs/superpowers/specs/2026-09-22-knowledge-base-tools-design.md`.

**Reports.** `list_reports` maps `ReportSearch` server-side (name text,
application, owner resolved through the people API) over the Report Builder
reports the signed-in user can see (147 for the owner on 2026-09-23).
`run_report` fetches one report with data (`GET /api/reports/{id}?withData=true`,
optional `dataSortExpression` validated to a column name plus ASC/DESC) and
returns the report's displayed columns plus rows keyed by header text, limited
to at most 200 rows per call with `total_rows` reporting the full size; cells
are passed through the same text scrubber as ticket bodies. The API returns
the whole result set regardless of the limit (the survey report is ~6,800
rows), so the limit bounds the tool output, not the tenant call. Rate limits
are per user: 45/min for listing, 30/min for running. No TDNext URL is
emitted because the report viewer's URL shape has not been verified.
Live-verified 2026-09-23 on production (deployment `e5a748b0`): name search,
owner-resolved listing (99 reports owned by the connector owner), and the
survey report (6,793 rows) sorted by completion date. An unknown sort column
is silently ignored by TeamDynamix rather than rejected.

**Ticket cards are on demand.** Only `show_tickets(ticket_ids)` carries the
widget `outputTemplate`, so ChatGPT renders the ticket viewer just when asked to
show tickets, for up to ten known IDs (missing or unpermitted IDs are reported).
`search_tickets`, `my_queue`, `get_ticket` and `ticket_feed` return text only, so
multi-step answers no longer splash cards at every intermediate call; `get_ticket`
and `ticket_feed` stay widget-callable for the card view's drill-down.

`create_ticket` posts a `Ticket` to `POST /api/{appId}/tickets` with fixed query
flags: no requestor creation, no responsible or reviewer notification,
`applyDefaults=true`, and requestor notification only when asked. Required inputs
are title, `type_id`, `account_id` and a requestor (name/email resolved through
the people API, or a UID); optional description, form, status, priority, service,
source and a responsible person or group. Preflight verifies every ID as active
in the tenant. Omitted status, priority and form use the tenant's defaults; the
result's `detail` reports the new ticket ID and the applied status, priority,
form, type, requestor, account and responsibility. If the created ticket is not
assigned as intended, `assign_ticket` on the new ID is the second step. Custom
attribute values and templates are not supported; a tenant rejection over a
required attribute comes back as a `rejected` outcome with the HTTP status.
`ticket_create_metadata` lists active types, forms and sources and searches
accounts server-side (`POST /api/accounts/search`).

`complete_ticket_task` posts `PercentComplete: 100` (optionally with a comment) to
the ticket task feed, the only documented way to change completion; see the task
section of `docs/tdx-write-api-contracts.md`. Preflight requires the task to be
active, incomplete and on the named ticket, and the stored baseline includes the
task so a concurrent task change produces a conflict. An applied outcome means
TDX accepted the update; the tool description tells the model to confirm
`CompletedDate` through `list_ticket_tasks` before reporting the task as done.

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

### Server-side ticket search and person resolution

`search_tickets` maps every filter one-to-one onto the API's `TicketSearch`
model and sends it to `POST /api/{appId}/tickets/search`; nothing is fetched
broadly and filtered locally. Exposed filters: ticket ID, status IDs, status
classes, on-hold flag, requestor UIDs, primary-responsible UIDs and group IDs,
priority/type/service/account/form IDs, created/modified/closed date ranges
(ISO 8601, validated before any request), and days-old bounds. `complete` is
true when fewer rows than `limit` came back, meaning every match was returned.
`my_queue` sends `StatusClassIDs` and `PrimaryResponsibilityUids` directly instead
of downloading the status list first.

Person filters are resolved before searching. `requestor` and `responsible`
accept a name, email or username; the connector calls `GET /api/people/lookup`,
keeps active accounts, prefers exact matches on full name, primary or alternate
email, username, or the primary email's local part (live lookups return an empty
`UserName`), otherwise accepts a single candidate, and searches by the
resulting UIDs. The result's `resolved_people` lists who was matched (UID, name,
primary email) so the model states, for example, "searching tickets requested by
mccaina@cedarville.edu" without asking for confirmation. When several partial
candidates match, no search runs; the candidates are returned with a warning so
the right person can be chosen and the search retried by UID.

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

**Resolving an unknown outcome (added 2026-09-22).** An unknown outcome holds
its ticket or asset lock, and its equivalence marker, until it is resolved; the
store never expires them. The live link test showed the consequence: a
misclassified 204 left scratch ticket 30879870 write-locked with no operator
path in. `resolve_ticket_write(operation_id, resolution, observation)` is that
path. It is grant-owner-only (the same ownership check as `ticket_write_result`),
accepts only operations whose effective state is unknown (including a dispatch
interrupted mid-send), and records the owner's verdict as authoritative evidence:
`applied` when the user sees the change in TeamDynamix, `not_applied` when it is
absent, which frees the equivalent change for a new request ID. The
`observation` text (10 to 500 characters, what the user saw) is stored in the
encrypted audit row. The tool carries write hints and its description tells the
model to ask the user to look before calling it; it is never a retry shortcut.
To make the stuck operation findable, a lock or equivalence conflict now
returns `detail.blocking_operation_id`, but only when the blocking operation
belongs to the same grant owner; another user's operation stays anonymous. Live-verified on production (deployment `e242682a`, commit `f89c086`) against
the locked scratch ticket: a new comment returned `conflict` naming the stuck
link operation; `ticket_write_result` showed it unknown; resolving it as
applied released the lock; a repeat resolve was refused; and the same comment
under a new request ID then applied (201).

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
