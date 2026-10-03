"""Phase 5b — publish approved posts through Ayrshare (decision 4: a posting
service that already has Facebook, Instagram and Google approval).

Each client business gets its own Ayrshare "profile". The owner links their
Facebook Page, Instagram account and Google Business Profile once, on
Ayrshare's hosted page; after that, approved posts publish at their planned
time without copy and paste.

Dormant until AYRSHARE_API_KEY is set (the primary account's key, from the
Ayrshare dashboard). Docs used (checked Oct 2026):
  auth            Authorization: Bearer <key>; Profile-Key: <profile key>
  POST /profiles               {title} -> {profileKey, refId}  (key shown once)
  POST /profiles/link-sessions -> {url, sessionId, expiresAt}  (open in a new tab)
  GET  /user                   -> {activeSocialAccounts, displayNames[]}
  POST /post {post, platforms, mediaUrls} -> {status, errors[], postIds[{platform, id, postUrl}], id}
Rate limit: 300 requests / 5 minutes per profile; a profile with 1,000 429s
in a day is suspended, so this client never retries on 429.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

log = logging.getLogger("popular_network.posting_service")

API_BASE = "https://api.ayrshare.com/api"
ENV_KEY = "AYRSHARE_API_KEY"
TIMEOUT = 30.0

# Dashboard channel keys → Ayrshare platform names. The website ("web") has
# no posting API; it stays copy-and-paste.
PLATFORM_MAP = {"fb": "facebook", "ig": "instagram", "gbp": "gmb"}
LABELS = {"facebook": "Facebook", "instagram": "Instagram", "gmb": "Google Business Profile"}


class PostingError(Exception):
    """Ayrshare refused or couldn't be reached. Message is owner-safe."""

    def __init__(self, message: str, *, code: Optional[int] = None, platform: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.platform = platform


def is_configured() -> bool:
    return bool(os.getenv(ENV_KEY, "").strip())


class AyrshareClient:
    def __init__(self, api_key: Optional[str] = None, *, http: Optional[httpx.Client] = None):
        key = api_key or os.getenv(ENV_KEY, "").strip()
        if not key:
            raise PostingError("Automatic posting isn't switched on yet.")
        self._key = key
        self._http = http or httpx.Client(timeout=TIMEOUT)

    def close(self) -> None:
        self._http.close()

    def _headers(self, profile_key: Optional[str]) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        if profile_key:
            h["Profile-Key"] = profile_key
        return h

    def _request(self, method: str, path: str, *, profile_key: Optional[str] = None,
                 json: Optional[dict] = None) -> dict[str, Any]:
        try:
            resp = self._http.request(method, f"{API_BASE}{path}", headers=self._headers(profile_key), json=json)
        except httpx.HTTPError as e:
            raise PostingError(f"Couldn't reach the posting service ({type(e).__name__}).") from e
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code == 429:
            raise PostingError("The posting service is rate-limiting this business; it will try again later.", code=429)
        if resp.status_code >= 400 or (isinstance(data, dict) and data.get("status") == "error" and path != "/post"):
            msg = (data.get("message") if isinstance(data, dict) else None) or f"HTTP {resp.status_code}"
            raise PostingError(f"The posting service said: {msg}", code=resp.status_code)
        return data if isinstance(data, dict) else {"data": data}

    # -- profiles and linking ------------------------------------------------
    def create_profile(self, title: str) -> dict[str, str]:
        data = self._request("POST", "/profiles", json={"title": title[:100]})
        if not data.get("profileKey"):
            raise PostingError("The posting service didn't return a profile key.")
        return {"profileKey": data["profileKey"], "refId": data.get("refId", "")}

    def link_session(self, profile_key: str, *, redirect: Optional[str] = None) -> dict[str, Any]:
        body: dict[str, Any] = {"allowedSocial": list(PLATFORM_MAP.values())}
        if redirect:
            body["redirect"] = redirect
        data = self._request("POST", "/profiles/link-sessions", profile_key=profile_key, json=body)
        if not data.get("url"):
            raise PostingError("The posting service didn't return a link page.")
        return {"url": data["url"], "expiresAt": data.get("expiresAt")}

    def linked_accounts(self, profile_key: str) -> list[dict[str, Any]]:
        data = self._request("GET", "/user", profile_key=profile_key)
        names = {d.get("platform"): d for d in data.get("displayNames") or []}
        out = []
        for platform in data.get("activeSocialAccounts") or []:
            d = names.get(platform, {})
            out.append({"platform": platform, "label": LABELS.get(platform, platform),
                        "name": d.get("displayName") or d.get("username") or "",
                        "url": d.get("profileUrl"), "needsReconnect": bool(d.get("refreshRequired"))})
        return out

    # -- publishing ------------------------------------------------------------
    def publish(self, profile_key: str, *, text: str, platforms: list[str],
                media_urls: Optional[list[str]] = None) -> dict[str, Any]:
        """Publish now. Returns {"ok", "id", "posts": [{platform, id, url}], "errors": [{platform, message}]}.
        A partial success (posted to Facebook, Instagram refused) is reported, not raised."""
        body: dict[str, Any] = {"post": text, "platforms": platforms}
        if media_urls:
            body["mediaUrls"] = media_urls
        data = self._request("POST", "/post", profile_key=profile_key, json=body)
        posts = [{"platform": p.get("platform"), "id": p.get("id"), "url": p.get("postUrl")}
                 for p in data.get("postIds") or [] if p.get("status") in (None, "success")]
        errors = [{"platform": e.get("platform"), "message": e.get("message") or "refused", "code": e.get("code")}
                  for e in data.get("errors") or []]
        return {"ok": bool(posts) and not errors, "id": data.get("id"), "posts": posts, "errors": errors}
