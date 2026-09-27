"""Shared entity base: attach to the kiosk's existing ESPHome device when possible."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import CONF_DEVICE_ID, DOMAIN
from .manager import KioskBackupManager


class KioskBackupEntity(Entity):
    """Base entity."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: KioskBackupManager, key: str) -> None:
        """Initialize."""
        self.manager = manager
        entry = manager.entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key

        device = None
        if device_id := entry.data.get(CONF_DEVICE_ID):
            device = dr.async_get(manager.hass).async_get(device_id)
        if device is not None and device.connections:
            # Link to the kiosk's ESPHome device so the entities sit next to its own.
            self._attr_device_info = DeviceInfo(connections=device.connections)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, entry.entry_id)},
                name=entry.title,
                manufacturer="Kiosk Satellite",
                model="Kiosk Satellite",
                configuration_url=manager.resolve_url(),
            )

    async def async_added_to_hass(self) -> None:
        """Subscribe to manager updates."""
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))
