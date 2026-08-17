"""Regression smoke — every external <script src> must be version-pinned.

Why this exists
---------------
On 2026-08-16 the production dashboard went down with zero code changes. The
cause: dashboard.html loaded

    https://unpkg.com/@babel/standalone/babel.min.js

with no version constraint. unpkg served Babel 7 when the file was written and
Babel 8.0.4 ten weeks later. Babel 8 flipped preset-react's default JSX runtime
from "classic" to "automatic", so the inline <script type="text/babel"> block
compiled to `import { jsx } from "react/jsx-runtime"` — a bare ESM import a
classic script tag cannot resolve. React never mounted and the page sat on its
"Loading..." placeholder forever.

`git log` could not find that bug: nothing in the repo changed. The whole
backend smoke suite stayed green throughout, because every other smoke mocks
the frontend and never parses the HTML that browsers actually execute.

This smoke closes that gap. It is deliberately a *static* check — it reads the
files off disk and never hits the network, so it is fast, hermetic, and does
not go red when a CDN has an outage.

Run with:  uv run python -m app.scripts.smoke_cdn_pins
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent.parent

# Every HTML file the app actually serves to a browser.
SERVED_HTML = ("dashboard.html", "login.html", "invite.html")

# Hosts that carry no package version in the URL, so there is nothing to pin.
# Keep this list SHORT and justified — it is the loophole in this check.
#   fonts.googleapis / fonts.gstatic — font CSS, keyed by family not version.
EXEMPT_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")

SRC_RE = re.compile(r"""<script[^>]*\ssrc=["']([^"']+)["']""", re.IGNORECASE)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASSED.append(label)
        print(f"  PASS  {label}")
    else:
        FAILED.append((label, detail))
        print(f"  FAIL  {label}  -- {detail}")


def is_pinned(url: str) -> bool:
    """True if the URL carries an explicit version.

    Two shapes are accepted:

      1. npm-style `pkg@version` (unpkg, jsdelivr, esm.sh). Scoped packages are
         the subtle case: "@babel/standalone" starts with '@' but that '@' is
         part of the NAME, not a version. So drop a leading '@' on the first
         path segment before looking for a version separator.

      2. A version directory in the path, e.g. cdn.tailwindcss.com/3.4.17

    A bare major ("react@18") counts as pinned — it cannot cross a breaking
    major boundary, which is the failure this check exists to prevent.
    """
    after_host = url.split("://", 1)[-1].split("/", 1)
    if len(after_host) < 2:
        return False  # host only, e.g. https://cdn.tailwindcss.com
    path = after_host[1]
    if not path.strip():
        return False  # trailing slash only, e.g. https://cdn.tailwindcss.com/

    # Shape 1: pkg@version, accounting for a scoped-package leading '@'.
    probe = path[1:] if path.startswith("@") else path
    if "@" in probe.split("/")[0] or "@" in "/".join(probe.split("/")[:2]):
        return True

    # Shape 2: a path segment that looks like a version number.
    return any(re.fullmatch(r"v?\d+(\.\d+)*", seg) for seg in path.split("/"))


def main() -> int:
    print("smoke_cdn_pins -- every external <script src> must be version-pinned\n")

    for name in SERVED_HTML:
        path = ROOT / name
        check(f"{name} exists", path.is_file(), str(path))
        if not path.is_file():
            continue

        html = path.read_text(encoding="utf-8")
        external = [
            u for u in SRC_RE.findall(html)
            if u.startswith(("http://", "https://", "//"))
            and not any(h in u for h in EXEMPT_HOSTS)
        ]
        check(f"{name} has at least one external script", bool(external), "none found")

        for url in external:
            check(f"{name} pinned: {url}", is_pinned(url), f"UNPINNED -> {url}")

    # Guard the specific regression that caused the outage, by name.
    dash = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    check(
        "dashboard.html pins @babel/standalone to major 7",
        "@babel/standalone@7/" in dash,
        "Babel 8 breaks JSX transform for classic <script> blocks",
    )
    check(
        "dashboard.html has no unpinned @babel/standalone",
        "standalone/babel.min.js" not in dash,
        "found the unpinned URL that caused the 2026-08-16 outage",
    )

    # Self-test the detector, so a bug in is_pinned() can't quietly pass
    # everything. These are assertions about the CHECK, not about the app.
    for url, want in [
        ("https://unpkg.com/@babel/standalone@7/babel.min.js", True),
        ("https://unpkg.com/@babel/standalone/babel.min.js", False),
        ("https://unpkg.com/react@18/umd/react.production.min.js", True),
        ("https://unpkg.com/react/umd/react.production.min.js", False),
        ("https://cdn.tailwindcss.com/3.4.17", True),
        ("https://cdn.tailwindcss.com", False),
        ("https://cdn.tailwindcss.com/", False),
    ]:
        check(f"detector: {url} -> {want}", is_pinned(url) is want, f"got {is_pinned(url)}")

    print(f"\n{len(PASSED)} pass / {len(FAILED)} fail")
    for label, detail in FAILED:
        print(f"  FAIL: {label} -- {detail}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
