"""Backup-now button."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KioskBackupConfigEntry
from .entity import KioskBackupEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KioskBackupConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([KioskBackupButton(entry.runtime_data, "backup_config")])


class KioskBackupButton(KioskBackupEntity, ButtonEntity):
    """Take a configuration backup now."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Run a backup; errors surface in the UI and in automation traces."""
        await self.manager.async_backup()
