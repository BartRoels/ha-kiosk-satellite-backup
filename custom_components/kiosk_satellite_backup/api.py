"""Minimal client for the Kiosk Satellite remote admin API.

Security model
--------------
Kiosk Satellite serves its admin API over HTTPS with a self-signed certificate,
so normal CA verification cannot be used. Instead the certificate is pinned
(trust on first use): every request only proceeds over a TLS connection whose
certificate matches the stored SHA-256 fingerprint (``aiohttp.Fingerprint``),
so the bearer token is never sent to anything else.

The kiosk renews its certificate yearly *with the same private key*. When the
certificate fingerprint changes but the public key (SPKI) is unchanged, the new
certificate is accepted automatically; the TLS handshake proves the server holds
that same key. A different key is refused until the user re-authenticates.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass
import hashlib
import json
from typing import Any

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from yarl import URL

from homeassistant.util.ssl import get_default_no_verify_context

from .const import DEFAULT_PORT, TOKEN_TTL_DAYS

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
CERT_FETCH_TIMEOUT = 10


class KioskSatelliteError(Exception):
    """Base error for the Kiosk Satellite API."""


class KioskSatelliteConnectionError(KioskSatelliteError):
    """The kiosk could not be reached."""


class KioskSatelliteAuthError(KioskSatelliteError):
    """The password or token was rejected."""


class KioskSatelliteResponseError(KioskSatelliteError):
    """The kiosk answered with something unexpected."""


class KioskSatelliteInsecureUrlError(KioskSatelliteError):
    """Plain HTTP would expose the password and token."""


class KioskSatelliteCertificateError(KioskSatelliteError):
    """The kiosk presented a certificate with a different key than the pinned one."""

    def __init__(self, message: str, presented: CertPin | None = None) -> None:
        """Keep the presented certificate so the user can review it."""
        super().__init__(message)
        self.presented = presented


@dataclass(frozen=True, slots=True)
class CertPin:
    """Pinned identity of a kiosk's TLS certificate."""

    cert_sha256: str  # hex, what the kiosk shows under Device > TLS
    spki_sha256: str  # hex, stable across renewals with the same key

    @classmethod
    def from_der(cls, der: bytes) -> CertPin:
        """Build a pin from a DER certificate."""
        spki = (
            x509.load_der_x509_certificate(der)
            .public_key()
            .public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        return cls(hashlib.sha256(der).hexdigest(), hashlib.sha256(spki).hexdigest())

    @property
    def display(self) -> str:
        """Colon-separated uppercase fingerprint for comparing on the kiosk."""
        raw = self.cert_sha256.upper()
        return ":".join(raw[i : i + 2] for i in range(0, len(raw), 2))


def normalize_url(url: str) -> str:
    """Return the base admin URL (scheme://host:port) without path or trailing slash."""
    url = url.strip()
    if "://" not in url:
        url = f"https://{url}"
    parsed = URL(url)
    if not parsed.host:
        raise ValueError(f"No host in {url!r}")
    port = parsed.explicit_port or DEFAULT_PORT
    return str(URL.build(scheme=parsed.scheme.lower(), host=parsed.host, port=port))


def require_https(url: str) -> None:
    """Refuse plain HTTP: it would send the admin password and token in clear text."""
    if URL(url).scheme != "https":
        raise KioskSatelliteInsecureUrlError(
            "Plain HTTP is not supported; turn on 'Use HTTPS' on the kiosk"
        )


async def async_fetch_certificate(url: str) -> CertPin:
    """Connect once and return the pin of the certificate the kiosk presents."""
    parsed = URL(url)
    ssl_context = get_default_no_verify_context()
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(parsed.host, parsed.port, ssl=ssl_context),
            CERT_FETCH_TIMEOUT,
        )
    except (OSError, TimeoutError) as err:
        raise KioskSatelliteConnectionError(
            f"Cannot reach {parsed.host}:{parsed.port}: {err or type(err).__name__}"
        ) from err
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
    if not der:
        raise KioskSatelliteCertificateError("The kiosk did not present a certificate")
    return CertPin.from_der(der)


class KioskSatelliteApi:
    """Talks to one kiosk's embedded admin server (default port 2324)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        token: str | None = None,
        *,
        pin: CertPin | None = None,
        verify_ssl: bool = False,
        on_pin_renewed: Callable[[CertPin], None] | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.url = normalize_url(url)
        self.token = token
        self.pin = pin
        self._verify_ssl = verify_ssl
        self._on_pin_renewed = on_pin_renewed

    def _ssl_param(self) -> aiohttp.Fingerprint | None:
        require_https(self.url)
        if self._verify_ssl:
            return None  # normal CA + hostname verification by the session
        if self.pin is None:
            raise KioskSatelliteCertificateError("No certificate pinned for this kiosk")
        return aiohttp.Fingerprint(bytes.fromhex(self.pin.cert_sha256))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        data: bytes | None = None,
        params: dict[str, str] | None = None,
        auth: bool = True,
        _retry: bool = True,
    ) -> bytes:
        headers: dict[str, str] = {}
        if auth:
            if not self.token:
                raise KioskSatelliteAuthError("No token available")
            headers["Authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["Content-Type"] = "application/json"
        ssl_param = self._ssl_param()
        try:
            async with self._session.request(
                method,
                f"{self.url}{path}",
                json=json_body,
                data=data,
                params=params,
                headers=headers,
                ssl=ssl_param if ssl_param is not None else True,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                body = await resp.read()
                if resp.status in (401, 403):
                    raise KioskSatelliteAuthError(
                        f"{method} {path} rejected with HTTP {resp.status}"
                    )
                if resp.status >= 300:
                    # Never echo the response body: it could end up in sensor attributes.
                    raise KioskSatelliteResponseError(
                        f"{method} {path} failed with HTTP {resp.status}"
                    )
                return body
        except aiohttp.ServerFingerprintMismatch as err:
            presented = await async_fetch_certificate(self.url)
            if self.pin is not None and presented.spki_sha256 == self.pin.spki_sha256:
                # Same key, renewed certificate: accept and retry once.
                self.pin = presented
                if self._on_pin_renewed:
                    self._on_pin_renewed(presented)
                if _retry:
                    return await self._request(
                        method,
                        path,
                        json_body=json_body,
                        data=data,
                        params=params,
                        auth=auth,
                        _retry=False,
                    )
            raise KioskSatelliteCertificateError(
                "The kiosk presented a certificate with a different key; "
                "re-authenticate to trust it",
                presented,
            ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise KioskSatelliteConnectionError(
                f"Cannot reach {self.url}: {type(err).__name__}"
            ) from err

    async def async_login(self, password: str) -> str:
        """Exchange the admin password for a token (valid TOKEN_TTL_DAYS days)."""
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
