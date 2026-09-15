"""
HTML artifact sanitization.

Model-generated HTML is untrusted input, **always** -- including when a frontier
cloud model produced it, because a prompt-injected transcript could steer either
provider. This module implements the first two of the four containment layers:

1. **Allowlist sanitization before persistence.** `nh3` strips `<script>`, every
   `on*` handler, `<iframe>/<object>/<embed>/<form>/<meta>/<base>/<link>`, and
   refuses `javascript:` and remote URLs.
2. **Visible removal.** A pre-pass records *what* was stripped into
   `artifacts.removed_elements`. Silent sanitization is a security control the user
   cannot audit, and a chart that quietly loses its data because an element was
   dropped is worse than being told.

Layers 3 and 4 live in the HTTP and browser: a no-egress CSP on the render
endpoint, and `<iframe sandbox="allow-scripts">` **without** `allow-same-origin`
combined with opaque-origin `srcDoc`. Combining those two flags is the specific
mistake that defeats iframe sandboxing.

`nh3` reports nothing about what it removed, which is why the audit is a separate
stdlib parse rather than a return value.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser

import nh3
import structlog

log = structlog.get_logger("app.artifacts.sanitize")

# Deliberately conservative: enough for a self-contained chart or table, no
# scripting, no framing, no forms, nothing that can phone home.
ALLOWED_TAGS = {
    "a",
    "abbr",
    "b",
    "blockquote",
    "br",
    "caption",
    "code",
    "col",
    "colgroup",
    "dd",
    "div",
    "dl",
    "dt",
    "em",
    "figcaption",
    "figure",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "li",
    "mark",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "table",
    "tbody",
    "td",
    "tfoot",
    "th",
    "thead",
    "tr",
    "u",
    "ul",
}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": {"class", "title"},
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
}

# `data:` is required for self-contained charts. Remote URLs are refused so an
# artifact cannot beacon out when opened.
ALLOWED_URL_SCHEMES = {"data", "http", "https", "mailto"}

# Attribute names permitted anywhere, for the removal report.
ALLOWED_ATTRIBUTE_NAMES: set[str] = set().union(*ALLOWED_ATTRIBUTES.values())

# Removed *with their content*: leaving the text of a <script> behind would paste
# JavaScript into the artifact as visible prose.
CLEAN_CONTENT_TAGS = {"script", "style"}

_DANGEROUS_URL_PREFIXES = ("javascript:", "vbscript:", "data:text/html")

# Schemes that are only ever an asset, never a document. `data:` is needed for
# self-contained charts, but `data:text/html` is a *page*: if it survived, a click
# would navigate the frame to attacker-authored HTML. nh3 filters by scheme and
# cannot tell the two apart, so dangerous values are neutralised before it runs.
_SAFE_DATA_PREFIXES = ("data:image/", "data:font/")

# href/src values, quoted or not.
_URL_ATTRIBUTE_RE = re.compile(
    r"""(?P<attr>\b(?:href|src)\s*=\s*)(?P<quote>["']?)(?P<url>[^"'\s>]*)(?P=quote)""",
    re.IGNORECASE,
)


def _is_dangerous_url(url: str) -> bool:
    lowered = url.strip().lower()
    if lowered.startswith(_DANGEROUS_URL_PREFIXES):
        return True
    return lowered.startswith("data:") and not lowered.startswith(_SAFE_DATA_PREFIXES)


def _neutralize_urls(raw: str) -> str:
    """Blank out dangerous href/src values, so the later scheme check sees a no-op."""

    def replace(match: re.Match[str]) -> str:
        if not _is_dangerous_url(match.group("url")):
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('attr')}{quote}#{quote}"

    return _URL_ATTRIBUTE_RE.sub(replace, raw)


@dataclass
class SanitizationResult:
    cleaned: str
    removed: list[dict] = field(default_factory=list)

    @property
    def removed_anything(self) -> bool:
        return bool(self.removed)


class _Auditor(HTMLParser):
    """Collects tag/attribute usage so removals can be reported after the fact."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tags: Counter[str] = Counter()
        self.attributes: Counter[str] = Counter()
        self.urls: Counter[str] = Counter()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags[tag] += 1
        for name, value in attrs:
            lowered = name.lower()
            self.attributes[lowered] += 1
            if lowered in ("href", "src") and value and _is_dangerous_url(value):
                self.urls[value.strip().split(":", 1)[0].lower() + ":"] += 1


def audit(raw: str) -> list[dict]:
    """Describe what sanitization will remove, without removing anything."""
    parser = _Auditor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as exc:  # noqa: BLE001 -- an unparseable artifact is just reported as-is
        log.warning("artifact_audit_failed", error=str(exc))

    removed: list[dict] = []

    for tag, count in sorted(parser.tags.items()):
        if tag not in ALLOWED_TAGS:
            removed.append({"type": "element", "name": tag, "count": count})

    for name, count in sorted(parser.attributes.items()):
        if name not in ALLOWED_ATTRIBUTE_NAMES:
            # The event-handler case gets its own label because `on*` is the
            # highest-signal thing to surface to a user.
            kind = "event_handler" if name.startswith("on") else "attribute"
            removed.append({"type": kind, "name": name, "count": count})

    for scheme, count in sorted(parser.urls.items()):
        removed.append({"type": "url_scheme", "name": scheme, "count": count})

    return removed


def sanitize_html(raw: str) -> SanitizationResult:
    """Strip everything not explicitly allowed, and report what was stripped."""
    removed = audit(raw)
    cleaned = nh3.clean(
        _neutralize_urls(raw),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        clean_content_tags=CLEAN_CONTENT_TAGS,
        strip_comments=True,
        link_rel="noopener noreferrer nofollow",
    )
    if removed:
        log.warning(
            "artifact_sanitized",
            removed=len(removed),
            kinds=sorted({item["type"] for item in removed}),
        )
    return SanitizationResult(cleaned=cleaned, removed=removed)


def sanitize_markdown(raw: str) -> SanitizationResult:
    """
    Markdown artifacts are stored as text and rendered client-side.

    Nothing is stripped here because raw HTML is *not* interpreted on this path --
    the viewer renders the text, so the artifact cannot execute regardless of what
    it contains. The audit still runs so a hostile payload is visible in the
    removal report even when the value is inert.
    """
    removed = audit(raw)
    if removed:
        log.warning("markdown_artifact_contains_html", removed=len(removed))
    return SanitizationResult(cleaned=raw, removed=removed)


def sanitize(kind: str, raw: str) -> SanitizationResult:
    return sanitize_html(raw) if kind == "html" else sanitize_markdown(raw)
