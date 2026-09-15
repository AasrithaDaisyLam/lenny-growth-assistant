"""
Credential scan.

`agent-transcripts/` is a deliverable that quotes real agent output, and raw
output is exactly where credentials leak: a key pasted into a prompt, a bearer
token echoed by a provider, an account identifier printed in a log line. The
transcripts are written **as each phase is completed**, so the leak would be
committed long before anyone re-read it.

So this runs as a pre-commit check rather than a review step. It fails on a
finding and prints only the location and the shape of the match -- never the
value -- because the output of a secret scanner is itself a place secrets leak.

    make scan-secrets
    python -m scripts.scan_secrets --root .

Exit codes: 0 clean, 1 findings, 2 usage error.

A line may opt out with `scan-secrets:ignore`, which exists so the documentation
can *describe* a pattern without tripping the scan. It should be rare and it is
worth reviewing when it appears.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


def default_root() -> Path:
    """
    Where to scan by default.

    On a checkout that is the repository root, found by walking up to the `.git`
    directory -- which is what makes `make scan-secrets` cover `agent-transcripts/`
    and `docs/`, not just `backend/`.

    Inside the container there is no `.git` (the image copies only the app), so it
    falls back to the directory that owns `scripts/` and `app/`. Without this the
    fallback would be `/` and the scan would walk the entire filesystem.
    """
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / ".git").exists():
            return candidate
    return here.parent


# Build output, dependencies and tool caches: never committed, always noise.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

# `.env` is gitignored, so a real credential belongs there and only there. `.env.example`
# is deliberately *not* skipped: it should never contain anything but placeholders.
SKIP_NAMES = frozenset({".env"})

# A regex over a multi-megabyte file is not the right tool, and nothing that big
# should be in the tree anyway.
MAX_BYTES = 2_000_000

IGNORE_TOKEN = "scan-secrets:ignore"


@dataclass(frozen=True)
class Pattern:
    """One credential shape, plus the words that make an ambiguous match count."""

    name: str
    regex: re.Pattern[str]
    note: str
    context: tuple[str, ...] = ()


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "anthropic_key",
        re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
        "Anthropic API key",
    ),
    Pattern(
        "openai_style_key",
        re.compile(r"sk-(?!ant-)[A-Za-z0-9]{20,}"),
        "OpenAI-style API key",
    ),
    Pattern(
        "bearer_token",
        re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
        "Bearer token",
    ),
    Pattern(
        "github_token",
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        "GitHub token",
    ),
    Pattern(
        "aws_access_key_id",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "AWS access key id",
    ),
    Pattern(
        "private_key_block",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        "PEM private key",
    ),
    # Absolute paths carry a username, which is an identity leak in a public repo
    # and is the most common way a build log doxxes its author.
    Pattern(
        "windows_user_path",
        re.compile(r"(?i)\b[A-Za-z]:\\+Users\\+[^\\\s\"']+"),
        "Absolute Windows path containing a username",
    ),
    Pattern(
        "posix_user_path",
        re.compile(r"\b/(?:Users|home)/[^/\s\"']+"),
        "Absolute POSIX path containing a username",
    ),
    # A bare 40-hex string is ambiguous -- git abbreviates differently, but a full
    # SHA is also 40 hex. Requiring an account-shaped word on the same line keeps
    # this from firing on every commit hash in the docs.
    Pattern(
        "account_identifier",
        re.compile(r"\b[a-f0-9]{40}\b"),
        "40-character account identifier",
        context=("account", "org", "provider", "identifier", "workspace", "user_id"),
    ),
)


@dataclass(frozen=True)
class Finding:
    path: Path
    line_number: int
    pattern: str
    note: str
    length: int

    def describe(self) -> str:
        # The match itself is never printed. A scanner's output is a durable log,
        # and a durable log of a credential is the same problem one step later.
        return (
            f"{self.path}:{self.line_number}: {self.pattern} "
            f"({self.note}; <redacted, {self.length} chars>)"
        )


def iter_files(root: Path) -> Iterator[Path]:
    """Every scannable file under `root`, skipping this script and build output."""
    this_file = Path(__file__).resolve()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_NAMES:
            continue
        if path.resolve() == this_file:
            # Its own patterns are the only near-misses in the tree.
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
        except OSError:
            continue
        yield path


def _read(path: Path) -> str | None:
    """Text of `path`, or None if it is binary or unreadable."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:4096]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def scan_file(path: Path, root: Path) -> list[Finding]:
    text = _read(path)
    if text is None:
        return []

    relative = path.relative_to(root) if path.is_relative_to(root) else path
    findings: list[Finding] = []

    for line_number, line in enumerate(text.splitlines(), start=1):
        if IGNORE_TOKEN in line:
            continue
        lowered = line.lower()
        for pattern in PATTERNS:
            if pattern.context and not any(word in lowered for word in pattern.context):
                continue
            match = pattern.regex.search(line)
            if match is not None:
                findings.append(
                    Finding(
                        path=relative,
                        line_number=line_number,
                        pattern=pattern.name,
                        note=pattern.note,
                        length=len(match.group(0)),
                    )
                )
    return findings


def scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root):
        findings.extend(scan_file(path, root))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if a credential pattern is in the tree.")
    parser.add_argument(
        "--root",
        type=Path,
        default=default_root(),
        help="directory to scan (default: the repository root)",
    )
    args = parser.parse_args()

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    findings = scan(root)

    if not findings:
        print(f"No credential patterns found under {root}.")
        return 0

    print(f"Found {len(findings)} potential credential(s):", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.describe()}", file=sys.stderr)
    print(
        "\nRemove the value (rotate it if it was real) or annotate the line with "
        f"`{IGNORE_TOKEN}` if it is documentation.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
