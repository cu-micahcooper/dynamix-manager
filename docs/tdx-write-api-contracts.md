# Ticket write API evidence

Retrieved 2026-09-18 using unauthenticated, read-only HTTPS GETs to public documentation. No credentials, ticket records, production mutations, or live notification tests were used. This document records wire contracts and gaps; it does not authorize or claim a working write implementation.

## Sources and scope

- [Cedarville API overview](https://cedarville.teamdynamix.com/TDWebApi/).
- [Cedarville OpenAPI v1](https://cedarville.teamdynamix.com/TDWebApi/swagger/v1/openapi.json): operation IDs and schema names below identify the precise evidence within this document.
- [Cedarville HTTP PATCH guide](https://cedarville.teamdynamix.com/TDWebApi/swagger/guides/patching).
- [Vendor form configuration guide, article 27062](https://solutions.teamdynamix.com/TDClient/1965/Portal/KB/PrintArticle?ID=27062&SIDs=9264).
- [Vendor standard attributes guide, article 161652](https://solutions.teamdynamix.com/TDClient/1965/Portal/KB/PrintArticle?ID=161652).

All API paths below are relative to `https://cedarville.teamdynamix.com/TDWebApi`. Bind `{appId}` to the configured positive ticket application ID even though the upstream reference permits `0`. The API overview requires JSON and bearer authentication. The three mutation operations below document authentication, 401 and 403 responses, but do not enumerate fine-grained ticket write permissions. They do not require administrative authentication in their published contracts.

## Support matrix and blocking gate

| Action | Documented wire support | Notification evidence | Remaining gate |
| --- | --- | --- | --- |
| Comment | `POST /api/{appId}/tickets/{id}/feed`; `TicketFeedEntry` inherits `FeedEntry` | Explicit `IsPrivate` and email-address array `Notify` | Contract supports explicit private/public and recipients. Public reference does not define the exact public audience or interaction of private visibility with external email recipients. Do not promise privacy prevents email disclosure. Live behavior remains untested. |
| Status/close | Same feed operation with `NewStatusID`; status metadata identifies completed/cancelled classes | Same explicit feed `Notify`; `CascadeStatus=false` avoids requesting cascade | No complete permitted-transition or closure-required-field metadata found. Reject unsupported status requirements during preparation; never guess a resolution field. On-hold statuses requiring a date cannot be fulfilled by the documented feed schema alone. |
| User/group assignment | `PATCH /api/{appId}/tickets/{id}`; `ResponsibleUid`, `ResponsibleGroupID`; vendor guide permits a person, group, or both | `notifyNewResponsible` query boolean defaults false; true notifies newly responsible resource(s) when responsibility changes | Setting explicitly selected fields while preserving the other field is supported. Clearing semantics are underdocumented; do not implicitly clear or invent precedence. |
| Title/description/priority | Same PATCH; `/Title`, `/Description`, `/PriorityID` | Same explicit responsibility notification flag; no responsibility change is requested | Partial edits documented. Can use a preflight snapshot and intended-field-only patch, with the final race disclosed. Do not infer other notification recipients or fall back to full-object POST. |
| Create | `POST /api/{appId}/tickets`; `Ticket` body | Explicit reviewer/requestor/responsible query controls | **Blocked for arbitrary forms:** no form field/rule/default metadata contract found. Generic custom-attribute metadata is insufficient to establish form-specific requirements. No savable creation preview until selected form requirements and effective defaults are evidenced. |

This is not a claim that all five capabilities passed Task 1. Comments, status changes without unsupported extra requirements, explicit-field assignment, and title/description/priority edits have documented contracts sufficient for local implementation and synthetic tests. Arbitrary creation remains blocked; this prevents claiming the full five-action scope passed the gate. An absent permission preflight or absent atomic conditional write is a limitation, not by itself a capability blocker under the approved design. Unknown *relevant* notification effects must block a savable preview; the explicit flags/recipient array are usable evidence and are not equivalent to unknown behavior. The public docs do not constitute an audit of tenant automation rules.

## Comments and status changes

OpenAPI operation `Tickets_PostTicketFeed` accepts a required `TicketFeedEntry` request body and returns HTTP 200 described as the generated item update. Its 200 response does **not** declare a JSON response schema; do not depend on invented response keys to decide success.

Live correction, 2026-09-19: Cedarville returned HTTP 201 for the user's saved
comment. The encrypted operation result preserved status_code=201; authoritative
ticket-feed read-back confirmed the exact comment and requested notification
recipient. Accept 200 or 201 for POST feed operations (comment/status), without
depending on a response-body schema. PATCH still requires 200 and matching ticket
identity. 202, unverified other statuses, and transport failures remain unknown;
never retry them automatically. The historical unknown record is not silently
rewritten by this classification fix and still requires operator reconciliation.

`FeedEntry` fields:

| Field | Type and meaning |
| --- | --- |
| `Comments` | Non-null string; no length limit or required-array entry declared |
| `IsPrivate` | Boolean private status |
| `IsRichHtml` | Boolean selecting rich versus plain text |
| `Notify` | Nullable array of email strings to notify; not user IDs or group IDs |
| `IsCommunication` | Nullable boolean marking a communications record |
| `NewStatusID` | Nullable integer; null or zero leaves status unchanged |
| `CascadeStatus` | Boolean; defaults false |

Use explicit `IsPrivate`, `IsRichHtml`, `Notify`, `NewStatusID`, and `CascadeStatus` in any eventual normalized request. Default proposed comments to private/plain text/empty recipients. An empty explicit recipient list requests no recipients through this field; it is not evidence about unspecified tenant rules. Showing a private feed entry and emailing its text are distinct effects that must both be visible in review.

`GET /api/{appId}/tickets/statuses` (`TicketStatuses_GetStatuses`) returns active `TicketStatus` objects. Fields include ID, name, active/default flags, `StatusClass`, `RequireGoesOffHold`, and `DoNotReopen`. `StatusClass` values: 1 new, 2 in process, 3 completed, 4 cancelled, 5 on hold, 6 requested; 0 is unknown. Closure uses a verified actual status ID whose class is 3 or 4, never a hardcoded ID inferred from its name. `Ticket.CompletedDate` is read-only. `RequireGoesOffHold` applies to on-hold statuses, but `TicketFeedEntry` has no corresponding date field. No transition graph, required resolution attribute list, or workflow bypass guarantee appears in this operation/schema. Status updates on tickets converted to incomplete project tasks are expressly restricted by the ticket edit/PATCH documentation; do not use another endpoint to evade that restriction.

## Partial edits, assignment, and concurrency

`Tickets_PatchTicket` accepts `JsonPatchDocumentOfTicket`, an array of operations, and returns HTTP 200 with a `Ticket`. It supports the ticket itself and custom attributes, not other collections. Each operation requires `op` and `path`; values follow the target field type. `Title` is non-null and at most 300 characters; `Description` is nullable text; `PriorityID` is an integer resolved from current active priority metadata. `ResponsibleUid` is a nullable GUID and `ResponsibleGroupID` an integer. The vendor form guide confirms that person, group, or both are valid responsibility states and that an individual taking a group ticket can preserve the group. The API reference does not specify cross-field clearing effects. Preview both fields and patch only explicit intended changes; do not silently clear the other field.

`notifyNewResponsible=false` is documented as the default; setting true notifies newly responsible resources if responsibility changes. Explicitly preserve the chosen flag in the preview and URL. Group recipients can depend on membership notification preferences (`GroupMember.IsGroupNotified`); exact recipients cannot be inferred from the group name alone. The workflow step reassignment endpoint is a different, admin-only operation and is outside scope.

The PATCH guide documents case-insensitive property names and custom-attribute addressing by attribute ID rather than array index. `add`/`copy` cannot add properties; `remove`/`move` clear properties rather than remove them. **The guide explicitly states that `test` is unsupported**, although the generated OpenAPI `op` enum contains `test`. Do not implement compare-and-set with it. No ETag/If-Match or revision precondition parameter is documented on the researched mutation operations. `ModifiedDate` is read-only snapshot evidence, not a conditional-write token. The approved preflight comparison plus partial patch still has a last-moment external race; local same-ticket serialization cannot prevent edits made outside this service.

`Tickets_EditTicket` is a separate full `Ticket` POST operation. It must not become a fallback for partial changes. Converted project tasks cannot edit start/end date, estimated minutes, or either responsibility field; incomplete converted tasks additionally cannot edit status.

## Creation and required metadata

`Tickets_CreateTicket` takes `Ticket` and returns HTTP 201 with the created `Ticket`. The `Ticket` schema marks `TypeID`, `Title`, `AccountID`, `StatusID`, `PriorityID`, and `RequestorUid` required. Endpoint prose also allows at least one of `RequestorEmail`, `RequestorUid`, or `AccountID`; `RequestorEmail` is marked read-only in the shared schema. This mismatch means an email-only minimal request is not a verified fixture. Supplying an existing verified `RequestorUid` and every schema-required field avoids relying on that alternative, but does not resolve form requirements.

Query parameters:

| Parameter | Published semantics |
| --- | --- |
| `EnableNotifyReviewer` | Enables reviewer notification |
| `NotifyRequestor` | Whether to notify requestor |
| `NotifyResponsible` | Whether to notify responsible resources |
| `AllowRequestorCreation` | Whether to create a requestor if none matches |
| `PreferRequestorAccountAndPriority` | Chooses requestor defaults versus supplied account and priority |
| `applyDefaults` | Applies defaults for unspecified properties; default true |
| `TemplateId` | If positive, template fills unspecified fields; supplied creation values take precedence |
| `SourceApplication` | Defaults to TDWebApi; identifies source for downstream filtering |

Defaults for the notification/requestor-creation/preference booleans are not specified in the generated query schemas. Any future supported creation must set those controls explicitly. Disable requestor creation; creating people is outside scope. Do not rely on omitted templates/default values without showing the effective result. `TicketType` includes reviewer/group IDs, `NotifyReviewer`, and other notification email addresses, which matter when reviewer notification is enabled.

`GET /api/{appId}/tickets/forms` (`Tickets_GetTicketForms`) returns active `Form` objects. `Form` contains identity, active/configured/default flags, component/classification IDs, names, timestamps and display flags. It has no fields collection, per-field required/default values, conditional dependencies, or form access rules. The OpenAPI paths/schema inventory did not expose another ticket-form detail/field endpoint. `TicketType` likewise has no creation-required-fields collection.

`GET /api/attributes/custom?componentId=9&appId={appId}` returns active non-protected `CustomAttribute` objects. `associatedTypeId` is documented as a **project** type filter; do not assume it selects a ticket form/type. Attributes expose `IsRequired`, `IsUpdatable`, `DataType`, `FieldType`, `Choices`, `AssociatedItemIDs`, and string `Value`. Protected and inactive attributes are excluded. `GET /api/attributes/{id}/choices` requires an admin service account, so do not use it as a personal-session fallback. The PATCH guide documents choice IDs as strings and multiple selected IDs as a comma-separated string.

The vendor form guide confirms configurable required/hidden/default fields and dependencies, but describes client portal/Work Management behavior. It does **not** prove which UI rules the API enforces or bypasses. The standard-attributes article also describes those UI contexts. Thus neither article closes the API metadata gap. An authoritative selected-form contract (including how API defaults and required attributes behave), or a vendor-supported metadata source, is needed before creation preparation can be savable. Do not discover requirements by repeatedly attempting real ticket creation.

## Read-only metadata and permission checks

| Need | Documented source | Limits |
| --- | --- | --- |
| Ticket baseline | `GET /api/{appId}/tickets/{id}` | Returns `Ticket`; does not prove write permission |
| Identity | `GET /api/auth/getuser` (existing personal adapter) | Must bind current authenticated identity; no supplied-user impersonation |
| Requestor/assignee discovery | `GET /api/people/lookup?searchText=...&maxResults=...` | Max 1–100, default 50; omits applications, groups, attributes, permissions |
| Person validation | `GET /api/people/{uid}` | Requires `TDPeople`; `User` includes `IsActive`, UID, emails and app associations |
| Structured people search | `POST /api/people/search` | Read operation, requires `TDPeople`; supports `IsActive` and `MaxResults`; omits app/group/permission collections; `AppName` is system-app name, not platform ID |
| Group discovery | `POST /api/groups/search` | Read operation, requires `TDPeople`; supports `AssociatedAppID`, `HasAppID`, `IsActive`, `NameLike`; no documented result limit in `GroupSearch` |
| Group validation | `GET /api/groups/{id}`, `/applications`, `/members` | Require `TDPeople`; validate active/app binding; members expose notification preference |
| Priorities | `GET /api/{appId}/tickets/priorities` | Returns active `Priority` objects |
| Types | `GET /api/{appId}/tickets/types?isActive=true` | Personal authenticated list; individual `/types/{id}` requires application administrator |
| Statuses/forms | The application-bound GETs above | Active metadata; not complete transition/form-rule validation |
| Permissions | `GET /api/securityroles/permissions?forAppId=...` | Catalog of available permissions, **not** actor's effective ticket authorization |

Search results alone do not prove assignability. Resolve selected records individually and filter by configured application before exposing choices. Fail closed when necessary metadata is unavailable to the personal actor; do not acquire admin credentials to fill the gap. No researched endpoint offers an authoritative per-ticket create/edit authorization dry run. A successful read cannot guarantee a subsequent write, and the real mutation remains authoritative about permission at dispatch time. Permission denial must be shown safely without blindly retrying.

Membership shape verified directly against OpenAPI during implementation:
`User.Applications` is an array of system-application strings, not platform IDs.
Use `User.OrgApplications` (`UserApplication` inheriting `Application`) with `ID`
and `IsActive` for user platform membership. Group application associations use
`GroupApplication.AppID` (and `GroupID`), not the user shape; the group itself has
`IsActive`. Do not use synthetic fixtures that make these distinct shapes identical.

## Synthetic contract fixtures (not live captures)

These fixtures show only documented wire shapes. IDs, text, dates and emails are synthetic; they do not assert that a tenant would accept them or that blocked capabilities are ready. Mutation responses for feed are intentionally not invented.

Private plain-text comment without explicitly requested recipients, `POST /api/42/tickets/1001/feed`:

```json
{"Comments":"Synthetic review note.","IsPrivate":true,"IsRichHtml":false,"Notify":[],"NewStatusID":0,"CascadeStatus":false}
```

Public comment explicitly emailing a synthetic address uses `IsPrivate:false` and `Notify:["reviewer@example.invalid"]`; all other fields are unchanged. Status change uses a metadata-verified synthetic status ID in `NewStatusID`; no closure-specific payload is asserted here. Published successful feed status: HTTP 200, generated item update with unspecified schema.

Partial title/priority edit, `PATCH /api/42/tickets/1001?notifyNewResponsible=false`:

```json
[
  {"op":"replace","path":"/Title","value":"Synthetic updated title"},
  {"op":"replace","path":"/PriorityID","value":7}
]
```

Synthetic projection of the documented HTTP 200 `Ticket` response, useful for snapshot/read-back parsing (not a complete wire response):

```json
{"ID":1001,"AppID":42,"Title":"Synthetic updated title","Description":"Synthetic description.","PriorityID":7,"StatusID":5,"ResponsibleUid":null,"ResponsibleGroupID":9,"ModifiedDate":"2026-09-18T12:00:00Z"}
```

Synthetic metadata projections:

```json
{
  "status":{"ID":5,"Name":"Synthetic closed","IsActive":true,"StatusClass":3,"RequireGoesOffHold":false},
  "form":{"ID":8,"AppID":42,"Name":"Synthetic form","IsActive":true,"IsConfigured":true},
  "attribute":{"ID":1234,"Name":"Synthetic required field","IsRequired":true,"IsUpdatable":true,"DataType":"String","FieldType":"Textbox","Choices":null}
}
```

There is deliberately no executable creation or implicit cross-field assignment-clearing fixture: unresolved form requirements and clearing semantics would otherwise be disguised as verified contracts. Explicitly setting selected responsibility fields while preserving the other field remains suitable for implementation.

## OAuth and baseline companion checks

The coordinating agent verified [OpenAI OAuth documentation](https://developers.openai.com/plugins/build/auth) on 2026-09-18. Per-tool `securitySchemes` scopes, protected-resource metadata, and a runtime `_meta["mcp/www_authenticate"]` challenge with `insufficient_scope`/`error_description` support requesting additional tool authorization. The server must independently validate issuer, audience, expiry, and required scopes. Existing read grants must not silently gain write authority. This documents the authorization mechanism, not a completed ChatGPT consent test.

The coordinating agent reported a fresh baseline of 344 Python tests and 14 frontend tests passing on 2026-09-18. Those tests validate the pre-implementation repository, not production write contracts. No write capability is live-verified by this research.

## Staged implementation checkpoint

The user subsequently approved deferring creation and implementing the other four
operation classes. A read-only call through the connected hosted pilot confirmed
personal authentication and InfoTech Tickets app 634. Its live status metadata
includes completed/cancelled statuses without required off-hold dates and several
on-hold statuses with `RequireGoesOffHold=true`. At least one on-hold-class status
does not require the date: validation must inspect the flag, not infer the
requirement solely from status class. No ticket content or writes were involved.

## Ticket tasks (verified 2026-09-20 against the published OpenAPI document)

Source: `https://demotemplate.teamdynamix.com/TDWebApi/swagger/v1/openapi.json`
(TeamDynamix Web API v1, OpenAPI 3.0.0).

- `GET /api/{appId}/tickets/{ticketId}/tasks` (`TicketTasks_GetTicketTasks`) returns
  `TicketTask[]`. Relevant fields: `ID`, `TicketID`, `Title`, `IsActive` (read-only),
  `PercentComplete` (read-only), `CompletedDate`/`CompletedUid`/`CompletedFullName`
  (read-only), `ResponsibleUid`, `ResponsibleGroupID`, `ModifiedDate` (read-only),
  `TypeID` (regular task vs. scheduled maintenance activity).
- `POST /api/{appId}/tickets/{ticketId}/tasks/{id}/feed` (`TicketTasks_AddUpdate`) takes
  `TicketTaskFeedEntry` = `FeedEntry` (`Comments`, `IsPrivate`, `IsRichHtml`, `Notify`,
  `IsCommunication`) plus `PercentComplete` (nullable int32). The schema states a value
  must be provided for either `PercentComplete` or `Comments`. Because
  `TicketTask.PercentComplete` is read-only, **the task feed is the only documented way
  to change completion**; `PUT .../tasks/{id}` (`TicketTasks_EditTicketTask`) is a full
  `TicketTask` replacement for title/description/dates/responsibility and must not be
  used as a completion fallback. Documented success is 200 returning the generated feed
  entry; Cedarville returns 201 for the analogous ticket feed, so treat 200 and 201 as
  accepted and everything else as documented for ticket feed writes.
- Not stated in the document but **confirmed live on 2026-09-20** (ticket 30605254, six
  template tasks): posting `PercentComplete: 100` to the task feed returns HTTP 201,
  sets `PercentComplete` to 100 and `CompletedDate` to the real completion time, and
  activates the task's successor (`IsActive` flipped from false to true on the next task
  in the chain). `IsActive` stays true on a completed task, so it signals predecessor
  gating, not completion; completion is `PercentComplete == 100` / `CompletedDate` set.
  An incomplete task reports `CompletedDate` as `0001-01-01T00:00:00` (the .NET minimum
  date), not null; the adapter treats that sentinel as unset. Whether TDX notified the
  responsible party was not observed. Rate limit: 60 requests per 60 seconds per IP.
