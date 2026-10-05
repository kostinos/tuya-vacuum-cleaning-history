"""Integration-owned Tuya Cloud credentials with legacy migration."""
from __future__ import annotations

from typing import Any
import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from . import History
from .credentials import independent_settings
from .const import (
    CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_REGION, CONF_DEVICE_ID, REGIONS,
    CONF_CLEAN_MODE_ENTITY_ID, CONF_SUCTION_ENTITY_ID, CONF_WATER_ENTITY_ID, DOMAIN,
)


class TuvioHistoryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Connect directly to the user's Tuya Cloud project."""
    VERSION = 2

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            errors = await self._validate(user_input)
            if not errors:
                if self._async_current_entries():
                    return self.async_abort(reason="already_configured")
                await self.async_set_unique_id(user_input[CONF_DEVICE_ID])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Tuya vacuum history", data=user_input)
        return self.async_show_form(step_id="user", data_schema=self._schema(user_input), errors=errors)

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Import old YAML even if its cloud reference was already deleted."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        data = independent_settings(import_data, self.hass.config_entries.async_get_entry)
        if not data.get(CONF_DEVICE_ID):
            return self.async_abort(reason="invalid_vacuum_entry")
        await self.async_set_unique_id(data[CONF_DEVICE_ID])
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title="Tuya vacuum history", data=data)

    async def async_step_reauth(self, entry_data) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        return await self._update("reauth_confirm", self._get_reauth_entry(), user_input)

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        return await self._update("reconfigure", self._get_reconfigure_entry(), user_input)

    async def _update(self, step, entry, user_input):
        errors = {}
        if user_input is not None:
            errors = await self._validate(user_input)
            if not errors:
                self.hass.config_entries.async_update_entry(entry, unique_id=user_input[CONF_DEVICE_ID])
                return self.async_update_reload_and_abort(entry, data=user_input)
        defaults = {**entry.data, **(user_input or {})}
        return self.async_show_form(step_id=step, data_schema=self._schema(defaults), errors=errors)

    async def _validate(self, data):
        """Check access to the archive before saving any configuration."""
        history = History(self.hass, data, {"device_id": data[CONF_DEVICE_ID]}, data)
        try:
            await history.listing(1, True)
        except (aiohttp.ClientError, TimeoutError):
            return {"base": "cannot_connect"}
        except ValueError:
            return {"base": "invalid_auth"}
        return {}

    @staticmethod
    def _schema(defaults=None):
        defaults = defaults or {}
        schema = {}
        for key in (CONF_CLIENT_ID, CONF_CLIENT_SECRET, CONF_DEVICE_ID):
            marker = vol.Required(key)
            if key != CONF_CLIENT_SECRET and defaults.get(key):
                marker = vol.Required(key, default=defaults[key])
            schema[marker] = selector.TextSelector(selector.TextSelectorConfig(
                type=selector.TextSelectorType.PASSWORD if key == CONF_CLIENT_SECRET
                else selector.TextSelectorType.TEXT))
        schema[vol.Required(CONF_REGION, default=defaults.get(CONF_REGION, "eu"))] = selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(REGIONS), mode=selector.SelectSelectorMode.DROPDOWN))
        for key in (CONF_CLEAN_MODE_ENTITY_ID, CONF_SUCTION_ENTITY_ID, CONF_WATER_ENTITY_ID):
            marker = vol.Optional(key, default=defaults[key]) if defaults.get(key) else vol.Optional(key)
            schema[marker] = selector.EntitySelector(selector.EntitySelectorConfig(domain="select"))
        return vol.Schema(schema)
