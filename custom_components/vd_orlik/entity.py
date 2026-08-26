"""Společný základ entit VD Orlík."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER
from .coordinator import VdOrlikCoordinator


class VdOrlikEntity(CoordinatorEntity[VdOrlikCoordinator]):
    """Základ pro všechny entity — jedno zařízení, jeden zdroj dat."""

    _attr_has_entity_name = True
    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: VdOrlikCoordinator, entry_id: str, klic: str) -> None:
        super().__init__(coordinator)
        self._klic = klic
        self._attr_unique_id = f"{entry_id}_{klic}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name="VD Orlík",
            manufacturer=MANUFACTURER,
            model="Vodní dílo Orlík (Vltava)",
            configuration_url="https://www.pvl.cz/portal/Nadrze/cz/pc/Mereni.aspx?id=VLOR&oid=2",
        )
