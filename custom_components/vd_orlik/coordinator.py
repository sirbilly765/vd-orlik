"""Stahování a sdílení dat VD Orlík."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_INTERVAL, CONF_URL, DEFAULT_INTERVAL, DEFAULT_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class VdOrlikCoordinator(DataUpdateCoordinator[dict]):
    """Drží poslední známý stav vodního díla."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.url: str = entry.options.get(
            CONF_URL, entry.data.get(CONF_URL, DEFAULT_URL)
        )
        minut: int = entry.options.get(
            CONF_INTERVAL, entry.data.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=minut),
        )

    async def _async_update_data(self) -> dict:
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(self.url, timeout=aiohttp.ClientTimeout(total=30)) as odpoved:
                odpoved.raise_for_status()
                data = await odpoved.json(content_type=None)
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"data se nepodařilo stáhnout: {err}") from err
        except ValueError as err:
            raise UpdateFailed(f"odpověď není platný JSON: {err}") from err

        if not isinstance(data, dict):
            raise UpdateFailed("odpověď nemá očekávaný tvar")
        if not data.get("ok"):
            raise UpdateFailed(f"zdroj hlásí chybu: {data.get('chyba')}")
        if not isinstance(data.get("hladina"), (int, float)):
            raise UpdateFailed("v datech chybí hladina")

        return data
