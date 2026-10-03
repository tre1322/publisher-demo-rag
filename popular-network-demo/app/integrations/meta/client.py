"""Thin sync wrapper over the Graph API for the Marketing API calls we make.

Every call carries the system user's token and `appsecret_proof` (the token
signed with the app secret, HMAC-SHA256, hex), so a leaked token alone can't
be replayed from somewhere else once the app has "Require app secret" on.

Errors come back as one JSON shape:
  {"error": {"message", "type", "code", "error_subcode", "error_user_title",
             "error_user_msg", "fbtrace_id"}}
MetaAPIError keeps those fields and sorts them into a `kind` the callers
branch on (auth / rate / permission / other).

`TRANSPORT` lets smokes run every call through an httpx.MockTransport.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Optional

import httpx

from .config import MetaConfig, get_config

_TIMEOUT = httpx.Timeout(30.0)
TRANSPORT: Optional[httpx.BaseTransport] = None   # smokes only

# Codes from Meta's error reference. 80000-80014 are the per-business
# ("business use case") Marketing API limits.
_RATE_CODES = {4, 17, 32, 613} | set(range(80000, 80015))
_AUTH_CODES = {102, 190}
_MAX_PAGES = 25


class MetaError(Exception):
    """Base for this package. Message is safe to show an owner."""


class MetaNotConfigured(MetaError):
    """A live call without META_ACCESS_TOKEN / META_APP_SECRET."""


class MetaAPIError(MetaError):
    def __init__(self, status: int, err: dict[str, Any]):
        self.status = status
        self.code = _int(err.get("code"))
        self.subcode = _int(err.get("error_subcode"))
        self.fbtrace_id = err.get("fbtrace_id")
        self.raw_message = str(err.get("message") or f"HTTP {status}")
        self.user_message = err.get("error_user_msg") or err.get("error_user_title")
        super().__init__(self._owner_message())

    @property
    def kind(self) -> str:
        if self.code in _AUTH_CODES:
            return "auth"
        if self.code in _RATE_CODES:
            return "rate"
        if self.code == 10 or (self.code is not None and 200 <= self.code <= 299):
            return "permission"
        return "other"

    def _owner_message(self) -> str:
        if self.kind == "auth":
            return "Amplafai's Meta connection needs to be renewed (the access token was refused)."
        if self.kind == "rate":
            return "Meta is limiting how fast Amplafai can make changes; try again in a few minutes."
        if self.kind == "permission":
            return ("Amplafai doesn't have permission for that on this ad account. Check that Amplafai's "
                    "business is a partner on it with permission to manage campaigns.")
        text = self.user_message or self.raw_message
        return f"{text} (code {self.code})" if self.code is not None else text


def _int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def appsecret_proof(token: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def _form(data: dict[str, Any]) -> dict[str, str]:
    """Graph takes form fields; nested values (targeting, lists) go as JSON."""
    out: dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        if isinstance(v, (dict, list, tuple)):
            out[k] = json.dumps(v, separators=(",", ":"))
        elif isinstance(v, bool):
            out[k] = "true" if v else "false"
        else:
            out[k] = str(v)
    return out


class MetaClient:
    def __init__(self, cfg: Optional[MetaConfig] = None, *, http: Optional[httpx.Client] = None):
        self._cfg = cfg or get_config()
        if not self._cfg.is_live:
            raise MetaNotConfigured("Meta's API isn't switched on yet.")
        self._owns = http is None
        self._http = http or httpx.Client(timeout=_TIMEOUT, transport=TRANSPORT)
        self.calls = 0

    def __enter__(self) -> "MetaClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns:
            self._http.close()
            self._owns = False

    def _auth(self) -> dict[str, str]:
        return {"access_token": self._cfg.access_token,
                "appsecret_proof": appsecret_proof(self._cfg.access_token, self._cfg.app_secret)}

    def _send(self, method: str, path: str, *, params: Optional[dict] = None,
              data: Optional[dict] = None) -> dict[str, Any]:
        url = f"{self._cfg.base_url}/{path.lstrip('/')}"
        self.calls += 1
        try:
            if method == "GET":
                resp = self._http.get(url, params={**_form(params or {}), **self._auth()})
            else:
                resp = self._http.post(url, data={**_form(data or {}), **self._auth()})
        except httpx.HTTPError as e:
            raise MetaError(f"Couldn't reach Meta ({type(e).__name__}).") from e
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or (isinstance(body, dict) and "error" in body):
            err = body.get("error") if isinstance(body, dict) else None
            raise MetaAPIError(resp.status_code, err if isinstance(err, dict) else {})
        return body if isinstance(body, dict) else {"data": body}

    def get(self, path: str, params: Optional[dict] = None) -> dict[str, Any]:
        return self._send("GET", path, params=params)

    def post(self, path: str, data: Optional[dict] = None) -> dict[str, Any]:
        return self._send("POST", path, data=data)

    def get_all(self, path: str, params: Optional[dict] = None) -> list[dict[str, Any]]:
        """Every row of a list edge, following the `after` cursor."""
        params = dict(params or {})
        rows: list[dict[str, Any]] = []
        for _ in range(_MAX_PAGES):
            page = self.get(path, params)
            rows.extend(page.get("data") or [])
            paging = page.get("paging") or {}
            after = (paging.get("cursors") or {}).get("after")
            if not paging.get("next") or not after:
                return rows
            params["after"] = after
        raise MetaError("Meta returned more pages than expected; stopped reading.")
