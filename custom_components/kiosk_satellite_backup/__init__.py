"""Kiosk Satellite Backup: scheduled config backups for Kiosk Satellite tablets."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
import voluptuous as vol

from .const import (
    ATTR_ADOPT_IDENTITY,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_FILE,
    ATTR_IMPORT_LOCAL_STORAGE,
    DOMAIN,
    SERVICE_BACKUP,
    SERVICE_RESTORE,
)
from .manager import KioskBackupManager

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type KioskBackupConfigEntry = ConfigEntry[KioskBackupManager]

BACKUP_SCHEMA = vol.Schema(
    {vol.Optional(ATTR_CONFIG_ENTRY_ID): vol.All(cv.ensure_list, [cv.string])}
)
RESTORE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(ATTR_FILE): cv.string,
        vol.Optional(ATTR_ADOPT_IDENTITY, default=True): cv.boolean,
        vol.Optional(ATTR_IMPORT_LOCAL_STORAGE, default=True): cv.boolean,
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide actions."""

    def _loaded_entry(entry_id: str) -> KioskBackupConfigEntry:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_entry",
                translation_placeholders={"entry_id": entry_id},
            )
        if entry.state is not ConfigEntryState.LOADED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_loaded",
                translation_placeholders={"name": entry.title},
            )
        return entry

    async def async_handle_backup(call: ServiceCall) -> ServiceResponse:
        if ids := call.data.get(ATTR_CONFIG_ENTRY_ID):
            entries = [_loaded_entry(entry_id) for entry_id in ids]
        else:
            entries = hass.config_entries.async_loaded_entries(DOMAIN)

        results: list[dict[str, str | int | None]] = []
        for entry in entries:
            manager: KioskBackupManager = entry.runtime_data
            try:
                backup = await manager.async_backup()
            except HomeAssistantError as err:
                results.append(
                    {"name": entry.title, "status": "failed", "error": str(err)}
                )
            else:
                results.append(
                    {
                        "name": entry.title,
                        "status": "ok",
                        "file": backup.path.name,
                        "size": backup.size,
                    }
                )

        failed = [r for r in results if r["status"] == "failed"]
        if failed and not call.return_response:
            raise HomeAssistantError(
                "Kiosk backup failed for: "
                + ", ".join(f"{r['name']} ({r['error']})" for r in failed)
            )
        return {"results": results} if call.return_response else None

    async def async_handle_restore(call: ServiceCall) -> ServiceResponse:
        entry = _loaded_entry(call.data[ATTR_CONFIG_ENTRY_ID])
        manager: KioskBackupManager = entry.runtime_data
        restored = await manager.async_restore(
            call.data.get(ATTR_FILE),
            adopt_identity=call.data[ATTR_ADOPT_IDENTITY],
            import_local_storage=call.data[ATTR_IMPORT_LOCAL_STORAGE],
        )
        return {"name": entry.title, "file": restored.path.name}

    hass.services.async_register(
        DOMAIN,
        SERVICE_BACKUP,
        async_handle_backup,
        schema=BACKUP_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_RESTORE,
        async_handle_restore,
        schema=RESTORE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KioskBackupConfigEntry) -> bool:
    """Set up one kiosk."""
    manager = KioskBackupManager(hass, entry)
    await manager.async_initialize()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: KioskBackupConfigEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: KioskBackupConfigEntry
) -> bool:
    """Unload a kiosk."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
