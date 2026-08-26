# Testy

Ověřují, že se integrace načte, vytvoří očekávané entity se správnými
`entity_id` a že rozbitý datový zdroj shodí jen aktualizaci, ne celý Home Assistant.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
pytest tests/
```

Jako vzorek dat se používá `docs/orlik.json`, takže test běží bez připojení k síti.
