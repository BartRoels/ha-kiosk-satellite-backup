"""Constants for the Kiosk Satellite Backup integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "kiosk_satellite_backup"

CONF_DEVICE_ID: Final = "device_id"
CONF_REMOTE_ADMIN_ENTITY: Final = "remote_admin_entity_id"
CONF_URL: Final = "url"
CONF_TOKEN: Final = "token"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_KEEP: Final = "keep"

DEFAULT_PORT: Final = 2324
DEFAULT_KEEP: Final = 8
MIN_KEEP: Final = 1
MAX_KEEP: Final = 100

# Long-lived automation token requested from the kiosk (the API clamps to 10 years).
TOKEN_TTL_DAYS: Final = 3650

# Backups are written below <config>/kiosk_satellite_backups/<device slug>/
BACKUP_DIR: Final = "kiosk_satellite_backups"
BACKUP_PREFIX: Final = "ks-backup_"

# Kiosk Satellite exposes its admin URL through an ESPHome sensor with this name.
REMOTE_ADMIN_SENSOR_NAME: Final = "Remote admin"
KIOSK_SATELLITE_MANUFACTURER: Final = "kiosk_satellite"

STATUS_OK: Final = "ok"
STATUS_FAILED: Final = "failed"
STATUS_NEVER: Final = "never"

SERVICE_BACKUP: Final = "backup"
SERVICE_RESTORE: Final = "restore"
ATTR_CONFIG_ENTRY_ID: Final = "config_entry_id"
ATTR_FILE: Final = "file"
ATTR_ADOPT_IDENTITY: Final = "adopt_identity"
ATTR_IMPORT_LOCAL_STORAGE: Final = "import_local_storage"
