"""Minimal client for the Kiosk Satellite remote admin API."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import aiohttp

from .const import TOKEN_TTL_DAYS

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class KioskSatelliteError(Exception):
    """Base error for the Kiosk Satellite API."""


class KioskSatelliteConnectionError(KioskSatelliteError):
    """The kiosk could not be reached."""


class KioskSatelliteAuthError(KioskSatelliteError):
    """The password or token was rejected."""


class KioskSatelliteResponseError(KioskSatelliteError):
    """The kiosk answered with something unexpected."""


def normalize_url(url: str) -> str:
    """Return the base admin URL without a trailing slash or path."""
    url = url.strip().rstrip("/")
    if "://" not in url:
        url = f"https://{url}"
    return url


class KioskSatelliteApi:
    """Talks to one kiosk's embedded admin server (default port 2324)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.url = normalize_url(url)
        self.token = token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: bytes | None = None,
        params: dict[str, str] | None = None,
        auth: bool = True,
    ) -> bytes:
        headers: dict[str, str] = {}
        if auth:
            if not self.token:
                raise KioskSatelliteAuthError("No token available")
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                json=json_body,
                data=data,
                params=params,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                body = await resp.read()
                if resp.status in (401, 403):
                    raise KioskSatelliteAuthError(
                        f"{method} {path} rejected with HTTP {resp.status}"
                    )
                if resp.status >= 400:
                    raise KioskSatelliteResponseError(
                        f"{method} {path} failed with HTTP {resp.status}: "
                        f"{body[:200].decode(errors='replace')}"
                    )
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise KioskSatelliteConnectionError(
                f"Cannot reach {self.url}: {err or type(err).__name__}"
            ) from err

    async def async_login(self, password: str) -> str:
        """Exchange the admin password for a long-lived token."""
        body = await self._request(
            "POST",
            "/api/login",
            json_body={"password": password, "ttl_days": TOKEN_TTL_DAYS},
            auth=False,
        )
        try:
            token = json.loads(body)["token"]
        except (ValueError, KeyError, TypeError) as err:
            raise KioskSatelliteResponseError("Login response had no token") from err
        if not isinstance(token, str) or not token:
            raise KioskSatelliteResponseError("Login response had an empty token")
        self.token = token
        return token

    async def async_info(self) -> dict[str, Any]:
        """Return device info (also validates the token)."""
        body = await self._request("GET", "/api/info")
        try:
            return json.loads(body)
        except ValueError as err:
            raise KioskSatelliteResponseError("Info response was not JSON") from err

    async def async_export(self) -> bytes:
        """Return the full backup (all settings incl. secrets + localStorage)."""
        body = await self._request("GET", "/api/config/export")
        try:
            parsed = json.loads(body)
        except ValueError as err:
            raise KioskSatelliteResponseError("Export was not valid JSON") from err
        if not isinstance(parsed, dict) or not parsed:
            raise KioskSatelliteResponseError("Export was empty")
        return body

    async def async_import(
        self,
        backup: bytes,
        *,
        adopt_identity: bool = True,
        import_local_storage: bool = True,
    ) -> None:
        """Apply a full backup produced by async_export."""
        await self._request(
            "POST",
            "/api/config/import",
            data=backup,
            params={
                "adoptIdentity": "1" if adopt_identity else "0",
                "importLocalStorage": "1" if import_local_storage else "0",
            },
        )
