"""Knowledge base body handling: readable text for the model, safe HTML for the tenant."""
import re
from html import escape
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}
_HIDDEN = {"script", "style"}
_ACTIVE = {"script", "iframe", "object", "embed"}
_TAG = re.compile(r"</?(?:p|div|br|ul|ol|li|a|b|i|em|strong|h[1-6]|table|thead|tbody|tr|td|th|img|span|pre|code|blockquote|hr|u|s|sub|sup|figure|figcaption|section|dl|dt|dd)\b[^>]*>", re.IGNORECASE)
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
    """Plain, single-line text for an HTML body; script and style content is dropped."""
    parser = _Text()
    parser.feed(str(html or ""))
    parser.close()
    return " ".join("".join(parser.parts).replace("\xa0", " ").split())


def truncate(text, limit):
    return (text[:limit], True) if len(text) > limit else (text, False)


def ensure_html(body):
    """Pass HTML through (recognised by a common tag); wrap plain-text paragraphs in <p> with escaping."""
    body = str(body or "")
    if _TAG.search(body):
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
    """Remove script/iframe/object/embed elements and on* attributes; report whether anything changed."""
    parser = _Sanitizer()
    parser.feed(str(html or ""))
    parser.close()
    return "".join(parser.out), parser.changed
