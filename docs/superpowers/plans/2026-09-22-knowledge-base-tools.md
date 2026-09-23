# Knowledge Base Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add knowledge base article and category tools (6 reads, 7 writes) to the hosted TeamDynamix connector, through the existing one-attempt, request-ID-deduplicated write pipeline.

**Architecture:** The Client Portal application (`AppClass == "TDClient"`) is discovered like the asset app. Read tools live in `plugin.py` beside the asset reads. Write actions are new pydantic models in `ticket_writes/models.py`; the adapter validates and applies them; the store's per-item locks already generalise by `(domain, id)`. HTML body handling (strip, sanitise, wrap) lives in one new module, `kb_text.py`, shared by reads and the adapter.

**Tech Stack:** Python 3.13/3.14, pydantic v2, FastMCP (`mcp` 1.30), `html.parser`, pytest, ruff.

**Spec:** `docs/superpowers/specs/2026-09-22-knowledge-base-tools-design.md`

## Global Constraints

- Run `.venv/bin/python3.14 -m pytest -q` and `.venv/bin/python3.14 -m ruff check src/ tests/` before every commit (the venv's `python` symlink is broken; `python3.14` works).
- No deletes: no article delete, no category delete. "Remove" = status `archived` + `is_published false`.
- Every write goes through `_submit`, takes `request_id`, and carries `annotations=prepare, meta=write_meta, structured_output=True`.
- Tool descriptions: one sentence of purpose plus must-know rules; keep them short (42 tools total after this work).
- Never print tokens, vault keys or credentials in tests or logs.
- Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Deploy only with `RAILWAY_TOKEN` from `.env`, after `railway status` names project `teamdynamix-connector`.

---

### Task 1: HTML body helpers module

**Files:**
- Create: `src/dynamix_manager/kb_text.py`
- Test: `tests/test_kb_text.py`

**Interfaces:**
- Produces: `html_to_text(html: str) -> str` (tags stripped, whitespace collapsed to single spaces, block tags become newlines, then newlines collapsed to one space for search snippets — see code), `truncate(text: str, limit: int) -> tuple[str, bool]`, `ensure_html(body: str) -> str` (wraps plain text paragraphs in `<p>`), `sanitize_html(html: str) -> tuple[str, bool]` (removes script/iframe/object/embed elements and `on*` attributes; second value is True when anything was removed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_kb_text.py
from dynamix_manager.kb_text import ensure_html, html_to_text, sanitize_html, truncate


def test_html_to_text_strips_tags_and_collapses_whitespace():
    html = "<p>Did you   change your <b>CedarNet</b>&nbsp;password?</p><ul><li>Yes</li><li>No</li></ul>"
    assert html_to_text(html) == "Did you change your CedarNet password? Yes No"


def test_html_to_text_drops_script_and_style_content():
    assert html_to_text("<style>p{}</style><p>Keep</p><script>alert(1)</script>") == "Keep"


def test_truncate_reports_whether_it_cut():
    assert truncate("short", 10) == ("short", False)
    assert truncate("a" * 12, 10) == ("a" * 10, True)


def test_ensure_html_wraps_plain_text_paragraphs_and_escapes():
    assert ensure_html("First line\n\nSecond <b>line") == "<p>First line</p><p>Second &lt;b&gt;line</p>"
    assert ensure_html("<p>Already html</p>") == "<p>Already html</p>"


def test_sanitize_html_removes_active_content_and_reports_it():
    dirty = '<p onclick="x()">Hi <script>evil()</script><iframe src="a"></iframe><a href="/x" onmouseover="y">link</a></p>'
    clean, changed = sanitize_html(dirty)
    assert changed is True
    assert clean == '<p>Hi <a href="/x">link</a></p>'
    assert sanitize_html("<p>Clean <em>text</em></p>") == ("<p>Clean <em>text</em></p>", False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_kb_text.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'dynamix_manager.kb_text'`

- [ ] **Step 3: Write the module**

```python
# src/dynamix_manager/kb_text.py
"""Knowledge base body handling: readable text for the model, safe HTML for the tenant."""
from html import escape
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
_HIDDEN = {"script", "style"}
_ACTIVE = {"script", "iframe", "object", "embed"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in _HIDDEN:
            self.hidden += 1
        if tag in _BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _HIDDEN:
            self.hidden = max(0, self.hidden - 1)
        if tag in _BLOCK:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def html_to_text(html):
    parser = _Text()
    parser.feed(str(html or ""))
    parser.close()
    return " ".join("".join(parser.parts).replace("\xa0", " ").split())


def truncate(text, limit):
    return (text[:limit], True) if len(text) > limit else (text, False)


def ensure_html(body):
    body = str(body or "")
    if "<" in body and ">" in body:
        return body
    paragraphs = [p.strip() for p in body.replace("\r\n", "\n").split("\n\n")]
    return "".join(f"<p>{escape(p)}</p>" for p in paragraphs if p)


class _Sanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out, self.skip, self.changed = [], 0, False

    def handle_starttag(self, tag, attrs):
        if tag in _ACTIVE:
            self.changed = True
            if tag not in _VOID:
                self.skip += 1
            return
        if self.skip:
            return
        kept = []
        for name, value in attrs:
            if name.lower().startswith("on"):
                self.changed = True
                continue
            kept.append(f' {name}="{escape(value, quote=True)}"' if value is not None else f" {name}")
        self.out.append(f"<{tag}{''.join(kept)}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in _ACTIVE:
            if tag not in _VOID:
                self.skip = max(0, self.skip - 1)
            return
        if not self.skip:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")


def sanitize_html(html):
    parser = _Sanitizer()
    parser.feed(str(html or ""))
    parser.close()
    return "".join(parser.out), parser.changed
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3.14 -m pytest tests/test_kb_text.py -q`
Expected: 5 passed. If `test_sanitize_html_removes_active_content_and_reports_it` fails on attribute quoting, adjust the expected string to match `escape(value, quote=True)` output only if the produced HTML is still well-formed; never loosen the `changed` assertions.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/kb_text.py tests/test_kb_text.py
git commit -m "Add knowledge base body helpers: text extraction, wrapping, sanitising

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Portal application discovery and URLs

**Files:**
- Modify: `src/dynamix_manager/plugin.py` (constants near line 49; `Connection._discover_application` ~153; add properties after `asset_app_id` ~191; `connection_status` ~317)
- Test: `tests/test_knowledge_base.py` (new; copy the `connection`, `server_for`, `call` helpers from `tests/test_assets.py`)

**Interfaces:**
- Produces: `PORTAL_APPLICATION_CLASS = "TDClient"`, `PORTAL_APPLICATION_NAME = "Client Portal"`, `Connection.portal_applications(applications)`, `Connection.portal_application`, `Connection.portal_app_id`, `Connection.article_url(article_id)`, `Connection.category_url(category_id)`. `connection_status` gains `portal_app_id`, `portal_app_name` (or `portal_warning`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_knowledge_base.py
import asyncio
import json
from unittest.mock import Mock

import pytest

import dynamix_manager.plugin as plugin
from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
APPS = [
    {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    {"AppID": 928, "Name": "InfoTech Assets/CIs", "AppClass": "TDAssets"},
    {"AppID": 2045, "Name": "Client Portal", "AppClass": "TDClient"},
]


def connection(apps=None):
    client = Mock()
    client.fetch_applications.return_value = apps if apps is not None else [dict(a) for a in APPS]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
    c = Connection(values=VALUES, client=client)
    c.routes, c.calls = {}, []

    def request(method, url, **kwargs):
        c.calls.append((method, url.removeprefix(c.base_url), kwargs))
        value = c.routes[(method, url.removeprefix(c.base_url))]
        if isinstance(value, Exception):
            raise value
        return Mock(json=lambda: json.loads(json.dumps(value)))

    client.session.get.side_effect = lambda url, **kw: request("GET", url, **kw)
    client.session.post.side_effect = lambda url, **kw: request("POST", url, **kw)
    return c


def server_for(c):
    return create_server(connection_provider=lambda: c)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_portal_application_is_discovered_by_class_and_reported():
    c = connection()
    assert c.portal_app_id == 2045 and c.portal_application["Name"] == "Client Portal"
    assert c.article_url(95821) == "https://example.test/TDClient/2045/Portal/KB/ArticleDet?ID=95821"
    assert c.category_url(9208) == "https://example.test/TDClient/2045/Portal/KB/?CategoryID=9208"
    status = call(server_for(c), "connection_status")
    assert status["portal_app_id"] == 2045 and status["portal_app_name"] == "Client Portal"


def test_missing_portal_application_fails_closed_but_status_still_answers():
    c = connection(apps=[dict(APPS[0]), dict(APPS[1])])
    with pytest.raises(RuntimeError, match="portal"):
        c.portal_app_id
    status = call(server_for(c), "connection_status")
    assert status["portal_app_id"] is None and "portal" in status["portal_warning"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_knowledge_base.py -q`
Expected: FAIL with `AttributeError: 'Connection' object has no attribute 'portal_app_id'`

- [ ] **Step 3: Implement discovery**

In `plugin.py` after `ASSET_METADATA_KINDS`:

```python
PORTAL_APPLICATION_CLASS = "TDClient"
PORTAL_APPLICATION_NAME = "Client Portal"
```

In `_discover_application`, replace the `else:` branch with a two-way branch:

```python
        elif kind == "assets":
            candidates = self.asset_applications(applications)
            if not candidates:
                raise RuntimeError("This account has no TeamDynamix asset application; asset tools are unavailable.")
            preferred = [a for a in candidates if a.get("Name") == ASSET_APPLICATION_NAME]
            matches = preferred or candidates
            label = "an asset application (" + ", ".join(str(a.get("Name")) for a in candidates) + ")"
        else:
            candidates = self.portal_applications(applications)
            if not candidates:
                raise RuntimeError("This account has no TeamDynamix client portal application; knowledge base tools are unavailable.")
            preferred = [a for a in candidates if a.get("Name") == PORTAL_APPLICATION_NAME]
            matches = preferred or candidates
            label = "a client portal application (" + ", ".join(str(a.get("Name")) for a in candidates) + ")"
```

After `asset_app_id`:

```python
    @staticmethod
    def portal_applications(applications):
        return [a for a in (applications or []) if isinstance(a, dict) and a.get("AppClass") == PORTAL_APPLICATION_CLASS]

    @property
    def portal_application(self):
        self.ready()
        return self._discover_application("portal")

    @property
    def portal_app_id(self):
        return int(self.portal_application["AppID"])

    def article_url(self, article_id):
        return self.base_url.rsplit("/", 1)[0] + f"/TDClient/{self.portal_app_id}/Portal/KB/ArticleDet?ID={int(article_id)}"

    def category_url(self, category_id):
        return self.base_url.rsplit("/", 1)[0] + f"/TDClient/{self.portal_app_id}/Portal/KB/?CategoryID={int(category_id)}"
```

In `connection_status`, after the asset block:

```python
        try:
            portal = c.portal_application
            result.update(portal_app_id=int(portal["AppID"]), portal_app_name=portal.get("Name"))
        except RuntimeError as error:
            result.update(portal_app_id=None, portal_app_name=None, portal_warning=str(error))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python3.14 -m pytest tests/test_knowledge_base.py tests/test_assets.py -q`
Expected: all pass (the asset discovery tests must still pass unchanged).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/plugin.py tests/test_knowledge_base.py
git commit -m "Discover the client portal application and report it in connection_status

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Read tools

**Files:**
- Modify: `src/dynamix_manager/plugin.py` (add after `asset_metadata`, before `survey_report`)
- Test: `tests/test_knowledge_base.py`

**Interfaces:**
- Consumes: `html_to_text`, `truncate` from Task 1; `portal_app_id`, `article_url`, `category_url` from Task 2; existing `resolve_people(c, wanted)` and `LIMIT`, `POSITIVE`, `PERSON`, `read` inside `create_server`.
- Produces: tools `search_articles`, `get_article`, `article_categories`, `related_articles`, `article_services`, `asset_articles`; module constants `ARTICLE_STATUSES = {"not_submitted": 1, "submitted": 2, "approved": 3, "rejected": 4, "archived": 5}`, `ARTICLE_KEYS`, `SNIPPET_LIMIT = 400`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_knowledge_base.py`:

```python
ARTICLE = {"ID": 95821, "AppID": 2045, "Subject": "Mac password doesn't match", "Summary": "Sync your Mac login",
           "Body": "<p>Did you change your <b>CedarNet</b> password?</p><script>x()</script>" + "<p>" + "x" * 500 + "</p>",
           "CategoryID": 9208, "CategoryName": "Office Devices", "Tags": ["macos", "password"], "Status": 5,
           "StatusName": "Archived", "DraftStatus": None, "IsPublished": False, "IsPublic": True,
           "RevisionNumber": 4, "ReviewDateUtc": "2027-02-01T00:00:00Z", "OwnerUid": "aaaaaaaa-0000-4000-8000-000000000001",
           "OwnerFullName": "David Mortenson", "OwningGroupID": None, "OwningGroupName": None,
           "ModifiedDate": "2026-09-01T00:00:00Z", "Attachments": [{"ID": "att-1", "Name": "screen.png", "Size": 1200}],
           "Attributes": [{"Name": "Audience", "ValueText": "Staff"}]}
CATEGORIES = [
    {"ID": 9208, "Name": "Office Devices", "ParentID": 0, "ParentName": None, "IsPublic": True, "Order": 1.0, "Subcategories": []},
    {"ID": 9106, "Name": "Software", "ParentID": 0, "ParentName": None, "IsPublic": True, "Order": 2.0, "Subcategories": [
        {"ID": 9107, "Name": "Adobe", "ParentID": 9106, "ParentName": "Software", "IsPublic": False, "Order": 1.0, "Subcategories": []}]},
]


def test_search_articles_maps_filters_and_returns_text_snippets():
    c = connection()
    c.routes[("POST", "/api/2045/knowledgebase/search")] = [ARTICLE]
    result = call(server_for(c), "search_articles", {"text": "password", "status": "archived", "is_public": True,
                                                     "category_id": 9208, "limit": 5})
    method, path, kwargs = c.calls[-1]
    assert kwargs["json"] == {"ReturnCount": 5, "SearchText": "password", "Status": 5, "IsPublic": True, "CategoryID": 9208}
    row = result["articles"][0]
    assert row["ID"] == 95821 and row["StatusName"] == "Archived" and row["Tags"] == ["macos", "password"]
    assert row["body_text"].startswith("Did you change your CedarNet password?") and "x()" not in row["body_text"]
    assert row["body_truncated"] is True and len(row["body_text"]) == 400
    assert row["url"] == "https://example.test/TDClient/2045/Portal/KB/ArticleDet?ID=95821"
    assert "Body" not in row and result["complete"] is True and result["resolved_people"] == []


def test_search_articles_resolves_the_author_and_reports_it():
    c = connection()
    c.routes[("GET", "/api/people/lookup")] = [{"UID": "aaaaaaaa-0000-4000-8000-000000000001", "FullName": "David Mortenson",
                                                "PrimaryEmail": "mortensond@example.test", "IsActive": True}]
    c.routes[("POST", "/api/2045/knowledgebase/search")] = []
    result = call(server_for(c), "search_articles", {"author": "David Mortenson"})
    assert c.calls[-1][2]["json"]["AuthorUID"] == "aaaaaaaa-0000-4000-8000-000000000001"
    assert result["resolved_people"][0]["matched"][0]["email"] == "mortensond@example.test"
    c.routes[("GET", "/api/people/lookup")] = []
    result = call(server_for(c), "search_articles", {"author": "Nobody Here"})
    assert result["articles"] == [] and "No article search was run" in result["warning"]


def test_get_article_returns_text_or_html_with_attachments():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/95821")] = ARTICLE
    server = server_for(c)
    text = call(server, "get_article", {"article_id": 95821})
    assert text["article"]["Subject"] == "Mac password doesn't match" and "Body" not in text["article"]
    assert text["body_text"].startswith("Did you change") and text["attachments"] == [{"ID": "att-1", "Name": "screen.png", "Size": 1200}]
    html = call(server, "get_article", {"article_id": 95821, "format": "html"})
    assert html["body_html"].startswith("<p>Did you change") and "body_text" not in html
    c.routes[("GET", "/api/2045/knowledgebase/1")] = {"ID": 2}
    with pytest.raises(Exception, match="could not be read"):
        call(server, "get_article", {"article_id": 1})


def test_article_categories_flattens_the_tree_with_depth_and_optional_subtree():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/categories")] = CATEGORIES
    server = server_for(c)
    result = call(server, "article_categories")
    assert [(r["ID"], r["depth"], r["ParentName"]) for r in result["categories"]] == [(9208, 0, None), (9106, 0, None), (9107, 1, "Software")]
    assert result["categories"][2]["url"] == "https://example.test/TDClient/2045/Portal/KB/?CategoryID=9107"
    sub = call(server, "article_categories", {"parent_id": 9106})
    assert [r["ID"] for r in sub["categories"]] == [9107]


def test_related_reads_use_their_endpoints():
    c = connection()
    c.routes[("GET", "/api/2045/knowledgebase/95821/related")] = [ARTICLE]
    c.routes[("GET", "/api/2045/knowledgebase/95821/relatedservices")] = [{"ID": 5, "Name": "Email", "IsActive": True, "Type": 1}]
    c.routes[("GET", "/api/928/assets/1973209/articles")] = [ARTICLE]
    server = server_for(c)
    assert call(server, "related_articles", {"article_id": 95821})["articles"][0]["ID"] == 95821
    assert call(server, "article_services", {"article_id": 95821})["services"] == [{"ID": 5, "Name": "Email", "IsActive": True, "Type": 1}]
    linked = call(server, "asset_articles", {"asset_id": 1973209})
    assert linked["articles"][0]["ID"] == 95821 and "Body" not in linked["articles"][0]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_knowledge_base.py -q`
Expected: the five new tests FAIL with "Unknown tool: search_articles" (or similar).

- [ ] **Step 3: Implement the read tools**

Add the import at the top of `plugin.py`: `from dynamix_manager.kb_text import html_to_text, truncate`. Add constants after `PORTAL_APPLICATION_NAME`:

```python
ARTICLE_STATUSES = {"not_submitted": 1, "submitted": 2, "approved": 3, "rejected": 4, "archived": 5}
ARTICLE_KEYS = ("ID", "Subject", "Summary", "StatusName", "IsPublished", "IsPublic", "CategoryID", "CategoryName",
                "Tags", "OwnerFullName", "OwningGroupName", "ModifiedDate", "RevisionNumber", "ReviewDateUtc")
SNIPPET_LIMIT = 400
```

Inside `create_server`, after `asset_metadata`:

```python
    def article_summary(c, article, *, snippet=True):
        row = {key: article.get(key) for key in ARTICLE_KEYS}
        row["url"] = c.article_url(article["ID"])
        if snippet:
            row["body_text"], row["body_truncated"] = truncate(html_to_text(article.get("Body")), SNIPPET_LIMIT)
        return row

    def article_rows(rows):
        return [r for r in rows if isinstance(r, dict) and type(r.get("ID")) is int] if isinstance(rows, list) else []

    @tool(annotations=read)
    def search_articles(
        text: Annotated[str, Field(max_length=500)] = "",
        category_id: POSITIVE | None = None,
        author: PERSON = None,
        author_uid: UUID | None = None,
        status: Literal["not_submitted", "submitted", "approved", "rejected", "archived"] | None = None,
        is_published: bool | None = None,
        is_public: bool | None = None,
        include_shortcuts: bool | None = None,
        limit: LIMIT = 25,
    ) -> dict[str, Any]:
        """Search knowledge base articles with the API's own filters; every filter runs server-side.

        Text search ranks archived articles with approved ones, so pass status="approved" and
        is_published=true for "what do we tell users" questions. `author` is resolved through the
        people API first (state who matched and continue; ambiguity runs no search). Bodies come
        back as plain-text snippets; use get_article for the full text.
        """
        c = conn()
        payload = {"ReturnCount": limit}
        resolved = []
        if author_uid is not None:
            payload["AuthorUID"] = str(author_uid)
        found, resolved, warnings = resolve_people(c, (("author", author),))
        if warnings:
            return {"articles": [], "returned": 0, "complete": True, "resolved_people": resolved,
                    "warning": " ".join(warnings) + " No article search was run."}
        if "author" in found:
            payload["AuthorUID"] = found["author"][0]
        for key, value in (("SearchText", text or None), ("Status", ARTICLE_STATUSES.get(status) if status else None),
                           ("IsPublished", is_published), ("IsPublic", is_public), ("CategoryID", category_id),
                           ("IncludeShortcuts", include_shortcuts)):
            if value is not None:
                payload[key] = value
        rows = article_rows(c.api_post(f"/api/{c.portal_app_id}/knowledgebase/search", payload))
        result = {"articles": [article_summary(c, a) for a in rows[:limit]], "returned": min(len(rows), limit),
                  "complete": len(rows) < limit, "resolved_people": resolved}
        if not result["complete"]:
            result["warning"] = f"Only the first {limit} matches are shown; narrow the filters or raise limit (max 100)."
        return result

    @tool(annotations=read)
    def get_article(article_id: POSITIVE, format: Literal["text", "html"] = "text") -> dict[str, Any]:
        """Read one knowledge base article: fields, tags, attachments, and the body as text (or raw HTML)."""
        c = conn()
        article = c.api_get(f"/api/{c.portal_app_id}/knowledgebase/{article_id}")
        if not isinstance(article, dict) or article.get("ID") != article_id:
            raise RuntimeError("The article could not be read.")
        body = article.get("Body")
        record = {k: v for k, v in article.items() if k not in ("Body", "Attachments", "Attributes")}
        result = {"article": record, "url": c.article_url(article_id),
                  "attachments": [{"ID": a.get("ID"), "Name": a.get("Name"), "Size": a.get("Size")}
                                  for a in (article.get("Attachments") or []) if isinstance(a, dict)],
                  "attributes": [{"Name": a.get("Name"), "Value": a.get("ValueText")}
                                 for a in (article.get("Attributes") or []) if isinstance(a, dict)]}
        if format == "html":
            result["body_html"] = body
        else:
            result["body_text"] = html_to_text(body)
        return result

    @tool(annotations=read)
    def article_categories(parent_id: POSITIVE | None = None) -> dict[str, Any]:
        """List knowledge base categories as a flattened tree (depth, parent), optionally under one parent."""
        c = conn()
        tree = c.api_get(f"/api/{c.portal_app_id}/knowledgebase/categories")
        rows = []

        def walk(nodes, depth):
            for node in nodes if isinstance(nodes, list) else []:
                if not isinstance(node, dict) or type(node.get("ID")) is not int:
                    continue
                rows.append({"ID": node["ID"], "Name": node.get("Name"), "ParentID": node.get("ParentID") or None,
                             "ParentName": node.get("ParentName"), "IsPublic": node.get("IsPublic"),
                             "Order": node.get("Order"), "depth": depth, "url": c.category_url(node["ID"])})
                walk(node.get("Subcategories"), depth + 1)

        def find(nodes, wanted):
            for node in nodes if isinstance(nodes, list) else []:
                if isinstance(node, dict) and node.get("ID") == wanted:
                    return node.get("Subcategories") or []
                inner = find(node.get("Subcategories") if isinstance(node, dict) else None, wanted)
                if inner is not None:
                    return inner
            return None

        if parent_id is None:
            walk(tree, 0)
        else:
            subtree = find(tree, parent_id)
            if subtree is None:
                raise RuntimeError("The parent category was not found.")
            walk(subtree, 1)
        return {"categories": rows, "returned": len(rows), "complete": True}

    @tool(annotations=read)
    def related_articles(article_id: POSITIVE) -> dict[str, Any]:
        """List knowledge base articles related to an article (no bodies)."""
        c = conn()
        rows = article_rows(c.api_get(f"/api/{c.portal_app_id}/knowledgebase/{article_id}/related"))
        return {"article_id": article_id, "articles": [article_summary(c, a, snippet=False) for a in rows],
                "returned": len(rows), "complete": True}

    @tool(annotations=read)
    def article_services(article_id: POSITIVE) -> dict[str, Any]:
        """List the services and offerings related to a knowledge base article."""
        c = conn()
        rows = c.api_get(f"/api/{c.portal_app_id}/knowledgebase/{article_id}/relatedservices")
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        return {"article_id": article_id, "services": rows, "returned": len(rows), "complete": True}

    @tool(annotations=read)
    def asset_articles(asset_id: POSITIVE) -> dict[str, Any]:
        """List knowledge base articles linked to an asset (no bodies)."""
        c = conn()
        rows = article_rows(c.api_get(f"/api/{c.asset_app_id}/assets/{asset_id}/articles"))
        return {"asset_id": asset_id, "articles": [article_summary(c, a, snippet=False) for a in rows],
                "returned": len(rows), "complete": True}
```

Also add `search_articles`, `get_article`, `article_categories`, `related_articles`, `article_services`, `asset_articles` to the registered-tool set in `tests/test_ticket_write_tools.py::test_registers_only_four_direct_tools_and_two_bounded_read_tools` and bump `assert len(tools) == 29` to `35` there and in `tests/test_hosted_connector.py` (line ~489); the external-mode read count in `tests/test_hosted_connector.py` (`== 15`, line ~346) becomes `21`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python3.14 -m pytest -q`
Expected: all pass. If `test_search_articles_resolves_the_author_and_reports_it` fails because `resolve_people` is defined after the tool, move the new tools below `resolve_people` (it is defined at ~line 490, before `search_assets`).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/plugin.py tests/test_knowledge_base.py tests/test_ticket_write_tools.py tests/test_hosted_connector.py
git commit -m "Add knowledge base read tools: search, article, categories, related, services, asset links

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Article and category action models

**Files:**
- Modify: `src/dynamix_manager/ticket_writes/models.py` (after `EditAssetAction`, before `Action = ...`; extend `Action`; `PreparedChange` gains `portal_app_id`)
- Test: `tests/test_ticket_write_models.py`

**Interfaces:**
- Produces: `ArticleAction` (base: `article_id: PositiveID`, `item == ("article", id)`, `ticket_id == 0`, explicit-field serializer), `ArticleEditAction(kind="article_edit")`, `ArticleLinkAction(kind="article_link", asset_id | related_article_id)`, `ArticleUnlinkAction(kind="article_unlink", same)`, `ArticleCreateAction(kind="article_create", subject, body, category_id, summary, tags, owner_uid, owning_group_id, review_date, is_public=False, is_published=False, status="not_submitted", notify_owner, notify_owner_of_review_date, order; item ("create", None), ticket_id 0)`, `CategoryAction` (base: `category_id`, item `("category", id)`), `CategoryEditAction(kind="category_edit", name, description, parent_id, order, is_public)`, `CategoryCreateAction(kind="category_create", name, description, parent_id, order, is_public=False, inherit_permissions=False; item ("create", None))`. `ARTICLE_STATUS = Literal["not_submitted", "submitted", "approved", "rejected", "archived"]`. `PreparedChange.portal_app_id: PositiveID | None = None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticket_write_models.py`:

```python
def test_article_actions_are_typed_bounded_and_carry_an_item_identity():
    from dynamix_manager.ticket_writes.models import (ArticleCreateAction, ArticleEditAction, ArticleLinkAction,
                                                       ArticleUnlinkAction, CategoryCreateAction, CategoryEditAction)
    create = parse(dict(kind="article_create", subject="Reset MFA", body="Step one", category_id=9212))
    assert isinstance(create, ArticleCreateAction) and create.item == ("create", None) and create.ticket_id == 0
    assert create.status == "not_submitted" and create.is_published is False and create.is_public is False
    assert create.model_dump(mode="json") == {"kind": "article_create", "subject": "Reset MFA", "body": "Step one", "category_id": 9212}
    edit = parse(dict(kind="article_edit", article_id=95821, status="archived", is_published=False, tags=["macos"]))
    assert isinstance(edit, ArticleEditAction) and edit.item == ("article", 95821) and edit.ticket_id == 0
    assert edit.model_dump(mode="json") == {"kind": "article_edit", "article_id": 95821, "status": "archived", "is_published": False, "tags": ["macos"]}
    link = parse(dict(kind="article_link", article_id=95821, asset_id=1973209))
    assert isinstance(link, ArticleLinkAction) and link.item == ("article", 95821)
    unlink = parse(dict(kind="article_unlink", article_id=95821, related_article_id=84764))
    assert isinstance(unlink, ArticleUnlinkAction) and unlink.related_article_id == 84764
    category = parse(dict(kind="category_create", name="Scratch", parent_id=10548))
    assert isinstance(category, CategoryCreateAction) and category.item == ("create", None) and category.is_public is False
    cat_edit = parse(dict(kind="category_edit", category_id=9107, name="Adobe apps"))
    assert isinstance(cat_edit, CategoryEditAction) and cat_edit.item == ("category", 9107) and cat_edit.ticket_id == 0
    for bad in (dict(kind="article_create", subject=" ", body="x", category_id=1),
                dict(kind="article_create", subject="s", body="", category_id=1),
                dict(kind="article_create", subject="s", body="x", category_id=1, status="published"),
                dict(kind="article_create", subject="s", body="x", category_id=1, tags=["a" * 101]),
                dict(kind="article_edit", article_id=95821),
                dict(kind="article_edit", article_id=95821, review_date="soon"),
                dict(kind="article_link", article_id=95821),
                dict(kind="article_link", article_id=95821, asset_id=1, related_article_id=2),
                dict(kind="article_link", article_id=95821, related_article_id=95821),
                dict(kind="category_edit", category_id=9107),
                dict(kind="category_create", name="  ")):
        with pytest.raises(ValidationError):
            parse(bad)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_models.py -q -k article`
Expected: FAIL with `ImportError` on `ArticleCreateAction`.

- [ ] **Step 3: Implement the models**

Insert before `Action = Annotated[...]` in `models.py`:

```python
ARTICLE_STATUS = Literal["not_submitted", "submitted", "approved", "rejected", "archived"]
ARTICLE_STATUS_IDS = {"not_submitted": 1, "submitted": 2, "approved": 3, "rejected": 4, "archived": 5}
ArticleTitle = Annotated[str, Field(strict=True, min_length=1, max_length=300)]
ArticleBody = Annotated[str, Field(strict=True, min_length=1, max_length=200000)]
Tags = Annotated[tuple[Annotated[str, Field(strict=True, min_length=1, max_length=100)], ...], Field(max_length=50)]
IsoDate = Annotated[str, Field(strict=True, min_length=10, max_length=35), AfterValidator(_iso_date)]


def _nonblank(value):
    if value is not None and not value.strip():
        raise ValueError("Text fields must contain text.")
    return value


class _NoTicket:
    @property
    def ticket_id(self):
        return 0

    @model_serializer(mode="wrap")
    def serialize_explicit(self, handler):
        return {key: value for key, value in handler(self).items() if key in self.model_fields_set}


class ArticleAction(_NoTicket, ImmutableModel):
    """Base for actions on an existing knowledge base article."""

    article_id: PositiveID

    @property
    def item(self):
        return ("article", self.article_id)


class _ArticleFields(ImmutableModel):
    subject: ArticleTitle | None = None
    summary: Annotated[str, Field(strict=True, max_length=2000)] | None = None
    body: ArticleBody | None = None
    tags: Tags | None = None
    category_id: PositiveID | None = None
    owner_uid: UUID | None = None
    owning_group_id: PositiveID | None = None
    review_date: IsoDate | None = None
    is_public: Annotated[bool, Field(strict=True)] | None = None
    is_published: Annotated[bool, Field(strict=True)] | None = None
    status: ARTICLE_STATUS | None = None
    notify_owner: Annotated[bool, Field(strict=True)] | None = None
    notify_owner_of_review_date: Annotated[bool, Field(strict=True)] | None = None
    order: Annotated[float, Field(strict=True, ge=0)] | None = None

    @field_validator("subject", "body", "summary")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)


class ArticleEditAction(ArticleAction, _ArticleFields):
    kind: Literal["article_edit"]
    EDITABLE: ClassVar = ("subject", "summary", "body", "tags", "category_id", "owner_uid", "owning_group_id", "review_date",
                          "is_public", "is_published", "status", "notify_owner", "notify_owner_of_review_date", "order")

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not (self.model_fields_set & set(self.EDITABLE)):
            raise ValueError("Select at least one article field to change.")
        return self


class _ArticleTarget(ArticleAction):
    asset_id: PositiveID | None = None
    related_article_id: PositiveID | None = None

    @model_validator(mode="after")
    def exactly_one_target(self):
        if (self.asset_id is None) == (self.related_article_id is None):
            raise ValueError("Give exactly one of asset_id or related_article_id.")
        if self.related_article_id == self.article_id:
            raise ValueError("An article cannot be related to itself.")
        return self


class ArticleLinkAction(_ArticleTarget):
    kind: Literal["article_link"]


class ArticleUnlinkAction(_ArticleTarget):
    kind: Literal["article_unlink"]


class ArticleCreateAction(_NoTicket, _ArticleFields):
    kind: Literal["article_create"]
    subject: ArticleTitle
    body: ArticleBody
    category_id: PositiveID
    is_public: Annotated[bool, Field(strict=True)] = False
    is_published: Annotated[bool, Field(strict=True)] = False
    status: ARTICLE_STATUS = "not_submitted"

    @property
    def item(self):
        return ("create", None)


class CategoryAction(_NoTicket, ImmutableModel):
    category_id: PositiveID

    @property
    def item(self):
        return ("category", self.category_id)


class _CategoryFields(ImmutableModel):
    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(strict=True, max_length=2000)] | None = None
    parent_id: Annotated[int, Field(strict=True, ge=0)] | None = None  # 0 = top level
    order: Annotated[float, Field(strict=True, ge=0)] | None = None
    is_public: Annotated[bool, Field(strict=True)] | None = None

    @field_validator("name")
    @classmethod
    def nonblank(cls, value):
        return _nonblank(value)


class CategoryEditAction(CategoryAction, _CategoryFields):
    kind: Literal["category_edit"]
    EDITABLE: ClassVar = ("name", "description", "parent_id", "order", "is_public")

    @model_validator(mode="after")
    def at_least_one_field(self):
        if not (self.model_fields_set & set(self.EDITABLE)):
            raise ValueError("Select at least one category field to change.")
        return self


class CategoryCreateAction(_NoTicket, _CategoryFields):
    kind: Literal["category_create"]
    name: Annotated[str, Field(strict=True, min_length=1, max_length=200)]
    is_public: Annotated[bool, Field(strict=True)] = False
    inherit_permissions: Annotated[bool, Field(strict=True)] = False

    @property
    def item(self):
        return ("create", None)
```

Extend the union and `PreparedChange`:

```python
Action = Annotated[CommentAction | StatusAction | AssignAction | EditAction | TaskAction | CreateAction
                   | AssetCommentAction | LinkAssetAction | EditAssetAction
                   | ArticleCreateAction | ArticleEditAction | ArticleLinkAction | ArticleUnlinkAction
                   | CategoryCreateAction | CategoryEditAction,
                   Field(discriminator="kind")]
```

```python
class PreparedChange(ImmutableModel):
    action: Action
    base_url: str
    app_id: PositiveID
    asset_app_id: PositiveID | None = None
    portal_app_id: PositiveID | None = None
    ...
```

The date validator is `_iso_date` (models.py line 31), already used by `EditAssetAction.expected_replacement_date`. `ClassVar`, `model_serializer`, `model_validator`, `field_validator` are already imported.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_models.py tests/test_ticket_write_store.py -q`
Expected: all pass. If pydantic complains about the mixin order (`_NoTicket` before `ImmutableModel`), make `_NoTicket` inherit from `ImmutableModel` instead and drop the second base.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/ticket_writes/models.py tests/test_ticket_write_models.py
git commit -m "Add knowledge base article and category write actions

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Adapter validation for article and category actions

**Files:**
- Modify: `src/dynamix_manager/ticket_writes/adapter.py` (`__init__` ~22, `from_connection` ~43, `validate` ~510, `_notices` ~541; new methods after `_validate_asset_edit`)
- Test: `tests/test_ticket_write_adapter.py`

**Interfaces:**
- Consumes: models from Task 4; `ensure_html`, `sanitize_html` from Task 1.
- Produces: `WriteAdapter(..., portal_app_id=None)`; `WriteAdapter.from_connection` fills `portal_app_id` from `connection.portal_app_id` (None on failure); `_validate_article(action)` and `_validate_category(action)` returning `PreparedChange` with `portal_app_id` set and preview `application="Knowledge Base"`; article baseline JSON `{"ModifiedDate", "RevisionNumber", ...}`; payloads: create → `Article` dict, edit → JSON Patch list, link/unlink → `{}`, category create → `ArticleCategory` dict, category edit → full category dict for PUT.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticket_write_adapter.py`:

```python
PORTAL_APP = 2045
ARTICLE_REC = dict(ID=95821, AppID=PORTAL_APP, Subject="Mac password", Summary="Sync", Body="<p>Old</p>", CategoryID=9208,
                   CategoryName="Office Devices", Tags=["macos"], Status=3, StatusName="Approved", IsPublished=True, IsPublic=False,
                   RevisionNumber=4, ReviewDateUtc="2027-02-01T00:00:00Z", OwnerUid=UID, OwnerFullName="Person",
                   OwningGroupID=None, ModifiedDate="art-v1")
CATEGORY_REC = dict(ID=9208, AppID=PORTAL_APP, Name="Office Devices", Description="Desk gear", ParentID=0, Order=1.0,
                    IsPublic=True, InheritPermissions=False, WhitelistGroups=False, ModifiedDate="cat-v1")
CATEGORY_TREE = [dict(CATEGORY_REC, Subcategories=[]),
                 dict(ID=10548, AppID=PORTAL_APP, Name="Tech FAQ", ParentID=0, Order=2.0, IsPublic=True, ModifiedDate="cat-v2", Subcategories=[])]
ARTICLE_ROUTES = {
    f"/api/{PORTAL_APP}/knowledgebase/95821": ARTICLE_REC,
    f"/api/{PORTAL_APP}/knowledgebase/84764": dict(ARTICLE_REC, ID=84764, Subject="Password reset"),
    f"/api/{PORTAL_APP}/knowledgebase/categories": CATEGORY_TREE,
    f"/api/{PORTAL_APP}/knowledgebase/categories/9208": CATEGORY_REC,
    f"/api/{PORTAL_APP}/knowledgebase/categories/10548": CATEGORY_TREE[1],
    f"/api/{ASSET_APP}/assets/1973209": ASSET_REC,
    f"/api/people/{UID}": dict(UID=UID, IsActive=True, FullName="Person", OrgApplications=[dict(ID=42, IsActive=True)]),
    "/api/groups/77": dict(ID=77, IsActive=True, Name="Service Desk"),
}


def kb_adapter(**overrides):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    records = {**ARTICLE_ROUTES, **overrides}
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: ARTICLE_REC.copy())

    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", request=request,
                           read=lambda path: records[path], asset_app_id=ASSET_APP, portal_app_id=PORTAL_APP)
    return adapter, calls, records


def test_article_create_builds_a_sanitised_draft_payload():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(
        kind="article_create", subject="Reset MFA", body="Step one\n\nStep <two>", category_id=9208,
        tags=["mfa", "auth"], owner_uid=UID, owning_group_id=77, summary="How to reset")))
    payload = json.loads(prepared.payload_json)
    assert payload == dict(Subject="Reset MFA", Body="<p>Step one</p><p>Step &lt;two&gt;</p>", CategoryID=9208, Summary="How to reset",
                           Tags=["mfa", "auth"], OwnerUid=UID, OwningGroupID=77, Status=1, IsPublished=False, IsPublic=False)
    assert prepared.portal_app_id == PORTAL_APP and prepared.preview.application == "Knowledge Base"
    assert prepared.preview.ticket_id == 0 and prepared.preview.action == "article_create"
    fields = {f.name: f.after for f in prepared.preview.fields}
    assert fields["Category"] == "Office Devices" and fields["Owner"] == "Person" and fields["Owning group"] == "Service Desk"
    assert fields["Status"] == "Not Submitted" and fields["Published"] == "False"


def test_article_create_strips_scripts_and_says_so():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_create", subject="S", category_id=9208,
                                                  body='<p onclick="x()">Hi</p><script>evil()</script>')))
    assert json.loads(prepared.payload_json)["Body"] == "<p>Hi</p>"
    assert any("script or frame markup was removed" in n for n in prepared.preview.notices)
    with pytest.raises(ValueError, match="category"):
        adapter.validate(parse_action(dict(kind="article_create", subject="S", body="b", category_id=999)))


def test_article_edit_builds_a_patch_with_baseline_and_publication_notices():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, status="archived",
                                                  is_published=False, is_public=True, tags=["macos", "password"], body="New text")))
    assert json.loads(prepared.payload_json) == [
        dict(op="replace", path="/Body", value="<p>New text</p>"), dict(op="replace", path="/Tags", value=["macos", "password"]),
        dict(op="replace", path="/IsPublic", value=True), dict(op="replace", path="/IsPublished", value=False),
        dict(op="replace", path="/Status", value=5)]
    baseline = json.loads(prepared.baseline_json)
    assert baseline["ModifiedDate"] == "art-v1" and baseline["RevisionNumber"] == 4
    fields = {f.name: (f.before, f.after) for f in prepared.preview.fields}
    assert fields["Status"] == ("Approved", "Archived") and fields["Published"] == ("True", "False")
    notices = " ".join(prepared.preview.notices)
    assert "archives the article" in notices and "visible without signing in" in notices and "publishes" not in notices
    assert prepared.preview.ticket_title == "Mac password"


def test_article_edit_verifies_category_owner_and_group():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, category_id=9208, owner_uid=UID, owning_group_id=77)))
    fields = {f.name: (f.before, f.after) for f in prepared.preview.fields}
    assert fields["Category"] == ("Office Devices", "Office Devices") and fields["Owner"] == ("Person", "Person")
    assert fields["Owning group"] == (None, "Service Desk")
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, owning_group_id=78)))
    with pytest.raises(ValueError, match="article"):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=1, subject="x")))


def test_article_link_and_unlink_verify_both_ends():
    adapter, _, _ = kb_adapter()
    to_asset = adapter.validate(parse_action(dict(kind="article_link", article_id=95821, asset_id=1973209)))
    assert json.loads(to_asset.baseline_json)["target"]["ID"] == 1973209 and to_asset.preview.action == "article_link"
    assert {f.name: f.after for f in to_asset.preview.fields} == {"Asset": "Micah Cooper MacBook"}
    to_article = adapter.validate(parse_action(dict(kind="article_unlink", article_id=95821, related_article_id=84764)))
    assert {f.name: f.before for f in to_article.preview.fields} == {"Related article": "Password reset"}
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="article_link", article_id=95821, related_article_id=1)))


def test_category_create_and_edit_payloads():
    adapter, _, _ = kb_adapter()
    created = adapter.validate(parse_action(dict(kind="category_create", name="Scratch", parent_id=10548, description="Test")))
    assert json.loads(created.payload_json) == dict(Name="Scratch", ParentID=10548, Description="Test", IsPublic=False, InheritPermissions=False)
    assert {f.name: f.after for f in created.preview.fields}["Parent"] == "Tech FAQ"
    edited = adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, name="Office devices", parent_id=10548)))
    payload = json.loads(edited.payload_json)
    assert payload["Name"] == "Office devices" and payload["ParentID"] == 10548 and payload["Description"] == "Desk gear"
    assert payload["ID"] == 9208 and "Subcategories" not in payload and "ModifiedDate" not in payload
    assert json.loads(edited.baseline_json)["ModifiedDate"] == "cat-v1"
    assert edited.preview.ticket_title == "Office Devices" and edited.preview.action == "category_edit"
    with pytest.raises(ValueError, match="parent"):
        adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, parent_id=9208)))


def test_article_actions_need_a_portal_application():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", read=lambda path: ARTICLE_ROUTES[path])
    with pytest.raises(ValueError, match="portal"):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, subject="x")))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_adapter.py -q -k "article or category or portal"`
Expected: FAIL with `TypeError: WriteAdapter.__init__() got an unexpected keyword argument 'portal_app_id'`.

- [ ] **Step 3: Implement validation**

`__init__`: add `portal_app_id=None` parameter, validate like `asset_app_id`, store `self.portal_app_id`. `from_connection`: mirror the asset block:

```python
        try:
            portal_app_id = connection.portal_app_id
        except Exception:
            portal_app_id = None
        return cls(connection.base_url, connection.app_id, connection.token,
                   header_app_id=connection.header_app_id, asset_app_id=asset_app_id, portal_app_id=portal_app_id)
```

Imports: add `ArticleAction, ArticleCreateAction, ArticleEditAction, ArticleLinkAction, ArticleUnlinkAction, CategoryAction, CategoryCreateAction, CategoryEditAction, ARTICLE_STATUS_IDS` to the `.models` import and `from dynamix_manager.kb_text import ensure_html, sanitize_html`.

`validate`: before the `AssetAction` branch add

```python
        if isinstance(action, (ArticleAction, ArticleCreateAction)):
            return self._validate_article(action)
        if isinstance(action, (CategoryAction, CategoryCreateAction)):
            return self._validate_category(action)
```

New methods (after `_validate_asset_edit`):

```python
    _ARTICLE_STATUS_NAMES = {1: "Not Submitted", 2: "Submitted", 3: "Approved", 4: "Rejected", 5: "Archived"}

    def _require_portal_app(self):
        if self.portal_app_id is None:
            raise ValueError("No client portal application is available for this account.")
        return self.portal_app_id

    def _article_snapshot(self, article_id):
        app = self._require_portal_app()
        article = self._get(f"/api/{app}/knowledgebase/{article_id}")
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
        if not isinstance(user, dict) or str(user.get("UID", "")).lower() != value.lower() or user.get("IsActive") is not True or not user.get("FullName"):
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

    # (name, wire field, preview label, baseline label field or None)
    _ARTICLE_FIELDS = (("subject", "Subject", "Subject", None), ("summary", "Summary", "Summary", None),
                       ("body", "Body", "Body", None), ("tags", "Tags", "Tags", None),
                       ("category_id", "CategoryID", "Category", "CategoryName"), ("owner_uid", "OwnerUid", "Owner", "OwnerFullName"),
                       ("owning_group_id", "OwningGroupID", "Owning group", "OwningGroupName"),
                       ("review_date", "ReviewDateUtc", "Review date", None), ("is_public", "IsPublic", "Public", None),
                       ("is_published", "IsPublished", "Published", None), ("status", "Status", "Status", "StatusName"),
                       ("notify_owner", "NotifyOwner", "Notify owner", None),
                       ("notify_owner_of_review_date", "NotifyOwnerOfReviewDate", "Notify owner of review date", None),
                       ("order", "Order", "Order", None))

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
            if action.asset_id is not None:
                target = self._asset_snapshot(action.asset_id)
                field = PreviewField(name="Asset", before=target["Name"] if isinstance(action, ArticleUnlinkAction) else None,
                                     after=None if isinstance(action, ArticleUnlinkAction) else target["Name"])
            else:
                target = self._article_snapshot(action.related_article_id)
                field = PreviewField(name="Related article", before=target["Subject"] if isinstance(action, ArticleUnlinkAction) else None,
                                     after=None if isinstance(action, ArticleUnlinkAction) else target["Subject"])
            return self._kb_prepared(action, {"article": article, "target": target}, {}, [field], title=article["Subject"])
        payload, fields = [], []
        for name, wire, label, before_key in self._ARTICLE_FIELDS:
            if name not in action.model_fields_set:
                continue
            value = getattr(action, name)
            wire_value, shown = self._article_value(name, value, notices)
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
            fields = [PreviewField(name="Name", before=None, after=action.name), PreviewField(name="Public", before=None, after=str(action.is_public))]
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
        payload = {k: v for k, v in category.items() if k not in ("Subcategories", "ModifiedDate", "CreatedDate", "CreatedUid",
                                                                  "CreatedFullName", "ModifiedUid", "ModifiedFullName", "AppName", "ParentName")}
        fields = []
        for name, wire, label in (("name", "Name", "Name"), ("description", "Description", "Description"), ("parent_id", "ParentID", "Parent"),
                                  ("order", "Order", "Order"), ("is_public", "IsPublic", "Public")):
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
        return self._kb_prepared(action, {"ID": category["ID"], "ModifiedDate": category["ModifiedDate"]}, payload, fields, title=category["Name"])
```

`_notices`: add before the final `AssetAction` branch

```python
        if isinstance(action, (ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction)):
            return common + ("Knowledge base changes are visible to everyone the portal shows the article to; there is no private mode.",)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_adapter.py -q`
Expected: all pass, including the pre-existing asset tests.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/ticket_writes/adapter.py tests/test_ticket_write_adapter.py
git commit -m "Validate knowledge base article and category actions with previews and baselines

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Adapter apply for article and category actions

**Files:**
- Modify: `src/dynamix_manager/ticket_writes/adapter.py` (`apply_once` ~651; new `_apply_kb` after `_apply_asset`)
- Test: `tests/test_ticket_write_adapter.py`

**Interfaces:**
- Produces: `_apply_kb(prepared) -> WriteResult`. Endpoints: create `POST /api/{portal}/knowledgebase`; edit `PATCH /api/{portal}/knowledgebase/{id}`; link/unlink to asset `POST|DELETE /api/{asset}/assets/{assetId}/articles/{articleId}`; link/unlink to article `POST|DELETE /api/{portal}/knowledgebase/{id}/related/{relatedId}`; category create `POST /api/{portal}/knowledgebase/categories`; category edit `PUT /api/{portal}/knowledgebase/categories/{id}`. `detail` for article create/edit: `{"article_id", "status", "is_published", "is_public", "revision"}`; for category create/edit: `{"category_id", "name", "parent_id"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_article_create_and_edit_apply_and_confirm_the_article():
    adapter, calls, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_create", subject="Reset MFA", body="x", category_id=9208)))
    created = dict(ARTICLE_REC, ID=170001, Subject="Reset MFA", Status=1, StatusName="Not Submitted", IsPublished=False, RevisionNumber=1)
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=201, json=lambda: created))
    result = adapter.apply_once(prepared)
    assert result.outcome == "applied" and result.status_code == 201
    assert result.detail == {"article_id": 170001, "status": "Not Submitted", "is_published": False, "is_public": False, "revision": 1}
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase")
    assert calls[-1][1]["json"]["Subject"] == "Reset MFA"
    edit = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, subject="Renamed")))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: dict(ARTICLE_REC, Subject="Renamed")))
    result = adapter.apply_once(edit)
    assert result.outcome == "applied" and result.detail["article_id"] == 95821 and result.detail["revision"] == 4
    assert calls[-1][0] == ("PATCH", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/95821")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: dict(ARTICLE_REC, ID=1))
    assert adapter.apply_once(edit).outcome == "unknown"
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=403, json=lambda: {})
    assert adapter.apply_once(edit).outcome == "rejected"


def test_article_links_use_the_right_endpoint_and_idempotent_statuses():
    adapter, calls, _ = kb_adapter()
    to_asset = adapter.validate(parse_action(dict(kind="article_link", article_id=95821, asset_id=1973209)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {"Message": "ok"}))
    assert adapter.apply_once(to_asset).outcome == "applied"
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{ASSET_APP}/assets/1973209/articles/95821")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=204)
    assert adapter.apply_once(to_asset).message == "The article was already linked."
    unlink = adapter.validate(parse_action(dict(kind="article_unlink", article_id=95821, related_article_id=84764)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {"Message": "ok"}))
    assert adapter.apply_once(unlink).outcome == "applied"
    assert calls[-1][0] == ("DELETE", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/95821/related/84764")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=404)
    result = adapter.apply_once(unlink)
    assert result.outcome == "applied" and result.message == "The link did not exist."


def test_category_create_and_edit_apply():
    adapter, calls, _ = kb_adapter()
    created = adapter.validate(parse_action(dict(kind="category_create", name="Scratch", parent_id=10548)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=201, json=lambda: dict(CATEGORY_REC, ID=30001, Name="Scratch", ParentID=10548)))
    result = adapter.apply_once(created)
    assert result.outcome == "applied" and result.detail == {"category_id": 30001, "name": "Scratch", "parent_id": 10548}
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/categories")
    edited = adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, name="Office devices")))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: dict(CATEGORY_REC, Name="Office devices")))
    result = adapter.apply_once(edited)
    assert result.outcome == "applied" and result.detail["name"] == "Office devices"
    assert calls[-1][0] == ("PUT", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/categories/9208")
    assert calls[-1][1]["json"]["ID"] == 9208
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_adapter.py -q -k "apply or links_use"`
Expected: FAIL (apply_once falls through to the ticket path and hits `action.ticket_id` = 0, or asserts on `calls`).

- [ ] **Step 3: Implement apply**

In `apply_once`, after the `AssetAction` branch:

```python
        if isinstance(action, (ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction)):
            return self._apply_kb(prepared)
```

New method:

```python
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
            method = "POST" if isinstance(action, ArticleLinkAction) else "DELETE"
            body = None
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
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python3.14 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/ticket_writes/adapter.py tests/test_ticket_write_adapter.py
git commit -m "Apply knowledge base article and category actions with verified responses

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Service and store wiring

**Files:**
- Modify: `src/dynamix_manager/ticket_writes/service.py` (`_ACTION_TYPES` ~67; `_item` ~395; `_ticket_identity` ~341)
- Test: `tests/test_ticket_write_service.py`, `tests/test_ticket_write_store.py`

**Interfaces:**
- Produces: `TicketWriteService.submit` accepts article/category actions; `TicketWriteStatus.item` = `{"type": "article", "id", "url": "https://<host>/TDClient/<portal>/Portal/KB/ArticleDet?ID=<id>"}` or `{"type": "category", ...KB/?CategoryID=}`; for creations `item` comes from `result.detail` (`article_id` / `category_id`). Ticket ID in status is 0 for all KB actions.

- [ ] **Step 1: Write the failing tests**

Store (append to `tests/test_ticket_write_store.py`):

```python
def test_article_and_category_items_lock_independently(store_setup):
    store, _, _, _ = store_setup
    article = prepared_for(dict(kind="article_edit", article_id=95821, subject="A"))
    other_article = prepared_for(dict(kind="article_edit", article_id=84764, subject="B"))
    category = prepared_for(dict(kind="category_edit", category_id=95821, name="C"))
    first, fence = submit(store, article, "one")
    assert fence.claimed
    assert submit(store, other_article, "two")[1].claimed
    assert submit(store, category, "three")[1].claimed  # same numeric ID, different domain
    blocked = store.prepare_direct(binding(), prepared_for(dict(kind="article_edit", article_id=95821, tags=["x"])), "four")
    assert not store.claim_direct(blocked.operation_id, binding()).claimed
```

Add the helper near `prepared()` in that file:

```python
def prepared_for(action, *, payload=None, baseline="v1"):
    parsed = parse_action(action)
    return PreparedChange(action=parsed, base_url="https://tenant.example/TDWebApi", app_id=42, asset_app_id=928, portal_app_id=2045,
                          baseline_json=json.dumps({"ModifiedDate": baseline}),
                          payload_json=json.dumps(payload or {"k": action["kind"]}, sort_keys=True, separators=(",", ":")),
                          preview=ChangePreview(application="Knowledge Base", ticket_id=0, ticket_title="t", action=action["kind"], fields=()))
```

Service (append to `tests/test_ticket_write_service.py`; check how `setup.upstream` routes reads — extend `Upstream.read` records with `f"/api/2045/knowledgebase/95821"` → an article dict with `AppID` 2045 and the categories route, mirroring how asset routes were added for `test_asset_actions_submit_through_the_pipeline` at ~line 640):

```python
def test_article_actions_submit_and_report_the_portal_item(setup):
    setup.upstream.records["/api/2045/knowledgebase/95821"] = dict(ID=95821, AppID=2045, Subject="Mac password", ModifiedDate="v1",
                                                                  RevisionNumber=4, Status=3, StatusName="Approved", IsPublished=True, IsPublic=False)
    setup.upstream.apply_result = SimpleNamespace(status_code=200, json=lambda: dict(ID=95821, AppID=2045, StatusName="Approved",
                                                                                     IsPublished=True, IsPublic=False, RevisionNumber=5))
    result = setup.service.submit("principal", parse_action(dict(kind="article_edit", article_id=95821, subject="Renamed")), "kb-1")
    assert result.outcome == "applied" and result.ticket_id == 0
    assert result.item == {"type": "article", "id": 95821, "url": f"{BASE.rsplit('/', 1)[0]}/TDClient/2045/Portal/KB/ArticleDet?ID=95821"}
    assert result.detail["revision"] == 5
    setup.upstream.records["/api/2045/knowledgebase/categories"] = [dict(ID=10548, AppID=2045, Name="Tech FAQ", ParentID=0, Subcategories=[])]
    setup.upstream.apply_result = SimpleNamespace(status_code=201, json=lambda: dict(ID=30001, AppID=2045, Name="Scratch", ParentID=10548))
    created = setup.service.submit("principal", parse_action(dict(kind="category_create", name="Scratch", parent_id=10548)), "kb-2")
    assert created.outcome == "applied"
    assert created.item == {"type": "category", "id": 30001, "url": f"{BASE.rsplit('/', 1)[0]}/TDClient/2045/Portal/KB/?CategoryID=30001"}
```

Confirm `FakeConnection` in that test file exposes `portal_app_id` (add `portal_app_id = 2045` and `asset_app_id = 928` attributes if the adapter factory builds `WriteAdapter` from it; the `setup` fixture's `adapter_factory` must pass `portal_app_id=connection.portal_app_id`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_store.py tests/test_ticket_write_service.py -q -k "article or category"`
Expected: store test may already pass (generic domain hashing); the service test FAILS with `TypeError: A supported typed ticket action is required.`

- [ ] **Step 3: Implement**

`service.py` imports: add `ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction` to the `.models` import. Then:

```python
_ACTION_TYPES = (CommentAction, StatusAction, AssignAction, EditAction, TaskAction, CreateAction, AssetAction,
                 ArticleAction, ArticleCreateAction, CategoryAction, CategoryCreateAction)
```

Replace `_item`:

```python
    @staticmethod
    def _item(record):
        if isinstance(record, DirectReplayResult):
            return None
        prepared, action = record.prepared, record.prepared.action
        host = f"https://{urlsplit(prepared.base_url).netloc}"
        detail = record.result.detail if record.result else None
        if isinstance(action, AssetAction):
            url = f"{host}/TDNext/Apps/{prepared.asset_app_id}/Assets/AssetDet?AssetID={action.asset_id}" if prepared.asset_app_id else None
            return {"type": "asset", "id": action.asset_id, "url": url}
        portal = prepared.portal_app_id
        if isinstance(action, (ArticleAction, ArticleCreateAction)):
            article_id = action.article_id if isinstance(action, ArticleAction) else (detail or {}).get("article_id")
            if article_id is None:
                return None
            return {"type": "article", "id": article_id,
                    "url": f"{host}/TDClient/{portal}/Portal/KB/ArticleDet?ID={article_id}" if portal else None}
        if isinstance(action, (CategoryAction, CategoryCreateAction)):
            category_id = action.category_id if isinstance(action, CategoryAction) else (detail or {}).get("category_id")
            if category_id is None:
                return None
            return {"type": "category", "id": category_id,
                    "url": f"{host}/TDClient/{portal}/Portal/KB/?CategoryID={category_id}" if portal else None}
        return None
```

`_ticket_identity` promotes `detail["ticket_id"]` only; KB details carry `article_id`/`category_id`, so nothing changes there. Ensure the `setup` fixture's `adapter_factory` in `tests/test_ticket_write_service.py` passes `portal_app_id=getattr(connection, "portal_app_id", None)` and `asset_app_id` likewise, and that `FakeConnection` defines both.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python3.14 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/ticket_writes/service.py tests/test_ticket_write_service.py tests/test_ticket_write_store.py
git commit -m "Route knowledge base actions through the write service with portal item links

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Write tools

**Files:**
- Modify: `src/dynamix_manager/ticket_writes/tools.py` (imports; new request model after `CreateTicketRequest`; tools after `edit_asset` ~352; `names` tuple ~427)
- Test: `tests/test_ticket_write_tools.py`, `tests/test_hosted_connector.py`

**Interfaces:**
- Consumes: `_submit`, `_resolve_people`-style resolution, `prepare`, `write_meta`, `REQUEST_ID`, `ResultOutput`, `parse_action`.
- Produces: tools `create_article(article: CreateArticleRequest, request_id)`, `edit_article(action: ArticleEditAction, request_id)`, `link_article(action: ArticleLinkAction, request_id)`, `unlink_article(action: ArticleUnlinkAction, request_id)`, `create_article_category(category: CategoryCreateAction, request_id)`, `edit_article_category(action: CategoryEditAction, request_id)`. `CreateArticleRequest` = the create fields with `owner: PERSON | None` and `owner_uid` (exactly one or neither) resolved through the people API, output `CreateResultOutput` with `resolved_people`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_ticket_write_tools.py`:

```python
@pytest.mark.parametrize(("name", "action"), [
    ("edit_article", {"kind": "article_edit", "article_id": 95821, "subject": "Renamed"}),
    ("link_article", {"kind": "article_link", "article_id": 95821, "asset_id": 1973209}),
    ("unlink_article", {"kind": "article_unlink", "article_id": 95821, "related_article_id": 84764}),
    ("edit_article_category", {"kind": "category_edit", "category_id": 9208, "name": "Office devices"}),
])
def test_knowledge_base_write_tools_submit_typed_actions(name, action, tools_server):
    from dynamix_manager.ticket_writes.models import ArticleAction, CategoryAction
    server, service = tools_server
    result = structured(run(server, name, {"action": action}))
    assert result["outcome"] == "applied"
    assert isinstance(service.actions[-1], (ArticleAction, CategoryAction)) and service.actions[-1].kind == action["kind"]


def test_create_article_resolves_the_owner_and_defaults_to_a_draft(tools_server):
    server, service = tools_server
    result = structured(run(server, "create_article", {"article": {
        "subject": "Reset MFA", "body": "Step one", "category_id": 9212, "owner": "David Mortenson", "tags": ["mfa"]}}))
    action = service.actions[-1]
    assert action.kind == "article_create" and str(action.owner_uid) == PEOPLE_UID and action.status == "not_submitted"
    assert action.is_published is False and result["resolved_people"][0]["role"] == "owner"
    without_owner = structured(run(server, "create_article", {"article": {"subject": "S", "body": "b", "category_id": 1}}))
    assert without_owner["resolved_people"] == [] and service.actions[-1].owner_uid is None
    bad = run(server, "create_article", {"article": {"subject": "S", "body": "b", "category_id": 1, "owner": "X Y", "owner_uid": PEOPLE_UID}})
    assert bad.isError


def test_create_article_category_submits_a_typed_action(tools_server):
    server, service = tools_server
    result = structured(run(server, "create_article_category", {"category": {"name": "Scratch", "parent_id": 10548}}))
    assert result["outcome"] == "applied" and service.actions[-1].kind == "category_create"
```

`PEOPLE_UID` and the people-lookup fake already exist for `create_ticket` tests in this file (search for `test_create_ticket_resolves`); reuse the same connection fake and constant names. Add `create_article`, `edit_article`, `link_article`, `unlink_article`, `create_article_category`, `edit_article_category` to `run()`'s request-ID set and to the registered-tool set; counts: `35 → 41` in `tests/test_ticket_write_tools.py` and `tests/test_hosted_connector.py` (external read-only count stays 21). Add the new names to the loop that asserts write annotations, if one exists there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python3.14 -m pytest tests/test_ticket_write_tools.py -q -k "knowledge_base or create_article"`
Expected: FAIL with unknown tool errors.

- [ ] **Step 3: Implement the tools**

Imports in `tools.py`: add `ArticleEditAction, ArticleLinkAction, ArticleUnlinkAction, CategoryCreateAction, CategoryEditAction, ARTICLE_STATUS` from `.models`. After `CreateTicketRequest`:

```python
class CreateArticleRequest(BaseModel):
    """Article creation as the model states it; the owner is resolved to a UID before submission."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: Annotated[str, Field(strict=True, min_length=1, max_length=300)]
    body: Annotated[str, Field(strict=True, min_length=1, max_length=200000)]
    category_id: TICKET_ID
    summary: Annotated[str, Field(strict=True, max_length=2000)] | None = None
    tags: Annotated[list[Annotated[str, Field(strict=True, min_length=1, max_length=100)]], Field(max_length=50)] | None = None
    owner: PERSON | None = None
    owner_uid: UUID | None = None
    owning_group_id: TICKET_ID | None = None
    review_date: Annotated[str, Field(strict=True, min_length=10, max_length=35)] | None = None
    is_public: Annotated[bool, Field(strict=True)] = False
    is_published: Annotated[bool, Field(strict=True)] = False
    status: ARTICLE_STATUS = "not_submitted"
    notify_owner: Annotated[bool, Field(strict=True)] | None = None
    notify_owner_of_review_date: Annotated[bool, Field(strict=True)] | None = None
    order: Annotated[float, Field(strict=True, ge=0)] | None = None

    @model_validator(mode="after")
    def one_way_to_name_the_owner(self):
        if self.owner is not None and self.owner_uid is not None:
            raise ValueError("Give owner (name/email) or owner_uid, not both.")
        return self
```

Tools, after `edit_asset`:

```python
    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def create_article(article: CreateArticleRequest, request_id: REQUEST_ID) -> CreateResultOutput:
        """Create a knowledge base article only on an explicit user request; defaults to an unpublished draft.

        Resolve category_id with article_categories. `owner` is resolved through the people API
        (state who matched and continue). Body may be HTML or plain text; scripts are stripped.
        Set is_published/is_public/status only when the user asked to publish. Generate a unique
        request_id and reuse it with identical arguments for recovery for 30 days.
        """
        connection = connection_provider()
        resolved, uids = [], {}
        if article.owner is not None:
            entry, found = connection.resolve_person("owner", article.owner)
            resolved.append(entry)
            if len(found) != 1:
                return _error("The owner search was ambiguous or matched nobody; nothing was submitted.")
            uids["owner_uid"] = found[0]
        given = article.model_dump(exclude_unset=True, exclude={"owner"})
        try:
            parsed = parse_action({**given, **uids, "kind": "article_create"})
        except Exception:
            return _error("Invalid article creation request.")
        return _submit(service, parsed, request_id, extra={"resolved_people": resolved})

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def edit_article(action: ArticleEditAction, request_id: REQUEST_ID) -> ResultOutput:
        """Change article fields (subject, summary, body, tags, category, owner, group, review date, public,
        published, status, notifications, order) only on an explicit user request.

        Publishing, making public or archiving are stated in the preview notices; confirm intent first.
        The article is snapshotted so a concurrent revision conflicts. Generate a unique request_id
        and reuse it with identical arguments for recovery for 30 days.
        """
        return _submit(service, action, request_id)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def link_article(action: ArticleLinkAction, request_id: REQUEST_ID) -> ResultOutput:
        """Relate an article to an asset (asset_id) or to another article (related_article_id), on request."""
        return _submit(service, action, request_id)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def unlink_article(action: ArticleUnlinkAction, request_id: REQUEST_ID) -> ResultOutput:
        """Remove an article's relation to an asset or another article, on explicit user request."""
        return _submit(service, action, request_id)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def create_article_category(category: CategoryCreateAction, request_id: REQUEST_ID) -> ResultOutput:
        """Create a knowledge base category (name, optional parent, description, order, public) on request."""
        return _submit(service, category, request_id)

    @tool(annotations=prepare, meta=write_meta, structured_output=True)
    def edit_article_category(action: CategoryEditAction, request_id: REQUEST_ID) -> ResultOutput:
        """Rename, move, reorder, describe or change visibility of a knowledge base category, on request."""
        return _submit(service, action, request_id)
```

`CategoryCreateAction` as a direct tool parameter needs `kind`; if FastMCP schema requires the caller to pass `"kind": "category_create"`, that is acceptable (the `action` tools already do), but the docstring must say so. If the tests' arguments omit `kind` for `create_article_category`, either add it to the test or define a `CreateCategoryRequest` without `kind` mirroring `CreateArticleRequest`; pick the request model (consistent with `create_ticket`).

Add the six names to the `names` tuple so `_harden_tool` covers them.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python3.14 -m pytest -q`
Expected: all pass, tool counts 41 (personal) and 21 (external).

- [ ] **Step 5: Ruff and commit**

```bash
.venv/bin/python3.14 -m ruff check src/ tests/
git add src/dynamix_manager/ticket_writes/tools.py tests/test_ticket_write_tools.py tests/test_hosted_connector.py
git commit -m "Add knowledge base write tools: create/edit article, link/unlink, create/edit category

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/hosted-connector.md` ("ChatGPT tool surface" ~560: counts and a **Knowledge base.** paragraph), `docs/tdx-write-api-contracts.md` (new "Knowledge base" section listing the endpoints and observed statuses; mark unverified items "not yet exercised live"), `docs/superpowers/specs/2026-09-22-knowledge-base-tools-design.md` (Status line → "implemented <date>; live verification pending"), `CLAUDE.md` project structure (add `kb_text.py  # KB body text/sanitising helpers`).

- [ ] **Step 1: Update the tool-surface paragraph**

Change "Personal mode exposes 29 tools: fifteen read tools" to "Personal mode exposes 41 tools: twenty-one read tools (…the six knowledge base reads: `search_articles`, `get_article`, `article_categories`, `related_articles`, `article_services`, `asset_articles`), fifteen direct-write tools (… `create_article`, `edit_article`, `link_article`, `unlink_article`, `create_article_category`, `edit_article_category`) …" and "External issuer mode keeps the twenty-one read-only tools."

- [ ] **Step 2: Add the Knowledge base paragraph** after the **Assets.** paragraph:

> **Knowledge base.** The Client Portal application is discovered by class (`TDClient`; Cedarville has one, 2045). Reads map `ArticleSearch` one-to-one, resolve `author` through the people API, and return bodies as plain-text snippets (400 characters) with the full text or raw HTML from `get_article`. Writes go through the same pipeline: `create_article` defaults to an unpublished Not Submitted draft; `edit_article` is a JSON Patch with a revision-and-modified-date baseline and preview notices when a change publishes, makes public or archives; links to assets and related articles are add/remove only; categories can be created and edited, never deleted. Article deletion is not exposed: archive plus unpublish instead. Bodies are sanitised (script, iframe, object, embed, `on*` attributes) before saving.

- [ ] **Step 3: Contract notes** in `docs/tdx-write-api-contracts.md`: a table of the six write endpoints with documented codes (201 create, 200 edit/PUT, 200 link/unlink) and the adapter's classification (204 link → already linked, 404 unlink → did not exist), each marked "documented; verify live".

- [ ] **Step 4: Commit**

```bash
git add docs/hosted-connector.md docs/tdx-write-api-contracts.md docs/superpowers/specs/2026-09-22-knowledge-base-tools-design.md CLAUDE.md
git commit -m "Document the knowledge base tools

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Deploy and live verification

**Files:**
- Modify after verification: `docs/hosted-connector.md`, `docs/tdx-write-api-contracts.md` (observed statuses), memory file `project_state.md`.

- [ ] **Step 1: Deploy under the project token**

```bash
cd /Users/micahcooper/dynamix-manager && set -a && . ./.env && set +a && env RAILWAY_TOKEN="$RAILWAY_TOKEN" railway status | head -4
```
Expected: `Project: teamdynamix-connector`. Then:

```bash
S=/private/tmp/claude-502/-Users-micahcooper-dynamix-manager/0b63259b-15e3-4ab4-ad64-d3f4785f844a/scratchpad/bundle && rm -rf $S && mkdir -p $S && git archive HEAD pyproject.toml README.md LICENSE railway.toml deploy/ src/ | tar -x -C $S && env RAILWAY_TOKEN="$RAILWAY_TOKEN" railway up --path-as-root $S -s connector -e production -d
```
Poll `railway deployment list -s connector -e production --json` until SUCCESS (tolerate empty output), then `curl -s -o /dev/null -w "%{http_code}" https://connector-production-a492.up.railway.app/healthz` → 200, and `.venv/bin/python3.14 scripts/connector_client.py tools | grep -c readOnly` → 41.

- [ ] **Step 2: Live reads** with `scripts/connector_client.py` (`cc.tool(name, args)`): `connection_status` shows `portal_app_id` 2045; `search_articles` text "password", status "approved", is_published true → results with snippets; `get_article` on one result in both formats; `article_categories` and `article_categories(parent_id=9106)`; `related_articles`, `article_services` on it; `asset_articles(1973209)`. Open one `url` in the browser pane to confirm the portal URL shape resolves; fix `article_url` if it does not.

- [ ] **Step 3: Live writes**, each with a fresh `request_id`, replaying the first one to confirm dedup:
  1. `create_article_category` name "Connector test (safe to delete)" under Tech FAQ 10548 → applied 201, note the category ID.
  2. `create_article` subject "Connector test article (safe to delete)", body plain text, in that category → applied 201; `get_article` confirms Not Submitted, unpublished.
  3. `edit_article` summary + tags → applied 200, revision bumped.
  4. `edit_article` is_published true, status "approved" → applied; preview notices mention publishing; then is_published false.
  5. `link_article` to asset 1973209 → applied; repeat under a new request ID → 204 "already linked" (record the real status); `asset_articles(1973209)` lists it; `unlink_article` → applied; repeat → record the status (expect 404 → "did not exist").
  6. `link_article` related_article_id to an approved article, `related_articles` shows it, `unlink_article`.
  7. `edit_article_category` description on the scratch category → applied 200.
  8. `edit_article` status "archived" → applied; notice "archives the article".

- [ ] **Step 4: Record observations** (real status codes, any deviation from the spec such as 204/404 handling, whether PUT category needed fields the adapter strips) in both docs and the memory file; adjust the adapter with a failing test first if a classification was wrong; commit, redeploy, push.

- [ ] **Step 5: Report** to the owner: what was verified, the scratch category and archived article IDs to delete in TDNext, and any deviations.

---

## Self-review

- **Spec coverage:** discovery (T2), body handling (T1, T5), six reads (T3), seven writes (T4–T8), store item generalisation (T7 test), pipeline classification incl. 204/404 (T6), tool count (T3/T8), docs (T9), live verification (T10). `article_services`/`related_articles` covered in T3. No-delete constraint: no delete tool anywhere. ✔
- **Placeholders:** none; the one conditional in T8 (request model vs `kind`) gives a concrete default (request model).
- **Type consistency:** `ArticleEditAction.EDITABLE`, `CategoryEditAction.EDITABLE`; `portal_app_id` on `WriteAdapter`, `PreparedChange`, `Connection`; `ARTICLE_STATUS_IDS` in models used by the adapter; `html_to_text`/`truncate`/`ensure_html`/`sanitize_html` names match T1; `item` shapes in T7 match T6 `detail` keys (`article_id`, `category_id`). ✔
