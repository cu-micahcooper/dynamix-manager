# Personal Ticket Workbench Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development to implement this plan. Steps use checkbox syntax for tracking.

**Goal:** Deliver the approved local, personal TeamDynamix review-and-submit app.

**Architecture:** FastAPI serves a same-origin plain-JavaScript frontend. A workbench package wraps the existing TeamDynamix client, isolates public AI drafting, and persists drafts and submission intents in SQLite. Demo mode exercises all workflow operations without remote writes.

**Tech Stack:** Python 3.13+, FastAPI, uvicorn, requests, SQLite, OpenAI Responses API, pytest, HTML/CSS/JavaScript.

Use the existing checkout on codex/personal-ticket-workbench as requested. Preserve unrelated files. No live ticket writes during development. Do not commit or push unrelated work.

### 1. Establish environment and integration contracts
- [ ] Create `.venv-workbench` using available Python, without deleting the broken `.venv`.
- [ ] Install project dev and web dependencies, run baseline `python -m pytest -q`, record failures.
- [ ] Inspect tenant configuration without printing credential values; read official API documentation for auth/getuser, search, feed, statuses, and feed update.
- [ ] Verify read-only live identity and endpoints if personal credentials are available; keep unverified live writes disabled rather than guessing.

### 2. Domain and persistence
Files: `src/dynamix_manager/workbench/{__init__,domain,store,demo}.py`; `tests/test_workbench_domain.py`, `tests/test_workbench_store.py`.
- [ ] Write failing tests for active direct assignment, overdue/priority/activity ordering, stable revisions, account isolation, and pending intent restart behavior.
- [ ] Run focused tests and establish expected failure before implementing.
- [ ] Implement normalized ticket/history dictionaries, conservative visibility, owner-only SQLite storage, separate draft visibility slots, and fictional demo fixtures.
- [ ] Run focused tests; include concurrency and uncertain intent duplicate protection.

### 3. Remote adapters and AI
Files: `src/dynamix_manager/workbench/{tdx,ai}.py`; `tests/test_workbench_adapters.py`.
- [ ] Test user-scoped login (reject administrator credential pairs), identity and endpoint validation, complete history, status metadata, bounded queue with explicit completeness warnings, and no write retries.
- [ ] Extend the existing client through a workbench adapter without changing legacy analytics behavior.
- [ ] Test public/internal context isolation, schema validation and references, refusals, oversized input, and redacted errors before implementing Responses calls.
- [ ] Configure model explicitly through environment; generate only on request and use store=false. Demo suggestions must be clearly labelled simulated.
- [ ] Verify focused tests; record live-read results without sensitive contents.

### 4. HTTP application and submission state machine
Files: `src/dynamix_manager/workbench/{app,service,__main__}.py`; `tests/test_workbench_app.py`.
- [ ] Test session, allowed Host, same-origin/CSRF protection, draft save/restore, logout isolation, queue navigation and generation.
- [ ] Implement backend APIs: session/login/logout, queue, ticket detail, drafts, suggestions, preview, submit, reconciliation/acknowledgement, skip/reset.
- [ ] Bind previews to server-side immutable payload and ticket revision; submission uses preview ID, not replacement client text.
- [ ] Test changed assignment/status/history rejection, double submission, concurrent clicks, timeouts, uncertain writes surviving restart, rejection and confirmed receipts.
- [ ] Implement no automatic write retry; uncertain results block resubmit until reconciled or explicitly acknowledged after remote inspection. Live capability gates must be visible.
- [ ] Run focused tests and resolve failures.

### 5. Frontend and packaging
Files: `src/dynamix_manager/workbench/static/{index.html,app.js,style.css}`, `pyproject.toml`, `README.md`, `.gitignore`.
- [ ] Build approved two-column layout with accessible labels, original history, AI cards, separate public/internal drafts, instructions, recipients/status, preview dialog and Submit and next.
- [ ] Save drafts before navigating; preserve edits on AI generation and show a separate suggested replacement. Include skip/return, refresh, login and explicit demo indicators.
- [ ] Display source references, incomplete context, stale preview, network/auth failure, uncertain outcome and empty queue states.
- [ ] Add executable module/console entry, packaged static files, web optional dependencies, gitignored local state and setup instructions.

### 6. Verification and delivery
- [ ] Run all pytest tests and targeted ruff checks; distinguish existing failures from regressions and repair introduced issues.
- [ ] Start localhost demo and browser-test generation, edit, review, submission, skip/return, reload persistence and narrow layout. Inspect console and screenshots.
- [ ] Run spec compliance review, then code quality review and address material findings.
- [ ] Document exact start command, verified features, and any remaining live integration gates. Keep real writes behind the approved preview flow.


## Execution record

Implemented the workbench package, local interface, demo workflow, tests, and packaging. The original 193-test baseline passed in the new isolated environment. Independent spec review identified draft transition, stale revision, and uncertain recovery gaps; all were reproduced and fixed with regression tests. A final quality review identified concurrent logout/account-switch handling, addressed before delivery.

Live discovery used existing repository credentials, which identify **ServeCU Ticket**, not a verified personal user. Read-only tenant metadata and ticket/feed contracts were checked. Seven ticketing applications were discoverable; application 634 had no active directly assigned tickets for that configured account. This does not establish the user's personal queue. Login now requires explicit credentials or token and displays the returned identity.

Remaining integration gates: personal login by the user; OpenAI key/model and real generation (absent during development); actual authorized live write and receipt. Live submission remains disabled because automatic approval review rejected enabling the persistent setting. Do not bypass that decision. No real ticket updates were sent.

The local app runs at http://127.0.0.1:8765. Changes are retained on codex/personal-ticket-workbench, uncommitted and unpushed. Existing unrelated workspace files remain untouched.

Final validation: 226 Python tests passed (one upstream Starlette/AnyIO deprecation warning); four Node frontend regression tests passed; scoped Ruff and git diff whitespace checks passed. Wheel build succeeded with all three static assets and no local configuration. Browser verified demo generation/apply/review/submit-next, draft restoration, public/internal separation, skip/return, explicit-login rejection for blank credentials, and desktop/narrow layouts. Independent final reviewer reproduced the repaired account-switch race and found no remaining critical/high issues. No real OpenAI generation or personal-user queue validation occurred because those credentials are not supplied.

User subsequently approved enabling live submissions. Updated the local submission setting and restarted the app; 27 focused app/adapter tests passed. No real ticket update was sent. Personal login, OpenAI setup, and a real authorized submission remain live verification gates.
