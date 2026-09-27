"""Backup, retention, failure handling and restore."""

from __future__ import annotations

import os
import stat

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.kiosk_satellite_backup.const import (
    CONF_REMOTE_ADMIN_ENTITY,
    DOMAIN,
)

from .conftest import EXPORT, TOKEN, URL

BUTTON = "button.t65_dining_room_backup_configuration"
LAST = "sensor.t65_dining_room_last_backup"
STATUS = "sensor.t65_dining_room_backup_status"


async def _setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_button_backup_and_retention(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_entry,
    config_dir,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Button writes a private file, sensors update, old files are pruned."""
    aioclient_mock.get(f"{URL}/api/config/export", content=EXPORT)
    await hass.config.async_set_time_zone("Europe/Brussels")
    freezer.move_to("2026-09-27 01:07:00+00:00")  # 03:07 in Brussels
    await _setup(hass, mock_entry)
    assert hass.states.get(STATUS).state == "never"
    assert hass.states.get(LAST).state == "unknown"

    for _ in range(3):
        await hass.services.async_call(
            "button", "press", {"entity_id": BUTTON}, blocking=True
        )
        freezer.tick(60)

    folder = config_dir / "kiosk_satellite_backups" / "t65_dining_room"
    files = sorted(p.name for p in folder.iterdir())
    assert files == [
        "ks-backup_t65_dining_room_20260927_030800.json",
        "ks-backup_t65_dining_room_20260927_030900.json",
    ]  # keep=2: the 03:07 one was pruned
    newest = folder / files[-1]
    assert newest.read_bytes() == EXPORT
    assert stat.S_IMODE(os.stat(newest).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700

    # Bearer token was sent.
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == f"Bearer {TOKEN}"

    status = hass.states.get(STATUS)
    assert status.state == "ok"
    last = hass.states.get(LAST)
    assert last.attributes["file"] == files[-1]
    assert last.attributes["backups_stored"] == 2
    assert last.attributes["size_bytes"] == len(EXPORT)


async def test_state_restored_from_disk(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry, config_dir
) -> None:
    """After a restart the sensors come back from the files on disk."""
    folder = config_dir / "kiosk_satellite_backups" / "t65_dining_room"
    folder.mkdir(parents=True)
    (folder / "ks-backup_t65_dining_room_20260920_030700.json").write_bytes(EXPORT)
    await _setup(hass, mock_entry)
    assert hass.states.get(STATUS).state == "ok"
    assert hass.states.get(LAST).attributes["file"].endswith("20260920_030700.json")


async def test_backup_failure_and_service(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """A failing kiosk flips the status sensor and fails the action."""
    aioclient_mock.get(f"{URL}/api/config/export", status=500, text="boom")
    await _setup(hass, mock_entry)

    with pytest.raises(HomeAssistantError, match="T65 - Dining Room"):
        await hass.services.async_call(DOMAIN, "backup", {}, blocking=True)
    status = hass.states.get(STATUS)
    assert status.state == "failed"
    assert "HTTP 500" in status.attributes["error"]

    # With a response requested the action returns per-kiosk results instead.
    response = await hass.services.async_call(
        DOMAIN, "backup", {}, blocking=True, return_response=True
    )
    assert response["results"][0]["status"] == "failed"


async def test_backup_service_ok_response(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """Backing up a specific entry returns the file name."""
    aioclient_mock.get(f"{URL}/api/config/export", content=EXPORT)
    await _setup(hass, mock_entry)
    response = await hass.services.async_call(
        DOMAIN,
        "backup",
        {"config_entry_id": mock_entry.entry_id},
        blocking=True,
        return_response=True,
    )
    result = response["results"][0]
    assert result["status"] == "ok"
    assert result["file"].startswith("ks-backup_t65_dining_room_")


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """401 on export asks the user to re-authenticate."""
    aioclient_mock.get(f"{URL}/api/config/export", status=401)
    await _setup(hass, mock_entry)
    with pytest.raises(HomeAssistantError, match="re-authenticate"):
        await hass.services.async_call(
            "button", "press", {"entity_id": BUTTON}, blocking=True
        )
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_invalid_export_is_not_saved(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry, config_dir
) -> None:
    """A non-JSON answer never overwrites good backups."""
    aioclient_mock.get(f"{URL}/api/config/export", text="<html>login</html>")
    await _setup(hass, mock_entry)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "button", "press", {"entity_id": BUTTON}, blocking=True
        )
    assert not (config_dir / "kiosk_satellite_backups").exists()


async def test_url_follows_remote_admin_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry
) -> None:
    """If the kiosk moves to a new IP, the linked sensor's URL is used."""
    new_url = "https://192.0.2.20:2324"
    hass.config_entries.async_update_entry(
        mock_entry,
        data={
            **mock_entry.data,
            CONF_REMOTE_ADMIN_ENTITY: "sensor.t65_dining_room_remote_admin",
        },
    )
    hass.states.async_set("sensor.t65_dining_room_remote_admin", new_url)
    aioclient_mock.get(f"{new_url}/api/config/export", content=EXPORT)
    await _setup(hass, mock_entry)
    await hass.services.async_call(
        "button", "press", {"entity_id": BUTTON}, blocking=True
    )
    assert str(aioclient_mock.mock_calls[0][1]).startswith(new_url)


async def test_restore(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_entry, config_dir
) -> None:
    """Restore posts the stored file to /api/config/import."""
    folder = config_dir / "kiosk_satellite_backups" / "t65_dining_room"
    folder.mkdir(parents=True)
    older = folder / "ks-backup_t65_dining_room_20260913_030700.json"
    newer = folder / "ks-backup_t65_dining_room_20260920_030700.json"
    older.write_bytes(b'{"v":"old"}')
    newer.write_bytes(EXPORT)
    aioclient_mock.post(f"{URL}/api/config/import", json={"ok": True})
    await _setup(hass, mock_entry)

    # Default: newest file, keep identity.
    response = await hass.services.async_call(
        DOMAIN,
        "restore",
        {"config_entry_id": mock_entry.entry_id},
        blocking=True,
        return_response=True,
    )
    assert response["file"] == newer.name
    _, url, body, headers = aioclient_mock.mock_calls[-1]
    assert url.query == {"adoptIdentity": "1", "importLocalStorage": "1"}
    assert body == EXPORT
    assert headers["Authorization"] == f"Bearer {TOKEN}"

    # Explicit older file, clone mode.
    await hass.services.async_call(
        DOMAIN,
        "restore",
        {
            "config_entry_id": mock_entry.entry_id,
            "file": older.name,
            "adopt_identity": False,
            "import_local_storage": False,
        },
        blocking=True,
    )
    _, url, body, _ = aioclient_mock.mock_calls[-1]
    assert url.query == {"adoptIdentity": "0", "importLocalStorage": "0"}
    assert body == b'{"v":"old"}'

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "restore",
            {"config_entry_id": mock_entry.entry_id, "file": "nope.json"},
            blocking=True,
        )


async def test_unload(hass: HomeAssistant, mock_entry) -> None:
    """Entry unloads cleanly."""
    await _setup(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
