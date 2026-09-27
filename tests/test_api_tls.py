"""Certificate pinning against real TLS servers on 127.0.0.1."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
import ssl

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from custom_components.kiosk_satellite_backup.api import (
    CertPin,
    KioskSatelliteApi,
    KioskSatelliteCertificateError,
    KioskSatelliteInsecureUrlError,
    KioskSatelliteResponseError,
    async_fetch_certificate,
)


@pytest.fixture(autouse=True)
def _allow_local_sockets(socket_enabled):
    """These tests run real TLS servers on 127.0.0.1."""
    return


def _make_cert(key: ec.EllipticCurvePrivateKey, serial: int) -> bytes:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kiosk-test")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


class FakeKiosk:
    """A TLS admin server that records which Authorization headers reached it."""

    def __init__(self) -> None:
        self.auth_headers: list[str | None] = []
        self.url = ""

    async def export(self, request: web.Request) -> web.Response:
        self.auth_headers.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization") != "Bearer good":
            return web.Response(status=401)
        return web.json_response({"deviceName": "Test Kiosk", "settings": {}})

    async def broken(self, request: web.Request) -> web.Response:
        return web.Response(status=500, text="SECRET internal details")


@pytest.fixture
def keys():
    """Two different private keys."""
    return ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(
        ec.SECP256R1()
    )


async def _serve(
    tmp_path, cert_pem: bytes, key_pem: bytes, name: str
) -> tuple[FakeKiosk, web.AppRunner]:
    cert_file = tmp_path / f"{name}.crt"
    key_file = tmp_path / f"{name}.key"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_file, key_file)
    kiosk = FakeKiosk()
    app = web.Application()
    app.router.add_get("/api/config/export", kiosk.export)
    app.router.add_get("/api/broken", kiosk.broken)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=ctx)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    kiosk.url = f"https://127.0.0.1:{port}"
    return kiosk, runner


@pytest.fixture
async def servers(tmp_path, keys) -> AsyncIterator[dict]:
    """original (key A), renewed (key A, new cert) and impostor (key B)."""
    key_a, key_b = keys
    runners = []
    out = {}
    for name, key, serial in (
        ("original", key_a, 1),
        ("renewed", key_a, 2),
        ("impostor", key_b, 3),
    ):
        kiosk, runner = await _serve(
            tmp_path, _make_cert(key, serial), _key_pem(key), name
        )
        out[name] = kiosk
        runners.append(runner)
    yield out
    for runner in runners:
        await runner.cleanup()


async def test_pinned_request_and_mismatch(servers) -> None:
    """Token reaches the pinned kiosk; a different key never sees it; renewals are accepted."""
    original, renewed, impostor = (
        servers["original"],
        servers["renewed"],
        servers["impostor"],
    )
    pin = await async_fetch_certificate(original.url)
    renewed_pin = await async_fetch_certificate(renewed.url)
    impostor_pin = await async_fetch_certificate(impostor.url)
    assert pin.spki_sha256 == renewed_pin.spki_sha256
    assert pin.cert_sha256 != renewed_pin.cert_sha256
    assert impostor_pin.spki_sha256 != pin.spki_sha256

    renewed_seen: list[CertPin] = []
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=False)
    ) as session:
        # 1. Pinned kiosk: works.
        api = KioskSatelliteApi(session, original.url, "good", pin=pin)
        assert (await api.async_export()).startswith(b"{")
        assert original.auth_headers == ["Bearer good"]

        # 2. Impostor with another key (e.g. an attacker taking over the IP / DNS / sensor URL).
        api = KioskSatelliteApi(session, impostor.url, "good", pin=pin)
        with pytest.raises(KioskSatelliteCertificateError) as err:
            await api.async_export()
        assert err.value.presented == impostor_pin
        assert impostor.auth_headers == []  # the token never left

        # 3. Same key, renewed certificate: accepted and re-pinned.
        api = KioskSatelliteApi(
            session, renewed.url, "good", pin=pin, on_pin_renewed=renewed_seen.append
        )
        assert (await api.async_export()).startswith(b"{")
        assert renewed_seen == [renewed_pin]
        assert api.pin == renewed_pin
        assert renewed.auth_headers == ["Bearer good"]


async def test_http_refused_and_body_not_echoed(servers) -> None:
    """Plain HTTP is refused before connecting; error bodies are not echoed."""
    original = servers["original"]
    pin = await async_fetch_certificate(original.url)
    async with aiohttp.ClientSession() as session:
        api = KioskSatelliteApi(
            session, original.url.replace("https", "http"), "good", pin=pin
        )
        with pytest.raises(KioskSatelliteInsecureUrlError):
            await api.async_export()
        api = KioskSatelliteApi(session, original.url, "good", pin=pin)
        with pytest.raises(KioskSatelliteResponseError) as err:
            await api._request("GET", "/api/broken")
        assert "SECRET" not in str(err.value)
        assert "HTTP 500" in str(err.value)
