"""Senzory VD Orlík."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .entity import VdOrlikEntity

OBJEM = "mil. m³"
PRUTOK = "m³/s"


@dataclass(frozen=True, kw_only=True)
class VdOrlikSensorDescription(SensorEntityDescription):
    """Popis senzoru včetně toho, jak se z dat vytáhne hodnota."""

    hodnota: Callable[[dict], Any] = lambda d: None
    atributy: Callable[[dict], dict[str, Any]] | None = None


def _cislo(klic: str) -> Callable[[dict], Any]:
    return lambda d: d.get(klic)


SENZORY: tuple[VdOrlikSensorDescription, ...] = (
    VdOrlikSensorDescription(
        key="hladina", name="Hladina", icon="mdi:waves-arrow-up",
        native_unit_of_measurement="m n. m.", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2, hodnota=_cislo("hladina"),
    ),
    VdOrlikSensorDescription(
        key="objem", name="Objem", icon="mdi:cup-water",
        native_unit_of_measurement=OBJEM, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, hodnota=_cislo("objem"),
    ),
    VdOrlikSensorDescription(
        key="pritok", name="Přítok", icon="mdi:water-arrow-right",
        native_unit_of_measurement=PRUTOK, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, hodnota=_cislo("pritok"),
    ),
    VdOrlikSensorDescription(
        key="odtok", name="Odtok", icon="mdi:water-arrow-down",
        native_unit_of_measurement=PRUTOK, state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, hodnota=_cislo("odtok"),
    ),
    VdOrlikSensorDescription(
        key="cas_mereni", name="Čas měření", icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        hodnota=lambda d: dt_util.parse_datetime(d["cas_mereni"]) if d.get("cas_mereni") else None,
    ),
    VdOrlikSensorDescription(
        key="rezerva_zasobni", name="Rezerva zásobní", icon="mdi:arrow-expand-vertical",
        native_unit_of_measurement="m", state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2, hodnota=_cislo("rezerva_zasobni"),
    ),
    VdOrlikSensorDescription(
        key="odtok_prumer_24h", name="Odtok průměr 24h", icon="mdi:water-minus",
        native_unit_of_measurement=PRUTOK, suggested_display_precision=1,
        hodnota=_cislo("odtok_prumer_24h"),
    ),
    VdOrlikSensorDescription(
        key="odtok_hodin_24h", name="Odtok hodin 24h", icon="mdi:timer-sand",
        native_unit_of_measurement="h", suggested_display_precision=1,
        hodnota=_cislo("odtok_hodin_24h"),
    ),
    VdOrlikSensorDescription(
        key="denni_data", name="Denní data", icon="mdi:calendar-week",
        native_unit_of_measurement="dní",
        hodnota=lambda d: len(d.get("denni_data") or []),
        atributy=lambda d: {"dny": d.get("denni_data") or []},
    ),
    VdOrlikSensorDescription(
        key="tydenni_data", name="Týdenní data", icon="mdi:calendar-range",
        native_unit_of_measurement="týdnů",
        hodnota=lambda d: len(d.get("tydenni_data") or []),
        atributy=lambda d: {"tydny": d.get("tydenni_data") or []},
    ),
)

# --- řady 24h / 7d / 30d ---
_OBDOBI = (("24h", "24h"), ("7d", "7d"), ("30d", "30d"))

_RADY: list[VdOrlikSensorDescription] = []
for _pripona, _popis in _OBDOBI:
    _RADY.append(VdOrlikSensorDescription(
        key=f"delta_hladina_{_pripona}", name=f"Delta hladina {_popis}",
        icon="mdi:elevation-decline", native_unit_of_measurement="cm",
        suggested_display_precision=0, hodnota=_cislo(f"delta_hladina_{_pripona}"),
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"odtok_{_pripona}", name=f"Odtok {_popis}", icon="mdi:water-minus",
        native_unit_of_measurement=OBJEM, suggested_display_precision=1,
        hodnota=_cislo(f"odtok_{_pripona}"),
        atributy=lambda d, p=_pripona: {"info": d.get(f"odtok_{p}_info")},
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"pritok_{_pripona}", name=f"Přítok {_popis}", icon="mdi:water-plus",
        native_unit_of_measurement=OBJEM, suggested_display_precision=1,
        hodnota=_cislo(f"pritok_{_pripona}"),
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"bilance_{_pripona}", name=f"Bilance {_popis}", icon="mdi:scale-balance",
        native_unit_of_measurement=OBJEM, suggested_display_precision=1,
        hodnota=_cislo(f"bilance_{_pripona}"),
    ))

VSECHNY = SENZORY + tuple(_RADY)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Vytvoří senzory."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        VdOrlikSensor(coordinator, entry.entry_id, popis) for popis in VSECHNY
    )


class VdOrlikSensor(VdOrlikEntity, SensorEntity):
    """Jedna hodnota z dat VD Orlík."""

    entity_description: VdOrlikSensorDescription

    def __init__(self, coordinator, entry_id: str, popis: VdOrlikSensorDescription) -> None:
        super().__init__(coordinator, entry_id, popis.key)
        self.entity_description = popis

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data or {}
        try:
            return self.entity_description.hodnota(data)
        except (KeyError, TypeError, ValueError):
            return None

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.atributy is None:
            return None
        return self.entity_description.atributy(self.coordinator.data or {})
