import json
import pathlib

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vd_orlik.const import CONF_URL, DOMAIN

DATA = json.loads(
    pathlib.Path("docs/orlik.json").read_text(encoding="utf-8")
)
URL = "http://test.local/orlik.json"


async def _nastav(hass: HomeAssistant, aioclient_mock, payload=DATA, status=200):
    aioclient_mock.get(URL, json=payload, status=status)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_URL: URL}, title="VD Orlík")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_nastaveni_projde(hass, aioclient_mock):
    entry = await _nastav(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED


async def test_entity_maji_ocekavana_id(hass, aioclient_mock):
    await _nastav(hass, aioclient_mock)
    ocekavane = {
        "sensor.vd_orlik_hladina": "335.62",
        "sensor.vd_orlik_objem": "359.4",
        "sensor.vd_orlik_odtok": "348.85",
        "sensor.vd_orlik_rezerva_zasobni": "14.28",
        "sensor.vd_orlik_odtok_24h": "5.807",
        "sensor.vd_orlik_odtok_prumer_24h": "67.2",
        "sensor.vd_orlik_odtok_hodin_24h": "5.8",
        "sensor.vd_orlik_delta_hladina_24h": "-32.0",
        "sensor.vd_orlik_denni_data": "25",
        "sensor.vd_orlik_tydenni_data": "5",
    }
    chybi, spatne = [], []
    for eid, hodnota in ocekavane.items():
        st = hass.states.get(eid)
        if st is None:
            chybi.append(eid)
        elif st.state != hodnota:
            spatne.append((eid, st.state, hodnota))
    assert not chybi, f"chybejici entity: {chybi}"
    assert not spatne, f"spatne hodnoty: {spatne}"


async def test_binary_sensor(hass, aioclient_mock):
    await _nastav(hass, aioclient_mock)
    st = hass.states.get("binary_sensor.vd_orlik_data_aktualni")
    assert st is not None and st.state == "on"


async def test_atributy_denni_a_tydenni(hass, aioclient_mock):
    await _nastav(hass, aioclient_mock)
    dny = hass.states.get("sensor.vd_orlik_denni_data").attributes["dny"]
    assert len(dny) == 25 and "hladina_min" in dny[0] and "zmena_hladiny_cm" in dny[0]
    tydny = hass.states.get("sensor.vd_orlik_tydenni_data").attributes["tydny"]
    assert len(tydny) == 5


async def test_pocet_entit(hass, aioclient_mock):
    await _nastav(hass, aioclient_mock)
    vse = [s for s in hass.states.async_entity_ids() if "vd_orlik" in s]
    assert len(vse) == 23, sorted(vse)


async def test_config_flow(hass):
    from homeassistant import config_entries
    vysledek = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert vysledek["type"] == "form"
    vysledek = await hass.config_entries.flow.async_configure(vysledek["flow_id"], {})
    assert vysledek["type"] == "create_entry"
    assert vysledek["title"] == "VD Orlík"


async def test_chybny_zdroj_neshodi_setup(hass, aioclient_mock):
    entry = await _nastav(hass, aioclient_mock, payload={"ok": False, "chyba": "test"})
    assert entry.state is ConfigEntryState.SETUP_RETRY
