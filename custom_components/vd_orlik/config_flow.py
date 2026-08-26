"""Průvodce nastavením VD Orlík."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_INTERVAL,
    CONF_URL,
    DEFAULT_INTERVAL,
    DEFAULT_URL,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)

INTERVAL = vol.All(
    NumberSelector(
        NumberSelectorConfig(
            min=MIN_INTERVAL, max=MAX_INTERVAL, step=5,
            mode=NumberSelectorMode.BOX, unit_of_measurement="min",
        )
    ),
    vol.Coerce(int),
)
ADRESA = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


class VdOrlikConfigFlow(ConfigFlow, domain=DOMAIN):
    """Přidání integrace. Nic povinného — výchozí zdroj stačí."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="VD Orlík", data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): INTERVAL,
                    vol.Optional(CONF_URL, default=DEFAULT_URL): ADRESA,
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return VdOrlikOptionsFlow()


class VdOrlikOptionsFlow(OptionsFlow):
    """Změna intervalu nebo datového zdroje po instalaci."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        soucasne = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_INTERVAL,
                        default=soucasne.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                    ): INTERVAL,
                    vol.Optional(
                        CONF_URL, default=soucasne.get(CONF_URL, DEFAULT_URL)
                    ): ADRESA,
                }
            ),
        )
