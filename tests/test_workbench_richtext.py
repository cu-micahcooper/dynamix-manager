from dynamix_manager.workbench.richtext import render_html


def test_email_signature_preserves_blocks_links_and_tables():
    result = render_html(
        '<div>Accounts Payable<br>Cedarville University</div><table><tr><td><a href="mailto:ap@example.edu" style="color:red" onclick="bad()">Email</a></td></tr></table>'
    )
    assert "<div>Accounts Payable<br>Cedarville University</div>" in result
    assert "<table>" in result and "<td>" in result
    assert 'href="mailto:ap@example.edu"' in result
    assert "onclick" not in result and "style=" not in result


def test_active_content_and_tracking_resources_are_removed():
    result = render_html(
        '<script>alert(1)</script><iframe src="https://evil.example"></iframe><img src="https://evil.example/pixel" onerror="bad()"><a href="javascript:alert(1)">link</a><svg onload="bad()"></svg>'
    )
    assert "<script" not in result and "<iframe" not in result and "<img" not in result
    assert "<svg" not in result and "href=" not in result and "onerror" not in result


def test_plain_text_preserves_line_breaks_and_angle_brackets():
    assert (
        render_html("First line\n2 < 3 & 5 > 4")
        == "First line<br>2 &lt; 3 &amp; 5 &gt; 4"
    )


def test_malformed_html_is_repaired():
    assert "<strong>hello</strong>" in render_html("<div><strong>hello")
