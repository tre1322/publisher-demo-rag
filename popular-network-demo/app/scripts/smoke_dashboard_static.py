"""Phase 2c smoke — static checks on dashboard.html that a browser run would
otherwise have to find by clicking.

Run with:  uv run python -m app.scripts.smoke_dashboard_static

  A. Every JSX component the page renders is defined. In May a cleanup
     deleted PostDetail and DraftsTab while the Calendar still rendered
     them, so clicking any calendar post crashed the screen for four months.
  C. The plain (non-React) scripts in the other pages parse. A stray
     apostrophe in admin.html once broke the whole admin console.
  B. The page is built for phones: device-width viewport (it was a fixed
     1280px), the bottom tab bar and drawer exist, and dialogs render at
     the top of the page so they can't end up behind the tab bar.
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

ROOT = Path(__file__).resolve().parents[2]


def check(label: str, cond: bool, detail: object = "") -> None:
    if not cond:
        print(f"FAIL  {label} — {detail}")
        sys.exit(1)
    print(f"  ok  {label}")


def main() -> None:
    html = (ROOT / "dashboard.html").read_text(encoding="utf-8")
    m = re.search(r'<script type="text/babel"[^>]*>(.*?)</script>', html, re.S)
    check("A0 found the app script", m is not None)
    src = m.group(1)

    print("\nA. components")
    # Strip comments and string/template literals so prose like "<Card>" in
    # a comment doesn't count as a use.
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"(?m)//[^\n]*", "", code)
    used = set(re.findall(r"<([A-Z][A-Za-z0-9]*)[\s/>]", code))
    defined = set(re.findall(r"\b(?:const|let|function|class)\s+([A-Z][A-Za-z0-9]*)\b", code))
    destructured = re.search(r"const\s*\{([^}]*)\}\s*=\s*React\s*;", code)
    if destructured:
        defined |= {n.strip() for n in destructured.group(1).split(",") if n.strip()}
    missing = sorted(used - defined)
    check(f"A1 all {len(used)} rendered components are defined", not missing, missing)
    for name in ("PostDetail", "DraftsTab", "OnboardingView", "MobileTabBar"):
        check(f"A2 {name} is defined", name in defined)

    print("\nB. phones")
    check("B1 viewport is device-width (was a fixed 1280px page)",
          'name="viewport" content="width=device-width' in html and "width=1280" not in html)
    check("B2 phones get 16px type; 19px only from tablet width up",
          "html { font-size: 16px; }" in html and "@media (min-width: 768px) { html { font-size: 19px; } }" in html)
    check("B3 bottom tab bar + menu drawer", "<MobileTabBar" in code and 'aria-label="Menu"' in code
          and "md:hidden fixed bottom-0" in code)
    check("B4 the desktop sidebar hides on phones", '<Sidebar current={view} onNavigate={(v) => navigate(v)} className="hidden md:flex" />' in code)
    check("B5 dialogs portal to <body> (not trapped behind the tab bar)", "ReactDOM.createPortal(" in code)
    check("B6 main content clears the tab bar", "pb-20 md:pb-0" in code)

    print("\nC. plain page scripts parse")
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        print("  --  node isn't installed here; skipped (CI runs it)")
    else:
        for page in ("admin.html", "invite.html", "login.html", "forgot-password.html", "reset-password.html"):
            page_html = (ROOT / page).read_text(encoding="utf-8")
            blocks = [b for attrs, b in re.findall(r"<script([^>]*)>(.*?)</script>", page_html, re.S)
                      if "src=" not in attrs and "text/babel" not in attrs and b.strip()]
            for n, block in enumerate(blocks):
                with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
                    tmp.write(block)
                res = subprocess.run([node, "--check", tmp.name], capture_output=True, text=True)
                Path(tmp.name).unlink(missing_ok=True)
                check(f"C1 {page} script {n + 1} parses", res.returncode == 0, res.stderr[-400:])

    print("\nPASS  dashboard static smoke green ✓")


if __name__ == "__main__":
    main()
