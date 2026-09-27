"""Retention per kiosk as a number entity."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KioskBackupConfigEntry
from .const import MAX_KEEP, MIN_KEEP
from .entity import KioskBackupEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KioskBackupConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number."""
    async_add_entities([KioskBackupKeepNumber(entry.runtime_data, "keep_backups")])


class KioskBackupKeepNumber(KioskBackupEntity, NumberEntity):
    """How many backups to keep for this kiosk; lowering it prunes immediately."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = MIN_KEEP
    _attr_native_max_value = MAX_KEEP
    _attr_native_step = 1

    @property
    def native_value(self) -> float:
        """Current retention."""
        return self.manager.keep

    async def async_set_native_value(self, value: float) -> None:
        """Store the new retention and prune old backups."""
        await self.manager.async_set_keep(int(value))
