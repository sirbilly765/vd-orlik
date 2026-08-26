# Testy

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pytest tests/
```

Jako vzorek dat se používá `docs/orlik.json`, takže testy běží bez připojení k síti.

## `test_vd_orlik.py` — jednotlivé části

Ověřuje, že se integrace načte, vytvoří očekávané entity se správnými
`entity_id` a že rozbitý datový zdroj shodí jen aktualizaci, ne celý
Home Assistant.

## `test_e2e.py` — koncová zkouška instalace

Postaví Home Assistant, nasimuluje datový zdroj na Pages a projde přesně to,
co udělá cizí uživatel: HACS → restart → *Přidat integraci* → *Odeslat*.
Kontroluje se, že:

* průvodce projde bez vyplňování a při instalaci nepadne žádná chyba do logu,
* naskočí všech 23 senzorů a 2 binární senzory a žádný není `unavailable`,
* hladina, objem, přítok a odtok sedí na hodnoty ze zdrojového JSONu,
* existuje souhrnná entita `sensor.vd_orlik_data` i s atributy — na ní stojí
  většina karet dashboardu,
* **každá entita, na kterou se odkazují dashboardy v `dashboard/`, opravdu
  existuje** (jinak by se novému uživateli karty vykreslily jako chyby),
* číselné senzory mají `state_class` a jednotku, takže z nich jsou dlouhodobé
  statistiky,
* výpadek zdroje ani rozbitý JSON integraci nepoloží a po obnovení naskočí zpět,
* **integrace za celou aktualizaci pošle právě jeden požadavek, a to na
  sdílený JSON** — žádné stahování přímo z Povodí Vltavy. Test spadne, kdyby
  jakýkoli další požadavek přibyl.

Senzory `odtok_30d`, `pritok_30d`, `bilance_30d` a `delta_hladina_30d` smějí
být prázdné, dokud historie nemá 30 dní bez mezery — je to záměr, radši nic
než číslo spočítané z děravé řady. Důvod je vidět v atributu `odtok_30d_info`.
