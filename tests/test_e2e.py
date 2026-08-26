"""Koncová zkouška: čerstvá instalace integrace na reálných datech z Pages.

Simuluje přesně to, co udělá cizí uživatel:
  HACS -> stáhne -> restart -> Přidat integraci -> Odeslat
a ověří, že opravdu naskočí všechny entity s rozumnými hodnotami
a že na ně sedí dashboardy z repozitáře.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vd_orlik.const import DEFAULT_URL, DOMAIN

KOREN = Path(__file__).resolve().parents[1]
REPO = KOREN
DATA = json.loads((KOREN / "docs" / "orlik.json").read_text(encoding="utf-8"))


@pytest.fixture
def zdroj(aioclient_mock):
    """Nasimuluje https://sirbilly765.github.io/vd-orlik/orlik.json."""
    aioclient_mock.get(DEFAULT_URL, json=DATA)
    return aioclient_mock


async def _nainstaluj(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# ---------------------------------------------------------------- instalace


async def test_instalace_probehne(hass: HomeAssistant, zdroj) -> None:
    entry = await _nainstaluj(hass)
    assert entry.state is ConfigEntryState.LOADED


async def test_pruvodce_bez_vyplnovani(hass: HomeAssistant, zdroj) -> None:
    """Uživatel jen klikne Odeslat — nic nevyplňuje."""
    vysledek = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert vysledek["type"] == "form"
    vysledek = await hass.config_entries.flow.async_configure(
        vysledek["flow_id"], user_input={}
    )
    await hass.async_block_till_done()
    assert vysledek["type"] == "create_entry"


async def test_zadna_chyba_v_logu(hass: HomeAssistant, zdroj, caplog) -> None:
    caplog.set_level(logging.WARNING)
    await _nainstaluj(hass)
    zlobive = [
        z for z in caplog.records
        if z.levelno >= logging.ERROR and "vd_orlik" in z.name.lower() + z.getMessage().lower()
    ]
    assert not zlobive, [z.getMessage() for z in zlobive]


# ---------------------------------------------------------------- entity


async def test_vsechny_entity_naskoci(hass: HomeAssistant, zdroj) -> None:
    entry = await _nainstaluj(hass)
    reg = er.async_get(hass)
    zaznamy = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert len(zaznamy) >= 24, f"jen {len(zaznamy)} entit"

    chybne = []
    for z in zaznamy:
        stav = hass.states.get(z.entity_id)
        if stav is None:
            chybne.append((z.entity_id, "entita vůbec neexistuje"))
        elif stav.state == "unavailable":
            chybne.append((z.entity_id, "unavailable hned po instalaci"))
    assert not chybne, chybne


# Tyhle tři smějí být "neznámé", dokud historie nemá 30 dní bez mezery.
# Je to záměr: radši nic než číslo spočítané z děravé řady.
SMI_BYT_NEZNAME = {
    "sensor.vd_orlik_odtok_30d",
    "sensor.vd_orlik_pritok_30d",
    "sensor.vd_orlik_bilance_30d",
    "sensor.vd_orlik_delta_hladina_30d",
}


async def test_zadna_entita_neni_prazdna(hass: HomeAssistant, zdroj) -> None:
    """Na reálných datech musí mít každá entita skutečnou hodnotu."""
    entry = await _nainstaluj(hass)
    reg = er.async_get(hass)
    prazdne = [
        z.entity_id
        for z in er.async_entries_for_config_entry(reg, entry.entry_id)
        if z.entity_id not in SMI_BYT_NEZNAME
        and (hass.states.get(z.entity_id) or type("x", (), {"state": "unknown"})).state
        in ("unknown", "unavailable", "None", "")
    ]
    assert not prazdne, f"bez hodnoty: {prazdne}"


async def test_neuplna_historie_hlasi_duvod(hass: HomeAssistant, zdroj) -> None:
    """Když je 30denní řada děravá, musí být poznat proč."""
    await _nainstaluj(hass)
    souhrn = hass.states.get("sensor.vd_orlik_data")
    for eid in ("sensor.vd_orlik_odtok_30d", "sensor.vd_orlik_pritok_30d"):
        stav = hass.states.get(eid)
        if stav.state in ("unknown", "unavailable"):
            assert souhrn.attributes.get("odtok_30d_info"), (
                f"{eid} je prázdný, ale nikde není důvod"
            )


async def test_klicove_hodnoty_sedi_s_json(hass: HomeAssistant, zdroj) -> None:
    await _nainstaluj(hass)
    kontrola = {
        "sensor.vd_orlik_hladina": DATA["hladina"],
        "sensor.vd_orlik_objem": DATA["objem"],
        "sensor.vd_orlik_pritok": DATA["pritok"],
        "sensor.vd_orlik_odtok": DATA["odtok"],
    }
    for eid, ocekavano in kontrola.items():
        stav = hass.states.get(eid)
        assert stav is not None, f"{eid} neexistuje"
        assert float(stav.state) == pytest.approx(float(ocekavano)), eid


async def test_souhrnna_entita_ma_atributy(hass: HomeAssistant, zdroj) -> None:
    """Na sensor.vd_orlik_data stojí většina karet dashboardu."""
    await _nainstaluj(hass)
    stav = hass.states.get("sensor.vd_orlik_data")
    assert stav is not None, "sensor.vd_orlik_data chybí — dashboard by byl celý červený"
    assert stav.state == "ok"
    for klic in ("hladina", "odtok", "denni_data", "tydenni_data", "delta_hladina_24h"):
        assert klic in stav.attributes, f"v atributech chybí {klic}"
    assert len(stav.attributes["denni_data"]) == len(DATA["denni_data"])


async def test_binarni_senzory(hass: HomeAssistant, zdroj) -> None:
    await _nainstaluj(hass)
    aktualni = hass.states.get("binary_sensor.vd_orlik_data_aktualni")
    tece = hass.states.get("binary_sensor.vd_orlik_odtok_tece")
    assert aktualni is not None and tece is not None
    assert tece.state == ("on" if DATA["odtok"] > DATA["odtok_tece_nad"] else "off")


async def test_dlouhodoba_statistika(hass: HomeAssistant, zdroj) -> None:
    """Číselné senzory musí mít state_class, jinak nejsou grafy historie."""
    entry = await _nainstaluj(hass)
    reg = er.async_get(hass)
    bez = []
    for z in er.async_entries_for_config_entry(reg, entry.entry_id):
        if not z.entity_id.startswith("sensor."):
            continue
        stav = hass.states.get(z.entity_id)
        if stav is None:
            continue
        try:
            float(stav.state)
        except (TypeError, ValueError):
            continue  # textové / časové senzory
        if z.entity_id in ("sensor.vd_orlik_denni_data", "sensor.vd_orlik_tydenni_data"):
            continue  # stav je počet položek, statistika z něj nedává smysl
        if "state_class" not in stav.attributes:
            bez.append(z.entity_id)
    assert not bez, f"bez state_class: {bez}"


async def test_jednotky_jsou_vyplnene(hass: HomeAssistant, zdroj) -> None:
    entry = await _nainstaluj(hass)
    reg = er.async_get(hass)
    for z in er.async_entries_for_config_entry(reg, entry.entry_id):
        stav = hass.states.get(z.entity_id)
        if stav is None or "state_class" not in stav.attributes:
            continue
        assert stav.attributes.get("unit_of_measurement"), f"{z.entity_id} bez jednotky"


async def test_pocty_entit(hass: HomeAssistant, zdroj) -> None:
    entry = await _nainstaluj(hass)
    reg = er.async_get(hass)
    vsechny = er.async_entries_for_config_entry(reg, entry.entry_id)
    senzory = [z for z in vsechny if z.entity_id.startswith("sensor.")]
    binarni = [z for z in vsechny if z.entity_id.startswith("binary_sensor.")]
    assert len(senzory) == 23, [z.entity_id for z in senzory]
    assert len(binarni) == 2, [z.entity_id for z in binarni]


# ---------------------------------------------------------------- dashboardy


def _entity_z_dashboardu() -> set[str]:
    vzor = re.compile(r"\b((?:binary_)?sensor\.vd_orlik_[a-z0-9_]+)")
    nalezene: set[str] = set()
    for jmeno in ("vd-orlik-pc.yaml", "vd-orlik-mobil.yaml"):
        cesta = REPO / "dashboard" / jmeno
        if cesta.exists():
            nalezene |= set(vzor.findall(cesta.read_text(encoding="utf-8")))
    return nalezene


@pytest.mark.skipif(not (REPO / "dashboard").exists(), reason="dashboardy nejsou po ruce")
async def test_dashboardy_sedi_na_entity(hass: HomeAssistant, zdroj) -> None:
    """Každá entita, na kterou se dashboard odkazuje, musí existovat."""
    await _nainstaluj(hass)
    chybejici = sorted(e for e in _entity_z_dashboardu() if hass.states.get(e) is None)
    assert not chybejici, f"dashboard volá neexistující entity: {chybejici}"


# ---------------------------------------------------------------- provoz


async def test_vypadek_zdroje_nezpusobi_pad(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(DEFAULT_URL, json=DATA)
    entry = await _nainstaluj(hass)
    assert hass.states.get("sensor.vd_orlik_hladina").state != "unavailable"

    aioclient_mock.clear_requests()
    aioclient_mock.get(DEFAULT_URL, status=500)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert not coordinator.last_update_success

    aioclient_mock.clear_requests()
    aioclient_mock.get(DEFAULT_URL, json=DATA)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.last_update_success
    assert hass.states.get("sensor.vd_orlik_hladina").state != "unavailable"


async def test_rozbity_json_nezpusobi_pad(hass: HomeAssistant, aioclient_mock) -> None:
    aioclient_mock.get(DEFAULT_URL, text="tohle rozhodně není JSON")
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_odinstalace(hass: HomeAssistant, zdroj) -> None:
    entry = await _nainstaluj(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert hass.states.get("sensor.vd_orlik_hladina") in (None,) or \
        hass.states.get("sensor.vd_orlik_hladina").state == "unavailable"


async def test_jen_jeden_pozadavek_na_muj_server(hass: HomeAssistant, zdroj) -> None:
    """Integrace nesmí chodit nikam jinam než na jeden JSON (kvůli Povodí)."""
    await _nainstaluj(hass)
    adresy = {str(v[1]) for v in zdroj.mock_calls}
    assert adresy == {DEFAULT_URL}, adresy
    assert len(zdroj.mock_calls) == 1, f"{len(zdroj.mock_calls)} požadavků místo jednoho"


# ---------------------------------------------------------------- ikony


def _dostupne_ikony() -> set[str]:
    """Jména ikon, která opravdu existují v dodávaném Material Design Icons."""
    import glob
    import json as _json

    try:
        import hass_frontend
    except ImportError:  # pragma: no cover - jinde než v plné instalaci HA
        return set()
    zaklad = Path(hass_frontend.__file__).parent / "static" / "mdi"
    jmena: set[str] = set()
    for soubor in glob.glob(str(zaklad / "*.json")):
        with open(soubor, encoding="utf-8") as f:
            data = _json.load(f)
        if isinstance(data, dict):
            jmena |= set(data)
    return jmena


def test_vsechny_ikony_existuji() -> None:
    """Neexistující mdi: jméno se nikde nehlásí — ikona prostě zmizí."""
    import re

    ikony = _dostupne_ikony()
    if not ikony:
        pytest.skip("hass_frontend není nainstalované")

    spatne: dict[str, list[str]] = {}
    for cesta in (KOREN / "custom_components" / "vd_orlik").glob("*.py"):
        text = cesta.read_text(encoding="utf-8")
        chybne = sorted({i for i in re.findall(r"mdi:([a-z0-9-]+)", text) if i not in ikony})
        if chybne:
            spatne[cesta.name] = chybne
    assert not spatne, f"neexistující ikony: {spatne}"
