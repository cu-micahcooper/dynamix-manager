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
    assert ensure_html("First line\n\nSecond: 2 < 3 > 1") == "<p>First line</p><p>Second: 2 &lt; 3 &gt; 1</p>"
    assert ensure_html("<p>Already html</p>") == "<p>Already html</p>"


def test_sanitize_html_removes_active_content_and_reports_it():
    dirty = '<p onclick="x()">Hi <script>evil()</script><iframe src="a"></iframe><a href="/x" onmouseover="y">link</a></p>'
    clean, changed = sanitize_html(dirty)
    assert changed is True
    assert clean == '<p>Hi <a href="/x">link</a></p>'
    assert sanitize_html("<p>Clean <em>text</em></p>") == ("<p>Clean <em>text</em></p>", False)
