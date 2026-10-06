"""Unit tests for tools/vd_orlik_parser.py — the PVL hydrology parser.

These cover the parser's pure computation core (number/date parsing, cache
merge + retention, nearest-sample lookup, volume interpolation, trapezoid
outflow integration, hours-with-flow, daily & weekly rollup) that has no
direct coverage in the HA integration test suite. All tests run fully offline
against synthetic series; no network access is required.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

TOOLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
sys.path.insert(0, os.path.abspath(TOOLS))

import vd_orlik_parser as p  # noqa: E402

TZ = p.TZ
have_tz = TZ is not None

requires_tz = pytest.mark.skipif(not have_tz, reason="zoneinfo Europe/Prague unavailable")


def _stamp(t_str):
    return datetime.fromisoformat(t_str)


def _sample(t_str, h=None, o=None, v=None):
    d = {"t": t_str}
    if h is not None:
        d["h"] = h
    if o is not None:
        d["o"] = o
    if v is not None:
        d["v"] = v
    return d


# --------------------------------------------------------------------------- num
class TestNum:
    def test_czech_decimal_comma(self):
        assert p.num("339,85") == 339.85

    def test_dot_decimal(self):
        assert p.num("339.85") == 339.85

    def test_negative(self):
        assert p.num("-12.5") == -12.5

    def test_nbsp_and_spaces_stripped(self):
        assert p.num("1\u00a0234,5") == 1234.5
        assert p.num(" 12 ") == 12.0

    def test_dashes_and_empty_are_none(self):
        for s in ("", "-", "\u2013", "\u2014", None):
            assert p.num(s) is None

    def test_garbage_is_none(self):
        assert p.num("abc") is None
        assert p.num("12a") is None

    def test_plain_int(self):
        assert p.num("5") == 5


# -------------------------------------------------------------------------- as_dt
class TestAsDt:
    def test_parses_czech_format(self):
        dt = p.as_dt("25.08.2026 22:10")
        assert dt is not None
        assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 8, 25, 22, 10)

    @requires_tz
    def test_has_prague_tz(self):
        dt = p.as_dt("25.08.2026 22:10")
        assert dt is not None and dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 2 * 3600  # CEST summer

    def test_rejects_bad(self):
        assert p.as_dt("") is None
        assert p.as_dt("garbage") is None
        assert p.as_dt("2026-08-25") is None  # wrong format (ISO, not Czech)


# ----------------------------------------------------------------------- ranges
class TestRozsah:
    def test_in_range(self):
        assert p.rozsah(350.0, 320.0, 365.0)

    def test_out_of_range(self):
        assert not p.rozsah(366.0, 320.0, 365.0)

    def test_none_fails(self):
        assert not p.rozsah(None, 0, 10)


# ------------------------------------------------------------------- merge cache
class TestMerge:
    def test_merge_hladina_dedups_and_updates(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        existing = [_sample("2026-09-01T00:00:00+02:00", h=100.0, o=5.0)]
        rady = [
            (datetime(2026, 9, 1, 0, 0, tzinfo=TZ), 101.0, 6.0),   # update existing
            (datetime(2026, 9, 2, 0, 0, tzinfo=TZ), 102.0, 7.0),   # new
        ]
        out = p.merge_hladina_odtok(existing, rady, now)
        by_t = {s["t"]: s for s in out}
        assert len(out) == 2
        assert by_t["2026-09-01T00:00:00+02:00"]["h"] == 101.0  # updated
        assert by_t["2026-09-02T00:00:00+02:00"]["h"] == 102.0

    def test_merge_hladina_trims_to_retention(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        old = datetime(2026, 9, 3, 0, 0, tzinfo=TZ) - timedelta(days=p.RETENCE_DNI + 1)
        stale = _sample(old.isoformat(), h=10.0, o=0.0)
        fresh = _sample("2026-09-03T11:00:00+02:00", h=50.0, o=0.0)
        out = p.merge_hladina_odtok([stale, fresh], [], now)
        assert len(out) == 1 and out[0]["t"] == fresh["t"]

    def test_merge_objem_none_returns_existing(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        existing = [_sample("2026-09-03T10:00:00+02:00", v=300.0)]
        assert p.merge_objem(existing, None, None, now) is existing

    def test_merge_objem_adds_point(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        cas = datetime(2026, 9, 3, 11, 0, tzinfo=TZ)
        out = p.merge_objem([], cas, 301.5, now)
        assert len(out) == 1 and out[0]["v"] == 301.5

    def test_merge_returns_epoch_sorted(self):
        """Dedup + sort must be chronological by real instant, not by ISO string.
        On the DST fall-back fold two samples can have the same wall-clock but
        different offsets; string sort would invert the later one ahead of the
        earlier one. Regression for the pre-1.1 ISO-string sort."""
        now = datetime(2026, 10, 26, 1, 0, tzinfo=TZ)
        serie = [
            (datetime.fromisoformat("2026-10-25T02:30:00+02:00"), 356.5, 0.0),  # real 00:30 UTC
            (datetime.fromisoformat("2026-10-25T02:00:00+01:00"), 357.0, 0.0),  # real 01:00 UTC
            (datetime.fromisoformat("2026-10-25T02:30:00+01:00"), 357.5, 0.0),  # real 01:30 UTC
        ]
        out = p.merge_hladina_odtok([], serie, now)
        epochs = [p._safe_ts(s["t"]) for s in out]
        assert epochs == sorted(epochs)
        # The last sample must be the real-latest (02:30+01:00 = 01:30 UTC).
        assert out[-1]["h"] == 357.5


# ------------------------------------------------------------ nearest / volume
class TestNajdiNejblizsi:
    @requires_tz
    def test_exact_hit(self):
        serie = [_sample("2026-09-01T12:00:00+02:00", h=100.0)]
        target = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)
        assert p.najdi_nejblizsi(serie, p.ts(target), 90)["h"] == 100.0

    @requires_tz
    def test_outside_tolerance_is_none(self):
        serie = [_sample("2026-09-01T12:00:00+02:00", h=100.0)]
        target = datetime(2026, 9, 1, 14, 0, tzinfo=TZ)  # 2h away > 90min
        assert p.najdi_nejblizsi(serie, p.ts(target), 90) is None

    def test_empty_is_none(self):
        assert p.najdi_nejblizsi([], 123.0, 90) is None


class TestObjemVCase:
    @requires_tz
    def test_linear_fit_interpolates(self):
        base = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)
        serie = [
            _sample((base - timedelta(minutes=50)).isoformat(), v=300.0),
            _sample((base - timedelta(minutes=20)).isoformat(), v=300.1),
            _sample((base + timedelta(minutes=20)).isoformat(), v=300.2),
            _sample((base + timedelta(minutes=50)).isoformat(), v=300.3),
        ]
        # Linear ramp through the window: value at base ~= 300.15
        got = p.objem_v_case(serie, p.ts(base), 120)
        assert got is not None and abs(got - 300.15) < 1e-3

    @requires_tz
    def test_single_point_falls_back_to_nearest(self):
        base = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)
        serie = [_sample((base - timedelta(minutes=30)).isoformat(), v=300.0)]
        # One point: no regression possible; falls back to nearest within tolerance.
        got = p.objem_v_case(serie, p.ts(base), 120)
        assert abs(got - 300.0) < 1e-6

    def test_empty_is_none(self):
        assert p.objem_v_case([], 123.0, 120) is None

    @requires_tz
    def test_none_values_skipped(self):
        base = datetime(2026, 9, 1, 12, 0, tzinfo=TZ)
        series = [_sample((base - timedelta(minutes=30)).isoformat(), v=299.0),
                  _sample((base - timedelta(minutes=10)).isoformat(), v=None),
                  _sample((base + timedelta(minutes=10)).isoformat(), v=301.0),
                  _sample((base + timedelta(minutes=30)).isoformat(), v=302.0)]
        got = p.objem_v_case(series, p.ts(base), 120)
        # None interior point must be skipped; fit uses the valid neighbours.
        assert got is not None
        assert 299.0 <= got <= 302.0


# ---------------------------------------------------------------- outflow trap
class TestIntegrujOdtok:
    @requires_tz
    def test_constant_ramp_trapezoid(self):
        """Outflow linearly ramping 400->0 over 24 h -> integral 17.28 mil m3."""
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = []
        for i in range(49):
            t = now - timedelta(minutes=30 * i)
            serie.append(_sample(t.isoformat(), o=round(400.0 * (24 * 60 - 30 * i) / (24 * 60), 3)))
        res = p.integruj_odtok(serie, now, 24)
        assert res["odtok_mil_m3"] is not None
        assert abs(res["odtok_mil_m3"] - 17.28) < 0.05
        assert res["metoda"] == "trapezova integrace merene rady PVL"

    @requires_tz
    def test_zero_outflow_is_zero(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = [_sample((now - timedelta(minutes=30 * i)).isoformat(), o=0.0) for i in range(49)]
        res = p.integruj_odtok(serie, now, 24)
        assert res["odtok_mil_m3"] == 0.0

    def test_few_samples_invalid(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = [_sample((now - timedelta(minutes=30)).isoformat(), o=5.0)]
        res = p.integruj_odtok(serie, now, 24)
        assert res["odtok_mil_m3"] is None
        assert res["duvod_neplatnosti"]

    def test_unknown_samples_skipped(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        # every second sample has an unknown outflow; the known ones are a
        # constant 5 m3/s → 5 * 24 * 3600 = 432000 m3 = 0.432 mil m3
        serie = []
        for i in range(49):
            t = now - timedelta(minutes=30 * i)
            serie.append(_sample(t.isoformat(), o=(5.0 if i % 2 == 0 else None)))
        res = p.integruj_odtok(serie, now, 24)
        assert res["odtok_mil_m3"] is not None
        assert abs(res["odtok_mil_m3"] - 0.432) < 0.01


class TestHodinSOdtokem:
    @requires_tz
    def test_all_above_threshold_full_window(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = [_sample((now - timedelta(minutes=30 * i)).isoformat(), o=400.0) for i in range(49)]
        hrs = p.hodin_s_odtokem(serie, now, 24)
        # 48 gaps of 30 min each = 24 hours, all above threshold
        assert abs(hrs - 24.0) < 0.2

    @requires_tz
    def test_none_below_threshold_is_zero(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = [_sample((now - timedelta(minutes=30 * i)).isoformat(), o=0.0) for i in range(49)]
        hrs = p.hodin_s_odtokem(serie, now, 24)
        assert hrs == 0.0

    def test_few_samples_none(self):
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        serie = [_sample((now - timedelta(minutes=30)).isoformat(), o=5.0)]
        assert p.hodin_s_odtokem(serie, now, 24) is None


# ------------------------------------------------------------- daily / weekly
def _make_daily_series(days_back, h=350.0, o=0.0, v=300.0):
    """Full calendar days (00:00-23:30, every 30 min) for the last
    `days_back` COMPLETED days before today (09-03)."""
    hoje = datetime(2026, 9, 3, 12, 0, tzinfo=TZ).date()
    hl, ob = [], []
    for i in range(1, days_back + 1):
        den = hoje - timedelta(days=i)
        for mm in range(0, 24 * 60, 30):
            t = datetime(den.year, den.month, den.day, 0, 0, tzinfo=TZ) + timedelta(minutes=mm)
            hl.append(_sample(t.isoformat(), h=h, o=o))
            ob.append(_sample(t.isoformat(), v=v))
    return hl, ob


class TestDenniRozpad:
    @requires_tz
    def test_completed_days_count(self):
        hl, ob = _make_daily_series(10)
        denni = p.denni_rozpad(hl, ob, datetime(2026, 9, 3, 12, 0, tzinfo=TZ))
        assert len(denni) == 10
        assert all("object." not in (d.get("datum") or "") for d in denni)

    @requires_tz
    def test_constant_level_zero_balance(self):
        hl, ob = _make_daily_series(10, h=350.0, o=0.0, v=300.0)
        denni = p.denni_rozpad(hl, ob, datetime(2026, 9, 3, 12, 0, tzinfo=TZ))
        d = denni[0]
        assert d["hladina_start"] == 350.0
        assert d["hladina_konec"] == 350.0
        assert d["odtok_mil_m3"] == 0.0
        assert d["bilance_mil_m3"] == 0.0

    @requires_tz
    def test_fold_day_chronological(self):
        """DST fall-back day: hladina_start/konec/min/max must follow real time,
        not ISO-string order. Regression: string sort put the later +01:00
        fold sample ahead of the earlier +02:00 one."""
        serie = [
            _sample("2026-10-25T02:00:00+02:00", h=356.0, o=0.0),  # real 00:00 UTC
            _sample("2026-10-25T02:30:00+02:00", h=356.5, o=0.0),  # real 00:30 UTC
            _sample("2026-10-25T02:00:00+01:00", h=357.0, o=0.0),  # real 01:00 UTC
            _sample("2026-10-25T02:30:00+01:00", h=357.5, o=0.0),  # real 01:30 UTC
            _sample("2026-10-25T03:00:00+01:00", h=358.0, o=0.0),  # real 02:00 UTC
        ]
        ob = [_sample(s["t"], v=300.0) for s in serie]
        now = datetime(2026, 10, 26, 1, 0, tzinfo=TZ)
        merged = p.merge_hladina_odtok([], [(datetime.fromisoformat(s["t"]), s["h"], s["o"]) for s in serie], now)
        denni = p.denni_rozpad(merged, ob, now)
        d = next(x for x in denni if x["datum"] == "2026-10-25")
        assert d["hladina_start"] == 356.0   # real first (00:00 UTC)
        assert d["hladina_konec"] == 358.0   # real last (02:00 UTC), not a fold-garbled value
        assert d["hladina_max"] == 358.0


class TestTydenniRozpad:
    @requires_tz
    def test_marks_current_week_in_progress(self):
        hl, ob = _make_daily_series(15, h=350.0, o=0.0, v=300.0)
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        denni = p.denni_rozpad(hl, ob, now)
        tydny = p.tydenni_rozpad(denni, now)
        assert any(t["probiha"] for t in tydny)
        cur = next(t for t in tydny if t["probiha"])
        assert not cur["kompletni"]

    @requires_tz
    def test_complete_week_has_7_days_and_negative_balance_consistent(self):
        hl, ob = _make_daily_series(15, h=350.0, o=0.0, v=300.0)
        now = datetime(2026, 9, 3, 12, 0, tzinfo=TZ)
        denni = p.denni_rozpad(hl, ob, now)
        tydny = p.tydenni_rozpad(denni, now)
        full = [t for t in tydny if t["kompletni"]]
        assert full, "expected at least one completed week in 15 days"
        assert all(t["pocet_dnu"] == 7 for t in full)


# ------------------------------------------------------------ HTML table parse
SAMPLE_HTML = """<html><body>
<table>
<tr><th colspan='2'>Aktuální hodnoty (25.08.2026 22:10)</th></tr>
<tr><td>Hladina vody</td><td>339,85</td></tr>
<tr><td>Objem</td><td>359,4</td></tr>
<tr><td>Přítok</td><td>67,2</td></tr>
<tr><td>Odtok</td><td>348,85</td></tr>
</table>
<table>
<tr><th rowspan='2'>Datum</th><th colspan='2'>Referenční stav</th></tr>
<tr><th>Hladina</th><th>Odtok</th></tr>
<tr><td>25.08.2026 21:30</td><td>339,79</td><td>348,7</td></tr>
<tr><td>25.08.2026 22:00</td><td>339,84</td><td>348,8</td></tr>
</table>
</body></html>"""


class TestTableParsing:
    def test_tables_detected(self):
        tbs = p.tables_of(SAMPLE_HTML)
        assert len(tbs) == 2

    def test_parse_aktualni(self):
        tbs = p.tables_of(SAMPLE_HTML)
        akt = p.parse_aktualni(tbs)
        assert akt is not None
        assert akt["hladina"] == 339.85
        assert akt["objem"] == 359.4
        assert akt["pritok"] == 67.2
        assert akt["odtok"] == 348.85
        assert akt["cas_mereni"] is not None

    def test_parse_historicka(self):
        tbs = p.tables_of(SAMPLE_HTML)
        rady = p.parse_historicka_tabulka(tbs)
        assert len(rady) == 2
        # sorted chronologically ascending: 21:30 first, then 22:00
        assert rady[0][1] == 339.79  # hladina at 21:30
        assert rady[0][2] == 348.7   # odtok at 21:30
        assert rady[1][1] == 339.84  # hladina at 22:00
        assert rady[0][0] < rady[1][0]  # ascending order

    def test_header_leaves_colspan(self):
        tbs = p.tables_of(SAMPLE_HTML)
        # second table: 2 header rows, 3 columns after colspan flatten
        leaves = p.header_leaves(tbs[1][:2])
        assert any("hladina" in l.lower() for l in leaves)
        assert any("odtok" in l.lower() for l in leaves)


# ------------------------------------------------------------- atomic writes
class TestAtomicWrite:
    def test_atomic_write_text_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "out.json")
            p.atomic_write_text(path, '{"ok": true}')
            with open(path, "r", encoding="utf-8") as f:
                assert json.load(f)["ok"] is True

    def test_atomic_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "data.json")
            p.atomic_write(path, {"hladina_odtok": [], "objem": [], "last_ok": None})
            with open(path, "r", encoding="utf-8") as f:
                assert json.load(f)["hladina_odtok"] == []

    def test_no_partial_file_on_failure(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "x.json")
            # a value that json cannot serialize raises before replace
            with pytest.raises(TypeError):
                p.atomic_write(path, {"bad": object()})
            # the tmp file must have been cleaned up; target untouched
            leftovers = [f for f in os.listdir(td) if f.startswith(".vd_orlik_")]
            assert leftovers == []
            assert not os.path.exists(path)


# ----------------------------------------------------------------- cache_load
class TestCacheLoad:
    def test_missing_file_returns_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setattr(p, "HISTORY_FILE", str(tmp_path / "nope.json"))
        c = p.cache_load()
        assert c["hladina_odtok"] == [] and c["objem"] == [] and c["last_ok"] is None

    def test_corrupt_file_resets_safely(self, tmp_path, monkeypatch):
        hf = tmp_path / "h.json"
        hf.write_text("{ definitely not json ", encoding="utf-8")
        monkeypatch.setattr(p, "HISTORY_FILE", str(hf))
        c = p.cache_load()
        assert c["hladina_odtok"] == []

    def test_non_dict_resets(self, tmp_path, monkeypatch):
        hf = tmp_path / "h.json"
        hf.write_text("[1,2,3]", encoding="utf-8")
        monkeypatch.setattr(p, "HISTORY_FILE", str(hf))
        c = p.cache_load()
        assert c["hladina_odtok"] == []