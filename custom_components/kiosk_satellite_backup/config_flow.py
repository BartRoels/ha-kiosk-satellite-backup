"""Config flow for Kiosk Satellite Backup.

The admin password is only sent after the user has seen the kiosk's certificate
fingerprint, and only over a connection pinned to that certificate. The password
itself is never stored; only a token with a limited lifetime is.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_NAME, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    DeviceFilterSelectorConfig,
    DeviceSelector,
    DeviceSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .api import (
    CertPin,
    KioskSatelliteApi,
    KioskSatelliteAuthError,
    KioskSatelliteCertificateError,
    KioskSatelliteConnectionError,
    KioskSatelliteError,
    KioskSatelliteInsecureUrlError,
    async_fetch_certificate,
    normalize_url,
    require_https,
)
from .const import (
    CONF_CERT_SHA256,
    CONF_DEVICE_ID,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_SPKI_SHA256,
    CONF_TOKEN,
    CONF_TOKEN_ISSUED,
    CONF_URL,
    CONF_VERIFY_SSL,
    DEFAULT_KEEP,
    DEFAULT_PORT,
    DOMAIN,
    KIOSK_SATELLITE_MANUFACTURER,
    MAX_KEEP,
    MIN_KEEP,
    REMOTE_ADMIN_SENSOR_NAME,
)
from .manager import admin_url_from_sensor, pin_from_entry

_LOGGER = logging.getLogger(__name__)

PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _find_remote_admin_entity(hass: HomeAssistant, device_id: str) -> str | None:
    """Find the kiosk's own 'Remote admin' URL sensor on its ESPHome device."""
    for entry in er.async_entries_for_device(er.async_get(hass), device_id):
        if entry.domain != "sensor":
            continue
        if entry.original_name == REMOTE_ADMIN_SENSOR_NAME or entry.entity_id.endswith(
            "_remote_admin"
        ):
            return entry.entity_id
    return None


async def _async_fetch_pin(url: str, verify_ssl: bool) -> CertPin | None:
    """Certificate to pin (none when normal CA verification is used)."""
    require_https(url)
    if verify_ssl:
        return None
    return await async_fetch_certificate(url)


async def _async_login(
    hass: HomeAssistant,
    url: str,
    password: str,
    verify_ssl: bool,
    pin: CertPin | None,
) -> tuple[str, CertPin | None]:
    """Log in over the pinned connection; returns the token and the final pin."""
    api = KioskSatelliteApi(
        async_get_clientsession(hass, verify_ssl=verify_ssl),
        url,
        pin=pin,
        verify_ssl=verify_ssl,
    )
    token = await api.async_login(password)
    await api.async_info()  # prove the token works
    return token, api.pin


def _errors_for(err: Exception) -> dict[str, str]:
    if isinstance(err, KioskSatelliteInsecureUrlError):
        return {"base": "https_required"}
    if isinstance(err, KioskSatelliteAuthError):
        return {"base": "invalid_auth"}
    if isinstance(err, KioskSatelliteCertificateError):
        return {"base": "cert_changed"}
    if isinstance(err, KioskSatelliteConnectionError):
        return {"base": "cannot_connect"}
    if isinstance(err, ValueError):
        return {"base": "invalid_url"}
    _LOGGER.exception("Unexpected error talking to the kiosk")
    return {"base": "unknown"}


def _token_data(token: str, pin: CertPin | None) -> dict[str, Any]:
    return {
        CONF_TOKEN: token,
        CONF_TOKEN_ISSUED: dt_util.utcnow().isoformat(),
        CONF_CERT_SHA256: pin.cert_sha256 if pin else None,
        CONF_SPKI_SHA256: pin.spki_sha256 if pin else None,
    }


def _fingerprint_text(pin: CertPin | None) -> str:
    return pin.display if pin else "(verified by a trusted certificate authority)"


class KioskSatelliteBackupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow: one entry per kiosk."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._device_id: str | None = None
        self._name: str | None = None
        self._remote_admin_entity: str | None = None
        self._default_url: str = ""
        self._url: str = ""
        self._verify_ssl: bool = False
        self._pin: CertPin | None = None

    # ---- new entry ---------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the kiosk's ESPHome device (optional)."""
        if user_input is not None:
            if device_id := user_input.get(CONF_DEVICE_ID):
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                device = dr.async_get(self.hass).async_get(device_id)
                self._device_id = device_id
                self._name = (device.name_by_user or device.name) if device else None
                self._remote_admin_entity = _find_remote_admin_entity(
                    self.hass, device_id
                )
                self._default_url = (
                    admin_url_from_sensor(self.hass, self._remote_admin_entity) or ""
                )
            return await self.async_step_connect()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_DEVICE_ID): DeviceSelector(
                        DeviceSelectorConfig(
                            filter=DeviceFilterSelectorConfig(
                                integration="esphome",
                                manufacturer=KIOSK_SATELLITE_MANUFACTURER,
                            )
                        )
                    )
                }
            ),
        )

    async def async_step_connect(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Admin URL; fetches the certificate to show before any password is sent."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
                if self._device_id is None:
                    self._name = user_input[CONF_NAME]
                    await self.async_set_unique_id(urlparse(url).netloc.lower())
                    self._abort_if_unique_id_configured()
                self._pin = await _async_fetch_pin(url, user_input[CONF_VERIFY_SSL])
            except (KioskSatelliteError, ValueError) as err:
                errors = _errors_for(err)
            else:
                self._url = url
                self._verify_ssl = user_input[CONF_VERIFY_SSL]
                return await self.async_step_credentials()

        schema: dict[Any, Any] = {}
        if self._device_id is None:
            schema[vol.Required(CONF_NAME)] = str
        suggested_url = (user_input or {}).get(CONF_URL) or self._default_url
        schema[
            vol.Required(
                CONF_URL, description={"suggested_value": suggested_url or None}
            )
        ] = str
        schema[vol.Required(CONF_VERIFY_SSL, default=False)] = bool
        return self.async_show_form(
            step_id="connect",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "name": self._name or "your kiosk",
                "port": str(DEFAULT_PORT),
            },
        )

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the certificate fingerprint and ask for the admin password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, pin = await _async_login(
                    self.hass,
                    self._url,
                    user_input[CONF_PASSWORD],
                    self._verify_ssl,
                    self._pin,
                )
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_create_entry(
                    title=self._name or urlparse(self._url).hostname or self._url,
                    data={
                        CONF_DEVICE_ID: self._device_id,
                        CONF_REMOTE_ADMIN_ENTITY: self._remote_admin_entity,
                        CONF_URL: self._url,
                        CONF_VERIFY_SSL: self._verify_ssl,
                        **_token_data(token, pin),
                    },
                    options={CONF_KEEP: DEFAULT_KEEP},
                )

        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={
                "name": self._name or self._url,
                "url": self._url,
                "fingerprint": _fingerprint_text(self._pin),
            },
        )

    # ---- re-authentication -------------------------------------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Token rejected/expiring or certificate changed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the kiosk's current certificate and get a fresh token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        url = (
            admin_url_from_sensor(self.hass, entry.data.get(CONF_REMOTE_ADMIN_ENTITY))
            or entry.data[CONF_URL]
        )
        verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)
        stored = pin_from_entry(entry)

        if user_input is None or self._pin is None and not verify_ssl:
            try:
                self._pin = await _async_fetch_pin(url, verify_ssl)
            except (KioskSatelliteError, ValueError) as err:
                errors = _errors_for(err)
                user_input = None

        if user_input is not None and not errors:
            try:
                token, pin = await _async_login(
                    self.hass, url, user_input[CONF_PASSWORD], verify_ssl, self._pin
                )
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_URL: url, **_token_data(token, pin)}
                )

        key_changed = bool(
            stored and self._pin and stored.spki_sha256 != self._pin.spki_sha256
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={
                "name": entry.title,
                "url": url,
                "fingerprint": _fingerprint_text(self._pin),
                "warning": (
                    "⚠️ This is a DIFFERENT certificate key than the one trusted "
                    "before. Only continue if you replaced the kiosk's certificate "
                    "yourself or reinstalled the app.\n\n"
                    if key_changed
                    else ""
                ),
            },
        )

    # ---- reconfigure -------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the admin URL or certificate verification mode."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                url = normalize_url(user_input[CONF_URL])
                self._pin = await _async_fetch_pin(url, user_input[CONF_VERIFY_SSL])
            except (KioskSatelliteError, ValueError) as err:
                errors = _errors_for(err)
            else:
                self._url = url
                self._verify_ssl = user_input[CONF_VERIFY_SSL]
                return await self.async_step_reconfigure_credentials()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                    vol.Required(
                        CONF_VERIFY_SSL, default=entry.data.get(CONF_VERIFY_SSL, False)
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    async def async_step_reconfigure_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Password for the reconfigured connection."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                token, pin = await _async_login(
                    self.hass,
                    self._url,
                    user_input[CONF_PASSWORD],
                    self._verify_ssl,
                    self._pin,
                )
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: self._url,
                        CONF_VERIFY_SSL: self._verify_ssl,
                        **_token_data(token, pin),
                    },
                )
        return self.async_show_form(
            step_id="reconfigure_credentials",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={
                "name": entry.title,
                "url": self._url,
                "fingerprint": _fingerprint_text(self._pin),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Options: retention."""
        return KioskSatelliteBackupOptionsFlow()


class KioskSatelliteBackupOptionsFlow(OptionsFlow):
    """How many backups to keep."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data={CONF_KEEP: int(user_input[CONF_KEEP])})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_KEEP,
                        default=self.config_entry.options.get(CONF_KEEP, DEFAULT_KEEP),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_KEEP,
                            max=MAX_KEEP,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
