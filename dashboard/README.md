# Dashboardy

Dvě hotové stránky, obě staví na entitách, které vytvoří integrace VD Orlík:

| Soubor | Pro co | Karta „Odtok teď" |
|---|---|---|
| `vd-orlik-pc.yaml` | širokou obrazovku, tři sloupce | vedle sebe odtok i přítok |
| `vd-orlik-mobil.yaml` | telefon, jeden sloupec | pod sebou |

Můžete použít jen jeden z nich — nejsou na sobě závislé.

## 1. Doinstalujte čtyři karty z HACS

V HACS → **Frontend** najděte a stáhněte:

* [`apexcharts-card`](https://github.com/RomRider/apexcharts-card) — všechny grafy
* [`button-card`](https://github.com/custom-cards/button-card) — hlavička, dlaždice, stavový řádek
* [`card-mod`](https://github.com/thomasloven/lovelace-card-mod) — doladění vzhledu
* [`layout-card`](https://github.com/thomasloven/lovelace-layout-card) — rozvržení do mřížky

Po stažení **restartujte Home Assistant** a v prohlížeči udělejte tvrdé
načtení stránky (Ctrl+F5), jinak HA nové karty ještě nezná.

## 2. Vložte stránku do svého dashboardu

1. Otevřete dashboard, kam to chcete přidat
2. Vpravo nahoře **tužka** (Upravit dashboard)
3. Znovu vpravo nahoře **tři tečky → Editor v nezpracovaném formátu**
   (Raw configuration editor)
4. Najděte v něm řádek `views:` a **obsah zvoleného souboru vložte jako další
   položku toho seznamu** — tedy pod stávající stránky, na stejné odsazení
5. Uložit

Stránka se objeví jako nová záložka *VD Orlík*.

> Pokud v editoru zatím žádné `views:` nemáte, protože dashboard vznikl
> automaticky, HA vás při první úpravě vyzve k převzetí kontroly — potvrďte
> a `views:` se vytvoří.

## 3. Když je stránka celá červená

* **„Custom element doesn't exist"** — karta z kroku 1 chybí nebo se ještě
  nenačetla. Zkontrolujte HACS → Frontend a dejte Ctrl+F5.
* **„Entity not available: sensor.vd_orlik_…"** — integrace není nainstalovaná
  nebo se ještě nestihla poprvé stáhnout. Podívejte se do
  **Nastavení → Zařízení a služby → VD Orlík**.
* **Grafy hladiny za 30 dní jsou prázdné** — to je v pořádku, kreslí se
  z dat integrace, ne z vaší databáze, a naplní se, jak poroste historie.

## Odkud grafy berou data

Delší grafy **nečtou z databáze Home Assistanta**, ale z atributů
`denni_data` a `tydenni_data` na entitě `sensor.vd_orlik_data`. Díky tomu
vypadají plné hned po instalaci a nevadí jim výchozí nastavení recorderu,
který drží jen 10 dní. Krátké grafy (24 h, 7 dní) naopak berou z historie
vaší instalace, takže se dokreslují postupně.
