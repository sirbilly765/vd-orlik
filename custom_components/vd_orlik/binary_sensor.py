"""Binární senzor stáří dat VD Orlík."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VdOrlikEntity

# Povodí Vltavy měří po deseti minutách; tři hodiny bez nového měření
# už znamenají, že se něco děje na straně zdroje.
MEZ_MINUT = 180


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([VdOrlikDataAktualni(coordinator, entry.entry_id)])


class VdOrlikDataAktualni(VdOrlikEntity, BinarySensorEntity):
    """Zapnuto, dokud jsou data čerstvá."""

    _attr_name = "Data aktuální"
    _attr_icon = "mdi:database-check"

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "data_aktualni")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        vek = data.get("vek_dat_min")
        if vek is None:
            return None
        try:
            return float(vek) <= MEZ_MINUT
        except (TypeError, ValueError):
            return None
