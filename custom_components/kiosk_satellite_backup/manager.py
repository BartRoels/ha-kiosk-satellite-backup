"""Backup/restore logic for one Kiosk Satellite device."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
import re
from typing import TypeVar

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util, slugify

from .api import (
    CertPin,
    KioskSatelliteApi,
    KioskSatelliteAuthError,
    KioskSatelliteCertificateError,
    KioskSatelliteError,
    async_fetch_certificate,
    normalize_url,
)
from .const import (
    BACKUP_DIR,
    BACKUP_PREFIX,
    CONF_CERT_SHA256,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_SPKI_SHA256,
    CONF_TOKEN,
    CONF_TOKEN_ISSUED,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_KEEP,
    DOMAIN,
    STATUS_FAILED,
    STATUS_NEVER,
    STATUS_OK,
    TOKEN_RENEW_BEFORE_DAYS,
    TOKEN_TTL_DAYS,
)

_LOGGER = logging.getLogger(__name__)

_TS_RE = re.compile(r"_(\d{8}_\d{6})\.json$")
_T = TypeVar("_T")


@dataclass(slots=True)
class BackupFile:
    """A backup stored on disk."""

    path: Path
    size: int
    created: datetime


def admin_url_from_sensor(hass: HomeAssistant, entity_id: str | None) -> str | None:
    """HTTPS admin URL reported by the kiosk's 'Remote admin' ESPHome sensor, if usable.

    Only HTTPS URLs are accepted. Whatever host the sensor names, requests still
    only go through if the certificate matches the pinned kiosk identity.
    """
    state = hass.states.get(entity_id) if entity_id else None
    if state is None or not state.state.startswith("https://"):
        return None
    try:
        return normalize_url(state.state)
    except ValueError:
        return None


def pin_from_entry(entry: ConfigEntry) -> CertPin | None:
    """Stored certificate pin of an entry, if any."""
    cert = entry.data.get(CONF_CERT_SHA256)
    spki = entry.data.get(CONF_SPKI_SHA256)
    return CertPin(cert, spki) if cert and spki else None


def _parse_created(path: Path) -> datetime:
    """Timestamp from the filename, falling back to mtime."""
    if match := _TS_RE.search(path.name):
        parsed = datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
        return parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.utc_from_timestamp(path.stat().st_mtime)


class KioskBackupManager:
    """Owns backup state and file handling for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.slug = slugify(entry.title) or entry.entry_id
        self.directory = Path(hass.config.path(BACKUP_DIR, self.slug))
        verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)
        self._verify_ssl = verify_ssl
        self.api = KioskSatelliteApi(
            async_get_clientsession(hass, verify_ssl=verify_ssl),
            entry.data[CONF_URL],
            entry.data[CONF_TOKEN],
            pin=pin_from_entry(entry),
            verify_ssl=verify_ssl,
            on_pin_renewed=self._store_pin,
        )
        self.options_snapshot = dict(entry.options)
        self.status: str = STATUS_NEVER
        self.last_success: datetime | None = None
        self.last_attempt: datetime | None = None
        self.last_error: str | None = None
        self.last_file: BackupFile | None = None
        self.backup_count: int = 0
        self._listeners: list[Callable[[], None]] = []

    @property
    def keep(self) -> int:
        """Number of backups to retain."""
        return int(self.entry.options.get(CONF_KEEP, DEFAULT_KEEP))

    # ---- listeners -------------------------------------------------------

    @callback
    def async_add_listener(
        self, update_callback: Callable[[], None]
    ) -> Callable[[], None]:
        """Register an entity update callback."""
        self._listeners.append(update_callback)

        @callback
        def remove() -> None:
            self._listeners.remove(update_callback)

        return remove

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    # ---- security --------------------------------------------------------

    @callback
    def _store_pin(self, pin: CertPin) -> None:
        """Persist a (renewed or first-use) certificate pin."""
        _LOGGER.info("%s: pinned kiosk certificate %s", self.entry.title, pin.display)
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.entry.data,
                CONF_CERT_SHA256: pin.cert_sha256,
                CONF_SPKI_SHA256: pin.spki_sha256,
            },
        )

    async def _async_ensure_pin(self) -> None:
        """Entries created before pinning existed get pinned on first use."""
        if self._verify_ssl or self.api.pin is not None:
            return
        pin = await async_fetch_certificate(self.api.url)
        _LOGGER.warning(
            "%s: no certificate was pinned yet; trusting the certificate the kiosk "
            "presents now (%s). Compare it with Device > TLS on the kiosk",
            self.entry.title,
            pin.display,
        )
        self.api.pin = pin
        self._store_pin(pin)

    @callback
    def async_check_token_age(self) -> None:
        """Ask for re-authentication shortly before the token expires."""
        issued_raw = self.entry.data.get(CONF_TOKEN_ISSUED)
        issued = dt_util.parse_datetime(issued_raw) if issued_raw else None
        if issued is None:
            return  # token from an older version; its lifetime is unknown
        renew_at = issued + timedelta(days=TOKEN_TTL_DAYS - TOKEN_RENEW_BEFORE_DAYS)
        if dt_util.utcnow() >= renew_at:
            self.entry.async_start_reauth(self.hass)

    # ---- files -----------------------------------------------------------

    def _list_files(self) -> list[BackupFile]:
        """Backups on disk, newest first (runs in executor)."""
        if not self.directory.is_dir():
            return []
        files = [
            BackupFile(path=p, size=p.stat().st_size, created=_parse_created(p))
            for p in self.directory.glob(f"{BACKUP_PREFIX}*.json")
            if p.is_file()
        ]
        files.sort(key=lambda f: (f.created, f.path.name), reverse=True)
        return files

    async def async_list_backups(self) -> list[BackupFile]:
        """Backups on disk, newest first."""
        return await self.hass.async_add_executor_job(self._list_files)

    def _write_and_prune(self, payload: bytes, stamp: str) -> tuple[BackupFile, int]:
        """Write a new backup atomically and prune old ones (runs in executor)."""
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        target = self.directory / f"{BACKUP_PREFIX}{self.slug}_{stamp}.json"
        tmp = target.with_suffix(".json.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        os.replace(tmp, target)

        files = self._list_files()
        for old in files[self.keep :]:
            old.path.unlink(missing_ok=True)
        files = files[: self.keep]
        newest = next(f for f in files if f.path == target)
        return newest, len(files)

    def _read(self, path: Path) -> bytes:
        return path.read_bytes()

    # ---- lifecycle -------------------------------------------------------

    async def async_initialize(self) -> None:
        """Load state from the files already on disk."""
        files = await self.async_list_backups()
        self.backup_count = len(files)
        if files:
            self.last_file = files[0]
            self.last_success = files[0].created
            self.status = STATUS_OK
        self.async_check_token_age()

    def resolve_url(self) -> str:
        """Current admin URL; follows the kiosk's own 'Remote admin' sensor if linked."""
        return (
            admin_url_from_sensor(
                self.hass, self.entry.data.get(CONF_REMOTE_ADMIN_ENTITY)
            )
            or self.entry.data[CONF_URL]
        )

    # ---- actions ---------------------------------------------------------

    async def _async_call(self, action: str, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run an API call with shared error handling."""
        self.api.url = self.resolve_url()
        try:
            await self._async_ensure_pin()
            return await func()
        except KioskSatelliteAuthError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"{self.entry.title}: token rejected, re-authenticate the integration"
            ) from err
        except KioskSatelliteCertificateError as err:
            self.entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                f"{self.entry.title}: {action} refused, the kiosk's certificate "
                "changed; re-authenticate to review and trust it"
            ) from err
        except KioskSatelliteError as err:
            raise HomeAssistantError(
                f"{self.entry.title}: {action} failed: {err}"
            ) from err

    async def async_backup(self) -> BackupFile:
        """Export the kiosk configuration and store it on disk."""
        self.async_check_token_age()
        self.last_attempt = dt_util.now()
        stamp = self.last_attempt.strftime("%Y%m%d_%H%M%S")
        try:
            payload = await self._async_call("backup", self.api.async_export)
            newest, count = await self.hass.async_add_executor_job(
                self._write_and_prune, payload, stamp
            )
        except HomeAssistantError as err:
            self._fail(str(err))
            raise
        except OSError as err:
            self._fail(f"Cannot write backup: {err.strerror}")
            raise HomeAssistantError(
                f"{self.entry.title}: cannot write backup: {err.strerror}"
            ) from err

        self.status = STATUS_OK
        self.last_error = None
        self.last_success = self.last_attempt
        self.last_file = newest
        self.backup_count = count
        _LOGGER.info("Backed up %s to %s", self.entry.title, newest.path)
        self._notify()
        return newest

    @callback
    def _fail(self, error: str) -> None:
        _LOGGER.warning("Backup of %s failed: %s", self.entry.title, error)
        self.status = STATUS_FAILED
        self.last_error = error
        self._notify()

    async def async_restore(
        self,
        file_name: str | None,
        *,
        adopt_identity: bool,
        import_local_storage: bool,
    ) -> BackupFile:
        """Push a stored backup back to the kiosk."""
        files = await self.async_list_backups()
        if not files:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="no_backups",
                translation_placeholders={"name": self.entry.title},
            )
        if file_name:
            # Match by exact name among the listed files only: no path traversal.
            match = next((f for f in files if f.path.name == file_name), None)
            if match is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="file_not_found",
                    translation_placeholders={
                        "file": file_name,
                        "name": self.entry.title,
                    },
                )
        else:
            match = files[0]

        payload = await self.hass.async_add_executor_job(self._read, match.path)
        await self._async_call(
            "restore",
            lambda: self.api.async_import(
                payload,
                adopt_identity=adopt_identity,
                import_local_storage=import_local_storage,
            ),
        )
        _LOGGER.info("Restored %s from %s", self.entry.title, match.path)
        return match
