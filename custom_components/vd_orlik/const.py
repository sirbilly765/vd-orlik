"""Konstanty integrace VD Orlík."""

DOMAIN = "vd_orlik"

CONF_URL = "url"
CONF_INTERVAL = "interval"

# Veřejný datový zdroj. Data se stahují z portálu Povodí Vltavy jednou centrálně
# a publikují jako JSON, takže na pvl.cz chodí jeden klient místo každé instalace.
DEFAULT_URL = "https://sirbilly765.github.io/vd-orlik/orlik.json"
DEFAULT_INTERVAL = 30       # minut
MIN_INTERVAL = 10
MAX_INTERVAL = 180

ATTRIBUTION = "Data: Povodí Vltavy, s. p."
MANUFACTURER = "Povodí Vltavy, s. p."
