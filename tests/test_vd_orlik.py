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
    """Čerstvé měření = zapnuto. Čas bereme relativně, ať test nezestárne."""
    from homeassistant.util import dt as dt_util
    cerstve = {**DATA, "cas_mereni": dt_util.now().isoformat()}
    await _nastav(hass, aioclient_mock, payload=cerstve)
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
    assert len(vse) == 25, sorted(vse)


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


async def test_souhrnna_entita_pro_dashboard(hass, aioclient_mock):
    """Dashboard čte sensor.vd_orlik_data — musí existovat a nést celý payload."""
    await _nastav(hass, aioclient_mock)
    st = hass.states.get("sensor.vd_orlik_data")
    assert st is not None and st.state == "ok"
    for klic in ("hladina", "rezerva_zasobni", "odtok_tece_ted", "odtok_tece_nad",
                 "historie_nejstarsi", "delta_hladina_24h", "bilance_24h",
                 "odtok_prumer_24h", "odtok_hodin_24h", "cas_mereni", "objem"):
        assert klic in st.attributes, f"chybí atribut {klic}"


async def test_naivni_cas_neumlci_ostatni(hass, aioclient_mock):
    """Čas bez zóny nesmí shodit aktualizaci zbytku entit."""
    data = {**DATA, "cas_mereni": "2026-08-25T22:00:00"}
    await _nastav(hass, aioclient_mock, payload=data)
    assert hass.states.get("sensor.vd_orlik_cas_mereni").state == "unknown"
    assert hass.states.get("sensor.vd_orlik_hladina").state == "335.62"
    assert hass.states.get("sensor.vd_orlik_odtok_24h").state == "5.807"


async def test_chybejici_hodnota_je_unknown_ne_unavailable(hass, aioclient_mock):
    """Chybějící 30denní data znamenají 'zatím nevím', ne 'entita nefunguje'."""
    data = {**DATA, "odtok_30d": None, "bilance_30d": None, "pritok_30d": None}
    await _nastav(hass, aioclient_mock, payload=data)
    st = hass.states.get("sensor.vd_orlik_odtok_30d")
    assert st.state == "unknown"
    assert "info" in st.attributes


async def test_bool_neprojde_jako_hladina(hass, aioclient_mock):
    entry = await _nastav(hass, aioclient_mock, payload={**DATA, "hladina": True})
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_stare_datum_zhasne_data_aktualni(hass, aioclient_mock):
    """Zamrzlý zdroj musí binary_sensor poznat, i když v datech tvrdí, že je vše svěží."""
    data = {**DATA, "cas_mereni": "2020-01-01T00:00:00+01:00", "vek_dat_min": 5.0}
    await _nastav(hass, aioclient_mock, payload=data)
    assert hass.states.get("binary_sensor.vd_orlik_data_aktualni").state == "off"


async def test_vsechny_ciselne_maji_state_class(hass, aioclient_mock):
    await _nastav(hass, aioclient_mock)
    bez = []
    for eid in hass.states.async_entity_ids("sensor"):
        st = hass.states.get(eid)
        if st.state in ("unknown", "unavailable", "ok"):
            continue
        try:
            float(st.state)
        except ValueError:
            continue
        if st.attributes.get("unit_of_measurement") in ("dní", "týdnů"):
            continue
        if "state_class" not in st.attributes:
            bez.append(eid)
    assert not bez, f"bez state_class: {bez}"


async def test_spatne_typy_v_datech_neshodi_entity(hass, aioclient_mock):
    """Zdroj pošle místo seznamů nesmysl — nic nesmí zmizet ani spadnout."""
    data = {**DATA, "denni_data": {"a": 1}, "tydenni_data": 5}
    await _nastav(hass, aioclient_mock, payload=data)
    for eid in ("sensor.vd_orlik_denni_data", "sensor.vd_orlik_tydenni_data"):
        st = hass.states.get(eid)
        assert st is not None, f"{eid} zmizel"
        assert st.state == "0"
    assert hass.states.get("sensor.vd_orlik_hladina").state == "335.62"


async def test_odtok_tece_pro_automatizace(hass, aioclient_mock):
    """Vzorek je z večerní špičky, takže odtok teče."""
    await _nastav(hass, aioclient_mock)
    st = hass.states.get("binary_sensor.vd_orlik_odtok_tece")
    assert st is not None and st.state == "on"
    assert st.attributes["prah_m3_s"] == 5.0


async def test_odtok_tece_v_klidu(hass, aioclient_mock):
    """Když se nepouští, entita musí zhasnout — i bez příznaku ve zdroji."""
    klid = {k: v for k, v in DATA.items() if k != "odtok_tece_ted"}
    klid["odtok"] = 0.0
    await _nastav(hass, aioclient_mock, payload=klid)
    assert hass.states.get("binary_sensor.vd_orlik_odtok_tece").state == "off"


async def test_reloady_neuklada_listenery(hass, aioclient_mock):
    entry = await _nastav(hass, aioclient_mock)
    for _ in range(15):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert len(entry.update_listeners) == 1
    assert len(hass.data[DOMAIN]) == 1
    assert entry.state is ConfigEntryState.LOADED
