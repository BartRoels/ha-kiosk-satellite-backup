"""Fixtures. All addresses and identifiers here are fake (RFC 5737 / locally administered)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    CONF_VERIFY_SSL,
    DOMAIN,
)

URL = "https://192.0.2.10:2324"
TOKEN = "test-token"
MAC = "02:00:00:00:00:01"
PIN = CertPin("aa" * 32, "bb" * 32)
EXPORT = b'{"deviceName":"Test Kiosk","exportedAt":"2026-09-27T03:07:00Z","settings":{"screensaver.mode":"black"}}'


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations."""
    return


@pytest.fixture(autouse=True)
def config_dir(hass: HomeAssistant, tmp_path):
    """Write backups into a temp dir."""
    hass.config.config_dir = str(tmp_path)
    return tmp_path


@pytest.fixture(autouse=True)
def mock_fetch_certificate():
    """No real TLS in the HA-level tests: the kiosk always presents PIN."""
    with (
        patch(
            "custom_components.kiosk_satellite_backup.config_flow.async_fetch_certificate",
            return_value=PIN,
        ) as flow_mock,
        patch(
            "custom_components.kiosk_satellite_backup.manager.async_fetch_certificate",
            return_value=PIN,
        ),
    ):
        yield flow_mock


@pytest.fixture
def mock_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A manually configured, pinned kiosk."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Kiosk",
        unique_id="192.0.2.10:2324",
        data={
            CONF_DEVICE_ID: None,
            CONF_REMOTE_ADMIN_ENTITY: None,
            CONF_URL: URL,
            CONF_TOKEN: TOKEN,
            CONF_TOKEN_ISSUED: "2026-09-01T00:00:00+00:00",
            CONF_VERIFY_SSL: False,
            CONF_CERT_SHA256: PIN.cert_sha256,
            CONF_SPKI_SHA256: PIN.spki_sha256,
        },
        options={CONF_KEEP: 2},
    )
    entry.add_to_hass(hass)
    return entry
