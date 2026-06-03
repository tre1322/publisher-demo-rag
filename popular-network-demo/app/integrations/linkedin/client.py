"""Thin sync wrapper over the LinkedIn REST + v2 APIs.

Sync (not async) to match this codebase — every FastAPI route handler here is
`def`, not `async def`. Accepts an optional injected httpx.Client so smokes can
drive it through a MockTransport.

Header contract (LinkedIn Marketing API v2):
  Authorization: Bearer <token>
  X-Restli-Protocol-Version: 2.0.0
  LinkedIn-Version: <YYYYMM>        (versioned /rest/* endpoints only)

The legacy /v2/* endpoints (e.g. /v2/userinfo) are NOT versioned — pass
versioned=False for those.
"""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .config import API_BASE, REST_VERSION
from .errors import LinkedInAPIError

_TIMEOUT = httpx.Timeout(20.0)


class LinkedInClient:
    def __init__(self, access_token: str, *, http: Optional[httpx.Client] = None) -> None:
        self._token = access_token
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._owns = http is None

    # Context-manager sugar so callers can `with LinkedInClient(tok) as c:`.
    def __enter__(self) -> "LinkedInClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns:
            self._http.close()

    def _headers(self, versioned: bool) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {self._token}",
            "X-Restli-Protocol-Version": "2.0.0",
        }
        if versioned:
            h["LinkedIn-Version"] = REST_VERSION
        return h

    def get(self, path: str, *, params: Optional[dict] = None, versioned: bool = True) -> Any:
        resp = self._http.get(f"{API_BASE}{path}", params=params, headers=self._headers(versioned))
        return self._handle(resp)

    def post(
        self,
        path: str,
        *,
        json: Optional[dict] = None,
        headers: Optional[dict] = None,
        versioned: bool = True,
    ) -> Any:
        h = self._headers(versioned)
        if headers:
            h.update(headers)
        resp = self._http.post(f"{API_BASE}{path}", json=json, headers=h)
        return self._handle(resp)

    def create(self, path: str, *, json: dict, urn_prefix: str, headers: Optional[dict] = None) -> str:
        """POST a new entity and return its URN.

        Rest.li convention: a successful create returns 201 with the new id in
        the `x-restli-id` (or `x-linkedin-id`) response header, not the body.
        We assemble the full URN as `<urn_prefix>:<id>` (e.g.
        urn:li:sponsoredCampaign:1234567) for persistence.
        """
        h = self._headers(True)
        if headers:
            h.update(headers)
        resp = self._http.post(f"{API_BASE}{path}", json=json, headers=h)
        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise LinkedInAPIError(resp.status_code, "LinkedIn create failed", body)
        created_id = resp.headers.get("x-restli-id") or resp.headers.get("x-linkedin-id")
        if not created_id:
            # A few endpoints echo the entity (with an `id`) in the body instead.
            try:
                created_id = str(resp.json().get("id") or "")
            except Exception:
                created_id = ""
        if not created_id:
            raise LinkedInAPIError(resp.status_code, "create returned no entity id", resp.text)
        return f"{urn_prefix}:{created_id}"

    def _handle(self, resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            body: Any
            try:
                body = resp.json()
            except Exception:
                body = resp.text
            raise LinkedInAPIError(resp.status_code, "LinkedIn API error", body)
        if resp.status_code == 204 or not resp.content:
            return {}
        try:
            return resp.json()
        except Exception:
            return {"raw": resp.text}

    # ----- convenience reads used by the OAuth callback + status endpoint -----

    def me(self) -> dict[str, str]:
        """Authed member's display name, for the 'Connected as ...' UI.

        Uses the OpenID /v2/userinfo shape (name / given_name / family_name).
        Falls back to the legacy localizedFirstName fields if a profile-only
        scope returns the /v2/me shape instead.
        """
        data = self.get("/v2/userinfo", versioned=False)
        name = (
            data.get("name")
            or " ".join(filter(None, [data.get("given_name"), data.get("family_name")]))
            or " ".join(filter(None, [data.get("localizedFirstName"), data.get("localizedLastName")]))
        )
        return {"name": (name or "").strip(), "sub": data.get("sub", "")}

    def primary_ad_account_urn(self) -> Optional[str]:
        """First sponsored-ad account the authed member can manage, or None.

        We persist this URN on the AdConnection so campaign creation has an
        account to bill against without asking the owner to paste an account id.
        """
        data = self.get("/rest/adAccountUsers", params={"q": "authenticatedUser"})
        elements = data.get("elements", [])
        if not elements:
            return None
        # Each element references its account as a sponsoredAccount URN.
        return elements[0].get("account")
