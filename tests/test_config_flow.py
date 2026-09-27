"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kiosk_satellite_backup.api import CertPin
from custom_components.kiosk_satellite_backup.const import (
    CONF_CERT_SHA256,
    CONF_DEVICE_ID,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_SPKI_SHA256,
    CONF_TOKEN,
    CONF_TOKEN_ISSUED,
    CONF_URL,
    DOMAIN,
)

from .conftest import MAC, PIN, TOKEN, URL


def _mock_login_ok(aioclient_mock: AiohttpClientMocker, url: str = URL) -> None:
    aioclient_mock.post(f"{url}/api/login", json={"token": TOKEN})
    aioclient_mock.get(f"{url}/api/info", json={"appVersion": "2026.9.82"})


async def _start(hass: HomeAssistant, device_id: str | None = None):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device_id} if device_id else {}
    )


async def test_manual_flow_shows_fingerprint_before_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No device picked: name + URL, then fingerprint + password."""
    _mock_login_ok(aioclient_mock)
    result = await _start(hass)
    assert result["step_id"] == "connect"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Test Kiosk", "url": "192.0.2.10:2324/", "verify_ssl": False},
    )
    # Nothing has been sent to the kiosk yet; the user sees the fingerprint first.
    assert result["step_id"] == "credentials"
    assert aioclient_mock.call_count == 0
    assert result["description_placeholders"]["fingerprint"] == PIN.display

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Kiosk"
    data = result["data"]
    assert data[CONF_URL] == URL
    assert data[CONF_TOKEN] == TOKEN
    assert data[CONF_CERT_SHA256] == PIN.cert_sha256
    assert data[CONF_SPKI_SHA256] == PIN.spki_sha256
    assert data[CONF_TOKEN_ISSUED]
    assert "password" not in data
    assert result["options"] == {CONF_KEEP: 8}
    login_call = aioclient_mock.mock_calls[0]
    assert login_call[2] == {"password": "pw", "ttl_days": 365}


async def test_http_is_refused(hass: HomeAssistant, aioclient_mock) -> None:
    """Plain HTTP would leak the password: refused before anything is sent."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "x", "url": "http://192.0.2.10:2324", "verify_ssl": False},
    )
    assert result["errors"] == {"base": "https_required"}
    assert aioclient_mock.call_count == 0


async def test_device_flow_uses_remote_admin_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Picking the ESPHome device pre-fills the URL and links the entities."""
    esphome_entry = MockConfigEntry(domain="esphome", title="Test Kiosk")
    esphome_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=esphome_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, MAC)},
        name="Test Kiosk",
        manufacturer="kiosk_satellite",
        model="bluetooth_proxy",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "esphome",
        "remote_admin",
        suggested_object_id="test_kiosk_remote_admin",
        device_id=device.id,
        config_entry=esphome_entry,
        original_name="Remote admin",
    )
    hass.states.async_set("sensor.test_kiosk_remote_admin", URL)
    _mock_login_ok(aioclient_mock)

    result = await _start(hass, device.id)
    assert result["step_id"] == "connect"
    assert "name" not in result["data_schema"].schema
    url_key = next(k for k in result["data_schema"].schema if k == "url")
    assert url_key.description["suggested_value"] == URL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "verify_ssl": False}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "pw"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Kiosk"
    assert result["data"][CONF_REMOTE_ADMIN_ENTITY] == "sensor.test_kiosk_remote_admin"
    entry = result["result"]
    assert entry.unique_id == device.id
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    button = ent_reg.async_get("button.test_kiosk_backup_configuration")
    assert button is not None
    assert button.device_id == device.id
    assert ent_reg.async_get("sensor.test_kiosk_last_backup").device_id == device.id

    result = await _start(hass, device.id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_invalid_auth_and_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_fetch_certificate
) -> None:
    """Errors are shown on the forms."""
    aioclient_mock.post(f"{URL}/api/login", status=401)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "url": URL, "verify_ssl": False}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "bad"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{URL}/api/login", exc=TimeoutError())
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "pw"}
    )
    assert result["errors"] == {"base": "cannot_connect"}

    # Unreachable when fetching the certificate.
    from custom_components.kiosk_satellite_backup.api import (
        KioskSatelliteConnectionError,
    )

    mock_fetch_certificate.side_effect = KioskSatelliteConnectionError("down")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "y", "url": "https://192.0.2.11:2324", "verify_ssl": False},
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_warns_on_new_key_and_stores_new_pin(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_entry,
    mock_fetch_certificate,
) -> None:
    """Reauth shows a warning when the key changed, then trusts the new certificate."""
    new_pin = CertPin("cc" * 32, "dd" * 32)
    mock_fetch_certificate.return_value = new_pin
    aioclient_mock.post(f"{URL}/api/login", json={"token": "new-token"})
    aioclient_mock.get(f"{URL}/api/info", json={})
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["warning"].startswith("⚠️")
    assert result["description_placeholders"]["fingerprint"] == new_pin.display
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_TOKEN] == "new-token"
    assert mock_entry.data[CONF_SPKI_SHA256] == new_pin.spki_sha256


async def test_reauth_same_key_no_warning(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """Same certificate: plain token renewal without a warning."""
    aioclient_mock.post(f"{URL}/api/login", json={"token": "new-token"})
    aioclient_mock.get(f"{URL}/api/info", json={})
    result = await mock_entry.start_reauth_flow(hass)
    assert result["description_placeholders"]["warning"] == ""


async def test_options(hass: HomeAssistant, mock_entry) -> None:
    """Retention can be changed."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_KEEP: 12}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[CONF_KEEP] == 12
