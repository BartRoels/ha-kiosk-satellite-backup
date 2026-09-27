"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kiosk_satellite_backup.const import (
    CONF_DEVICE_ID,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_TOKEN,
    CONF_URL,
    DOMAIN,
)

from .conftest import TOKEN, URL


def _mock_login_ok(aioclient_mock: AiohttpClientMocker, url: str = URL) -> None:
    aioclient_mock.post(f"{url}/api/login", json={"token": TOKEN})
    aioclient_mock.get(f"{url}/api/info", json={"appVersion": "2026.9.82"})


async def test_manual_flow(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """No device picked: name + URL + password."""
    _mock_login_ok(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "credentials"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "T65 - Dining Room",
            "url": "192.0.2.10:2324/",
            "password": "pw",
            "verify_ssl": False,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "T65 - Dining Room"
    assert result["data"][CONF_URL] == URL
    assert result["data"][CONF_TOKEN] == TOKEN
    assert "password" not in result["data"]
    assert result["options"] == {CONF_KEEP: 8}
    login_call = aioclient_mock.mock_calls[0]
    assert login_call[2] == {"password": "pw", "ttl_days": 3650}


async def test_device_flow_uses_remote_admin_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Picking the ESPHome device pre-fills the URL and links the entities."""
    esphome_entry = MockConfigEntry(domain="esphome", title="T65 - Dining Room")
    esphome_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=esphome_entry.entry_id,
        connections={(dr.CONNECTION_NETWORK_MAC, "02:00:00:00:00:01")},
        name="T65 - Dining Room",
        manufacturer="kiosk_satellite",
        model="bluetooth_proxy",
    )
    er.async_get(hass).async_get_or_create(
        "sensor",
        "esphome",
        "remote_admin",
        suggested_object_id="t65_dining_room_remote_admin",
        device_id=device.id,
        config_entry=esphome_entry,
        original_name="Remote admin",
    )
    hass.states.async_set("sensor.t65_dining_room_remote_admin", URL)
    _mock_login_ok(aioclient_mock)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device.id}
    )
    assert result["step_id"] == "credentials"
    assert "name" not in result["data_schema"].schema
    url_key = next(k for k in result["data_schema"].schema if k == "url")
    assert url_key.description["suggested_value"] == URL

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": URL, "password": "pw", "verify_ssl": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "T65 - Dining Room"
    assert (
        result["data"][CONF_REMOTE_ADMIN_ENTITY]
        == "sensor.t65_dining_room_remote_admin"
    )
    entry = result["result"]
    assert entry.unique_id == device.id
    await hass.async_block_till_done()

    # Our entities landed on the kiosk's existing ESPHome device.
    ent_reg = er.async_get(hass)
    button = ent_reg.async_get("button.t65_dining_room_backup_configuration")
    assert button is not None
    assert button.device_id == device.id
    assert (
        ent_reg.async_get("sensor.t65_dining_room_last_backup").device_id == device.id
    )

    # Same device again is refused.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_DEVICE_ID: device.id}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_invalid_auth_and_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Errors are shown on the form."""
    aioclient_mock.post(f"{URL}/api/login", status=401)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    user = {"name": "x", "url": URL, "password": "bad", "verify_ssl": False}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "invalid_auth"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{URL}/api/login", exc=TimeoutError())
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """Reauth stores a new token."""
    aioclient_mock.post(f"{URL}/api/login", json={"token": "new-token"})
    aioclient_mock.get(f"{URL}/api/info", json={})
    result = await mock_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "pw"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_entry.data[CONF_TOKEN] == "new-token"


async def test_options(hass: HomeAssistant, mock_entry) -> None:
    """Retention can be changed."""
    await hass.config_entries.async_setup(mock_entry.entry_id)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_KEEP: 12}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options[CONF_KEEP] == 12
