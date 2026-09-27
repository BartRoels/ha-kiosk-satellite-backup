"""Fixtures."""

from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kiosk_satellite_backup.const import (
    CONF_DEVICE_ID,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_TOKEN,
    CONF_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
)

URL = "https://192.0.2.10:2324"
TOKEN = "long-lived-token"
EXPORT = b'{"deviceName":"T65 - Dining Room","exportedAt":"2026-09-27T03:07:00Z","settings":{"screensaver.mode":"black"}}'


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
def config_dir(hass: HomeAssistant, tmp_path):
    """Write backups into a temp dir."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A manually configured kiosk."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="T65 - Dining Room",
        unique_id="192.0.2.10:2324",
        data={
            CONF_DEVICE_ID: None,
            CONF_REMOTE_ADMIN_ENTITY: None,
            CONF_URL: URL,
            CONF_TOKEN: TOKEN,
            CONF_VERIFY_SSL: False,
        },
        options={CONF_KEEP: 2},
    )
    entry.add_to_hass(hass)
    return entry
