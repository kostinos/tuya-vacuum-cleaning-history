"""Config flow for Tuya Vacuum Cleaning History."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_CLEAN_MODE_ENTITY_ID,
    CONF_CLOUD_ENTRY_ID,
    CONF_SUCTION_ENTITY_ID,
    CONF_VACUUM_ENTRY_ID,
    CONF_WATER_ENTITY_ID,
    DOMAIN,
)


class TuvioHistoryConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure an archived Tuya vacuum history source."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle setup from the Home Assistant UI."""
        errors: dict[str, str] = {}
        if user_input is not None:
            error = self._validate_entries(user_input)
            if error is None:
                await self.async_set_unique_id(user_input[CONF_VACUUM_ENTRY_ID])
                self._abort_if_unique_id_configured()
                vacuum = self.hass.config_entries.async_get_entry(
                    user_input[CONF_VACUUM_ENTRY_ID]
                )
                return self.async_create_entry(
                    title=vacuum.title if vacuum else "Tuya vacuum",
                    data=user_input,
                )
            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(user_input),
            errors=errors,
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> FlowResult:
        """Import the legacy YAML package without exposing credentials."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        error = self._validate_entries(import_data)
        if error is not None:
            return self.async_abort(reason=error)
        await self.async_set_unique_id(import_data[CONF_VACUUM_ENTRY_ID])
        self._abort_if_unique_id_configured()
        vacuum = self.hass.config_entries.async_get_entry(
            import_data[CONF_VACUUM_ENTRY_ID]
        )
        return self.async_create_entry(
            title=vacuum.title if vacuum else "Tuya vacuum",
            data=import_data,
        )

    def _validate_entries(self, data: dict[str, Any]) -> str | None:
        cloud = self.hass.config_entries.async_get_entry(data[CONF_CLOUD_ENTRY_ID])
        vacuum = self.hass.config_entries.async_get_entry(data[CONF_VACUUM_ENTRY_ID])
        if cloud is None or not {"client_id", "client_secret"} <= set(cloud.data):
            return "invalid_cloud_entry"
        if vacuum is None or "device_id" not in vacuum.data:
            return "invalid_vacuum_entry"
        return None

    @staticmethod
    def _schema(defaults: dict[str, Any] | None) -> vol.Schema:
        defaults = defaults or {}
        return vol.Schema(
            {
                vol.Required(
                    CONF_CLOUD_ENTRY_ID,
                    default=defaults.get(CONF_CLOUD_ENTRY_ID),
                ): selector.ConfigEntrySelector(
                    selector.ConfigEntrySelectorConfig(integration="localtuya")
                ),
                vol.Required(
                    CONF_VACUUM_ENTRY_ID,
                    default=defaults.get(CONF_VACUUM_ENTRY_ID),
                ): selector.ConfigEntrySelector(
                    selector.ConfigEntrySelectorConfig(integration="tuya_local")
                ),
                vol.Optional(CONF_CLEAN_MODE_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="select")
                ),
                vol.Optional(CONF_SUCTION_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="select")
                ),
                vol.Optional(CONF_WATER_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="select")
                ),
            }
        )
