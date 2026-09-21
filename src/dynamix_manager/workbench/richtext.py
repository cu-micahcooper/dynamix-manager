"""Sanitize vendor HTML for insertion into a normal HTML body, never attributes."""

import html
import re

import bleach

TAGS = frozenset(
    {
        "a",
        "abbr",
        "b",
        "blockquote",
        "br",
        "code",
        "dd",
        "del",
        "div",
        "dl",
        "dt",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "s",
        "small",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tfoot",
        "tr",
        "u",
        "ul",
    }
)


def render_html(value):
    source = str(value or "")
    if not re.search(r"</?[a-zA-Z][^>]*>", source):
        return html.escape(source).replace("\r\n", "\n").replace("\n", "<br>")
    # A fresh cleaner per call avoids sharing parser state between request threads.
    # No styles, events, remote images, embedded documents, IDs or form controls.
    return bleach.clean(
        source,
        tags=TAGS,
        attributes={
            "a": ["href", "title"],
            "abbr": ["title"],
            "td": ["colspan", "rowspan"],
            "th": ["colspan", "rowspan"],
        },
        protocols={"http", "https", "mailto"},
        strip=True,
        strip_comments=True,
    )
