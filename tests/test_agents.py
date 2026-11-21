"""Tests für das v2-Fundament und die Agenten (ohne Netz – alle Abrufe sind Attrappen)."""
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from polybot import config, events, migrations, notify, polymarket, reports, settings, sources
from polybot.agents import Agent, Registry
from polybot.agents import learning as L
from polybot.agents import news as N
from polybot.agents import strategy as S
from polybot.agents.coordinator import Coordinator
from polybot.agents.guardian import GuardianAgent, read_resources
from polybot.agents.probability import ProbabilityAgent, horizon, weather_emp
from polybot.agents.scanner import ScannerAgent, priority, quality_flags
from polybot.agents.validation import ValidationAgent
from polybot.http import HttpError
from polybot.store import Store

from tests.test_engine import _end, _engine, _market

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, name="t.sqlite"):
    return Store(str(tmp_path / name))


def _m(mid="m1", cat="crypto", bid=0.40, ask=0.42, liq=10000, end=None, **kw):
    mid_p = (bid + ask) / 2 if bid and ask else 0.5
    d = {"id": mid, "question": f"Will thing {mid} happen?", "category": cat, "best_bid": bid, "best_ask": ask,
         "prices": [mid_p, 1 - mid_p], "liquidity": liq, "volume": 5000.0, "volume24h": 800.0,
         "end_date": end or NOW + timedelta(hours=30), "event_id": "e1", "event_title": "Ereignis", "event_slug": "ereignis",
         "slug": mid, "tags": ["x"], "neg_risk": False, "event_n_markets": 1, "outcomes": ["Yes", "No"]}
    d.update(kw)
    return d


def _o(m, p, variants=None, hours=30.0, **extra):
    mp = (m["best_bid"] + m["best_ask"]) / 2
    a = {"p_yes": p, "model_id": f"{m['category']}.x@1", "variants": variants or {f"{m['category']}.x@1": p},
         "uncertainty": 0.05, "sources": ["test"], "data_ts": time.time(), "hours": hours, "features": None}
    a.update(extra)
    return {"market": m, "analysis": a, "market_p": mp, "base": [], "group": f"g:{m['id']}", "status": {}}


# ================================================================ Quellen-Budgets
def test_sources_budget_blocks_and_counts(monkeypatch):
    monkeypatch.setitem(sources.SOURCES, "odds_api", {**sources.SOURCES["odds_api"], "day_budget": 2})
    sources.before("odds_api")
    sources.before("odds_api")
    with pytest.raises(sources.Blocked):
        sources.before("odds_api")
    snap = {s["id"]: s for s in sources.snapshot()}["odds_api"]
    assert snap["calls"] == 2 and snap["blocked"] == 1


def test_sources_pause_after_429_and_recover():
    sources.before("open_meteo")
    sources.after("open_meteo", False, status=429, msg="Too many")
    assert sources.paused("open_meteo") > 0
    with pytest.raises(sources.Blocked):
        sources.before("open_meteo")


def test_sources_redact_and_source_for():
    text = "Fehler bei https://api.telegram.org/bot123456:AAH-abc_DEF/sendMessage und apiKey=geheim123&x=1 token=abc"
    out = sources.redact(text)
    assert "AAH-abc_DEF" not in out and "geheim123" not in out and "token=abc" not in out
    assert sources.source_for("https://gamma-api.polymarket.com/events") == "polymarket_gamma"
    assert sources.source_for("https://irgendwo.example/x") == "sonstige"


def test_sources_flush_and_load_survive_restart(tmp_path):
    st = _store(tmp_path)
    sources.before("binance")
    sources.after("binance", True, ms=12)
    sources.flush(st)
    sources.reset()
    sources.load(st)
    snap = {s["id"]: s for s in sources.snapshot()}["binance"]
    assert snap["calls"] == 1 and snap["last_ok"]


def test_http_error_never_contains_secret():
    e = HttpError("GET https://x.example/?apiKey=SEHRGEHEIM fehlgeschlagen", status=500)
    assert "SEHRGEHEIM" not in str(e)


# ================================================================ Migrationen / Datenbank
def test_migrations_fresh_db_and_idempotent(tmp_path):
    st = _store(tmp_path)
    assert st.schema_version == migrations.LATEST
    st2 = Store(st.path)  # zweiter Start: nichts mehr zu tun
    assert st2.migrated == [] and st2.schema_version == migrations.LATEST
    assert st.scalar("PRAGMA journal_mode") == "wal"


def test_migration_backs_up_existing_data(tmp_path):
    path = str(tmp_path / "alt.sqlite")
    db = sqlite3.connect(path)
    from polybot.store import SCHEMA
    db.executescript(SCHEMA)
    db.execute("INSERT INTO positions(market_id, question, side, shares, price, cost) VALUES('m','q',0,1,0.5,0.5)")
    db.commit()
    db.close()
    st = Store(path)
    assert st.migrated == [m[0] for m in migrations.MIGRATIONS]
    assert os.path.exists(tmp_path / "backups" / "vor-migration-v1.sqlite")
    assert st.scalar("SELECT COUNT(*) FROM positions") == 1  # Altdaten unberührt


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    path = str(tmp_path / "x.sqlite")
    Store(path)
    bad = migrations.MIGRATIONS + [(99, "kaputt", ["CREATE TABLE neu_a (x)", "DAS IST KEIN SQL"])]
    monkeypatch.setattr(migrations, "MIGRATIONS", bad)
    with pytest.raises(sqlite3.Error):
        Store(path)
    db = sqlite3.connect(path)
    assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='neu_a'").fetchone()[0] == 0
    assert db.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == migrations.LATEST


def test_forecast_history_is_immutable(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    pa.log([_o(_m(), 0.55)], NOW)
    with pytest.raises(sqlite3.Error):
        st.x("UPDATE forecasts SET p=0.99")
    with pytest.raises(sqlite3.Error):
        st.x("DELETE FROM forecasts")
    assert st.scalar("SELECT p FROM forecasts") == 0.55


def test_backup_keeps_only_last_n(tmp_path):
    st = _store(tmp_path)
    folder = tmp_path / "backups"
    folder.mkdir()
    for d in range(1, 10):
        (folder / f"polybot-2026010{d}.sqlite").write_bytes(b"x")
    name, size = st.backup(keep=3)
    files = sorted(f for f in os.listdir(folder) if f.startswith("polybot-"))
    assert len(files) == 3 and os.path.basename(name) in files and size > 0
    assert sqlite3.connect(name).execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == migrations.LATEST  # lesbare Kopie


# ================================================================ Einstellungen
def test_settings_validate_persist_and_reject(tmp_path):
    st = _store(tmp_path)
    done = settings.update(st, {"MAX_DIVERGENCE": "0,30", "TRADE_WEATHER": False})
    assert ("MAX_DIVERGENCE", 0.99, 0.30) in done and config.MAX_DIVERGENCE == 0.30 and config.TRADE_WEATHER is False
    for bad in ({"MAX_DIVERGENCE": 5}, {"TELEGRAM_BOT_TOKEN": "x"}, {"NOTIFY_MIN_LEVEL": "laut"}, {"SCAN_INTERVAL_MIN": "abc"}):
        with pytest.raises(ValueError):
            settings.update(st, bad)
    config.MAX_DIVERGENCE = 0.99
    settings.load(st)  # wie nach einem Neustart
    assert config.MAX_DIVERGENCE == 0.30
    assert all("TOKEN" not in k and "PASSWORD" not in k for k in settings.SPEC)


# ================================================================ Telegram-Prioritäten
def test_notify_levels_dedupe_quiet_and_cap(monkeypatch):
    noon = datetime(2026, 11, 14, 12, 0, tzinfo=notify.BERLIN)
    night = datetime(2026, 11, 14, 2, 0, tzinfo=notify.BERLIN)
    monkeypatch.setattr(config, "NOTIFY_MIN_LEVEL", "normal")
    assert notify.decide("info", now=noon) == (False, "unter Mindeststufe")
    assert notify.decide("normal", "k1", now=noon)[0] is True
    assert notify.decide("normal", "k1", now=noon) == (False, "schon gemeldet")
    assert notify.decide("hoch", now=night) == (False, "Ruhezeit")
    assert notify.decide("kritisch", now=night)[0] is True      # Kritisches kommt immer durch
    monkeypatch.setattr(config, "NOTIFY_DAILY_CAP", 2)
    notify.reset()
    assert [notify.decide("normal", now=noon)[0] for _ in range(4)] == [True, True, False, False]
    assert notify.decide("hoch", now=noon)[0] is True            # Tageslimit gilt nur für normale Meldungen


def test_telegram_retries_connection_error_once_but_not_refusals(monkeypatch):
    from polybot.http import HttpError
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456789:AAExampleExampleExampleExampleExample")
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify.time, "sleep", lambda s: None)
    calls = []

    def flaky(url, payload):
        calls.append(payload["text"])
        if len(calls) == 1:
            raise HttpError("ConnectionError bei Telegram")
        return {"ok": True}
    monkeypatch.setattr(notify, "post_json", flaky)
    assert notify._send("hallo") is True and calls == ["hallo", "hallo"] and notify.last_status == "OK"

    def refused(url, payload):
        calls.append("x")
        raise HttpError("HTTP 400: chat not found", 400)
    calls.clear()
    monkeypatch.setattr(notify, "post_json", refused)
    assert notify._send("hallo") is False and calls == ["x"]                 # eine Ablehnung wird nicht wiederholt
    assert "AAExample" not in notify.last_status


def test_events_dedupe_and_redaction(tmp_path):
    st = _store(tmp_path)
    assert events.emit(st, "hoch", "quelle", "Ausfall token=abc123", "x", key="q1", telegram=False)
    assert events.emit(st, "hoch", "quelle", "Ausfall", "x", key="q1", telegram=False) is None
    rows = events.recent(st)
    assert len(rows) == 1 and rows[0]["count"] == 2 and "abc123" not in rows[0]["title"]
    assert events.recent(st, min_level="kritisch") == []


# ================================================================ Agenten-Gerüst
def test_registry_catches_errors_and_tracks_status(tmp_path):
    st = _store(tmp_path)

    class Bad(Agent):
        id, name, interval = "bad", "Bad", 600

        def run(self, now, deadline):
            raise RuntimeError("kaputt apiKey=GEHEIM")
    reg = Registry(st)
    reg.add(Bad(st))
    assert reg.due("bad")
    assert reg.run("bad") is None
    s = reg.view()[0]
    assert s["state"] == "fehler" and s["errors"] == 1 and "GEHEIM" not in s["last_error"]
    assert not reg.due("bad")  # Takt wird auch nach einem Fehler eingehalten
    assert st.q1("SELECT ok, error FROM agent_runs")["ok"] == 0
    assert reg.run("bad", fn=lambda n, d: {"x": 1}) == {"x": 1} and reg.view()[0]["state"] == "ok"


# ================================================================ Probability
def test_horizon_buckets():
    assert [horizon(h) for h in (0.5, 6, 23.9, 24, 71, 72, 500)] == ["<6h", "6-24h", "6-24h", "24-72h", "24-72h", ">72h", ">72h"]


def test_probability_log_is_append_only_per_horizon_and_chained(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    m = _m()
    o = _o(m, 0.55, {"crypto.options@1": 0.55, "crypto.hist@1": 0.50}, model_id="crypto.options@1")
    assert pa.log([o], NOW)["neu"] == 2
    o2 = _o(m, 0.70, {"crypto.options@1": 0.70, "crypto.hist@1": 0.60}, model_id="crypto.options@1")
    assert pa.log([o2], NOW + timedelta(minutes=10))["neu"] == 0          # selber Horizont -> erste Prognose bleibt
    assert pa.log([_o(m, 0.70, {"crypto.options@1": 0.70}, hours=3.0, model_id="crypto.options@1")], NOW)["neu"] == 1
    rows = st.q("SELECT * FROM forecasts ORDER BY id")
    assert [r["p"] for r in rows] == [0.55, 0.50, 0.70] and [r["champion"] for r in rows] == [1, 0, 1]
    assert rows[0]["sources"] == '["test"]' and rows[0]["uncertainty"] == 0.05 and rows[0]["market_p"] == 0.41
    assert pa.verify_chain() == (True, 3, None, rows[-1]["id"])
    # Manipulation an der Datenbank vorbei (Trigger entfernt) fällt über die Prüfsumme auf
    st.x("DROP TRIGGER forecasts_kein_update")
    st.x("UPDATE forecasts SET p=0.99 WHERE id=?", (rows[1]["id"],))
    ok, _n, bad, _last = pa.verify_chain()
    assert ok is False and bad == rows[1]["id"]


def test_probability_select_registry_mix_and_rollback(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    assert {r["category"]: r["model"] for r in st.q("SELECT * FROM model_registry")} == {
        "crypto": "crypto.options", "weather": "weather.ens", "sports": "sports.books", "base": "base.calib"}
    m = _m(cat="weather")
    a = pa.select(m, {"p_yes": 0.30, "model_id": "weather.ens@2", "variants": {"weather.ens@2": 0.30, "weather.wide@1": 0.22}}, 0.10)
    assert a["p_yes"] == 0.30 and a["model_id"] == "weather.ens@2"
    pa.activate("weather", "weather.wide", "1", 1.0, "Test")
    a = pa.select(m, {"p_yes": 0.30, "model_id": "weather.ens@2", "variants": {"weather.ens@2": 0.30, "weather.wide@1": 0.22}}, 0.10)
    assert a["p_yes"] == 0.22 and a["model_id"] == "weather.wide@1"
    pa.activate("weather", "weather.wide", "1", 0.5, "Mischung")
    a = pa.select(m, {"p_yes": 0.30, "model_id": "weather.ens@2", "variants": {"weather.ens@2": 0.30, "weather.wide@1": 0.22}}, 0.10)
    assert abs(a["p_yes"] - 0.16) < 1e-9 and ".mix@" in a["model_id"]
    assert pa.rollback("weather") and pa.reg["weather"]["mix_w"] == 1.0
    assert pa.rollback("weather") and pa.reg["weather"]["model"] == "weather.wide"  # Rückholung der Rückholung
    assert [r["action"] for r in st.q("SELECT action FROM model_log WHERE category='weather' ORDER BY id")] == ["start"] + ["aktiviert"] * 4
    assert ProbabilityAgent(st).reg["weather"]["model"] == "weather.wide"            # überlebt Neustart


def test_probability_select_falls_back_when_active_model_missing(tmp_path):
    pa = ProbabilityAgent(_store(tmp_path))
    a = pa.select(_m(cat="crypto"), {"p_yes": 0.4, "model_id": "crypto.histprem@1", "variants": {"crypto.histprem@1": 0.4, "crypto.hist@1": 0.38}}, 0.5)
    assert a["p_yes"] == 0.4 and a["model_id"] == "crypto.histprem@1"   # Optionen fehlen -> Ersatz des Modells, nicht des Zufalls


def test_weather_emp_probability():
    f = {"icao": "EGLC", "kind": "high", "unit": "C", "ens_mean": 12.0, "lead_h": 20, "lo": 11.5, "hi": 12.5}
    params = {"bias": {"EGLC|high": 1.0}, "bias_kind": {"C|high": 0.2}, "sd": {"C": 1.0}}
    p = weather_emp(f, params)
    want = 0.5 * (math.erf((12.5 - 13) / math.sqrt(2)) - math.erf((11.5 - 13) / math.sqrt(2)))
    assert abs(p - want) < 1e-9
    assert weather_emp({**f, "lo": None}, params) > weather_emp(f, params)        # offener Rand
    assert weather_emp(f, {"bias": {}, "sd": {}}) is None                          # ohne gelernte Streuung keine Prognose


# ================================================================ Validation
def test_validation_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_DIVERGENCE", 0.25)
    va = ValidationAgent(_store(tmp_path))
    ok = _o(_m("a"), 0.50)
    far = _o(_m("b", bid=0.10, ask=0.12), 0.60)                                  # 49 Punkte gegen liquiden Markt
    illiquid = _o(_m("c", bid=0.10, ask=0.12, liq=50), 0.60)                     # dünner Markt -> kein Urteil über das Modell
    stale = _o(_m("d", cat="crypto"), 0.45, data_ts=time.time() - 3600)
    nan = _o(_m("e"), float("nan"))
    over = _o(_m("f"), 0.45, hours=-1.0)
    res = va.check([ok, far, illiquid, stale, nan, over], NOW)
    assert ok["analysis"]["valid"] is True and ok["base"] == []
    assert far["analysis"]["valid"] is False and "Abweichung unplausibel" in far["base"][0]
    assert illiquid["analysis"]["valid"] is True
    assert "Daten veraltet" in stale["analysis"]["reject"]
    assert nan["analysis"]["reject"] == "Wert ungültig" and over["analysis"]["reject"] == "Markt läuft nicht mehr"
    assert res["geprüft"] == 6 and res["gültig"] == 2
    assert dict(ok["analysis"]["checks"]) == {"wert": True, "ende": True, "frische": True, "abweichung": True}


def _excl(prefix, eid, prices, n=None):
    """Märkte eines Ereignisses mit Kursen, die zusammen etwa 100 % ergeben (echte „genau eine trifft zu“-Frage)."""
    return [_m(f"{prefix}{i}", event_id=eid, neg_risk=True, event_n_markets=n or len(prices), bid=p - 0.01, ask=p + 0.01)
            for i, p in enumerate(prices)]


def test_validation_sum_check_for_exclusive_events(tmp_path):
    va = ValidationAgent(_store(tmp_path))
    good = [_o(m, p) for m, p in zip(_excl("g", "E1", (0.45, 0.35, 0.2)), (0.5, 0.3, 0.2))]
    bad = [_o(m, p) for m, p in zip(_excl("b", "E2", (0.45, 0.35, 0.2)), (0.6, 0.5, 0.4))]
    part = [_o(m, p) for m, p in zip(_excl("p", "E3", (0.3, 0.3, 0.2), n=5), (0.6, 0.5, 0.4))]
    va.check(good + bad + part, NOW)
    assert all(o["analysis"]["valid"] for o in good + part)                     # unvollständiges Ereignis: keine Aussage
    assert all(o["analysis"]["valid"] is False and "Summe" in o["analysis"]["reject"] for o in bad)


def test_validation_sum_check_skips_price_ladders(tmp_path):
    """Live-Befund 18.11.: „Bitcoin über 64k / 66k / 68k“ – mehrere Antworten können eintreffen, die Kurse ergeben zusammen
    weit über 100 %. Die Summen-Regel sperrte dort 653 Prognosen, bei denen das Modell besser lag als der Markt."""
    va = ValidationAgent(_store(tmp_path))
    ladder = [_o(m, p) for m, p in zip(_excl("l", "E4", (0.90, 0.62, 0.30)), (0.95, 0.70, 0.25))]   # Markt 182 %, Modell 190 %
    out = va.check(ladder, NOW)
    assert out["gültig"] == 3 and all(o["analysis"]["valid"] for o in ladder)
    raw = {"id": "1", "question": "Will the price of Bitcoin be above $66,000 on November 15?", "outcomes": '["Yes","No"]',
           "outcomePrices": '["0.6","0.4"]', "clobTokenIds": '["a","b"]', "negRisk": False, "active": True, "closed": False}
    assert polymarket.normalize_market(raw, {"id": "e", "enableNegRisk": True, "markets": [raw, raw]}, "crypto")["neg_risk"] is False
    assert polymarket.normalize_market({**raw, "negRisk": True}, {"id": "e"}, "crypto")["neg_risk"] is True


def test_select_keeps_raw_model_value_next_to_the_mixed_one(tmp_path):
    pa = ProbabilityAgent(_store(tmp_path))
    pa.activate("weather", "weather.ens", "2", 0.5, "Test")
    a = {"p_yes": 0.8, "model_id": "weather.ens@2", "variants": {"weather.ens@2": 0.8}}
    pa.select(_m(cat="weather"), a, 0.2)
    assert a["p_model"] == 0.8 and abs(a["p_yes"] - 0.5) < 1e-9 and a["model_id"] == "weather.ens.mix@2w50"


def test_validation_sum_check_uses_raw_model_value_not_the_mix(tmp_path):
    """Live-Befund 14.11.: Nach einer Mischung mit dem Marktpreis wurde die Summe der Marktkurse geprüft (517 Ablehnungen)."""
    va = ValidationAgent(_store(tmp_path))
    ms = [_m(f"s{i}", cat="weather", bid=p - 0.01, ask=p + 0.01, neg_risk=True, event_id="ev", event_n_markets=3)
          for i, p in enumerate((0.3, 0.3, 0.4))]
    mixed = [_o(m, 0.5, p_model=p) for m, p in zip(ms, (0.2, 0.3, 0.5))]       # gehandelter Wert 3 × 50 %, Modell selbst stimmig
    out = va.check(mixed, NOW)
    assert out["gültig"] == 3
    broken = [_o(m, 0.5, p_model=p) for m, p in zip(ms, (0.6, 0.6, 0.6))]      # Modell selbst unstimmig -> gesperrt
    out = va.check(broken, NOW)
    assert out["gültig"] == 0 and va.cycle_by_cat == {"weather": {"Summe der Antworten": 3}}


# ================================================================ Scanner
def test_scanner_quality_and_priority():
    good = _m(liq=50000, volume24h=200000.0, end=NOW + timedelta(hours=10))
    thin = _m(liq=100, bid=None, ask=None, volume24h=10.0)
    thin["end_date"] = None
    assert quality_flags(good, NOW) == []
    assert set(quality_flags(thin, NOW)) == {"kein_orderbuch", "wenig_liquiditaet", "kein_ende"}
    assert priority(good, [], NOW, True) > priority(thin, quality_flags(thin, NOW), NOW, False)
    assert priority(good, [], NOW, False, news=3) > priority(good, [], NOW, False, news=0)


def test_scanner_ingest_upsert_snapshots_and_sweep(tmp_path):
    st = _store(tmp_path)
    raw = {"id": "k1", "question": "Will candidate A win the election?", "outcomes": '["Yes","No"]', "outcomePrices": '["0.30","0.70"]',
           "bestBid": 0.29, "bestAsk": 0.31, "liquidityNum": 40000, "volumeNum": 900000, "volume24hr": 120000,
           "endDate": "2026-12-01T00:00:00Z", "active": True, "closed": False, "events": [{"id": "ev9", "title": "Election", "slug": "election"}]}
    updown = {**raw, "id": "k2", "question": "Bitcoin Up or Down - 5 minutes"}
    calls = []

    def fetch(params):
        calls.append(params)
        return [raw, updown] if params["tag_id"] == 2 else []
    sc = ScannerAgent(st, fetch_markets=fetch)
    sc.ingest([_m("m1", end=NOW + timedelta(hours=30))], NOW)
    out = sc.run(NOW, time.time() + 60)
    assert out["katalog"]["politics"] == 1 and all(c["limit"] == 100 and c["closed"] == "false" for c in calls)
    rows = {r["id"]: r for r in st.q("SELECT * FROM markets")}
    assert set(rows) == {"m1", "k1"} and rows["k1"]["category"] == "politics" and rows["m1"]["modeled"] == 1
    assert st.scalar("SELECT COUNT(*) FROM snapshots") == 2
    # eine Stunde später: Kurs +15 Punkte -> Bewegung erkannt, Katalog-Zeile aktualisiert statt verdoppelt
    raw2 = {**raw, "bestBid": 0.44, "bestAsk": 0.46, "outcomePrices": '["0.45","0.55"]'}
    sc2 = ScannerAgent(st, fetch_markets=lambda p: [raw2] if p["tag_id"] == 2 else [])
    out = sc2.run(NOW + timedelta(hours=1), time.time() + 60)
    k1 = st.q1("SELECT * FROM markets WHERE id='k1'")
    assert st.scalar("SELECT COUNT(*) FROM markets") == 2 and abs(k1["move_1h"] - 0.15) < 1e-6 and out["bewegungen"] == 1
    assert st.scalar("SELECT COUNT(*) FROM events WHERE type='bewegung'") == 1
    # Kategorie eines Modell-Marktes wird vom stündlichen Durchlauf nicht umgehängt
    sc.ingest([_m("m1")], NOW, modeled=False, category="politics")
    assert st.q1("SELECT category, modeled FROM markets WHERE id='m1'") == {"category": "crypto", "modeled": 1}


# ================================================================ Learning
def _fill(st, pa, n, p_fn, mp_fn, out_fn, cat="weather", key="weather.ens@2", champion=True, start=NOW, per_cluster=1, extra=None):
    """n entschiedene Prognosen direkt eintragen (ein Markt je Prognose)."""
    rows = []
    for i in range(n):
        mid = f"{key}-{i}"
        m = _m(mid, cat=cat, bid=mp_fn(i) - 0.01, ask=mp_fn(i) + 0.01)
        variants = {key: p_fn(i)}
        if extra:
            variants.update({k: f(i) for k, f in extra.items()})
        o = _o(m, p_fn(i), variants, model_id=key if champion else "nix")
        o["group"] = f"c{i // per_cluster}"
        rows.append((o, start + timedelta(hours=i)))
        st.x("INSERT OR IGNORE INTO outcomes(market_id, outcome, resolved_at, source) VALUES(?,?,?,?)",
             (mid, out_fn(i), (start + timedelta(hours=i + 40)).isoformat(), "test"))
    for o, t in rows:
        pa.log([o], t)


def test_learning_metrics_math():
    rows = [{"p": 0.8, "market_p": 0.6, "outcome": 1.0, "market_id": "a", "cluster": "a"},
            {"p": 0.3, "market_p": 0.4, "outcome": 0.0, "market_id": "b", "cluster": "b"},
            {"p": 0.9, "market_p": 0.5, "outcome": 0.0, "market_id": "c", "cluster": "c"}]
    assert abs(L.brier(rows) - (0.04 + 0.09 + 0.81) / 3) < 1e-12
    assert abs(L.brier(rows, "market_p") - (0.16 + 0.16 + 0.25) / 3) < 1e-12
    assert abs(L.logloss(rows) - (-(math.log(0.8) + math.log(0.7) + math.log(0.1)) / 3)) < 1e-12
    s = L.summarize(rows)
    assert s["n"] == 3 and abs(s["hit"] - 2 / 3) < 1e-12 and abs(s["bias"] - (-0.2 + 0.3 + 0.9) / 3) < 1e-12 and s["brier_half"] == 0.25
    z, n = L.paired_z(rows)
    assert n == 3 and z > 0                                   # positiv = schlechter als der Markt
    cal = L.calibration(rows)
    assert [(b["von"], b["n"]) for b in cal] == [(0.3, 1), (0.8, 1), (0.9, 1)]


def test_learning_time_split_respects_clusters_and_order():
    rows = [{"market_id": f"m{i}", "cluster": f"c{i // 2}", "created_at": f"2026-11-{10 + i // 2:02d}T00:00:00"} for i in range(10)]
    early, late = L.time_split(rows)
    assert len(early) == 6 and len(late) == 4
    assert max(r["created_at"] for r in early) < min(r["created_at"] for r in late)          # nichts Späteres im frühen Teil
    assert not {r["cluster"] for r in early} & {r["cluster"] for r in late}                  # Gruppen werden nicht zerschnitten


def test_learning_proposes_but_never_activates_without_approval(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    out = lambda i: 1.0 if i % 4 == 0 else 0.0                                               # noqa: E731 – 25 % Ja
    # aktives Modell liegt krass daneben (sagt 80 %), Herausforderer trifft (25 %), Markt 30 %
    _fill(st, pa, 400, lambda i: 0.80, lambda i: 0.30, out, extra={"weather.wide@1": lambda i: 0.25})
    res = la.run(NOW + timedelta(days=30), time.time() + 60)
    assert res["prognosen_mit_ergebnis"] == 800 and res["offene_vorschläge"] == 1
    assert pa.reg["weather"]["model"] == "weather.ens" and pa.reg["weather"]["mix_w"] == 1.0   # nichts wurde von allein umgestellt
    prop = st.q1("SELECT * FROM proposals")
    assert prop["status"] == "offen" and "weather.wide@1" in prop["title"]
    d = json.loads(prop["detail"])
    assert d["n_oos"] == 160 and d["passed"] and d["brier_chal_oos"] < d["brier_champ_oos"] and d["z"] < -1.64
    la.run(NOW + timedelta(days=30, hours=1), time.time() + 60)
    assert st.scalar("SELECT COUNT(*) FROM proposals") == 1                                   # kein Stapel gleicher Vorschläge
    summ = la.summary()
    m = next(x for x in summ["modelle"] if x["model"] == "weather.ens@2")
    assert m["active"] and m["n"] == 400 and m["z"] > 1.64 and abs(m["bias"] - 0.55) < 1e-9
    assert any("Überschätzung" in f for c in summ["fehler"]["kategorien"] for f in c["befunde"])
    ok, _ = la.decide(prop["id"], True, "passt")
    assert ok and pa.reg["weather"]["model"] == "weather.wide" and pa.reg["weather"]["prev_model"] == "weather.ens"
    assert la.decide(prop["id"], True) == (False, "Vorschlag nicht (mehr) offen")


def test_learning_no_proposal_on_small_or_noisy_data(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    out = lambda i: 1.0 if i % 4 == 0 else 0.0                                               # noqa: E731
    _fill(st, pa, 60, lambda i: 0.80, lambda i: 0.30, out, extra={"weather.wide@1": lambda i: 0.25})
    la.run(NOW + timedelta(days=10), time.time() + 60)
    assert st.scalar("SELECT COUNT(*) FROM proposals") == 0                                   # klar besser, aber zu wenige Daten
    exp = st.q1("SELECT * FROM experiments WHERE challenger='weather.wide@1'")
    assert exp["passed"] == 0 and "zu wenige" in json.loads(exp["detail"])["reasons"][0]


def test_learning_mix_proposal_and_reject(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    _fill(st, pa, 400, lambda i: 0.80, lambda i: 0.26, lambda i: 1.0 if i % 4 == 0 else 0.0)   # kein Herausforderer, Markt ist gut
    la.run(NOW + timedelta(days=30), time.time() + 60)
    prop = st.q1("SELECT * FROM proposals")
    assert prop["kind"] == "mischung" and "Marktpreis ist klar genauer" in prop["title"]
    assert la.decide(prop["id"], False, "erst abwarten")[0] and pa.reg["weather"]["mix_w"] == 1.0
    assert st.q1("SELECT status, note FROM proposals") == {"status": "abgelehnt", "note": "erst abwarten"}


def test_learning_weather_residuals_create_challenger_version(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    for i in range(40):  # Station liegt im Schnitt 1 °C über dem Ensemble
        feat = {"icao": "EGLC", "kind": "high", "unit": "C", "ens_mean": 10.0, "ens_sd": 0.5, "lead_h": 20, "lo": 10.5, "hi": 11.5,
                "date": f"2026-10-{i % 28 + 1:02d}"}
        m = _m(f"w{i}", cat="weather")
        pa.log([_o(m, 0.3, {"weather.ens@2": 0.3}, model_id="weather.ens@2", features=feat)], NOW + timedelta(hours=i))
        st.add_outcome(f"w{i}", 1.0)
    assert la.learn_weather(NOW + timedelta(days=5)) == "1"
    v = st.q1("SELECT * FROM model_versions")
    params = json.loads(v["params"])
    assert v["n_samples"] == 40 and abs(params["bias"]["EGLC|high"] - 1.0 * 40 / 45 - (5 / 45) * 1.0) < 0.01 and params["sd"]["C"] >= 0.6
    assert la.learn_weather(NOW + timedelta(days=5)) is None                                  # nicht zweimal am selben Tag
    # ab jetzt läuft die gelernte Variante als Herausforderer mit – das aktive Modell bleibt
    a = pa.select(_m(cat="weather"), {"p_yes": 0.3, "model_id": "weather.ens@2", "variants": {"weather.ens@2": 0.3},
                                      "features": {**feat, "lo": 10.5, "hi": 11.5}}, 0.3)
    assert "weather.emp@1" in a["variants"] and a["model_id"] == "weather.ens@2"


# ================================================================ Strategy Lab
def test_price_at_and_no_lookahead_after_early_close():
    hist = [(1000, 0.2), (5000, 0.3), (9000, 0.9)]
    assert S.price_at(hist, 6000) == 0.3 and S.price_at(hist, 999) is None
    assert S.price_at(hist, 9000 + 2 * 3600) == 0.9
    assert S.price_at(hist, 9000 + 4 * 3600) is None                                          # älter als 3 h -> Datenlücke
    end = 200 * 3600
    hist = [(end - 170 * 3600, 0.40), (end - 73 * 3600, 0.45), (end - 25 * 3600, 0.50), (end - 7 * 3600, 0.99), (end - 2 * 3600, 1.0)]
    full = S.horizon_prices(hist, end)
    assert (full["p_7d"], full["p_72h"], full["p_24h"], full["p_6h"], full["p_1h"]) == (0.40, 0.45, 0.50, 0.99, 1.0)
    closed = S.horizon_prices(hist, end, closed_ts=end - 8 * 3600)                            # 8 h vor Ende entschieden
    assert closed["p_24h"] == 0.50 and closed["p_6h"] is None and closed["p_1h"] is None      # Kurse nach der Entscheidung zählen nicht


def test_bet_costs_and_payout():
    v = S.bet(0.05, 1, 0.0, 0.05)                                                             # Nein zu 95 ¢, gewinnt
    cost = 0.95 + 0.01 + 0.05 * 0.95 * 0.05
    assert abs(v - (1 / cost - 1)) < 1e-12 and v > 0
    assert S.bet(0.05, 1, 1.0, 0.05) == -1.0
    assert S.bet(0.995, 0, 1.0, 0.05) is None                                                 # unhandelbar


def _resolved_rows(n, start=datetime(2026, 1, 1, tzinfo=timezone.utc)):
    rows = []
    for i in range(n):
        p24 = (i % 19 + 1) / 20
        # im frühen Teil sind Außenseiter unterschätzt, im späten Teil nicht mehr (Überanpassungs-Falle)
        early = i < n * 0.6
        outcome = 1.0 if ((i * 7) % 20) / 20 < (p24 + (0.15 if early and p24 <= 0.10 else 0)) else 0.0
        rows.append({"market_id": f"r{i}", "question": "q", "category": "politics", "end_date": (start + timedelta(hours=6 * i)).isoformat(),
                     "closed_at": None, "outcome": outcome, "volume": 1e4, "early": 0, "p_1h": p24, "p_6h": p24, "p_24h": p24,
                     "p_72h": p24 - 0.12 if i % 3 == 0 else p24, "p_7d": None, "n_points": 50, "fetched_at": "x"})
    return rows


def test_split_by_time_is_chronological():
    rows = _resolved_rows(200)
    early, late = S.split_by_time(rows)
    assert early and late and max(r["end_date"] for r in early) < min(r["end_date"] for r in late)
    assert 0.55 < len(early) / len(rows) < 0.65


def test_backtest_runs_reproducibly_and_flags_overfit(tmp_path):
    st = _store(tmp_path)
    sa = S.StrategyAgent(st, fetch_markets=lambda p: [], fetch_history=lambda *a: [])
    rows = _resolved_rows(3000)
    a = sa.backtest(NOW, rows)
    b = sa.backtest(NOW, rows)
    assert a == b                                                                              # gleiche Daten -> gleiches Ergebnis
    by = {s["strategy"]: s for s in a["strategien"]}
    assert set(by) == {"always_yes", "always_no", "longshot_no", "favorite_yes", "momentum", "reversal", "calib_map"}
    assert by["always_yes"]["baseline"] and not by["always_yes"]["bestanden"]
    assert by["calib_map"]["oos"]["n"] > 0 and any("gelernt" in w for w in by["calib_map"]["warnungen"])
    assert a["datensatz"]["lücken"]["p_7d"] == 3000 and a["datensatz"]["mit_kurs_24h"] == 3000
    assert st.scalar("SELECT COUNT(*) FROM backtests") == 14
    # Die Kalibrierungs-Strategie hat im frühen Teil „Außenseiter kaufen“ gelernt – im späten Teil trägt das nicht
    assert not by["calib_map"]["bestanden"]
    small = sa.backtest(NOW, rows[:20])
    assert small["strategien"] == [] and "zu wenige" in small["hinweis"]


def test_calibration_strategy_learns_only_from_early_part():
    rows = _resolved_rows(3000)
    early, late = S.split_by_time(rows)
    fn = S.s_calib(S.calibration_map(early))
    poisoned = [dict(r, outcome=1.0 - r["outcome"]) for r in late]
    # dieselben Wetten, egal wie der späte Teil ausgeht -> die Regel kennt die Zukunft nicht
    assert [fn(r) for r in late] == [fn(r) for r in poisoned] and any(fn(r) for r in late)


def test_strategy_collect_builds_dataset(tmp_path):
    st = _store(tmp_path)
    end = NOW - timedelta(days=2)
    base = {"outcomes": '["Yes","No"]', "closed": True, "umaResolutionStatus": "resolved", "volumeNum": 50000,
            "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "clobTokenIds": '["tokY","tokN"]'}
    raws = [{**base, "id": "1", "question": "Will A win?", "outcomePrices": '["1","0"]', "closedTime": (end + timedelta(hours=2)).isoformat()},
            {**base, "id": "2", "question": "Will B resign by date?", "outcomePrices": '["0","1"]', "closedTime": (end - timedelta(hours=30)).isoformat()},
            {**base, "id": "3", "question": "Ethereum Up or Down - 1 hour", "outcomePrices": '["1","0"]'},
            {**base, "id": "4", "question": "Unklar?", "outcomePrices": '["0.5","0.5"]'}]
    hist = [(int(end.timestamp()) - h * 3600, 0.5) for h in range(200, 0, -1)]
    asked = []

    def fetch_history(token, a, b):
        asked.append(token)
        return list(hist)
    sa = S.StrategyAgent(st, fetch_markets=lambda p: raws, fetch_history=fetch_history)
    out = sa.collect(NOW, time.time() + 60)
    assert out["neu"] == 2 and asked == ["tokY", "tokY"]
    r1, r2 = st.q("SELECT * FROM resolved ORDER BY market_id")
    assert (r1["outcome"], r1["early"], r1["p_24h"], r1["p_1h"]) == (1.0, 0, 0.5, 0.5)
    assert (r2["outcome"], r2["early"], r2["p_72h"], r2["p_24h"], r2["p_1h"]) == (0.0, 1, 0.5, None, None)
    assert sa.collect(NOW, time.time() + 60)["neu"] == 0                                       # keine Duplikate


def test_strategy_collect_pages_past_minute_markets(tmp_path):
    """Live-Befund 14.11.: Bei Krypto ist die erste Seite fast nur „Up or Down“ – dann muss weitergeblättert werden."""
    st = _store(tmp_path)
    end = NOW - timedelta(days=1)
    base = {"outcomes": '["Yes","No"]', "closed": True, "umaResolutionStatus": "resolved", "volumeNum": 9000, "outcomePrices": '["1","0"]',
            "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "clobTokenIds": '["tokY","tokN"]', "closedTime": "2026-11-13 12:13:19+00"}
    pages = {0: [{**base, "id": f"u{i}", "question": "Bitcoin Up or Down - 5 min"} for i in range(100)],
             100: [{**base, "id": f"k{i}", "question": f"Will the price of Bitcoin be above ${60 + i},000?"} for i in range(40)]}
    offsets = []

    def fetch_markets(params):
        offsets.append(params["offset"])
        return pages.get(params["offset"], [])
    sa = S.StrategyAgent(st, fetch_markets=fetch_markets, fetch_history=lambda t, a, b: [(int(end.timestamp()) - 3600 * h, 0.4) for h in range(100, 0, -1)])
    out = sa.collect(NOW, time.time() + 60, limit=25)
    assert out["neu"] == 25 and offsets == [0, 100] and out["geprüft"] == 140
    assert st.scalar("SELECT COUNT(*) FROM resolved WHERE question LIKE '%Up or Down%'") == 0


# ================================================================ News
RSS = b"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>BBC</title>
<item><title>Starmer reportedly weighs snap election as Labour slides</title><link>https://www.bbc.co.uk/news/articles/abc#frag</link>
<description><![CDATA[The <b>Prime Minister</b> Keir Starmer could call an election.]]></description><pubDate>Sat, 14 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Weather: sunny spells</title><link>https://www.bbc.co.uk/news/articles/def</link><pubDate>kaputt</pubDate></item>
<item><title>Kein Link</title></item></channel></rss>"""


def test_news_parse_rss_and_reject_entities():
    items = N.parse_rss(RSS)
    assert len(items) == 2 and items[0]["url"] == "https://www.bbc.co.uk/news/articles/abc"
    assert items[0]["published"] == datetime(2026, 11, 14, 10, 0, tzinfo=timezone.utc) and items[1]["published"] is None
    assert "<b>" not in items[0]["summary"] and "Keir Starmer" in items[0]["summary"]
    with pytest.raises(ValueError):
        N.parse_rss(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel><item><title>&a;</title></item></channel></rss>')


def test_news_matching_needs_name_and_second_term():
    q = "Will Keir Starmer call a snap election before March?"
    nt = N.tokens("Starmer reportedly weighs snap election as Labour slides")
    assert N.match_score(N.names(q), N.tokens(q), nt) > 0
    assert N.match_score(N.names(q), N.tokens(q), N.tokens("Starmer visits school in Leeds")) == 0     # nur der Name reicht nicht
    q2 = "Will the highest temperature in London be 12°C on November 14?"
    assert N.match_score(N.names(q2), N.tokens(q2), N.tokens("London election results announced")) == 0
    assert N.is_hedged("Starmer reportedly weighs election") and not N.is_hedged("Starmer calls election")


def test_news_agent_stores_links_and_dedupes(tmp_path, monkeypatch):
    st = _store(tmp_path)
    monkeypatch.setattr(config, "NEWS_FEEDS", [("BBC Test", "https://feeds.bbci.co.uk/x.xml"), ("Kaputt", "https://feeds.bbci.co.uk/y.xml")])
    ScannerAgent(st).ingest([_m("pol", cat="politics", question="Will Keir Starmer call a snap election before March?")], NOW)

    def fetch(url):
        if url.endswith("y.xml"):
            raise HttpError("503", status=503)
        return RSS
    na = N.NewsAgent(st, fetch=fetch)
    out = na.run(NOW, time.time() + 60)
    assert out == {"feeds": 1, "fehler": 1, "neu": 2, "zugeordnet": 1}
    row = st.q1("SELECT n.source, n.flags, n.published, l.market_id FROM news n JOIN news_links l ON l.news_id=n.id")
    assert row == {"source": "BBC Test", "flags": "unbestätigt", "published": "2026-11-14T10:00:00+00:00", "market_id": "pol"}
    assert st.scalar("SELECT news_48h FROM markets WHERE id='pol'") == 1
    assert na.run(NOW, time.time() + 60)["neu"] == 0 and st.scalar("SELECT COUNT(*) FROM news") == 2


# ================================================================ Guardian
def test_guardian_measures_backs_up_prunes_and_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    st = _store(tmp_path)
    res = read_resources(str(tmp_path), st.path)
    assert res["rss_mb"] > 5 and res["db_mb"] > 0 and res["disk_total_gb"] > 0 and res["threads"] >= 1
    pa = ProbabilityAgent(st)
    pa.log([_o(_m(), 0.5)], NOW)
    old = (NOW - timedelta(days=40)).strftime("%Y-%m-%dT")
    st.xmany("INSERT INTO snapshots(market_id, ts, price) VALUES(?,?,?)", [("m1", old + "00:00", 0.5), ("m1", old + "13:00", 0.5)])
    st.x("INSERT INTO events(ts, level, type, title) VALUES(?,?,?,?)", ((NOW - timedelta(days=200)).isoformat(), "info", "x", "alt"))
    reg = Registry(st)
    g = reg.add(GuardianAgent(st, reg, pa))
    out = reg.run("guardian", NOW)
    assert out["sicherung"].startswith("polybot-") and out["kette"] == "ok (+1)" and out["integrität"] == "ok"
    assert st.q("SELECT ts FROM snapshots") == [{"ts": old + "00:00"}]                          # Tagespunkt bleibt
    assert st.scalar("SELECT COUNT(*) FROM events WHERE title='alt'") == 0
    assert st.scalar("SELECT COUNT(*) FROM forecasts") == 1                                     # Historie wird nie aufgeräumt
    v = g.view()
    assert v["sicherungen"] and v["schema"] == migrations.LATEST and v["integrität"]["ergebnis"] == "ok"
    assert reg.run("guardian", NOW).get("sicherung") is None                                    # nur einmal am Tag


def test_guardian_reports_source_outage_and_recovery(tmp_path):
    st = _store(tmp_path)
    g = GuardianAgent(st)
    for _ in range(5):
        sources.before("deribit")
        sources.after("deribit", False, status=500, msg="Server Error")
    assert g.check_sources() == 1 and g.check_sources() == 1
    assert st.scalar("SELECT COUNT(*) FROM events WHERE type='quelle' AND level='hoch'") == 1   # einmal melden, nicht jede Runde
    sources.reset()
    assert g.check_sources() == 0
    assert st.q1("SELECT title FROM events ORDER BY id DESC")["title"].startswith("Datenquelle wieder erreichbar")


# ================================================================ Koordinator + Handels-Lauf
def _coord(store, **kw):
    return Coordinator(store, fetch_markets=kw.get("fetch_markets", lambda p: []), fetch_history=lambda *a: [],
                       fetch_text=kw.get("fetch_text", lambda url: RSS))


def test_cycle_logs_forecasts_stores_outcomes_and_blocks_implausible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_DIVERGENCE", 0.25)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    markets = [_market("m1", 100000, end, 0.20, 0.22),     # Modell ~49 % gegen 21 % am liquiden Markt -> unplausibel
               _market("m2", 100000, end, 0.30, 0.32)]     # 18 Punkte Abstand -> plausibel, wird gekauft
    resolved = {}
    store, eng = _engine(tmp_path, [{"id": "e1", "title": "BTC", "slug": "btc", "markets": markets}], resolved)
    assert eng.run_cycle() and eng.state["last_error"] is None, eng.state["last_error"]
    fc = store.q("SELECT * FROM forecasts WHERE champion=1 ORDER BY market_id")
    assert [f["market_id"] for f in fc] == ["m1", "m2"] and [f["valid"] for f in fc] == [0, 1]
    assert "Abweichung" in fc[0]["reject_reason"] and fc[0]["model"] == "crypto.histprem" and fc[0]["cluster"].startswith("BTC:")
    assert store.scalar("SELECT COUNT(*) FROM forecasts WHERE champion=0 AND model='crypto.hist'") == 2    # Herausforderer läuft mit
    assert not any(p["market_id"] == "m1" for p in store.open_positions())                                  # gesperrt -> keine Wette
    assert any(p["market_id"] == "m2" for p in store.open_positions("haupt_krypto"))
    assert store.scalar("SELECT COUNT(*) FROM markets WHERE modeled=1") == 2 and store.scalar("SELECT COUNT(*) FROM snapshots") == 2
    assert eng.intel.validation.cycle["gültig"] == 1 and eng.intel.contradictions == []
    n = store.scalar("SELECT COUNT(*) FROM forecasts")
    eng.run_cycle()
    assert store.scalar("SELECT COUNT(*) FROM forecasts") == n                                              # keine Duplikate
    for p in store.open_positions():
        store.update_position(p["id"], end_date=(now - timedelta(hours=2)).isoformat())
    resolved["m2"] = {**markets[1], "closed": True, "outcomePrices": '["0", "1"]', "umaResolutionStatus": "resolved"}
    eng.settle()
    assert store.q1("SELECT outcome, source FROM outcomes") == {"outcome": 0.0, "source": "polymarket"}
    assert eng.intel.learning.rows()[0]["outcome"] == 0.0


def test_trade_switch_per_category(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TRADE_CRYPTO", False)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    store, eng = _engine(tmp_path, [{"id": "e1", "markets": [_market("m1", 100000, _end(now), 0.20, 0.22)]}])
    eng.run_cycle()
    assert store.open_positions() == [] and store.scalar("SELECT COUNT(*) FROM forecasts") == 2            # Prognosen laufen weiter


def test_risk_brake_day_lock_stays_until_next_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RISK_PAUSE", True)   # Pausen nur noch auf Wunsch (Einstellung)
    store, eng = _engine(tmp_path, [])
    eng.run_cycle()
    aid = "haupt_krypto"
    day = datetime.now(timezone.utc)
    eng.check_risk(day)
    store.add_cash(-100, config.START_BALANCE, aid)                                                         # −10 % heute
    eng.check_risk(day)
    assert "heute" in eng.state["paused"][aid]
    store.add_cash(+100, config.START_BALANCE, aid)                                                         # erholt sich
    eng.check_risk(day)
    assert "heute" in eng.state["paused"][aid]                                                              # bleibt gesperrt (kein Flattern)
    eng.check_risk(day + timedelta(days=1))
    assert eng.state["paused"][aid] is None
    assert store.scalar("SELECT COUNT(*) FROM events WHERE type='risikobremse'") == 1


def test_coordinator_cycle_runs_background_agents_within_budget(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    store = _store(tmp_path)
    co = _coord(store)
    from polybot.engine import Engine
    from polybot.sports_model import OddsClient
    from tests.test_engine import CLOSES
    eng = Engine(store, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                 odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None, book_fn=None, intel=co)
    assert eng.intel is co and co.engine is eng
    with caplog.at_level("INFO", logger="polybot.takt"):
        tick = co.cycle()
    assert set(tick["gelaufen"]) >= {"scanner", "news", "learning", "strategy", "guardian"} and tick["zurückgestellt"] == []
    line = [r.getMessage() for r in caplog.records if r.name == "polybot.takt"][-1]                          # Zustand steht auch im Log
    assert "gelaufen: scanner" in line and "Agenten mit Fehler: keine" in line and "Märkte offen" in line
    states = {a["id"]: a for a in co.registry.view()}
    assert all(states[k]["state"] == "ok" for k in ("trader", "scanner", "news", "learning", "strategy", "guardian")), states
    assert store.scalar("SELECT COUNT(*) FROM source_stats") >= 0
    tick = co.cycle()                                                                                        # gleich danach: stündliche Agenten nicht fällig
    assert tick["gelaufen"] == ["guardian"]
    # knappes Zeitbudget: alles außer dem Guardian wird zurückgestellt
    monkeypatch.setattr(config, "CYCLE_BUDGET_SEC", 0.0)
    for s in co.registry.status.values():
        s["_last_ts"] = 0
    tick = co.cycle()
    assert tick["gelaufen"] == ["guardian"] and set(tick["zurückgestellt"]) == {"scanner", "news", "learning", "strategy"}


def test_coordinator_backs_off_failing_agent(tmp_path):
    store = _store(tmp_path)

    def boom(url):
        raise RuntimeError("Feed kaputt")
    co = _coord(store)
    co.news.run = lambda now, deadline: boom("x")
    co.news.interval = 0
    for _ in range(3):
        co.tick(NOW, time.time() + 100)
    assert co.registry.status["news"]["error_streak"] == 3
    ran, skipped = co.tick(NOW, time.time() + 100)
    assert "news" in skipped and "news" not in ran                                                          # zurückgestuft statt jede Runde zu scheitern
    assert co.guardian.check_agents() == 1 and store.scalar("SELECT COUNT(*) FROM events WHERE type='agent'") == 1


def test_coordinator_settings_change_is_audited(tmp_path):
    co = _coord(_store(tmp_path))
    assert co.change_settings({"NOTIFY_DAILY_CAP": 10}) == [("NOTIFY_DAILY_CAP", 40, 10)]
    ev = co.store.q1("SELECT title, detail FROM events")
    assert ev == {"title": "Einstellung geändert: NOTIFY_DAILY_CAP", "detail": "40 → 10"}


# ================================================================ Claude-Review-Bericht
def test_report_due_only_on_report_days_after_hour(tmp_path):
    st = _store(tmp_path)
    wed_early = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)     # Mi 16 Uhr Berlin
    wed = datetime(2026, 11, 11, 16, 30, tzinfo=timezone.utc)          # Mi 17:30 Berlin
    thu = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
    sun = datetime(2026, 11, 15, 17, 0, tzinfo=timezone.utc)
    assert [reports.due(st, t) for t in (wed_early, wed, thu, sun)] == [False, True, False, True]
    st.kv_set("report:last_day", "2026-11-11")
    assert reports.due(st, wed) is False


def test_report_has_all_sections_only_measured_values(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    store, eng = _engine(tmp_path, [])
    co = eng.intel
    eng.run_cycle()
    with caplog.at_level("INFO", logger="polybot.bericht"):
        rid = reports.generate(co, NOW, kind="manuell")
    r = store.q1("SELECT * FROM reports WHERE id=?", (rid,))
    md = r["markdown"]
    for i, name in enumerate(("Allgemeiner Systemzustand", "Neue Funktionen", "Prognosequalität", "Fehlerentwicklung", "Erfolgreiche Verbesserungen",
                              "Fehlgeschlagene Verbesserungen", "Neue erkannte Muster", "Datenquellenqualität", "Strategievergleich",
                              "Ressourcenverbrauch", "Offene technische Probleme", "Verbesserungsvorschläge",
                              "Empfehlungen für die nächsten Entwicklungsschritte"), 1):
        assert f"## {i}. {name}" in md
    assert "Prognosen mit bekanntem Ergebnis: 0" in md and "Keine Daten." in md and "Strategie × Thema" in md and "| Haupt |" in md
    lines = [x.message for x in caplog.records if x.message.startswith(f"CLAUDE-REVIEW#{rid}| ")]
    assert lines[0].endswith(r["title"]) and lines[-1].endswith("ENDE") and len(lines) == md.count("\n") + 3
    assert store.scalar("SELECT COUNT(*) FROM events WHERE type='bericht'") == 1
    n = store.scalar("SELECT COUNT(*) FROM recommendations")
    reports.generate(co, NOW + timedelta(hours=1))
    assert store.scalar("SELECT COUNT(*) FROM recommendations") == n                                        # offene Empfehlungen nicht doppelt
