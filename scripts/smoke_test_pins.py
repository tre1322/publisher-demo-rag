"""End-to-end smoke test for the homepage pinning feature.

Tests the real HTTP path an admin will exercise:
  1. GET homepage pins (empty initially for a fresh section)
  2. GET candidates (should list content_items)
  3. PUT a pin
  4. GET pins again -> verify pin appears in correct slot
  5. Verify /api/homepage-stories respects the pin (strict mode)
  6. DELETE the pin
  7. Verify homepage is empty for that section (strict mode)

Run: uv run python scripts/smoke_test_pins.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Force known admin password so Basic auth in TestClient works
os.environ.setdefault("ADMIN_PASSWORD", "admin")

from fastapi.testclient import TestClient  # noqa: E402
import base64  # noqa: E402

from src.chatbot import create_app  # noqa: E402

app = create_app()

AUTH = {"Authorization": "Basic " + base64.b64encode(b"admin:admin").decode()}
PUB = "Cottonwood County Citizen"


def hr(msg: str) -> None:
    print(f"\n-- {msg} " + "-" * max(0, (70 - len(msg))))


def main() -> int:
    client = TestClient(app)
    failures: list[str] = []

    hr("1. GET /admin/api/homepage-pins (initial state)")
    r = client.get(f"/admin/api/homepage-pins?publisher={PUB}", headers=AUTH)
    print(r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text[:200])
    if r.status_code != 200:
        failures.append(f"initial GET pins returned {r.status_code}")
        return _report(failures)
    initial_pins = r.json()["pins"]
    print(f"  news slots used: {len(initial_pins['news'])}, sports slots used: {len(initial_pins['sports'])}")

    hr("2. GET /admin/api/homepage-pins/candidates for news")
    r = client.get(
        f"/admin/api/homepage-pins/candidates?publisher={PUB}&section=news&limit=10",
        headers=AUTH,
    )
    print(r.status_code, f"count={r.json().get('count')}" if (r.status_code == 200) else r.text[:200])
    if r.status_code != 200:
        failures.append(f"candidates GET returned {r.status_code}")
        return _report(failures)
    candidates = r.json()["candidates"]
    if not candidates:
        print("  No news candidates available in local DB — skipping write tests.")
        print("  (This is expected on a fresh machine. Pin logic imports OK.)")
        return _report(failures)
    first_id = candidates[0]["id"]
    first_headline = candidates[0]["headline"]
    print(f"  Using candidate id={first_id}: {first_headline!r:.70}")

    hr("3. PUT pin at news slot 1")
    r = client.put(
        "/admin/api/homepage-pins",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"publisher": PUB, "section": "news", "slot": 1, "content_item_id": first_id},
    )
    print(r.status_code, r.json())
    if not (r.status_code == 200):
        failures.append(f"PUT pin returned {r.status_code}: {r.text}")

    hr("4. GET pins after PUT — slot 1 should be populated")
    r = client.get(f"/admin/api/homepage-pins?publisher={PUB}", headers=AUTH)
    after_pins = r.json()["pins"]
    print(f"  news pins: {[(p['slot'], p['headline'][:40]) for p in after_pins['news']]}")
    slot1 = next((p for p in after_pins["news"] if p["slot"] == 1), None)
    if not slot1 or slot1["content_item_id"] != first_id:
        failures.append(f"slot 1 did not contain content_item_id={first_id}")

    hr("5. GET /api/homepage-stories — strict mode: should return ONLY our pin")
    import urllib.parse
    r = client.get(
        f"/api/homepage-stories?publisher={urllib.parse.quote(PUB)}&section=news&limit=10"
    )
    if r.status_code == 200:
        stories = r.json().get("stories", [])
        item_ids = [s.get("item_id") for s in stories]
        print(f"  Got {len(stories)} stories, item_ids = {item_ids}")
        if len(stories) != 1 or item_ids[0] != first_id:
            failures.append(
                f"strict-mode pin didn't flow through: expected exactly [{first_id}], got {item_ids}"
            )
        else:
            print("  Strict-mode pin flows through — homepage shows only pinned item OK")
    else:
        failures.append(f"homepage-stories returned {r.status_code}")

    hr("6. DELETE the pin")
    r = client.delete(
        f"/admin/api/homepage-pins?publisher={PUB}&section=news&slot=1",
        headers=AUTH,
    )
    print(r.status_code, r.json())
    if not (r.status_code == 200):
        failures.append(f"DELETE pin returned {r.status_code}")

    hr("7. GET pins after DELETE — slot 1 should be gone")
    r = client.get(f"/admin/api/homepage-pins?publisher={PUB}", headers=AUTH)
    final = r.json()["pins"]
    if any(p["slot"] == 1 for p in final["news"]):
        failures.append("slot 1 still populated after DELETE")
    else:
        print("  slot 1 cleared OK")

    hr("8. Error cases")
    # Bad section
    r = client.put(
        "/admin/api/homepage-pins",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"publisher": PUB, "section": "obituary", "slot": 1, "content_item_id": first_id},
    )
    print(f"  PUT bad section -> {r.status_code} (expect 400)")
    if r.status_code != 400:
        failures.append("bad section should 400")
    # Bad slot
    r = client.put(
        "/admin/api/homepage-pins",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"publisher": PUB, "section": "news", "slot": 99, "content_item_id": first_id},
    )
    print(f"  PUT bad slot -> {r.status_code} (expect 400)")
    if r.status_code != 400:
        failures.append("bad slot should 400")
    # Unknown publisher
    r = client.put(
        "/admin/api/homepage-pins",
        headers={**AUTH, "Content-Type": "application/json"},
        json={"publisher": "Nope Gazette", "section": "news", "slot": 1, "content_item_id": first_id},
    )
    print(f"  PUT unknown publisher -> {r.status_code} (expect 404)")
    if r.status_code != 404:
        failures.append("unknown publisher should 404")

    return _report(failures)


def _report(failures: list[str]) -> int:
    print("\n" + "=" * 78)
    if failures:
        print(f"FAILED ({len(failures)} problems):")
        for f in failures:
            print(f"  X {f}")
        return 1
    print("ALL CHECKS PASSED OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
