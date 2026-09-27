"""Config flow for Kiosk Satellite Backup."""

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
import voluptuous as vol

from .api import (
    KioskSatelliteApi,
    KioskSatelliteAuthError,
    KioskSatelliteConnectionError,
    KioskSatelliteError,
    normalize_url,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_KEEP,
    CONF_REMOTE_ADMIN_ENTITY,
    CONF_TOKEN,
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
from .manager import admin_url_from_sensor

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


async def _async_login(
    hass: HomeAssistant, url: str, password: str, verify_ssl: bool
) -> str:
    api = KioskSatelliteApi(async_get_clientsession(hass, verify_ssl=verify_ssl), url)
    token = await api.async_login(password)
    await api.async_info()  # prove the token works
    return token


def _errors_for(err: Exception) -> dict[str, str]:
    if isinstance(err, KioskSatelliteAuthError):
        return {"base": "invalid_auth"}
    if isinstance(err, KioskSatelliteConnectionError):
        return {"base": "cannot_connect"}
    _LOGGER.exception("Unexpected error talking to the kiosk")
    return {"base": "unknown"}


class KioskSatelliteBackupConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow: one entry per kiosk."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._device_id: str | None = None
        self._name: str | None = None
        self._remote_admin_entity: str | None = None
        self._default_url: str = ""

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
            return await self.async_step_credentials()

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

    async def async_step_credentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Admin URL and password."""
        errors: dict[str, str] = {}
        if user_input is not None and CONF_PASSWORD in user_input:
            url = normalize_url(user_input[CONF_URL])
            name = (
                self._name or user_input.get(CONF_NAME) or urlparse(url).hostname or url
            )
            if self._device_id is None:
                await self.async_set_unique_id(urlparse(url).netloc.lower())
                self._abort_if_unique_id_configured()
            try:
                token = await _async_login(
                    self.hass,
                    url,
                    user_input[CONF_PASSWORD],
                    user_input[CONF_VERIFY_SSL],
                )
            except KioskSatelliteError as err:
                errors = _errors_for(err)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_create_entry(
                    title=name,
                    data={
                        CONF_DEVICE_ID: self._device_id,
                        CONF_REMOTE_ADMIN_ENTITY: self._remote_admin_entity,
                        CONF_URL: url,
                        CONF_TOKEN: token,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                    options={CONF_KEEP: DEFAULT_KEEP},
                )

        schema: dict[Any, Any] = {}
        if self._name is None:
            schema[vol.Required(CONF_NAME)] = str
        suggested_url = (user_input or {}).get(CONF_URL) or self._default_url
        schema[
            vol.Required(
                CONF_URL, description={"suggested_value": suggested_url or None}
            )
        ] = str
        schema[vol.Required(CONF_PASSWORD)] = PASSWORD_SELECTOR
        schema[vol.Required(CONF_VERIFY_SSL, default=False)] = bool
        return self.async_show_form(
            step_id="credentials",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "name": self._name or "your kiosk",
                "port": str(DEFAULT_PORT),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Token rejected: ask for the admin password again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Get a fresh token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        url = (
            admin_url_from_sensor(self.hass, entry.data.get(CONF_REMOTE_ADMIN_ENTITY))
            or entry.data[CONF_URL]
        )
        if user_input is not None:
            try:
                token = await _async_login(
                    self.hass,
                    url,
                    user_input[CONF_PASSWORD],
                    entry.data[CONF_VERIFY_SSL],
                )
            except KioskSatelliteError as err:
                errors = _errors_for(err)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token, CONF_URL: url}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            errors=errors,
            description_placeholders={"name": entry.title, "url": url},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the admin URL or password."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            url = normalize_url(user_input[CONF_URL])
            try:
                token = await _async_login(
                    self.hass,
                    url,
                    user_input[CONF_PASSWORD],
                    user_input[CONF_VERIFY_SSL],
                )
            except KioskSatelliteError as err:
                errors = _errors_for(err)
            except Exception as err:  # noqa: BLE001
                errors = _errors_for(err)
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_TOKEN: token,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL, default=entry.data[CONF_URL]): str,
                    vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                    vol.Required(
                        CONF_VERIFY_SSL, default=entry.data.get(CONF_VERIFY_SSL, False)
                    ): bool,
                }
            ),
            errors=errors,
            description_placeholders={"name": entry.title},
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
