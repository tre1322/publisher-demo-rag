"""Phase B.4 smoke — exercise settings PUT + escalations POST end-to-end.

Run with:  uv run python -m app.scripts.smoke_settings
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

_tmpdir = Path(tempfile.mkdtemp(prefix="popular_smoke_b4_"))
os.environ["POPULAR_DB_PATH"] = str(_tmpdir / "smoke.db")


def _fail(msg: str) -> None:
    print(f"FAIL  {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"  ok  {msg}")


def main() -> None:
    import shutil

    from fastapi.testclient import TestClient
    from app.main import app
    from app.scripts._auth_helper import bootstrap_login
    from app.db import SessionLocal
    from app.models import Escalation, SettingsRow

    with TestClient(app) as client:
        bootstrap_login(client)
        _run_assertions(client, SessionLocal, Escalation, SettingsRow)

    shutil.rmtree(_tmpdir, ignore_errors=True)
    print("\nPASS  Phase B.4 (Settings + Escalations) smoke green ✓")


def _run_assertions(client, SessionLocal, Escalation, SettingsRow) -> None:
    # Bootstrap exposes settings.cadence + settings.notifications
    boot = client.get("/api/bootstrap").json()
    if boot["settings"]["cadence"] != "weekly":
        _fail(f"seeded cadence should be 'weekly', got {boot['settings']['cadence']!r}")
    notifs = boot["settings"]["notifications"]
    if not isinstance(notifs, list) or len(notifs) < 4:
        _fail(f"settings.notifications missing or too short: {notifs}")
    keys = {n["key"] for n in notifs}
    if "neg_review" not in keys or "weekly_digest" not in keys:
        _fail(f"notifications missing seeded keys: {keys}")
    _ok(f"bootstrap → cadence='weekly', {len(notifs)} notification prefs seeded")

    # --- PUT cadence only ---
    r = client.put("/api/settings/notifications", json={"cadence": "each"})
    if r.status_code != 200:
        _fail(f"PUT cadence → HTTP {r.status_code} {r.text}")
    if r.json()["settings"]["cadence"] != "each":
        _fail(f"PUT response cadence: {r.json()}")
    with SessionLocal() as s:
        row = s.get(SettingsRow, 1)
        if row.cadence != "each":
            _fail(f"DB cadence didn't persist: {row.cadence}")
    _ok("PUT cadence='each' → persisted, response echoes value")

    # --- PUT notifications (toggle neg_review off) ---
    modified = [
        {**n, "on": False} if n["key"] == "neg_review" else n
        for n in notifs
    ]
    r = client.put("/api/settings/notifications", json={"notifications": modified})
    if r.status_code != 200:
        _fail(f"PUT notifications → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        row = s.get(SettingsRow, 1)
        neg = next((n for n in row.notifications_json if n["key"] == "neg_review"), None)
        if neg is None or neg["on"] is not False:
            _fail(f"neg_review toggle didn't persist: {neg}")
    _ok("PUT notifications → neg_review toggled to off, persisted")

    # --- PUT both at once ---
    r = client.put("/api/settings/notifications", json={
        "cadence": "auto",
        "notifications": notifs,  # restore originals
    })
    if r.status_code != 200:
        _fail(f"PUT both → HTTP {r.status_code} {r.text}")
    with SessionLocal() as s:
        row = s.get(SettingsRow, 1)
        if row.cadence != "auto":
            _fail(f"combined update cadence: {row.cadence}")
        neg = next((n for n in row.notifications_json if n["key"] == "neg_review"), None)
        if not neg["on"]:
            _fail(f"combined update notifications: neg_review still off: {neg}")
    _ok("PUT both cadence + notifications in one request → both persisted")

    # --- 422 on bogus cadence ---
    r = client.put("/api/settings/notifications", json={"cadence": "instant"})
    if r.status_code != 422:
        _fail(f"bogus cadence expected 422, got {r.status_code}")
    _ok("PUT cadence='instant' → 422")

    # --- 422 on empty body ---
    r = client.put("/api/settings/notifications", json={})
    if r.status_code != 422:
        _fail(f"empty body expected 422, got {r.status_code}")
    _ok("PUT empty body → 422")

    # --- 404 on unknown section ---
    r = client.put("/api/settings/billing", json={"cadence": "weekly"})
    if r.status_code != 404:
        _fail(f"unknown section expected 404, got {r.status_code}")
    _ok("PUT /settings/billing → 404 (not editable in this phase)")

    # --- POST escalation creates row ---
    msg = "I want to talk to a real person about a billing question."
    r = client.post("/api/escalations", json={"message": msg})
    if r.status_code != 200:
        _fail(f"POST escalation → HTTP {r.status_code} {r.text}")
    body = r.json()
    if not body.get("ok") or not body["escalation"].get("id"):
        _fail(f"escalation response missing id: {body}")
    with SessionLocal() as s:
        esc = s.get(Escalation, body["escalation"]["id"])
        if esc is None or esc.message != msg or esc.business_id != 1:
            _fail(f"escalation didn't persist correctly: {esc}")
    _ok(f"POST escalation → row id={body['escalation']['id']} created")

    # --- 422 on empty / whitespace message ---
    r = client.post("/api/escalations", json={"message": "   "})
    if r.status_code != 422:
        _fail(f"whitespace message expected 422, got {r.status_code}")
    _ok("POST escalation message='   ' → 422")

    # --- bootstrap reflects the latest cadence + notifications ---
    boot2 = client.get("/api/bootstrap").json()
    if boot2["settings"]["cadence"] != "auto":
        _fail(f"bootstrap cadence not updated: {boot2['settings']['cadence']}")
    _ok(f"/api/bootstrap → cadence='auto' visible after PUT")


if __name__ == "__main__":
    main()
