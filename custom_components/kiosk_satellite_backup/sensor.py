"""Backup status sensors."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KioskBackupConfigEntry
from .const import STATUS_FAILED, STATUS_NEVER, STATUS_OK
from .entity import KioskBackupEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KioskBackupConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""
    manager = entry.runtime_data
    async_add_entities(
        [
            LastBackupSensor(manager, "last_backup"),
            BackupStatusSensor(manager, "backup_status"),
        ]
    )


class LastBackupSensor(KioskBackupEntity, SensorEntity):
    """When the last successful backup was taken."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        """Last successful backup."""
        return self.manager.last_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """File details."""
        last = self.manager.last_file
        return {
            "file": last.path.name if last else None,
            "size_bytes": last.size if last else None,
            "backups_stored": self.manager.backup_count,
            "keep": self.manager.keep,
            "directory": str(self.manager.directory),
        }


class BackupStatusSensor(KioskBackupEntity, SensorEntity):
    """Result of the most recent backup attempt."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATUS_OK, STATUS_FAILED, STATUS_NEVER]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str:
        """ok / failed / never."""
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Error details."""
        attempt = self.manager.last_attempt
        return {
            "last_attempt": attempt.isoformat() if attempt else None,
            "error": self.manager.last_error,
        }
