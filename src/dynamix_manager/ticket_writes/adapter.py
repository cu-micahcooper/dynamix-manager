"""Tenant-bound, single-attempt writes using verified TDX wire contracts.

The caller must durably claim the operation and compare the baseline before calling
apply_once. This adapter deliberately provides neither retries nor approval logic.
"""
import json
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import requests

from dynamix_manager.kb_text import ensure_html, sanitize_html

from .models import (ARTICLE_STATUS_IDS, ArticleAction, ArticleCreateAction, ArticleEditAction, ArticleLinkAction,
                     ArticleUnlinkAction, AssetAction, AssetCommentAction, AssignAction, CategoryAction, CategoryCreateAction,
                     CategoryEditAction, ChangePreview, CommentAction, CreateAction, EditAction, EditAssetAction,
                     LinkAssetAction, PreparedChange, PreviewField, StatusAction, TaskAction, WriteResult, parse_action)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


class WriteAdapter:
    def __init__(self, base_url, app_id, token, *, request=None, read=None,
                 application_name="InfoTech Tickets", header_app_id=None, asset_app_id=None, portal_app_id=None):
        base_url = base_url.rstrip("/")
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path.lower() != "/tdwebapi"):
            raise ValueError("Expected an HTTPS tenant API URL.")
        if type(app_id) is not int or app_id <= 0 or not token:
            raise ValueError("An application and personal token are required.")
        self.base_url, self.app_id, self.application_name = base_url, app_id, application_name
        if asset_app_id is not None and (type(asset_app_id) is not int or asset_app_id <= 0):
            raise ValueError("The asset application ID must be positive.")
        self.asset_app_id = asset_app_id
        if portal_app_id is not None and (type(portal_app_id) is not int or portal_app_id <= 0):
            raise ValueError("The client portal application ID must be positive.")
        self.portal_app_id = portal_app_id
        self._headers = {"Authorization": f"Bearer {token}", "X-TDClient-ID": str(header_app_id or app_id),
                         "Accept": "application/json"}
        # requests.request creates a fresh Session with zero retry adapters. Do not use
        # the shared read client's retry/error-collapsing transport for mutations.
        self.request = request or requests.request
        self.read = read or self._read

    @classmethod
    def from_connection(cls, connection):
        connection.ready()
        if connection.auth_mode != "user":
            raise ValueError("Ticket writes require a personal connection.")
        try:
            asset_app_id = connection.asset_app_id
        except Exception:
            asset_app_id = None  # asset tools then fail closed with a clear message
        try:
            portal_app_id = connection.portal_app_id
        except Exception:
            portal_app_id = None  # knowledge base tools then fail closed with a clear message
        return cls(connection.base_url, connection.app_id, connection.token,
                   header_app_id=connection.header_app_id, asset_app_id=asset_app_id, portal_app_id=portal_app_id)

    def _read(self, path):
        if not path.startswith("/api/"):
            raise ValueError("Metadata path is outside this tenant.")
        response = self.request("GET", self.base_url + path, headers=self._headers,
                                timeout=(5, 30), allow_redirects=False)
        if response.status_code != 200:
            raise ValueError("Required ticket metadata is unavailable.")
        return response.json()

    def _get(self, path):
        try:
            return self.read(path)
        except Exception:
            raise ValueError("Required ticket metadata is unavailable.") from None

    def _metadata_request(self, method, path, *, payload=None):
        """Make one bounded read-only metadata request and collapse all failures."""
        if method not in {"GET", "POST"} or not path.startswith("/api/"):
            raise ValueError("Ticket write metadata is unavailable.")
        options = {
            "headers": self._headers,
            "timeout": (5, 30),
            "allow_redirects": False,
        }
        if method == "POST":
            options["json"] = payload
        try:
            response = self.request(method, self.base_url + path, **options)
            if response.status_code != 200:
                raise ValueError
            result = response.json()
        except Exception:
            raise ValueError("Ticket write metadata is unavailable.") from None
        if not isinstance(result, list):
            raise ValueError("Ticket write metadata is unavailable.")
        return result

    @staticmethod
    def _valid_option(item):
        return (
            isinstance(item, dict)
            and type(item.get("ID")) is int
            and item["ID"] > 0
            and isinstance(item.get("Name"), str)
            and bool(item["Name"].strip())
            and item.get("IsActive") is True
        )

    def discover_metadata(self, kind, *, search=None, limit=10):
        """Return a minimal, bounded projection of write-supporting metadata."""
        if kind not in {"statuses", "priorities", "people", "groups"}:
            raise ValueError("Unsupported ticket write metadata kind.")
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError("Metadata limit must be between 1 and 10.")
        if kind in {"statuses", "priorities"}:
            if search is not None:
                raise ValueError("Search is unsupported for this metadata kind.")
            rows = self._metadata_request(
                "GET", f"/api/{self.app_id}/tickets/{kind}"
            )
            results = []
            for item in rows:
                if not self._valid_option(item):
                    continue
                if kind == "statuses":
                    if (
                        type(item.get("StatusClass")) is not int
                        or item["StatusClass"] not in range(1, 7)
                        or item.get("RequireGoesOffHold") is not False
                    ):
                        continue
                    results.append({
                        "ID": item["ID"],
                        "Name": item["Name"],
                        "StatusClass": item["StatusClass"],
                        "RequireGoesOffHold": item["RequireGoesOffHold"],
                    })
                else:
                    results.append({"ID": item["ID"], "Name": item["Name"]})
                if len(results) == limit:
                    break
            return self._metadata_result(kind, results)

        if not isinstance(search, str):
            raise ValueError("Person and group searches require 2 to 100 characters.")
        search = search.strip()
        if not 2 <= len(search) <= 100:
            raise ValueError("Person and group searches require 2 to 100 characters.")
        if kind == "people":
            return self._discover_people(search, limit)
        return self._discover_groups(search, limit)

    @staticmethod
    def _metadata_result(kind, results):
        return {
            "kind": kind,
            "results": results,
            "returned": len(results),
            "complete": False,
        }

    PEOPLE_CANDIDATE_WINDOW = 50

    def _discover_people(self, search, limit):
        # The lookup mixes customers and technicians and carries no application data, so
        # scan a bounded window and verify each candidate until ``limit`` are eligible.
        query = urlencode({"searchText": search, "maxResults": self.PEOPLE_CANDIDATE_WINDOW})
        candidates = self._metadata_request("GET", f"/api/people/lookup?{query}")
        results, seen = [], set()
        for candidate in candidates[:self.PEOPLE_CANDIDATE_WINDOW]:
            if len(results) == limit:
                break
            if not isinstance(candidate, dict):
                continue
            try:
                uid = str(UUID(str(candidate.get("UID"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if uid in seen:
                continue
            seen.add(uid)
            details = self._metadata_object("GET", f"/api/people/{uid}")
            name = details.get("FullName")
            try:
                details_uid = str(UUID(str(details.get("UID"))))
            except (TypeError, ValueError, AttributeError):
                continue
            if (
                details_uid != uid
                or details.get("IsActive") is not True
                or not isinstance(name, str)
                or not name.strip()
                or not self._app_eligible(details.get("OrgApplications"), person=True)
            ):
                continue
            results.append({"ID": uid, "Name": name, "AppID": self.app_id})
        return self._metadata_result("people", results)

    def _discover_groups(self, search, limit):
        candidates = self._metadata_request(
            "POST",
            "/api/groups/search",
            payload={"NameLike": search, "IsActive": True, "HasAppID": self.app_id},
        )
        results, seen = [], set()
        for candidate in candidates[:limit]:
            if (
                not isinstance(candidate, dict)
                or type(candidate.get("ID")) is not int
                or candidate["ID"] <= 0
                or candidate["ID"] in seen
            ):
                continue
            group_id = candidate["ID"]
            seen.add(group_id)
            group = self._metadata_object("GET", f"/api/groups/{group_id}")
            applications = self._metadata_request(
                "GET", f"/api/groups/{group_id}/applications"
            )
            if (
                not self._valid_option(group)
                or group["ID"] != group_id
                or not self._app_eligible(applications, group_id=group_id)
            ):
                continue
            results.append({"ID": group_id, "Name": group["Name"], "AppID": self.app_id})
        return self._metadata_result("groups", results)

    def _metadata_object(self, method, path):
        if method != "GET" or not path.startswith("/api/"):
            raise ValueError("Ticket write metadata is unavailable.")
        try:
            response = self.request(
                method,
                self.base_url + path,
                headers=self._headers,
                timeout=(5, 30),
                allow_redirects=False,
            )
            if response.status_code != 200:
                raise ValueError
            result = response.json()
        except Exception:
            raise ValueError("Ticket write metadata is unavailable.") from None
        if not isinstance(result, dict):
            raise ValueError("Ticket write metadata is unavailable.")
        return result

    def snapshot(self, ticket_id):
        if type(ticket_id) is not int or ticket_id <= 0:
            raise ValueError("Ticket ID must be positive.")
        ticket = self._get(f"/api/{self.app_id}/tickets/{ticket_id}")
        if (not isinstance(ticket, dict) or ticket.get("AppID") != self.app_id
                or ticket.get("ID") != ticket_id or not ticket.get("ModifiedDate")
                or not isinstance(ticket.get("Title"), str)):
            raise ValueError("A matching application-bound ticket baseline is required.")
        return ticket

    TASK_KEYS = ("ID", "Title", "IsActive", "PercentComplete", "CompletedDate",
                 "ResponsibleFullName", "ResponsibleGroupName", "TypeID")

    @staticmethod
    def _completed_date(task):
        """TDX serializes an unset completion date as the .NET minimum date, not null."""
        value = task.get("CompletedDate")
        if not isinstance(value, str) or not value or value.startswith("0001-01-01"):
            return None
        return value

    def list_tasks(self, ticket_id, *, limit=25):
        """Return a bounded, minimal projection of a ticket's tasks (no descriptions or emails)."""
        if type(ticket_id) is not int or ticket_id <= 0:
            raise ValueError("Ticket ID must be positive.")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Task limit must be between 1 and 100.")
        rows = self._metadata_request("GET", f"/api/{self.app_id}/tickets/{ticket_id}/tasks")
        valid = [row for row in rows if isinstance(row, dict) and type(row.get("ID")) is int
                 and row["ID"] > 0 and isinstance(row.get("Title"), str)]
        tasks = [{**{key: row.get(key) for key in self.TASK_KEYS}, "CompletedDate": self._completed_date(row)}
                 for row in valid[:limit]]
        return {"ticket_id": ticket_id, "tasks": tasks, "returned": len(tasks),
                "complete": len(valid) <= limit}

    CREATE_KINDS = {
        "types": ("/tickets/types", ("ID", "Name", "CategoryName")),
        "forms": ("/tickets/forms", ("ID", "Name", "IsDefaultForApp")),
        "sources": ("/tickets/sources", ("ID", "Name")),
    }
    CREATE_FLAGS = {"EnableNotifyReviewer": "false", "NotifyResponsible": "false",
                    "AllowRequestorCreation": "false", "applyDefaults": "true"}

    def discover_create_metadata(self, kind, *, search=None, limit=10):
        """Bounded, minimal options for creating tickets: types, forms, sources (lists) and accounts (search)."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Limit must be between 1 and 100.")
        if kind == "accounts":
            if not isinstance(search, str) or not 2 <= len(search.strip()) <= 100:
                raise ValueError("Account searches require 2 to 100 characters.")
            rows = self._metadata_request("POST", "/api/accounts/search",
                                          payload={"SearchText": search.strip(), "IsActive": True, "MaxResults": limit})
            valid = [row for row in rows if self._valid_option(row)]
            results = [{"ID": row["ID"], "Name": row["Name"]} for row in valid[:limit]]
            return {"kind": kind, "results": results, "returned": len(results), "complete": len(valid) <= limit}
        if kind not in self.CREATE_KINDS:
            raise ValueError("Unsupported ticket creation metadata kind.")
        path, keys = self.CREATE_KINDS[kind]
        rows = self._metadata_request("GET", f"/api/{self.app_id}{path}")
        needle = search.strip().casefold() if isinstance(search, str) and search.strip() else None
        valid = [row for row in rows if self._valid_option(row)
                 and (needle is None or needle in row["Name"].casefold())]
        results = [{key: row.get(key) for key in keys} for row in valid[:limit]]
        return {"kind": kind, "results": results, "returned": len(results), "complete": len(valid) <= limit}

    def _named_option(self, path, identifier, label):
        options = self._get(path)
        matches = [row for row in options if isinstance(row, dict) and row.get("ID") == identifier] if isinstance(options, list) else []
        if len(matches) != 1 or not self._valid_option(matches[0]):
            raise ValueError(f"Selected {label} could not be verified as active.")
        return matches[0]["Name"]

    def _named_object(self, path, identifier, label):
        item = self._get(path)
        if not isinstance(item, dict) or item.get("ID") != identifier or not self._valid_option(item):
            raise ValueError(f"Selected {label} could not be verified as active.")
        return item["Name"]

    def _validate_create(self, action):
        app = self.app_id
        snapshot = {
            "type": self._named_option(f"/api/{app}/tickets/types", action.type_id, "ticket type"),
            "account": self._named_object(f"/api/accounts/{action.account_id}", action.account_id, "account"),
        }
        requestor = self._get(f"/api/people/{action.requestor_uid}")
        if (not isinstance(requestor, dict) or str(requestor.get("UID", "")).lower() != str(action.requestor_uid)
                or requestor.get("IsActive") is not True or not requestor.get("FullName")):
            raise ValueError("Selected requestor is not a verified active person.")
        snapshot["requestor"] = requestor["FullName"]
        payload = dict(TypeID=action.type_id, Title=action.title, IsRichHtml=False,
                       AccountID=action.account_id, RequestorUid=str(action.requestor_uid))
        fields = [PreviewField(name="Title", before=None, after=action.title),
                  PreviewField(name="Type", before=None, after=snapshot["type"]),
                  PreviewField(name="Account", before=None, after=snapshot["account"]),
                  PreviewField(name="Requestor", before=None, after=snapshot["requestor"])]
        if action.description is not None:
            payload["Description"] = action.description
            fields.append(PreviewField(name="Description", before=None, after=action.description))
        if action.form_id is not None:
            snapshot["form"] = self._named_option(f"/api/{app}/tickets/forms", action.form_id, "form")
            payload["FormID"] = action.form_id
            fields.append(PreviewField(name="Form", before=None, after=snapshot["form"]))
        for attr, collection, wire, label in (("status_id", "statuses", "StatusID", "Status"),
                                              ("priority_id", "priorities", "PriorityID", "Priority")):
            value = getattr(action, attr)
            if value is None:
                fields.append(PreviewField(name=label, before=None, after="Tenant default"))
                continue
            option = self._active_option(collection, value)
            snapshot[label.lower()] = option["Name"]
            payload[wire] = value
            fields.append(PreviewField(name=label, before=None, after=option["Name"]))
        if action.service_id is not None:
            snapshot["service"] = self._named_object(f"/api/{app}/services/{action.service_id}", action.service_id, "service")
            payload["ServiceID"] = action.service_id
            fields.append(PreviewField(name="Service", before=None, after=snapshot["service"]))
        if action.source_id is not None:
            snapshot["source"] = self._named_option(f"/api/{app}/tickets/sources", action.source_id, "source")
            payload["SourceID"] = action.source_id
            fields.append(PreviewField(name="Source", before=None, after=snapshot["source"]))
        if action.responsible_uid is not None:
            user = self._get(f"/api/people/{action.responsible_uid}")
            if (not isinstance(user, dict) or str(user.get("UID", "")).lower() != str(action.responsible_uid)
                    or user.get("IsActive") is not True or not self._app_eligible(user.get("OrgApplications"), person=True)
                    or not user.get("FullName")):
                raise ValueError("Selected person is not verified active and application eligible.")
            snapshot["responsible"] = user["FullName"]
            payload["ResponsibleUid"] = str(action.responsible_uid)
            fields.append(PreviewField(name="ResponsibleUid", before=None, after=user["FullName"]))
        if action.responsible_group_id is not None:
            group = self._get(f"/api/groups/{action.responsible_group_id}")
            apps = self._get(f"/api/groups/{action.responsible_group_id}/applications")
            if (not isinstance(group, dict) or group.get("ID") != action.responsible_group_id
                    or group.get("IsActive") is not True or not self._app_eligible(apps, group_id=action.responsible_group_id)
                    or not group.get("Name")):
                raise ValueError("Selected group is not verified active and application eligible.")
            snapshot["responsible_group"] = group["Name"]
            payload["ResponsibleGroupID"] = action.responsible_group_id
            fields.append(PreviewField(name="ResponsibleGroupID", before=None, after=group["Name"]))
        preview = ChangePreview(application=self.application_name, ticket_id=0, ticket_title=action.title,
                                action=action.kind, fields=tuple(fields), notices=self._notices(action))
        return PreparedChange(action=action, base_url=self.base_url, app_id=app, baseline_json=canonical_json(snapshot),
                              payload_json=canonical_json(payload), preview=preview)

    def _task_snapshot(self, action):
        task = self._get(f"/api/{self.app_id}/tickets/{action.ticket_id}/tasks/{action.task_id}")
        percent = task.get("PercentComplete") if isinstance(task, dict) else None
        if (not isinstance(task, dict) or task.get("ID") != action.task_id
                or task.get("TicketID") != action.ticket_id or task.get("IsActive") is not True
                or type(percent) is not int or not 0 <= percent < 100 or self._completed_date(task)
                or not isinstance(task.get("Title"), str) or not task.get("ModifiedDate")):
            raise ValueError("The task is not an open, incomplete task on this ticket.")
        return task

    def _task_feed(self, action, task):
        payload = dict(IsPrivate=action.is_private, IsRichHtml=False, Notify=list(action.notify),
                       PercentComplete=100)
        fields = [PreviewField(name="Task", before=task["Title"], after=task["Title"]),
                  PreviewField(name="PercentComplete", before=str(task["PercentComplete"]), after="100")]
        if action.comments is not None:
            payload["Comments"] = action.comments
            fields.append(PreviewField(name="Comment", before=None, after=action.comments))
        return payload, fields

    def metadata(self, action):
        if isinstance(action, StatusAction):
            return self._active_option("statuses", action.status_id)
        if isinstance(action, EditAction) and "priority_id" in action.model_fields_set:
            return self._active_option("priorities", action.priority_id)
        if isinstance(action, AssignAction):
            return self._assignment_metadata(action)
        return {}

    def _active_option(self, collection, identifier):
        options = self._get(f"/api/{self.app_id}/tickets/{collection}")
        matches = [item for item in options if isinstance(item, dict) and item.get("ID") == identifier] if isinstance(options, list) else []
        if len(matches) != 1 or matches[0].get("IsActive") is not True or not matches[0].get("Name"):
            raise ValueError("Selected active metadata could not be verified.")
        return matches[0]

    def _app_eligible(self, applications, *, person=False, group_id=None):
        return isinstance(applications, list) and any(
            isinstance(app, dict) and app.get("ID" if person else "AppID") == self.app_id
            and (app.get("IsActive") is True if person else app.get("GroupID") == group_id)
            for app in applications)

    def _assignment_metadata(self, action):
        selected = {}
        if "responsible_uid" in action.model_fields_set:
            user = self._get(f"/api/people/{action.responsible_uid}")
            if (not isinstance(user, dict) or str(user.get("UID", "")).lower() != str(action.responsible_uid)
                    or user.get("IsActive") is not True or not self._app_eligible(user.get("OrgApplications"), person=True)
                    or not user.get("FullName")):
                raise ValueError("Selected person is not verified active and application eligible.")
            selected["ResponsibleUid"] = user["FullName"]
        if "responsible_group_id" in action.model_fields_set:
            group = self._get(f"/api/groups/{action.responsible_group_id}")
            apps = self._get(f"/api/groups/{action.responsible_group_id}/applications")
            if (not isinstance(group, dict) or group.get("ID") != action.responsible_group_id
                    or group.get("IsActive") is not True or not self._app_eligible(apps, group_id=action.responsible_group_id) or not group.get("Name")):
                raise ValueError("Selected group is not verified active and application eligible.")
            selected["ResponsibleGroupID"] = group["Name"]
        if action.notify_new_responsible:
            raise ValueError("Exact assignment notification recipients are not verified; disable notifications.")
        return selected

    def _require_asset_app(self):
        if self.asset_app_id is None:
            raise ValueError("No asset application is available for this account.")
        return self.asset_app_id

    def _asset_snapshot(self, asset_id):
        app = self._require_asset_app()
        asset = self._get(f"/api/{app}/assets/{asset_id}")
        if (not isinstance(asset, dict) or asset.get("ID") != asset_id or asset.get("AppID") != app
                or not isinstance(asset.get("Name"), str) or not asset.get("ModifiedDate")):
            raise ValueError("A matching asset baseline is required.")
        return asset

    def _asset_prepared(self, action, baseline, payload, fields, *, ticket_id=0, title, visibility=None, recipients=()):
        preview = ChangePreview(application="Assets", ticket_id=ticket_id, ticket_title=title, action=action.kind,
                                fields=tuple(fields), visibility=visibility, recipients=recipients,
                                notices=self._notices(action))
        return PreparedChange(action=action, base_url=self.base_url, app_id=self.app_id, asset_app_id=self.asset_app_id,
                              baseline_json=canonical_json(baseline), payload_json=canonical_json(payload), preview=preview)

    def _validate_asset(self, action):
        asset = self._asset_snapshot(action.asset_id)
        if isinstance(action, AssetCommentAction):
            payload = dict(Comments=action.comments, IsPrivate=action.is_private, IsRichHtml=False, Notify=list(action.notify))
            # Observed live 2026-09-22: TDX asset feeds show comments regardless of IsPrivate.
            return self._asset_prepared(action, asset, payload, [PreviewField(name="Comment", before=None, after=action.comments)],
                                        title=asset["Name"], recipients=action.notify)
        if isinstance(action, LinkAssetAction):
            ticket = self.snapshot(action.ticket_id)
            return self._asset_prepared(action, {"ticket": ticket, "asset": asset}, {},
                                        [PreviewField(name="Asset", before=None, after=asset["Name"])],
                                        ticket_id=action.ticket_id, title=ticket["Title"])
        return self._validate_asset_edit(action, asset)

    def _validate_asset_edit(self, action, asset):
        app = self.asset_app_id
        mapping = (("name", "Name", None), ("tag", "Tag", None), ("serial_number", "SerialNumber", None),
                   ("status_id", "StatusID", "StatusName"), ("owner_uid", "OwningCustomerID", "OwningCustomerName"),
                   ("owning_department_id", "OwningDepartmentID", "OwningDepartmentName"),
                   ("location_id", "LocationID", "LocationName"), ("location_room_id", "LocationRoomID", "LocationRoomName"),
                   ("external_id", "ExternalID", None), ("expected_replacement_date", "ExpectedReplacementDate", None))
        payload, fields = [], []
        for name, wire, label in mapping:
            if name not in action.model_fields_set:
                continue
            value = getattr(action, name)
            before = asset.get(label) if label else asset.get(wire)
            after = value
            if name == "status_id":
                after = self._named_option(f"/api/{app}/assets/statuses", value, "asset status")
            elif name == "owner_uid":
                value = str(value)
                user = self._get(f"/api/people/{value}")
                if not isinstance(user, dict) or str(user.get("UID", "")).lower() != value or user.get("IsActive") is not True or not user.get("FullName"):
                    raise ValueError("Selected owner is not a verified active person.")
                after = user["FullName"]
            elif name == "owning_department_id":
                after = self._named_object(f"/api/accounts/{value}", value, "department")
            payload.append(dict(op="replace", path=f"/{wire}", value=value))
            fields.append(PreviewField(name=wire, before=None if before is None else str(before), after=None if after is None else str(after)))
        return self._asset_prepared(action, asset, payload, fields, title=asset["Name"])

    _ARTICLE_STATUS_NAMES = {1: "Not Submitted", 2: "Submitted", 3: "Approved", 4: "Rejected", 5: "Archived"}
    # (action field, wire field, preview label, baseline display field or None)
    _ARTICLE_FIELDS = (("subject", "Subject", "Subject", None), ("summary", "Summary", "Summary", None),
                       ("body", "Body", "Body", None), ("tags", "Tags", "Tags", None),
                       ("category_id", "CategoryID", "Category", "CategoryName"), ("owner_uid", "OwnerUid", "Owner", "OwnerFullName"),
                       ("owning_group_id", "OwningGroupID", "Owning group", "OwningGroupName"),
                       ("review_date", "ReviewDateUtc", "Review date", None), ("is_public", "IsPublic", "Public", None),
                       ("is_published", "IsPublished", "Published", None), ("status", "Status", "Status", "StatusName"),
                       ("notify_owner", "NotifyOwner", "Notify owner", None),
                       ("notify_owner_of_review_date", "NotifyOwnerOfReviewDate", "Notify owner of review date", None),
                       ("order", "Order", "Order", None))
    _CATEGORY_READ_ONLY = ("Subcategories", "ModifiedDate", "CreatedDate", "CreatedUid", "CreatedFullName",
                           "ModifiedUid", "ModifiedFullName", "AppName", "ParentName")

    def _require_portal_app(self):
        if self.portal_app_id is None:
            raise ValueError("No client portal application is available for this account.")
        return self.portal_app_id

    def _article_snapshot(self, article_id):
        app = self._require_portal_app()
        try:
            article = self._get(f"/api/{app}/knowledgebase/{article_id}")
        except ValueError:
            raise ValueError("The article could not be read.") from None
        if (not isinstance(article, dict) or article.get("ID") != article_id or article.get("AppID") != app
                or not isinstance(article.get("Subject"), str) or not article.get("ModifiedDate")):
            raise ValueError("A matching article baseline is required.")
        return article

    def _category_tree(self):
        app = self._require_portal_app()
        tree = self._get(f"/api/{app}/knowledgebase/categories")
        flat = {}

        def walk(nodes):
            for node in nodes if isinstance(nodes, list) else []:
                if isinstance(node, dict) and type(node.get("ID")) is int and node.get("Name"):
                    flat[node["ID"]] = node
                    walk(node.get("Subcategories"))

        walk(tree)
        return flat

    def _category_name(self, category_id, label="category"):
        node = self._category_tree().get(category_id)
        if node is None:
            raise ValueError(f"Selected {label} was not found in the knowledge base.")
        return node["Name"]

    def _person_name(self, uid, label):
        value = str(uid)
        user = self._get(f"/api/people/{value}")
        if (not isinstance(user, dict) or str(user.get("UID", "")).lower() != value.lower()
                or user.get("IsActive") is not True or not user.get("FullName")):
            raise ValueError(f"Selected {label} is not a verified active person.")
        return user["FullName"]

    def _group_name(self, group_id):
        group = self._get(f"/api/groups/{group_id}")
        if not isinstance(group, dict) or group.get("ID") != group_id or group.get("IsActive") is not True or not group.get("Name"):
            raise ValueError("Selected group is not verified active.")
        return group["Name"]

    def _kb_prepared(self, action, baseline, payload, fields, *, title, notices=()):
        preview = ChangePreview(application="Knowledge Base", ticket_id=0, ticket_title=title, action=action.kind,
                                fields=tuple(fields), notices=self._notices(action) + tuple(notices))
        return PreparedChange(action=action, base_url=self.base_url, app_id=self.app_id, asset_app_id=self.asset_app_id,
                              portal_app_id=self.portal_app_id, baseline_json=canonical_json(baseline),
                              payload_json=canonical_json(payload), preview=preview)

    def _article_body(self, body, notices):
        html, changed = sanitize_html(ensure_html(body))
        if changed:
            notices.append("Script or frame markup was removed from the body before saving.")
        if not html.strip():
            raise ValueError("The article body is empty after sanitising.")
        return html

    def _article_value(self, name, value, notices):
        """Resolve one article field to (wire value, preview text)."""
        if name == "body":
            return self._article_body(value, notices), "(updated body)"
        if name == "tags":
            return list(value), ", ".join(value)
        if name == "category_id":
            return value, self._category_name(value)
        if name == "owner_uid":
            return str(value), self._person_name(value, "owner")
        if name == "owning_group_id":
            return value, self._group_name(value)
        if name == "status":
            return ARTICLE_STATUS_IDS[value], self._ARTICLE_STATUS_NAMES[ARTICLE_STATUS_IDS[value]]
        return value, str(value)

    def _validate_article(self, action):
        self._require_portal_app()
        notices = []
        if isinstance(action, ArticleCreateAction):
            payload, fields = {}, []
            given = action.model_fields_set | {"status", "is_published", "is_public"}
            for name, wire, label, _ in self._ARTICLE_FIELDS:
                if name not in given or getattr(action, name) is None:
                    continue
                wire_value, shown = self._article_value(name, getattr(action, name), notices)
                payload[wire] = wire_value
                fields.append(PreviewField(name=label, before=None, after=shown))
            if action.is_published:
                notices.append("This publishes the article in the portal on creation.")
            if action.is_public:
                notices.append("This makes the article visible without signing in.")
            return self._kb_prepared(action, {"category": payload["CategoryID"]}, payload, fields, title=action.subject, notices=notices)
        article = self._article_snapshot(action.article_id)
        if isinstance(action, (ArticleLinkAction, ArticleUnlinkAction)):
            removing = isinstance(action, ArticleUnlinkAction)
            if action.asset_id is not None:
                target = self._asset_snapshot(action.asset_id)
                label, name = "Asset", target["Name"]
            else:
                target = self._article_snapshot(action.related_article_id)
                label, name = "Related article", target["Subject"]
            field = PreviewField(name=label, before=name if removing else None, after=None if removing else name)
            return self._kb_prepared(action, {"article": article, "target": target}, {}, [field], title=article["Subject"])
        payload, fields = [], []
        for name, wire, label, before_key in self._ARTICLE_FIELDS:
            if name not in action.model_fields_set:
                continue
            wire_value, shown = self._article_value(name, getattr(action, name), notices)
            before = article.get(before_key) if before_key else article.get(wire)
            if name == "body":
                before = "(current body)"
            elif name == "tags" and isinstance(before, list):
                before = ", ".join(map(str, before))
            payload.append(dict(op="replace", path=f"/{wire}", value=wire_value))
            fields.append(PreviewField(name=label, before=None if before is None else str(before), after=shown))
        if action.is_published is True and article.get("IsPublished") is not True:
            notices.append("This publishes the article in the portal.")
        if action.is_public is True and article.get("IsPublic") is not True:
            notices.append("This makes the article visible without signing in.")
        if action.status == "archived" and article.get("Status") != 5:
            notices.append("This archives the article.")
        baseline = {"ID": article["ID"], "ModifiedDate": article["ModifiedDate"], "RevisionNumber": article.get("RevisionNumber"),
                    "Status": article.get("Status"), "IsPublished": article.get("IsPublished"), "IsPublic": article.get("IsPublic")}
        return self._kb_prepared(action, baseline, payload, fields, title=article["Subject"], notices=notices)

    def _validate_category(self, action):
        app = self._require_portal_app()
        if isinstance(action, CategoryCreateAction):
            payload = dict(Name=action.name, IsPublic=action.is_public, InheritPermissions=action.inherit_permissions)
            fields = [PreviewField(name="Name", before=None, after=action.name),
                      PreviewField(name="Public", before=None, after=str(action.is_public))]
            if action.description is not None:
                payload["Description"] = action.description
                fields.append(PreviewField(name="Description", before=None, after=action.description))
            if action.parent_id:
                payload["ParentID"] = action.parent_id
                fields.append(PreviewField(name="Parent", before=None, after=self._category_name(action.parent_id, "parent category")))
            if action.order is not None:
                payload["Order"] = action.order
                fields.append(PreviewField(name="Order", before=None, after=str(action.order)))
            return self._kb_prepared(action, {"parent": action.parent_id}, payload, fields, title=action.name)
        category = self._get(f"/api/{app}/knowledgebase/categories/{action.category_id}")
        if (not isinstance(category, dict) or category.get("ID") != action.category_id or category.get("AppID") != app
                or not category.get("Name") or not category.get("ModifiedDate")):
            raise ValueError("A matching category baseline is required.")
        payload = {k: v for k, v in category.items() if k not in self._CATEGORY_READ_ONLY}
        fields = []
        for name, wire, label in (("name", "Name", "Name"), ("description", "Description", "Description"),
                                  ("parent_id", "ParentID", "Parent"), ("order", "Order", "Order"), ("is_public", "IsPublic", "Public")):
            if name not in action.model_fields_set:
                continue
            value = getattr(action, name)
            before, after = category.get(wire), value
            if name == "parent_id":
                if value == action.category_id:
                    raise ValueError("A category cannot be its own parent category.")
                before = category.get("ParentName")
                after = self._category_name(value, "parent category") if value else "(top level)"
            payload[wire] = value
            fields.append(PreviewField(name=label, before=None if before is None else str(before), after=str(after)))
        return self._kb_prepared(action, {"ID": category["ID"], "ModifiedDate": category["ModifiedDate"]}, payload, fields,
                                 title=category["Name"])

    def validate(self, action):
        action = parse_action(action)
        if isinstance(action, CreateAction):
            return self._validate_create(action)
        if isinstance(action, AssetAction):
            return self._validate_asset(action)
        if isinstance(action, (ArticleAction, ArticleCreateAction)):
            return self._validate_article(action)
        if isinstance(action, (CategoryAction, CategoryCreateAction)):
            return self._validate_category(action)
        ticket, metadata = self.snapshot(action.ticket_id), self.metadata(action)
        if ticket.get("IsConvertedToTask") is True and isinstance(action, (AssignAction, StatusAction)):
            raise ValueError("Assignment and status changes on converted project tasks are unsupported.")
        if isinstance(action, StatusAction):
            if metadata.get("RequireGoesOffHold") is not False or metadata.get("StatusClass") not in range(1, 7):
                raise ValueError("Status requirements are unsupported or unavailable.")
        baseline = ticket
        if isinstance(action, TaskAction):
            task = self._task_snapshot(action)
            payload, fields = self._task_feed(action, task)
            baseline = {"ticket": ticket, "task": task}
        elif isinstance(action, CommentAction):
            payload, fields = self._feed(action, ticket, metadata)
        else:
            payload, fields = self._patch(action, ticket, metadata)
        feed_like = isinstance(action, (CommentAction, TaskAction))
        preview = ChangePreview(application=self.application_name, ticket_id=action.ticket_id,
                                ticket_title=ticket["Title"], action=action.kind, fields=tuple(fields),
                                visibility=("private" if action.is_private else "public") if feed_like else None,
                                recipients=action.notify if feed_like else (),
                                notices=self._notices(action))
        return PreparedChange(action=action, base_url=self.base_url, app_id=self.app_id, baseline_json=canonical_json(baseline),
                              payload_json=canonical_json(payload), preview=preview)

    @staticmethod
    def _notices(action):
        common = ("Read access does not guarantee write permission; TeamDynamix authorizes the save.",
                  "Preflight comparison cannot prevent a last-moment external edit.",
                  "Tenant automation rules have not been audited.")
        if isinstance(action, CommentAction):
            return common + ("Only the listed email recipients are requested through Notify. Private visibility does not prevent disclosure to email recipients.",)
        if isinstance(action, TaskAction):
            return common + ("TeamDynamix accepting the update is not proof the task shows as completed; confirm CompletedDate with list_ticket_tasks.",)
        if isinstance(action, CreateAction):
            return common + ("Omitted status, priority and form use the tenant's defaults; the result reports what was applied.",
                             "Responsible and reviewer notifications are off; the requestor is notified only when notify_requestor is set.")
        if isinstance(action, AssetCommentAction):
            return common + ("TeamDynamix asset feeds do not honor private visibility: the comment is visible to anyone who can view the asset.",
                             "Only the listed email recipients are requested through Notify.")
        if isinstance(action, AssetAction):
            return common + ("Asset changes are applied to the asset record only; linked tickets and CMDB relationships are not modified.",)
        if isinstance(action, (ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction)):
            return common + ("Knowledge base changes are visible to everyone the portal shows the article or category to; there is no private mode.",)
        return common + ("New-responsible notification is disabled.",)

    def _feed(self, action, ticket, metadata):
        status = action.status_id if isinstance(action, StatusAction) else 0
        payload = dict(Comments=action.comments, IsPrivate=action.is_private, IsRichHtml=False,
                       Notify=list(action.notify), NewStatusID=status, CascadeStatus=False)
        fields = [PreviewField(name="Comment", before=None, after=action.comments)]
        if status:
            if "StatusID" not in ticket:
                raise ValueError("Status baseline is unavailable.")
            fields.append(PreviewField(name="Status", before=str(ticket.get("StatusName") or ticket["StatusID"]), after=metadata["Name"]))
        return payload, fields

    def _patch(self, action, ticket, metadata):
        mapping = (("responsible_uid", "ResponsibleUid"), ("responsible_group_id", "ResponsibleGroupID")) if isinstance(action, AssignAction) else (
            ("title", "Title"), ("description", "Description"), ("priority_id", "PriorityID"))
        payload, fields = [], []
        labels = {"ResponsibleUid": "ResponsibleFullName", "ResponsibleGroupID": "ResponsibleGroupName", "PriorityID": "PriorityName"}
        for name, wire in mapping:
            if name not in action.model_fields_set and not isinstance(action, AssignAction):
                continue
            if wire not in ticket:
                raise ValueError("Relevant ticket baseline is unavailable.")
            before = ticket.get(labels.get(wire)) or ticket[wire]
            value = getattr(action, name) if name in action.model_fields_set else ticket[wire]
            if name == "responsible_uid" and value is not None:
                value = str(value)
            after = metadata.get(wire, value) if isinstance(action, AssignAction) else metadata.get("Name", value) if wire == "PriorityID" else value
            if name not in action.model_fields_set:
                after = before
            else:
                payload.append(dict(op="replace", path=f"/{wire}", value=value))
            fields.append(PreviewField(name=wire, before=None if before is None else str(before), after=None if after is None else str(after)))
        return payload, fields

    CREATED_KEYS = (("status", "StatusName"), ("priority", "PriorityName"), ("responsible", "ResponsibleFullName"),
                    ("responsible_group", "ResponsibleGroupName"), ("form", "FormName"), ("type", "TypeName"),
                    ("requestor", "RequestorName"), ("account", "AccountName"))

    def _apply_create(self, prepared):
        params = {**self.CREATE_FLAGS, "NotifyRequestor": "true" if prepared.action.notify_requestor else "false"}
        try:
            response = self.request("POST", self.base_url + f"/api/{self.app_id}/tickets", headers=self._headers,
                                    params=params, json=json.loads(prepared.payload_json),
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        if status in (200, 201):
            try:
                ticket = response.json()
                if (not isinstance(ticket, dict) or type(ticket.get("ID")) is not int or ticket["ID"] <= 0
                        or ticket.get("AppID") != self.app_id):
                    raise ValueError("Unrecognized ticket response.")
            except Exception:
                return WriteResult(outcome="unknown", status_code=status,
                                   message="The response did not confirm a created ticket; do not retry.")
            detail = {"ticket_id": ticket["ID"], **{key: ticket.get(wire) for key, wire in self.CREATED_KEYS}}
            return WriteResult(outcome="applied", message="TeamDynamix created the ticket.", status_code=status, detail=detail)
        outcome = "rejected" if 400 <= status < 500 and status != 408 else "unknown"
        return WriteResult(outcome=outcome, status_code=status,
                           message="TeamDynamix rejected the change." if outcome == "rejected" else "The upstream outcome is unknown; do not retry.")

    def _apply_asset(self, prepared):
        action, app = prepared.action, prepared.asset_app_id
        if app is None or app != self.asset_app_id:
            return WriteResult(outcome="rejected", message="Asset application binding does not match.")
        if isinstance(action, LinkAssetAction):
            method, path, body = "POST", f"/api/{self.app_id}/tickets/{action.ticket_id}/assets/{action.asset_id}", None
        elif isinstance(action, AssetCommentAction):
            method, path, body = "POST", f"/api/{app}/assets/{action.asset_id}/feed", json.loads(prepared.payload_json)
        else:
            method, path, body = "PATCH", f"/api/{app}/assets/{action.asset_id}", json.loads(prepared.payload_json)
        try:
            response = self.request(method, self.base_url + path, headers=self._headers, json=body,
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        if status == 204 and isinstance(action, LinkAssetAction):
            # Observed live: linking an asset that is already on the ticket returns 204 (the
            # spec documents only 200). The requested end state holds, so record it as applied
            # rather than leaving the ticket locked behind an unknown outcome.
            return WriteResult(outcome="applied", status_code=status,
                               message="The asset was already linked to the ticket.")
        if status in (200, 201):
            detail = None
            if isinstance(action, EditAssetAction):
                try:
                    asset = response.json()
                    if not isinstance(asset, dict) or asset.get("ID") != action.asset_id or asset.get("AppID") != app:
                        raise ValueError("Unrecognized asset response.")
                except Exception:
                    return WriteResult(outcome="unknown", status_code=status,
                                       message="The response did not confirm the target asset; do not retry.")
                detail = {"asset_id": asset["ID"], "status": asset.get("StatusName"), "tag": asset.get("Tag"), "name": asset.get("Name")}
            return WriteResult(outcome="applied", message="TeamDynamix accepted the change.", status_code=status, detail=detail)
        outcome = "rejected" if 400 <= status < 500 and status != 408 else "unknown"
        return WriteResult(outcome=outcome, status_code=status,
                           message="TeamDynamix rejected the change." if outcome == "rejected" else "The upstream outcome is unknown; do not retry.")

    def _apply_kb(self, prepared):
        action, app = prepared.action, prepared.portal_app_id
        if app is None or app != self.portal_app_id:
            return WriteResult(outcome="rejected", message="Client portal application binding does not match.")
        body = json.loads(prepared.payload_json)
        if isinstance(action, ArticleCreateAction):
            method, path = "POST", f"/api/{app}/knowledgebase"
        elif isinstance(action, ArticleEditAction):
            method, path = "PATCH", f"/api/{app}/knowledgebase/{action.article_id}"
        elif isinstance(action, (ArticleLinkAction, ArticleUnlinkAction)):
            method, body = ("POST" if isinstance(action, ArticleLinkAction) else "DELETE"), None
            if action.asset_id is not None:
                if prepared.asset_app_id is None or prepared.asset_app_id != self.asset_app_id:
                    return WriteResult(outcome="rejected", message="Asset application binding does not match.")
                path = f"/api/{prepared.asset_app_id}/assets/{action.asset_id}/articles/{action.article_id}"
            else:
                path = f"/api/{app}/knowledgebase/{action.article_id}/related/{action.related_article_id}"
        elif isinstance(action, CategoryCreateAction):
            method, path = "POST", f"/api/{app}/knowledgebase/categories"
        else:
            method, path = "PUT", f"/api/{app}/knowledgebase/categories/{action.category_id}"
        try:
            response = self.request(method, self.base_url + path, headers=self._headers, json=body,
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        if isinstance(action, (ArticleLinkAction, ArticleUnlinkAction)):
            if status == 200:
                return WriteResult(outcome="applied", message="TeamDynamix accepted the change.", status_code=status)
            if status == 204 and isinstance(action, ArticleLinkAction):
                return WriteResult(outcome="applied", message="The article was already linked.", status_code=status)
            if status == 404 and isinstance(action, ArticleUnlinkAction):
                return WriteResult(outcome="applied", message="The link did not exist.", status_code=status)
        elif status in (200, 201):
            try:
                record = response.json()
                if not isinstance(record, dict) or record.get("AppID") != app or type(record.get("ID")) is not int:
                    raise ValueError("Unrecognized response.")
                if isinstance(action, ArticleEditAction) and record["ID"] != action.article_id:
                    raise ValueError("Wrong article.")
                if isinstance(action, CategoryEditAction) and record["ID"] != action.category_id:
                    raise ValueError("Wrong category.")
            except Exception:
                return WriteResult(outcome="unknown", status_code=status,
                                   message="The response did not confirm the target record; do not retry.")
            if isinstance(action, (ArticleCreateAction, ArticleEditAction)):
                detail = {"article_id": record["ID"], "status": record.get("StatusName"), "is_published": record.get("IsPublished"),
                          "is_public": record.get("IsPublic"), "revision": record.get("RevisionNumber")}
                message = "TeamDynamix created the article." if status == 201 else "TeamDynamix accepted the change."
            else:
                detail = {"category_id": record["ID"], "name": record.get("Name"), "parent_id": record.get("ParentID")}
                message = "TeamDynamix created the category." if status == 201 else "TeamDynamix accepted the change."
            return WriteResult(outcome="applied", message=message, status_code=status, detail=detail)
        outcome = "rejected" if 400 <= status < 500 and status != 408 else "unknown"
        return WriteResult(outcome=outcome, status_code=status,
                           message="TeamDynamix rejected the change." if outcome == "rejected" else "The upstream outcome is unknown; do not retry.")

    def apply_once(self, prepared):
        if prepared.base_url != self.base_url or prepared.app_id != self.app_id:
            return WriteResult(outcome="rejected", message="Tenant or application binding does not match.")
        action = prepared.action
        if isinstance(action, CreateAction):
            return self._apply_create(prepared)
        if isinstance(action, AssetAction):
            return self._apply_asset(prepared)
        if isinstance(action, (ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction)):
            return self._apply_kb(prepared)
        path = f"/api/{self.app_id}/tickets/{action.ticket_id}"
        feed = isinstance(action, (CommentAction, TaskAction))
        if isinstance(action, TaskAction):
            path += f"/tasks/{action.task_id}/feed"
        else:
            path += "/feed" if feed else "?notifyNewResponsible=false"
        try:
            response = self.request("POST" if feed else "PATCH", self.base_url + path,
                                    headers=self._headers, json=json.loads(prepared.payload_json),
                                    timeout=(5, 30), allow_redirects=False)
        except Exception:
            return WriteResult(outcome="unknown", message="The upstream outcome is unknown; do not retry.")
        status = response.status_code
        # The published feed contract lists 200; Cedarville also returns 201
        # for a created feed entry (verified against persisted dispatch status
        # and authoritative feed read-back). Do not broaden PATCH or accept 202.
        if status == 200 or (feed and status == 201):
            if not feed:
                try:
                    ticket = response.json()
                    if not isinstance(ticket, dict) or ticket.get("ID") != action.ticket_id or ticket.get("AppID") != self.app_id:
                        raise ValueError("Unrecognized ticket response.")
                except Exception:
                    return WriteResult(outcome="unknown", message="The response did not confirm the target ticket; do not retry.", status_code=status)
            return WriteResult(outcome="applied", message="TeamDynamix accepted the change.", status_code=status)
        outcome = "rejected" if 400 <= status < 500 and status != 408 else "unknown"
        return WriteResult(outcome=outcome, message="TeamDynamix rejected the change." if outcome == "rejected" else "The upstream outcome is unknown; do not retry.", status_code=status)
