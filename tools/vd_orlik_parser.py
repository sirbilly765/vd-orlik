#!/usr/bin/env python3
"""VD Orlik - parser dat Povodi Vltavy (v3.3).

Tato kopie bezi v GitHub Actions a publikuje vysledek jako orlik.json.
Home Assistant si ho pak jen stahuje - na pvl.cz chodi jeden klient, ne kazda instalace.

(Aktualni hodnoty + historicka archivace,
24h/7d/30d statistiky, denni a tydenni souhrny).

Kazdy beh (jeden GET na PVL Mereni.aspx) parsuje:
  A) sekci "Aktualni hodnoty" - hladina, objem, pritok, odtok, cas
  B) celou dostupnou historickou tabulku (Datum/Hladina/Odtok)

Vse se merguje do /config/vd_orlik_history.json:
  - retence 45 dni (RETENCE_DNI)
  - dedup podle timestampu, aktualizace pri opakovanem vyskytu
  - atomicky zapis (tempfile + os.replace)
  - Europe/Prague, epoch aritmetika (DST-safe)
  - poskozeny soubor -> bezpecny reset, parser nespadne

Odvozene statistiky:
  - delta_hladina_24h/7d/30d  (nejblizsi historicky vzorek k cilovemu casu)
  - odtok_24h/7d/30d          (trapezova integrace, kvalitni brany)
  - bilance_24h/7d/30d        (z VLASTNI cache objemu - PVL objem historii nema;
                               objem se cte prolozenim primky pres okno +-60 min)
  - pritok_24h/7d/30d         (= odtok + bilance stejneho obdobi)
  - odtok_prumer_24h          (prumerny odtok za 24 h v m3/s)
  - odtok_hodin_24h           (kolik hodin z 24 skutecne teklo - spickovy provoz)
  - denni_data                (rozpad po dnech, max 35 poslednich DOKONCENYCH dnu;
                               vc. hladina_min/max/konec - denni rada pro graf hladiny,
                               ktera nezavisi na retenci recorderu)
  - tydenni_data              (kalendarni tydny Po-Ne z denni_data, min. 5-6 tydnu)

SSL: lokalni CA bundle /config/vd_orlik_ca.pem (nemeni system ani certifi).
NAVRATOVY KOD: vzdy 0 pokud lze vypsat platny JSON.
"""
import html as htmllib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta
from html.parser import HTMLParser

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        TZ = ZoneInfo("Europe/Prague")
        TZ_CHYBA = None
    except ZoneInfoNotFoundError:
        TZ = None
        TZ_CHYBA = "zoneinfo: Europe/Prague nedostupna (chybi tzdata)"
except ImportError:
    TZ = None
    TZ_CHYBA = "modul zoneinfo neni dostupny (Python < 3.9)"

ZDE = os.path.dirname(os.path.abspath(__file__))
CA_BUNDLE = os.environ.get("VD_ORLIK_CA", os.path.join(ZDE, "vd_orlik_ca.pem"))
PVL_URL = "https://www.pvl.cz/portal/Nadrze/cz/pc/Mereni.aspx?id=VLOR&oid=2"
HISTORY_FILE = os.environ.get("VD_ORLIK_HISTORY", os.path.join(ZDE, "vd_orlik_history.json"))
TIMEOUT = 25

LIM_HLADINA = (320.0, 365.0)
LIM_OBJEM = (0.0, 800.0)
LIM_PRUTOK = (0.0, 5000.0)

RETENCE_DNI = 45                  # min. 40 dni pozadovano, 45 doporuceno
MIN_POKRYTI_H = 22.0              # baze pro 24h; skaluje se pro 7d/30d
MIN_VZORKU = 20                   # baze pro 24h; skaluje se pro 7d/30d
MAX_MEZERA_MIN = 120.0
TOLERANCE_24H_MIN = 90.0
TOLERANCE_7D_MIN = 360.0          # 6 h
TOLERANCE_30D_MIN = 720.0         # 12 h - stary bod je vzacnejsi, tolerance vetsi
ZASOBNI_HLADINA = 349.90
MAX_RETENCNI_HLADINA = 353.60
MAX_DNI_DENNI_DATA = 35
MIN_TYDNU_VYSTUP = 6
OKNO_OBJEM_MIN = 60               # +-minut pro prolozeni primkou pri cteni objemu
ODTOK_TECE_NAD = 5.0              # m3/s - nad timto prahem se odtok povazuje za "tece"


def ts(dt):
    """Epoch. NUTNE pro aritmetiku pres DST prechody."""
    return dt.timestamp()


def vysledek(ok, **kw):
    now = datetime.now(TZ) if TZ else None
    d = {"ok": ok, "fetched_at": now.isoformat() if now else None}
    d.update(kw)
    text = json.dumps(d, ensure_ascii=False)
    cil = os.environ.get("VD_ORLIK_OUT")
    if cil:
        atomic_write_text(cil, text)
    print(text)
    return 0


def atomic_write_text(path, text):
    dirn = os.path.dirname(path) or "."
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix=".vd_orlik_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------- HTML tabulky
class TableParser(HTMLParser):
    """Kolspan/rowspan-aware parser tabulek."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self._t, self._r, self._c, self._attrs = [], None, None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            if self._t is not None:
                self.tables.append(self._t)
            self._t = []
        elif tag == "tr" and self._t is not None:
            self._flush_cell()
            self._r = []
        elif tag in ("td", "th") and self._r is not None:
            self._flush_cell()
            self._c, self._attrs = [], (a, tag == "th")
        elif tag == "br" and self._c is not None:
            self._c.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._flush_cell()
        elif tag == "tr" and self._r is not None:
            self._flush_cell()
            self._t.append(self._r)
            self._r = None
        elif tag == "table" and self._t is not None:
            self._flush_cell()
            if self._r:
                self._t.append(self._r)
            self._r = None
            self.tables.append(self._t)
            self._t = None

    def handle_data(self, data):
        if self._c is not None:
            self._c.append(data)

    def _flush_cell(self):
        if self._c is None or self._r is None:
            self._c = self._attrs = None
            return
        a, is_th = self._attrs
        txt = htmllib.unescape("".join(self._c)).replace("\xa0", " ")
        txt = re.sub(r"\s+", " ", txt).strip()
        self._r.append((txt, int(a.get("colspan", 1) or 1), int(a.get("rowspan", 1) or 1), is_th))
        self._c = self._attrs = None


def tables_of(html_text):
    p = TableParser()
    p.feed(html_text)
    p.close()
    return p.tables


def header_leaves(rows):
    grid, n = {}, 0
    for ri, row in enumerate(rows):
        ci = 0
        for txt, cs, rs, _ in row:
            while (ri, ci) in grid:
                ci += 1
            for dr in range(rs):
                for dc in range(cs):
                    grid[(ri + dr, ci + dc)] = txt
            ci += cs
            n = max(n, ci)
    out = []
    for c in range(n):
        seen, uniq = set(), []
        for r in range(len(rows)):
            p = grid.get((r, c), "")
            if p and p not in seen:
                seen.add(p)
                uniq.append(p)
        out.append(" | ".join(uniq))
    return out


def num(s):
    """Bezpecny prevod ceskeho cisla ('339,85' -> 339.85). '-'/prazdne -> None."""
    if s is None:
        return None
    s = str(s).replace("\xa0", "").replace(" ", "").strip()
    if s in ("", "-", "–", "—"):
        return None
    s = s.replace(",", ".")
    return float(s) if re.fullmatch(r"-?\d+(\.\d+)?", s) else None


def rozsah(v, lo, hi):
    return v is not None and lo <= v <= hi


DT_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})\s+(\d{2}):(\d{2})$")


def as_dt(s):
    m = DT_RE.match((s or "").strip())
    if not m:
        return None
    d, mo, y, h, mi = map(int, m.groups())
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def parse_aktualni(tables):
    keys = {"hladina": re.compile(r"hladina\s+vody", re.I),
            "objem": re.compile(r"^objem", re.I),
            "pritok": re.compile(r"^p[rř][ií]tok", re.I),
            "odtok": re.compile(r"^odtok", re.I)}
    for t in tables:
        flat = " ".join(c[0] for row in t for c in row)
        m = re.search(r"Aktu[aá]ln[ií]\s+hodnoty\s*\(?\s*(\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})",
                      flat, re.I)
        if not m:
            continue
        cas = as_dt(m.group(1))
        out = {"cas_mereni": cas, "hladina": None, "objem": None, "pritok": None, "odtok": None}
        for row in t:
            if len(row) < 2:
                continue
            label, value = row[0][0], row[1][0]
            for k, rx in keys.items():
                if rx.search(label):
                    out[k] = num(value)
        if cas and out["hladina"] is not None:
            return out
    return None


def parse_historicka_tabulka(tables):
    """Rada (datum, hladina, odtok) - sloupce podle nazvu v hlavicce, colspan-safe."""
    best = []
    for t in tables:
        if len(t) < 3:
            continue
        hdr_rows = [r for r in t[:3] if any(c[3] for c in r) or
                    any(re.search(r"hladina|odtok|datum|referen", c[0], re.I) for c in r)]
        if not hdr_rows:
            continue
        leaves = header_leaves(hdr_rows)
        idx = {}
        for i, name in enumerate(leaves):
            low = name.lower()
            if "hladina" in low and "hladina" not in idx:
                idx["hladina"] = i
            elif "odtok" in low and "odtok" not in idx:
                idx["odtok"] = i
            elif ("datum" in low or "referen" in low) and "datum" not in idx:
                idx["datum"] = i
        if not {"datum", "hladina", "odtok"} <= set(idx):
            continue
        rows = []
        for row in t[len(hdr_rows):]:
            if len(row) <= max(idx.values()):
                continue
            dt = as_dt(row[idx["datum"]][0])
            if dt is None:
                continue
            rows.append((dt, num(row[idx["hladina"]][0]), num(row[idx["odtok"]][0])))
        if len(rows) > len(best):
            best = rows
    best.sort(key=lambda r: ts(r[0]))
    return best


# ------------------------------------------------------------------------ cache
def cache_load():
    try:
        d = json.load(open(HISTORY_FILE, encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
        d.setdefault("hladina_odtok", [])
        d.setdefault("objem", [])
        d.setdefault("last_ok", None)
        return d
    except Exception:
        return {"hladina_odtok": [], "objem": [], "last_ok": None}   # poskozeny soubor -> bezpecny reset


def atomic_write(path, data):
    dirn = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=dirn, prefix=".vd_orlik_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def merge_hladina_odtok(existing, rady, now):
    by_t = {v["t"]: v for v in existing if isinstance(v, dict) and "t" in v}
    for dt, hl, od in rady:
        key = dt.isoformat()
        by_t[key] = {"t": key, "h": hl, "o": od}
    hranice = ts(now) - RETENCE_DNI * 86400
    out = [v for v in by_t.values() if _safe_ts(v["t"]) >= hranice]
    out.sort(key=lambda v: _safe_ts(v["t"]))
    return out


def merge_objem(existing, cas_mereni, objem, now):
    if objem is None or cas_mereni is None:
        return existing
    by_t = {v["t"]: v for v in existing if isinstance(v, dict) and "t" in v}
    by_t[cas_mereni.isoformat()] = {"t": cas_mereni.isoformat(), "v": objem}
    hranice = ts(now) - RETENCE_DNI * 86400
    out = [v for v in by_t.values() if _safe_ts(v["t"]) >= hranice]
    out.sort(key=lambda v: _safe_ts(v["t"]))
    return out


def _safe_ts(iso):
    try:
        return ts(datetime.fromisoformat(iso))
    except Exception:
        return 0.0


def najdi_nejblizsi(serie, cil_ts, tolerance_min):
    """Vzorek nejblizsi cilovemu casu; None mimo toleranci."""
    if not serie:
        return None
    nej = min(serie, key=lambda v: abs(_safe_ts(v["t"]) - cil_ts))
    return nej if abs(_safe_ts(nej["t"]) - cil_ts) <= tolerance_min * 60 else None


def objem_v_case(serie, cil_ts, tolerance_min, okno_min=OKNO_OBJEM_MIN):
    """Objem v presnem case: prolozi primku vzorky v okne +-okno_min a vrati
    jeji hodnotu v cilovem case. Vraci float nebo None.

    PVL hlasi objem po skocich ~0,15 mil. m3 (= 1 cm hladiny) a k teze hladine
    hlasi v ruznych chvilich ruzne hodnoty. Prolozeni pres vic vzorku toto
    kvantovani prumeruje a soucasne interpoluje na presny cilovy cas - merene
    o ~32 % mensi sum v dennim pritoku nez puvodni "nejblizsi vzorek".

    Okno 60 min je optimum pro vzorkovani po 30 minutach. Delsi okno je HORSI:
    objem se behem odpousteni meni prudce a primka ten tvar prestane vystihovat
    (merene: +-60 min 0,177 / +-90 min 0,210 / +-180 min 0,370 mil. m3).
    Pri zmene scan_interval je potreba okno preladit.

    Min. 2 vzorky v okne; jinak fallback na nejblizsi vzorek v toleranci.
    """
    if not serie:
        return None
    okno = okno_min * 60.0
    body = []
    for v in serie:
        if v.get("v") is None:
            continue
        dx = _safe_ts(v["t"]) - cil_ts
        if abs(dx) <= okno:
            body.append((dx, v["v"]))
    if len(body) >= 2:
        n = len(body)
        sx = sum(dx for dx, _ in body)
        sy = sum(y for _, y in body)
        sxx = sum(dx * dx for dx, _ in body)
        sxy = sum(dx * y for dx, y in body)
        det = n * sxx - sx * sx
        if det != 0:
            # y = a*dx + b ; hodnota v cilovem case je b (dx = 0)
            return round((sy * sxx - sx * sxy) / det, 3)
        return round(sy / n, 3)
    nej = najdi_nejblizsi(serie, cil_ts, tolerance_min)
    return nej["v"] if nej and nej.get("v") is not None else None


def integruj_odtok(serie, cas, okno_h):
    """Trapezova integrace 'hladina_odtok' serie za poslednich `okno_h` hodin.
    Kvalitni brany se skaluji linearne s velikosti okna vuci referencnim 24h."""
    hranice, konec = ts(cas) - okno_h * 3600, ts(cas)
    syrove = [v for v in serie if v.get("o") is not None and hranice <= _safe_ts(v["t"]) <= konec]
    videno, win = set(), []
    for v in sorted(syrove, key=lambda v: _safe_ts(v["t"]), reverse=True):
        k = _safe_ts(v["t"])
        if k in videno:
            continue
        videno.add(k)
        win.append(v)
    win.sort(key=lambda v: _safe_ts(v["t"]))
    res = {"odtok_mil_m3": None, "pocet_vzorku": len(win), "pokryti_h": 0.0,
           "max_mezera_min": None, "od": None, "do": None,
           "metoda": "trapezova integrace merene rady PVL", "duvod_neplatnosti": None}
    if len(win) < 2:
        res["duvod_neplatnosti"] = f"pouze {len(win)} vzorku"
        return res
    kroky = [_safe_ts(win[i + 1]["t"]) - _safe_ts(win[i]["t"]) for i in range(len(win) - 1)]
    delka = _safe_ts(win[-1]["t"]) - _safe_ts(win[0]["t"])
    res.update({"od": win[0]["t"], "do": win[-1]["t"],
                "pokryti_h": round(delka / 3600.0, 2),
                "max_mezera_min": round(max(kroky) / 60.0, 1)})
    min_pokryti = MIN_POKRYTI_H if okno_h == 24 else okno_h * (MIN_POKRYTI_H / 24)
    min_vzorku = MIN_VZORKU if okno_h == 24 else int(MIN_VZORKU * okno_h / 24)
    duvody = []
    if delka / 3600.0 < min_pokryti:
        duvody.append(f"pokryti {delka/3600:.2f} h < {min_pokryti:.1f} h")
    if len(win) < min_vzorku:
        duvody.append(f"pouze {len(win)} vzorku < {min_vzorku}")
    if max(kroky) / 60.0 > MAX_MEZERA_MIN:
        duvody.append(f"mezera {max(kroky)/60:.0f} min > {MAX_MEZERA_MIN:.0f} min")
    if min(kroky) <= 0:
        duvody.append("nulovy/zaporny casovy krok")
    if duvody:
        res["duvod_neplatnosti"] = "; ".join(duvody)
        return res
    res["odtok_mil_m3"] = round(
        sum((win[i]["o"] + win[i + 1]["o"]) / 2 * kroky[i] for i in range(len(win) - 1)) / 1e6, 3)
    return res


def hodin_s_odtokem(serie, cas, okno_h, prah=ODTOK_TECE_NAD):
    """Kolik hodin z poslednich okno_h skutecne teklo (odtok > prah m3/s).

    VD Orlik jede spickove: odtok je vetsinu dne presna nula a pak 3-6 hodin
    pres 400 m3/s. Prumerna hodnota proto o provozu skoro nic nerika - tohle
    doplnuje, jak dlouho se opravdu poustelo.

    Scita delku intervalu nad prahem; prechod prahem mezi dvema vzorky se
    linearne interpoluje. Vraci hodiny (float) nebo None pri malo vzorcich.
    """
    hranice, konec = ts(cas) - okno_h * 3600, ts(cas)
    win = sorted([v for v in serie if v.get("o") is not None
                  and hranice <= _safe_ts(v["t"]) <= konec],
                 key=lambda v: _safe_ts(v["t"]))
    if len(win) < 2:
        return None
    sekund = 0.0
    for i in range(len(win) - 1):
        krok = _safe_ts(win[i + 1]["t"]) - _safe_ts(win[i]["t"])
        if krok <= 0:
            continue
        a, b = win[i]["o"], win[i + 1]["o"]
        if a > prah and b > prah:
            sekund += krok
        elif a > prah or b > prah:
            rozdil = abs(b - a)
            podil = (max(a, b) - prah) / rozdil if rozdil > 0 else 1.0
            sekund += krok * min(1.0, max(0.0, podil))
    return round(sekund / 3600.0, 1)


def denni_rozpad(hl_serie, ob_serie, cas_ted, max_dni=MAX_DNI_DENNI_DATA):
    """Rozpad po dnech (00:00-24:00 Europe/Prague) za poslednich max_dni DOKONCENYCH dnu."""
    dny = []
    dnes = cas_ted.date()
    for offset in range(1, max_dni + 1):
        den = dnes - timedelta(days=offset)
        zac = datetime(den.year, den.month, den.day, 0, 0, tzinfo=TZ)
        kon = zac + timedelta(days=1)
        zac_ts, kon_ts = ts(zac), ts(kon)
        hl_okno = [v for v in hl_serie
                   if v.get("h") is not None and zac_ts <= _safe_ts(v["t"]) < kon_ts]
        if len(hl_okno) < 2:
            continue
        hl_okno.sort(key=lambda v: _safe_ts(v["t"]))
        hl_hodnoty = [v["h"] for v in hl_okno]
        okno_odtok = [v for v in hl_serie
                      if v.get("o") is not None and zac_ts <= _safe_ts(v["t"]) < kon_ts]
        okno_odtok.sort(key=lambda v: _safe_ts(v["t"]))
        odtok_den = None
        if len(okno_odtok) >= 2:
            kroky = [_safe_ts(okno_odtok[i + 1]["t"]) - _safe_ts(okno_odtok[i]["t"]) for i in range(len(okno_odtok) - 1)]
            if all(k > 0 for k in kroky) and (_safe_ts(okno_odtok[-1]["t"]) - _safe_ts(okno_odtok[0]["t"])) >= 20 * 3600:
                odtok_den = round(sum((okno_odtok[i]["o"] + okno_odtok[i + 1]["o"]) / 2 * kroky[i]
                                     for i in range(len(okno_odtok) - 1)) / 1e6, 3)
        # cela rada, ne jen vzorky uvnitr dne - prolozeni potrebuje body z obou stran pulnoci
        objem_start = objem_v_case(ob_serie, ts(zac), 120)
        objem_konec = objem_v_case(ob_serie, ts(kon), 120)
        zaznam = {
            "datum": den.isoformat(),
            "hladina_start": hl_okno[0]["h"], "hladina_konec": hl_okno[-1]["h"],
            "hladina_min": round(min(hl_hodnoty), 2), "hladina_max": round(max(hl_hodnoty), 2),
            "zmena_hladiny_cm": round((hl_okno[-1]["h"] - hl_okno[0]["h"]) * 100, 1),
            "objem_start": objem_start,
            "objem_konec": objem_konec,
            "odtok_mil_m3": odtok_den,
            "bilance_mil_m3": None, "pritok_mil_m3": None,
        }
        if zaznam["objem_start"] is not None and zaznam["objem_konec"] is not None:
            zaznam["bilance_mil_m3"] = round(zaznam["objem_konec"] - zaznam["objem_start"], 3)
            if odtok_den is not None:
                zaznam["pritok_mil_m3"] = round(odtok_den + zaznam["bilance_mil_m3"], 3)
        dny.append(zaznam)
    dny.sort(key=lambda d: d["datum"], reverse=True)
    return dny


def tydenni_rozpad(denni, cas_ted, min_tydnu=MIN_TYDNU_VYSTUP):
    """Agreguje denni_data (nejnovejsi napřed) do kalendarnich tydnu Po-Ne.
    Aktualni (probihajici) tyden je oznacen probiha=True a nikdy nema kompletni=True.
    Sumy (pritok/odtok/bilance) se pocitaji jen pro kompletni tydny se 7 platnymi dny."""
    dnes = cas_ted.date()
    aktualni_tyden = dnes.isocalendar()[:2]        # (ISO rok, ISO tyden)

    skupiny = {}
    for d in denni:
        den = datetime.fromisoformat(d["datum"]).date()
        klic = den.isocalendar()[:2]
        skupiny.setdefault(klic, []).append(d)

    tydny = []
    for (rok, cislo), dny in skupiny.items():
        dny_serazene = sorted(dny, key=lambda x: x["datum"])
        pondeli = datetime.fromisoformat(dny_serazene[0]["datum"]).date() - timedelta(
            days=datetime.fromisoformat(dny_serazene[0]["datum"]).date().weekday())
        nedele = pondeli + timedelta(days=6)
        probiha = (rok, cislo) == aktualni_tyden
        kompletni = (not probiha) and len(dny_serazene) == 7

        ma_vsechny_hodnoty = all(
            x.get("odtok_mil_m3") is not None and x.get("bilance_mil_m3") is not None
            for x in dny_serazene
        )
        pritok_celkem = odtok_celkem = bilance_celkem = None
        if kompletni and ma_vsechny_hodnoty:
            odtok_celkem = round(sum(x["odtok_mil_m3"] for x in dny_serazene), 3)
            bilance_celkem = round(sum(x["bilance_mil_m3"] for x in dny_serazene), 3)
            pritok_celkem = round(odtok_celkem + bilance_celkem, 3)

        zmena_hladiny_cm = None
        prvni_h = dny_serazene[0].get("hladina_start")
        posledni_h = dny_serazene[-1].get("hladina_konec")
        if prvni_h is not None and posledni_h is not None:
            zmena_hladiny_cm = round((posledni_h - prvni_h) * 100, 1)

        tydny.append({
            "rok": rok, "tyden": cislo,
            "od": pondeli.isoformat(), "do": nedele.isoformat(),
            "probiha": probiha, "kompletni": kompletni,
            "pocet_dnu": len(dny_serazene),
            "pritok_mil_m3": pritok_celkem, "odtok_mil_m3": odtok_celkem,
            "bilance_mil_m3": bilance_celkem, "zmena_hladiny_cm": zmena_hladiny_cm,
        })

    tydny.sort(key=lambda t: (t["rok"], t["tyden"]), reverse=True)
    return tydny[:min_tydnu + 1]     # +1 pro jistotu (probihajici + min_tydnu dokoncenych)


# ------------------------------------------------------------------------ main
def main():
    if TZ is None:
        return vysledek(False, chyba=TZ_CHYBA)

    try:
        ctx = (ssl.create_default_context(cafile=CA_BUNDLE) if os.path.isfile(CA_BUNDLE)
               else ssl.create_default_context())
    except Exception as e:
        return vysledek(False, chyba=f"CA bundle {CA_BUNDLE} nelze nacist: {type(e).__name__}: {e}")
    try:
        req = urllib.request.Request(PVL_URL, headers={"User-Agent": "HomeAssistant-VD-Orlik/3.2"})
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as r:
            raw = r.read()
            enc = r.headers.get_content_charset()
    except Exception as e:
        return vysledek(False, chyba=f"stazeni PVL selhalo: {type(e).__name__}: {e}")

    if not enc:
        m = re.search(rb'charset=["\']?\s*([\w-]+)', raw[:4096], re.I)
        enc = m.group(1).decode("ascii", "ignore") if m else "cp1250"
    try:
        html_text = raw.decode(enc)
    except Exception:
        html_text = raw.decode("cp1250", "replace")

    hu = htmllib.unescape(html_text)
    if not (re.search(r"VD\s+Orl[ií]k", hu, re.I) and re.search(r"Vltava", hu, re.I)):
        return vysledek(False, chyba="stranka neobsahuje 'VD Orlik' + 'Vltava' - nejspis chybna odpoved")

    tb = tables_of(html_text)
    akt = parse_aktualni(tb)
    rady = parse_historicka_tabulka(tb)

    if akt is None and not rady:
        return vysledek(False, chyba="ani 'Aktualni hodnoty' ani historicka tabulka nenalezeny")

    cas_mereni = akt["cas_mereni"] if akt else rady[-1][0]
    hladina = akt["hladina"] if akt else rady[-1][1]
    objem = akt["objem"] if akt else None
    pritok_okamzity = akt["pritok"] if akt else None
    odtok_okamzity = akt["odtok"] if akt else (rady[-1][2] if rady else None)

    # --- sanity ---
    now = datetime.now(TZ)
    chyby = []
    if not rozsah(hladina, *LIM_HLADINA):
        chyby.append(f"hladina {hladina} mimo rozsah {LIM_HLADINA}")
    if objem is not None and not rozsah(objem, *LIM_OBJEM):
        chyby.append(f"objem {objem} mimo rozsah {LIM_OBJEM}")
    if pritok_okamzity is not None and not rozsah(pritok_okamzity, *LIM_PRUTOK):
        chyby.append(f"pritok {pritok_okamzity} mimo rozsah {LIM_PRUTOK}")
    if odtok_okamzity is not None and not rozsah(odtok_okamzity, *LIM_PRUTOK):
        chyby.append(f"odtok {odtok_okamzity} mimo rozsah {LIM_PRUTOK}")
    if (ts(cas_mereni) - ts(now)) > 300:
        chyby.append("cas mereni je v budoucnosti")
    if chyby:
        return vysledek(False, chyba="; ".join(chyby))

    # --- cache: merge historie ---
    cache = cache_load()
    hl_serie = merge_hladina_odtok(cache["hladina_odtok"], rady, now)
    if akt and hladina is not None:
        hl_serie = merge_hladina_odtok(hl_serie, [(cas_mereni, hladina, odtok_okamzity)], now)
    ob_serie = merge_objem(cache["objem"], cas_mereni, objem, now)

    # --- delta hladiny 24h / 7d / 30d ---
    d24 = najdi_nejblizsi(hl_serie, ts(cas_mereni) - 86400, TOLERANCE_24H_MIN)
    d7 = najdi_nejblizsi(hl_serie, ts(cas_mereni) - 7 * 86400, TOLERANCE_7D_MIN)
    d30 = najdi_nejblizsi(hl_serie, ts(cas_mereni) - 30 * 86400, TOLERANCE_30D_MIN)
    delta_24h = round((hladina - d24["h"]) * 100, 1) if d24 and d24.get("h") is not None else None
    delta_7d = round((hladina - d7["h"]) * 100, 1) if d7 and d7.get("h") is not None else None
    delta_30d = round((hladina - d30["h"]) * 100, 1) if d30 and d30.get("h") is not None else None

    # --- odtok 24h / 7d / 30d (trapez) ---
    integ_24 = integruj_odtok(hl_serie, cas_mereni, 24)
    integ_7d = integruj_odtok(hl_serie, cas_mereni, 7 * 24)
    integ_30d = integruj_odtok(hl_serie, cas_mereni, 30 * 24)

    # --- rezim odtoku za poslednich 24 h (spickovy provoz) ---
    odtok_prumer_24h = (round(integ_24["odtok_mil_m3"] * 1e6 / 86400.0, 1)
                        if integ_24["odtok_mil_m3"] is not None else None)
    odtok_hodin_24h = hodin_s_odtokem(hl_serie, cas_mereni, 24)
    odtok_tece_ted = (odtok_okamzity is not None and odtok_okamzity > ODTOK_TECE_NAD)

    # --- bilance 24h / 7d / 30d (VLASTNI cache objemu, prolozeni +-60 min) ---
    bilance_24h = bilance_7d = bilance_30d = None
    ob24 = objem_v_case(ob_serie, ts(cas_mereni) - 86400, TOLERANCE_24H_MIN)
    ob7 = objem_v_case(ob_serie, ts(cas_mereni) - 7 * 86400, TOLERANCE_7D_MIN)
    ob30 = objem_v_case(ob_serie, ts(cas_mereni) - 30 * 86400, TOLERANCE_30D_MIN)
    if objem is not None and ob24 is not None:
        bilance_24h = round(objem - ob24, 3)
    if objem is not None and ob7 is not None:
        bilance_7d = round(objem - ob7, 3)
    if objem is not None and ob30 is not None:
        bilance_30d = round(objem - ob30, 3)

    pritok_24h = (round(integ_24["odtok_mil_m3"] + bilance_24h, 3)
                  if integ_24["odtok_mil_m3"] is not None and bilance_24h is not None else None)
    pritok_7d = (round(integ_7d["odtok_mil_m3"] + bilance_7d, 3)
                 if integ_7d["odtok_mil_m3"] is not None and bilance_7d is not None else None)
    pritok_30d = (round(integ_30d["odtok_mil_m3"] + bilance_30d, 3)
                  if integ_30d["odtok_mil_m3"] is not None and bilance_30d is not None else None)

    denni = denni_rozpad(hl_serie, ob_serie, cas_mereni)
    tydenni = tydenni_rozpad(denni, cas_mereni)

    atomic_write(HISTORY_FILE, {"hladina_odtok": hl_serie, "objem": ob_serie, "last_ok": None})

    vek_min = round((ts(now) - ts(cas_mereni)) / 60.0, 1)

    return vysledek(
        True,
        cas_mereni=cas_mereni.isoformat(),
        hladina=hladina,
        objem=objem,
        pritok=pritok_okamzity,
        odtok=odtok_okamzity,
        vek_dat_min=vek_min,
        rezerva_zasobni=round(ZASOBNI_HLADINA - hladina, 2),
        delta_hladina_24h=delta_24h,
        delta_hladina_7d=delta_7d,
        delta_hladina_30d=delta_30d,
        odtok_prumer_24h=odtok_prumer_24h,
        odtok_hodin_24h=odtok_hodin_24h,
        odtok_tece_ted=odtok_tece_ted,
        odtok_tece_nad=ODTOK_TECE_NAD,
        odtok_24h=integ_24["odtok_mil_m3"],
        odtok_24h_info=integ_24,
        odtok_7d=integ_7d["odtok_mil_m3"],
        odtok_7d_info=integ_7d,
        odtok_30d=integ_30d["odtok_mil_m3"],
        odtok_30d_info=integ_30d,
        bilance_24h=bilance_24h,
        bilance_7d=bilance_7d,
        bilance_30d=bilance_30d,
        pritok_24h=pritok_24h,
        pritok_7d=pritok_7d,
        pritok_30d=pritok_30d,
        denni_data=denni,
        tydenni_data=tydenni,
        historie_pocet_vzorku=len(hl_serie),
        historie_nejstarsi=hl_serie[0]["t"] if hl_serie else None,
        historie_nejnovejsi=hl_serie[-1]["t"] if hl_serie else None,
    )


if __name__ == "__main__":
    try:
        kod = main()
    except Exception as e:
        try:
            print(json.dumps({"ok": False, "chyba": f"fatalni chyba parseru: {type(e).__name__}: {e}"[:250]},
                             ensure_ascii=False))
        except Exception:
            pass
        kod = 0
    sys.exit(kod)
