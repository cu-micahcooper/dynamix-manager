# Ticket features for the hosted connector — design notes

Status: prepared 2026-09-23, not yet approved or implemented. Written so the work can be picked
up cold. Source of API facts: the tenant OpenAPI document
(`https://cedarville.teamdynamix.com/TDWebApi/swagger/v1/openapi.json`). Nothing here has been
exercised live yet; every status code below is "documented, verify live".

Owner decisions already taken elsewhere: no time entries, no projects or portfolios, no tenant
configuration (SLA definitions, escalation steps, blackout windows, operational hours, automatic
assignment stay out). Reports shipped separately (`list_reports`, `run_report`).

## Tenant facts

Seven ticketing applications (`AppClass == "TDTickets"`): 634 InfoTech Tickets (the only one the
connector uses today), 1072 CTL Tickets, 1497 Enrollment Services Tickets, 3327 MarCom,
1729 Operations WorkOrders, 1054 UHR, 3406 Contracting Requests. Access is governed per user by
TDX; the connector must never assume membership.

## Features, in suggested build order

### 1. Multi-application ticket reads

The biggest scope gap. Today `Connection.app_id` is pinned to "InfoTech Tickets" and every ticket
tool uses it.

| Operation | Notes |
| --- | --- |
| `GET /api/tickets/{id}` | Cross-application lookup: returns the ticket if the caller has TDNext-level visibility in whatever app holds it. Rate limit 60/min/IP. The response carries `AppID`/`AppName`. |
| `GET /api/applications` | Already fetched; filter `TDTickets` for the user's apps. |
| `POST /api/{appId}/tickets/search`, `.../statuses`, `.../feed` etc. | All existing per-app routes work for any `appId` the user belongs to. |

Design: add an optional `app_id` (or `app` name) parameter to `search_tickets`, `my_queue`,
`ticket_statuses`, `get_ticket`, `ticket_feed`, `show_tickets`, defaulting to InfoTech Tickets so
nothing changes for existing callers. `get_ticket` without `app_id` first tries the default app,
then falls back to `GET /api/tickets/{id}` and reports which application answered.
`connection_status` lists the user's ticketing applications. Writes stay on the default app until
the write adapter learns per-request `app_id` (the adapter is constructed with one `app_id`;
generalising it is a separate, larger change and should be its own spec).

### 2. Saved searches

| Operation | Notes |
| --- | --- |
| `GET /api/{appId}/tickets/searches` | `SavedSearch[]`: `ID`, `Name`, `ComponentName`, `CreatedFullName`, `AppName`. |
| `POST /api/{appId}/tickets/searches/{searchId}/results` (`TicketSavedSearchOptions`) | `SearchText`, `OnlyMy`, `OnlyOpen`, `Page` (page object: `PageIndex`, `PageSize`; verify shape live). Returns `ResultPageOfTicket` (`Items`, `TotalRecords`?, verify). |

Tools: `list_saved_searches(app_id)`, `run_saved_search(search_id, text, only_mine, only_open,
page, page_size, app_id)`. Read-only; same ticket projection as `search_tickets`. High value:
people already trust their TDNext saved searches.

### 3. Attachments

| Operation | Notes |
| --- | --- |
| `GET /api/attachments/{id}` | `Attachment`: `ID` (GUID string), `Name`, `Size`, `IsPrivate`, `CreatedFullName`, `CreatedDate`, `ItemID`, `ContentUri`. |
| `GET /api/attachments/{id}/content` | Raw bytes. `.../contentBase64` returns base64 text. 60/min/IP. |
| `POST /api/{appId}/tickets/{id}/attachments` (multipart, `showViewLink`, `isPrivate`) | Upload. Also `.../attachments/bulk`. `POST .../knowledgebase/{id}/attachments` for articles. |
| `DELETE /api/attachments/{id}` | "Cannot be undone" — do not expose. |

Reads: `ticket_attachments(ticket_id)` from the ticket's `Attachments` collection (already in
`GET .../tickets/{id}`), `read_attachment(attachment_id)` returning extracted text for text-like
types (txt, csv, json, html via `display_text`, pdf via the existing PDF tooling if wanted) and
metadata only for binaries, with a size cap (e.g. 2 MB). ChatGPT cannot hand the connector a file,
so upload is only meaningful as "attach this text as a file" — defer unless asked.

### 4. Templates and response templates

| Operation | Notes |
| --- | --- |
| `GET /api/{appId}/tickets/templates?classification=` | `TicketTemplateListing[]` visible to the caller (own, global, shared). |
| `GET /api/{appId}/tickets/templates/{id}` | `TicketTemplate` with the field values it applies. |
| `GET /api/{appId}/tickets/responseTemplates?categoryId=&searchText=` | `ResponseTemplate[]`: `ID`, `Name`, `Description`, `Comments` (the canned text), `CategoryName`. |

Tools: `ticket_templates(app_id, search)`, `get_ticket_template(id)`, `response_templates(search,
category_id)`. Response templates pair naturally with `add_ticket_comment`: the model reads the
canned text and posts it (optionally personalised) as a comment; no new write needed. Ticket
templates could later seed `create_ticket` arguments client-side.

### 5. Contacts, children, tags, configuration items

| Operation | Notes |
| --- | --- |
| `GET/POST/DELETE /api/{appId}/tickets/{id}/contacts[/{contactUid}]` | Contacts are `User[]`; add/remove by UID (people resolution applies). 200 with a message. |
| `POST /api/{appId}/tickets/{id}/children` (array of ticket IDs) | Adds children to a parent; no unlink endpoint documented. |
| `POST/DELETE /api/{appId}/tickets/{id}/tags` (array of strings) | Add/remove tags; 200 with a message. |
| `GET/POST/DELETE /api/{appId}/tickets/{id}/configurationItems[/{ciId}]` | CI links; the asset link already exists (`link_asset_to_ticket`), this is the CMDB-side equivalent. |

Writes follow the asset-link pattern: item lock on the ticket, link-state preflight (read the
current contacts/tags/CIs first) so repeats are no-ops, 200 applied, 4xx rejected. Tools:
`ticket_contacts` (read), `add_ticket_contact`, `remove_ticket_contact`, `tag_ticket`,
`untag_ticket`, `add_child_tickets`. Reads of CIs already exist through `ticket_assets`.

### 6. SLA on a ticket

| Operation | Notes |
| --- | --- |
| `GET /api/{appId}/tickets/slas`, `.../slas/{slaId}` | List and detail (read for name resolution). |
| `PUT /api/{appId}/tickets/{id}/sla` (`SlaAssignmentOptions`: `NewSlaID`, `Comments`, `Notify`, `ShouldCascade`, `StartBasis`) | Returns the updated `Ticket`. |
| `PUT /api/{appId}/tickets/{id}/sla/delete` (`SlaRemovalOptions`) | Removes the SLA; returns the ticket. |

Tools: `ticket_slas` (read), `set_ticket_sla(ticket_id, sla_id, comments, cascade)`,
`remove_ticket_sla`. Verify `StartBasis` enum values live. SLA definitions (create/edit/escalation
steps) stay out.

### 7. Workflow

| Operation | Notes |
| --- | --- |
| `GET /api/{appId}/tickets/{id}/workflow` | `TicketWorkflow`: `Name`, `Status`, `IsComplete`, `CurrentStepIDs`, `Steps`, `History`. |
| `GET /api/{appId}/tickets/{id}/workflow/actions?stepId=` | Actions the caller may take on a step. |
| `POST /api/{appId}/tickets/{id}/workflow/approve` (`StepID`, `ActionID`, `Comments`) | Performs an action; returns `TicketWorkflowStepActionResult`. |
| `POST /api/{appId}/tickets/{id}/workflow/reassign` (`StepID`, `UserId` or `GroupId`) | Reassigns a step. |
| `PUT /api/{appId}/tickets/{id}/workflow?newWorkflowId=&allowRemoveExisting=`, `DELETE .../workflow` | Assign or remove a workflow. |

Tools: `ticket_workflow` (read, with current steps and the caller's available actions),
`act_on_workflow_step(ticket_id, step_id, action_id, comments)`,
`reassign_workflow_step`. This is the approval workflow the AP tickets use (the task-completion
work of 2026-09-20 was the task-based variant). Assign/remove workflow is more invasive; include
only if asked. The approve action must be validated against `workflow/actions` at validation time
so the preview can name the action ("Approve", "Reject") before the one attempt.

### 8. Classification and moving between applications

| Operation | Notes |
| --- | --- |
| `PUT /api/{appId}/tickets/{id}/classification?newClassificationId=` | Incident / Service Request / Change / Problem / Release etc.; returns the ticket. |
| `POST /api/{appId}/tickets/{id}/application` (`MoveTicketOptions`: `NewAppID`, `NewFormID`, `NewStatusID`, `NewTicketTypeID`, `Comments`) | Moves a ticket to another ticketing app; returns the ticket. |

Tools: `reclassify_ticket`, `move_ticket(ticket_id, app_id, type_id, form_id, status_id,
comments)`. Move needs destination metadata (types, forms, statuses of the target app), so it
depends on feature 1's per-app metadata reads. Both are edits on the ticket item with the usual
baseline conflict check.

## Cross-cutting

- Every write goes through the existing validate → claim → one attempt → finish pipeline with
  request-ID dedup; new action kinds are added in `ticket_writes/models.py` like the asset and
  knowledge base ones. Per-request `app_id` on writes requires the adapter to be built per app;
  design that once (feature 1) before any write that targets other apps.
- Link-style writes (contacts, tags, children, CIs) use the link-state preflight introduced for
  articles so duplicates never produce an unknown outcome.
- Tool count: features 1 to 8 add roughly 20 tools (43 → ~63). Consider grouping (e.g. one
  `ticket_links` read for contacts+tags+CIs) if the list gets unwieldy for ChatGPT.
- Rate limits are 60/min/IP for all ticket routes; the connector's tenant calls share one IP.
