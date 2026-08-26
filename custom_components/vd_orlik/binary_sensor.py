"""Binární senzor stáří dat VD Orlík."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import VdOrlikEntity

# Povodí Vltavy měří po deseti minutách; tři hodiny bez nového měření
# už znamenají, že se něco děje na straně zdroje.
MEZ = timedelta(minutes=180)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        VdOrlikDataAktualni(coordinator, entry.entry_id),
        VdOrlikOdtokTece(coordinator, entry.entry_id),
    ])


class VdOrlikDataAktualni(VdOrlikEntity, BinarySensorEntity):
    """Zapnuto, dokud jsou data čerstvá.

    Stáří se počítá z času měření proti aktuálnímu času, ne z pole
    'vek_dat_min' v datech — to je spočítané v okamžiku publikace, takže
    kdyby se sběr dat zastavil, zůstalo by navěky nízké a tahle entita
    by mlčela právě ve chvíli, kdy má upozornit.
    """

    _attr_name = "Data aktuální"
    _attr_icon = "mdi:database-check"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "data_aktualni")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        cas = data.get("cas_mereni")
        if isinstance(cas, str):
            zmereno = dt_util.parse_datetime(cas)
            if zmereno is not None and zmereno.tzinfo is not None:
                return (dt_util.utcnow() - zmereno) <= MEZ
        vek = data.get("vek_dat_min")
        if isinstance(vek, bool) or not isinstance(vek, (int, float)):
            return None
        return vek <= MEZ.total_seconds() / 60


class VdOrlikOdtokTece(VdOrlikEntity, BinarySensorEntity):
    """Zapnuto, když se právě pouští.

    Orlík jede špičkově — odtok je většinu dne nula a pak pár hodin přes
    400 m³/s. Tahle entita je určená pro automatizace: upozornění na začátek
    odpouštění, zavření vrat na molu, cokoli, co se má stát právě tehdy.
    """

    _attr_name = "Odtok teče"
    _attr_icon = "mdi:water-alert"
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "odtok_tece")

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        tece = data.get("odtok_tece_ted")
        if isinstance(tece, bool):
            return tece
        odtok, prah = data.get("odtok"), data.get("odtok_tece_nad")
        if isinstance(odtok, bool) or not isinstance(odtok, (int, float)):
            return None
        if isinstance(prah, bool) or not isinstance(prah, (int, float)):
            prah = 5.0
        return odtok > prah

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        data = self.coordinator.data or {}
        return {"prah_m3_s": data.get("odtok_tece_nad", 5.0)}
