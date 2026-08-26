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
MERENI = SensorStateClass.MEASUREMENT

# Velké atributy nemá smysl ukládat do databáze — dashboard je čte z živého
# stavu a jinak by recorder rostl o stovky kB denně.
NEUKLADAT = frozenset(
    {"dny", "tydny", "info", "denni_data", "tydenni_data",
     "odtok_24h_info", "odtok_7d_info", "odtok_30d_info"}
)


def _cas_se_zonou(hodnota: Any) -> datetime | None:
    """Vrátí datetime jen tehdy, když má časovou zónu.

    Naivní čas by Home Assistant u device_class TIMESTAMP odmítl výjimkou,
    která by proletěla až do rozesílání aktualizací a umlčela ostatní entity.
    Radši vrátíme None a tvářme se, že hodnota chybí.
    """
    if not isinstance(hodnota, str):
        return None
    cas = dt_util.parse_datetime(hodnota)
    if cas is None or cas.tzinfo is None:
        return None
    return cas


@dataclass(frozen=True, kw_only=True)
class VdOrlikSensorDescription(SensorEntityDescription):
    """Popis senzoru včetně toho, jak se z dat vytáhne hodnota."""

    hodnota: Callable[[dict], Any] = lambda d: None
    atributy: Callable[[dict], dict[str, Any]] | None = None


def _seznam(d: dict, klic: str) -> list:
    """Vrátí seznam, i když zdroj pošle nesmysl. Nikdy nevyhodí výjimku."""
    v = d.get(klic)
    return v if isinstance(v, list) else []


def _cislo(klic: str) -> Callable[[dict], Any]:
    def cti(d: dict) -> Any:
        v = d.get(klic)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return v

    return cti


SENZORY: tuple[VdOrlikSensorDescription, ...] = (
    VdOrlikSensorDescription(
        key="hladina", name="Hladina", icon="mdi:waves-arrow-up",
        native_unit_of_measurement="m n. m.", state_class=MERENI,
        suggested_display_precision=2, hodnota=_cislo("hladina"),
    ),
    VdOrlikSensorDescription(
        key="objem", name="Objem", icon="mdi:cup-water",
        native_unit_of_measurement=OBJEM, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo("objem"),
    ),
    VdOrlikSensorDescription(
        key="pritok", name="Přítok", icon="mdi:waves-arrow-right",
        native_unit_of_measurement=PRUTOK, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo("pritok"),
    ),
    VdOrlikSensorDescription(
        key="odtok", name="Odtok", icon="mdi:waterfall",
        native_unit_of_measurement=PRUTOK, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo("odtok"),
    ),
    VdOrlikSensorDescription(
        key="cas_mereni", name="Čas měření", icon="mdi:clock-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        hodnota=lambda d: _cas_se_zonou(d.get("cas_mereni")),
    ),
    VdOrlikSensorDescription(
        key="rezerva_zasobni", name="Rezerva zásobní", icon="mdi:arrow-expand-vertical",
        native_unit_of_measurement="m", state_class=MERENI,
        suggested_display_precision=2, hodnota=_cislo("rezerva_zasobni"),
    ),
    VdOrlikSensorDescription(
        key="odtok_prumer_24h", name="Odtok průměr 24h", icon="mdi:water-minus",
        native_unit_of_measurement=PRUTOK, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo("odtok_prumer_24h"),
    ),
    VdOrlikSensorDescription(
        key="odtok_hodin_24h", name="Odtok hodin 24h", icon="mdi:timer-sand",
        native_unit_of_measurement="h", state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo("odtok_hodin_24h"),
    ),
    VdOrlikSensorDescription(
        key="denni_data", name="Denní data", icon="mdi:calendar-week",
        native_unit_of_measurement="dní",
        hodnota=lambda d: len(_seznam(d, "denni_data")),
        atributy=lambda d: {"dny": _seznam(d, "denni_data")},
    ),
    VdOrlikSensorDescription(
        key="tydenni_data", name="Týdenní data", icon="mdi:calendar-range",
        native_unit_of_measurement="týdnů",
        hodnota=lambda d: len(_seznam(d, "tydenni_data")),
        atributy=lambda d: {"tydny": _seznam(d, "tydenni_data")},
    ),
)

# --- řady 24h / 7d / 30d ---
_RADY: list[VdOrlikSensorDescription] = []
for _p in ("24h", "7d", "30d"):
    _RADY.append(VdOrlikSensorDescription(
        key=f"delta_hladina_{_p}", name=f"Delta hladina {_p}",
        icon="mdi:elevation-decline", native_unit_of_measurement="cm",
        state_class=MERENI, suggested_display_precision=0,
        hodnota=_cislo(f"delta_hladina_{_p}"),
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"odtok_{_p}", name=f"Odtok {_p}", icon="mdi:water-minus",
        native_unit_of_measurement=OBJEM, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo(f"odtok_{_p}"),
        atributy=lambda d, p=_p: {"info": d.get(f"odtok_{p}_info")},
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"pritok_{_p}", name=f"Přítok {_p}", icon="mdi:water-plus",
        native_unit_of_measurement=OBJEM, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo(f"pritok_{_p}"),
    ))
    _RADY.append(VdOrlikSensorDescription(
        key=f"bilance_{_p}", name=f"Bilance {_p}", icon="mdi:scale-balance",
        native_unit_of_measurement=OBJEM, state_class=MERENI,
        suggested_display_precision=1, hodnota=_cislo(f"bilance_{_p}"),
    ))

VSECHNY = SENZORY + tuple(_RADY)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Vytvoří senzory."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entity: list[SensorEntity] = [
        VdOrlikSensor(coordinator, entry.entry_id, popis) for popis in VSECHNY
    ]
    entity.append(VdOrlikSouhrn(coordinator, entry.entry_id))
    async_add_entities(entity)


class VdOrlikSensor(VdOrlikEntity, SensorEntity):
    """Jedna hodnota z dat VD Orlík."""

    entity_description: VdOrlikSensorDescription
    _unrecorded_attributes = NEUKLADAT

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
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.atributy is None:
            return None
        try:
            return self.entity_description.atributy(self.coordinator.data or {})
        except (KeyError, TypeError, ValueError):
            return None


class VdOrlikSouhrn(VdOrlikEntity, SensorEntity):
    """Souhrnná entita s celou odpovědí v atributech.

    Existuje kvůli hotovým dashboardům, které z jednoho místa čtou i hodnoty,
    pro které nemá smysl zakládat vlastní entitu (příznak špičkování, meze,
    rozsah nasbírané historie). Stav 'ok' / 'chyba' odpovídá původnímu
    senzoru z YAML verze, takže stejný dashboard funguje v obou případech.
    """

    _attr_name = "Data"
    _attr_icon = "mdi:database"
    _unrecorded_attributes = NEUKLADAT

    def __init__(self, coordinator, entry_id: str) -> None:
        super().__init__(coordinator, entry_id, "data")

    @property
    def native_value(self) -> str:
        data = self.coordinator.data or {}
        return "ok" if data.get("ok") else "chyba"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return dict(self.coordinator.data or {})
