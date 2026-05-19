"""End-to-end tests for the React business dashboard backend.

Exercises the real user path with a real SQLite DB (a tmp file) and a
real FastAPI app built by create_app() — not mocks. Covers:

  Roundtrip 1  login -> shell -> /api/bootstrap -> PUT /api/settings
  Roundtrip 2  PMC none -> draft -> edit -> accept -> supersede invariant
  Roundtrip 3  voice handoff redirect contract

DB redirection: get_connection() reads src.core.database.DATABASE_PATH at
call time, so monkeypatching that module attribute BEFORE create_app()
(which runs init_all_tables()) cleanly points every query at the tmp DB.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh app + tmp DB + one seeded org/user. Returns a context dict."""
    import src.core.database as db

    monkeypatch.setattr(db, "DATABASE_PATH", tmp_path / "test.db")

    from src.business_frontend.auth import create_business_user
    from src.chatbot import create_app
    from src.modules.organizations.database import insert_organization

    app = create_app()  # runs init_all_tables() against the tmp DB

    org_id = insert_organization("Westbrook Auto & Tire")
    email, password = "owner@westbrook.test", "hunter2pass"
    user_id = create_business_user(email, password, "Dale Henderson", org_id)

    client = TestClient(app, follow_redirects=False)
    return {
        "app": app,
        "client": client,
        "org_id": org_id,
        "user_id": user_id,
        "email": email,
        "password": password,
        "db": db,
    }


def _login(client, email, password):
    r = client.post(
        "/business/login", data={"email": email, "password": password}
    )
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/business/"
    assert "biz_session" in client.cookies
    return r


def _make_draft(org_id, qualitative_md, quantitative):
    """Build a draft PMC via the data layer (hermetic — no LLM call)."""
    from src.modules.pmc import database as pmc_db

    sid = pmc_db.create_session(org_id, voice_provider="manual_paste")
    pmc_db.complete_session_with_transcript(sid, "Q: ... A: ... full transcript")
    return pmc_db.create_pmc_draft(
        organization_id=org_id,
        qualitative_md=qualitative_md,
        quantitative=quantitative,
        transcript_text="Q: ... A: ... full transcript",
        interview_session_id=sid,
        generator_model="claude-sonnet-4-20250514",
        generator_prompt_version="v4",
        script_version="1.3.0",
        created_by_user_id=None,
    )


# ── Roundtrip 1 — login, shell, bootstrap, settings ─────────────────


def test_login_then_bootstrap_and_settings(env):
    c = env["client"]
    _login(c, env["email"], env["password"])

    boot = c.get("/business/api/bootstrap")
    assert boot.status_code == 200, boot.text
    data = boot.json()
    assert data["user"]["email"] == env["email"]
    assert data["org"]["name"] == "Westbrook Auto & Tire"
    assert data["flags"]["billing_enabled"] is False  # pilot default
    assert data["billing"]["subscription"] is None
    assert len(data["billing"]["tier_catalog"]) == 3
    assert data["pmc"]["state"] == "none"
    assert data["pmc"]["current"] is None

    # Live Settings write against the real DB, then re-read.
    put = c.put(
        "/business/api/settings", json={"description": "Family-owned since 2003"}
    )
    assert put.status_code == 200, put.text
    assert put.json() == {"ok": True}
    again = c.get("/business/api/bootstrap").json()
    assert again["org"]["description"] == "Family-owned since 2003"


def test_shell_html_is_auth_gated(env):
    """GET /business/ renders the React shell template (auth-gated)."""
    c = env["client"]
    _login(c, env["email"], env["password"])
    r = c.get("/business/")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert 'id="root"' in r.text
    assert "/business/api/bootstrap" in r.text  # bootstrap loader present


def test_bootstrap_without_cookie_redirects_to_login(env):
    fresh = TestClient(env["app"], follow_redirects=False)
    r = fresh.get("/business/api/bootstrap")
    assert r.status_code == 303
    assert r.headers["location"] == "/business/login"


# ── Roundtrip 2 — PMC none -> draft -> edit -> accept -> supersede ──


def test_pmc_draft_edit_accept_and_supersede(env):
    c = env["client"]
    _login(c, env["email"], env["password"])
    org_id = env["org_id"]

    pmc_id = _make_draft(
        org_id,
        "## STRATEGIC SUMMARY\n- TARGET: lake-bound drivers\n\n## origin_story\nFounded 2003.",
        {"business_name": "Westbrook Auto & Tire", "hours": "M-F 8-6"},
    )

    boot = c.get("/business/api/bootstrap").json()
    assert boot["pmc"]["state"] == "draft"
    cur = boot["pmc"]["current"]
    assert cur["id"] == pmc_id
    assert "STRATEGIC SUMMARY" in cur["qualitative_md"]
    assert cur["quantitative"]["hours"] == "M-F 8-6"
    # Serializer must strip the raw transcript + raw JSON blob.
    assert "transcript_text" not in cur
    assert "quantitative_json" not in cur

    # Edit the draft via the JSON twin of POST /pmc/save.
    edit = c.put(
        "/business/api/pmc/draft",
        json={
            "pmc_id": pmc_id,
            "qualitative_md": "## STRATEGIC SUMMARY\n- TARGET: edited",
            "quantitative": {"business_name": "Westbrook Auto & Tire"},
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["pmc"]["current"]["qualitative_md"].endswith("edited")
    # Persisted?
    assert (
        c.get("/business/api/pmc").json()["current"]["qualitative_md"].endswith("edited")
    )

    # Org-scoping: another org's PMC id must 404, not leak.
    from src.modules.organizations.database import insert_organization

    other_org = insert_organization("Rival Garage")
    other_pmc = _make_draft(other_org, "## STRATEGIC SUMMARY\nrival", {})
    leak = c.put("/business/api/pmc/draft", json={"pmc_id": other_pmc})
    assert leak.status_code == 404
    assert leak.json() == {"error": "not_found"}

    # Accept -> canonical.
    acc = c.post("/business/api/pmc/accept", json={"pmc_id": pmc_id})
    assert acc.status_code == 200, acc.text
    assert acc.json()["pmc"]["state"] == "accepted"
    assert acc.json()["pmc"]["current"]["status"] == "accepted"

    from src.modules.pmc import database as pmc_db

    assert pmc_db.get_canonical_pmc(org_id)["id"] == pmc_id

    # Re-accepting an already-accepted row is a 409, not a 500.
    again = c.post("/business/api/pmc/accept", json={"pmc_id": pmc_id})
    assert again.status_code == 409
    assert again.json() == {"error": "not_draft"}

    # Supersede invariant: a 2nd accepted draft demotes the 1st.
    pmc2 = _make_draft(org_id, "## STRATEGIC SUMMARY\nv2", {})
    c.post("/business/api/pmc/accept", json={"pmc_id": pmc2})
    versions = pmc_db.list_pmc_versions(org_id)
    accepted = [v for v in versions if v["status"] == "accepted"]
    assert len(accepted) == 1
    assert accepted[0]["id"] == pmc2
    assert next(v for v in versions if v["id"] == pmc_id)["status"] == "superseded"


# ── Roundtrip 3 — voice handoff redirect contract ──────────────────


def test_voice_status_handoff_targets_profile_view(env):
    """After a completed voice interview, the owner must be sent into the
    React Marketing Profile view (?view=profile), not the old page."""
    c = env["client"]
    _login(c, env["email"], env["password"])
    org_id = env["org_id"]

    from src.modules.pmc import database as pmc_db

    sid = pmc_db.create_session(org_id, voice_provider="livekit")
    pmc_db.complete_voice_session(
        sid, "voice transcript", 900, None, partial=False
    )
    _make_draft(org_id, "## STRATEGIC SUMMARY\nfrom voice", {})
    # Point the freshly-made draft at this session so the watchdog finds it.
    conn = env["db"].get_connection()
    conn.execute(
        "UPDATE product_marketing_contexts SET interview_session_id=? "
        "WHERE organization_id=?",
        (sid, org_id),
    )
    conn.commit()
    conn.close()

    r = c.get(f"/business/pmc/voice/status?sid={sid}")
    assert r.status_code == 200, r.text
    # Documented backend contract.
    assert r.json()["redirect_to"] == "/business/?view=profile"

    # The constant the browser actually obeys: pmc_interview.js navigates to
    # window.PMC_INTERVIEW.redirectTo (set in pmc_interview.html), NOT the
    # poll's JSON. Pin the real handoff path.
    from pathlib import Path

    tpl = Path("src/business_frontend/templates/pmc_interview.html").read_text(
        encoding="utf-8"
    )
    assert 'redirectTo: "/business/?view=profile"' in tpl
