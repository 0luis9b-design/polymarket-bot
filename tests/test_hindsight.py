"""Rückblick (2.8, 22.11.): keine Modell-Wetten in Bereichen, in denen solche Wetten nachweislich verloren hätten.

Anlass Bericht #4: Wetter-Modellwetten gewannen 19 % bei 40 % Preis – fast alle „Nein“ auf den Temperatur-Bereich,
den der Markt für den wahrscheinlichsten hielt, mit 15–22 Punkten Abweichung."""
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, engine as engine_mod, hindsight  # noqa: E402
from polybot.agents.learning import LearningAgent  # noqa: E402
from tests.test_games import _new  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
Q = "Will the highest temperature in {} be 13°C on November {}?"


def _fc(st, mid, p, mkt, outcome, cluster, cat="weather", q=None, valid=1, days_ago=2):
    st.x("INSERT OR IGNORE INTO markets(id, question, category) VALUES(?,?,?)", (mid, q or Q.format(cluster, 20), cat))
    st.x("INSERT INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, champion, valid, cluster) "
         "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (mid, cat, "weather.live", "1", "6-24h",
                                          (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds"), p, mkt, 1, valid, cluster))
    st.x("INSERT OR REPLACE INTO outcomes(market_id, outcome, resolved_at) VALUES(?,?,?)", (mid, outcome, NOW.isoformat()))


def _fill(st, n=25):
    """n Stadt-Tage: „Nein“ mit 20 Pkt Abweichung verliert fast immer; „Ja“ mit 5 Pkt gewinnt öfter als der Preis."""
    for i in range(n):
        _fc(st, f"no{i}", 0.25, 0.45, 1 if i % 5 else 0, f"city{i}")          # Nein zum Preis 55 %, gewinnt nur 20 %
        _fc(st, f"yes{i}", 0.55, 0.50, 1 if i % 3 else 0, f"city{i}b")       # Ja zum Preis 50 %, gewinnt 67 %


def test_losing_area_is_blocked_and_winning_area_stays_open(tmp_path):
    st, _ = _new(tmp_path)
    _fill(st)
    res = hindsight.compute(st, NOW)
    t = hindsight.Table(res)
    why = t.block("weather", "Genauer Wert (1 Grad)", False, 0.20)
    assert why and why.startswith("Rückblick: „Nein“ mit 15–25 Pkt Abweichung · Genauer Wert (1 Grad)")
    assert t.block("weather", "Genauer Wert (1 Grad)", True, 0.05) is None          # gewinnt -> offen
    assert t.block("weather", "Genauer Wert (1 Grad)", False, 0.05) is None         # keine Daten -> offen
    assert t.block("crypto", "Fällt auf einen Kurs", False, 0.20) is None           # anderes Thema -> offen
    # Ohne passende Wettart entscheidet das ganze Thema
    assert t.block("weather", "Spanne (2 Grad)", False, 0.20).startswith("Rückblick: „Nein“ mit 15–25 Pkt Abweichung hätte verloren (")
    # abschaltbar und Schwelle einstellbar
    config.HINDSIGHT = False
    assert t.block("weather", "Genauer Wert (1 Grad)", False, 0.20) is None
    config.HINDSIGHT = True
    config.HINDSIGHT_Z = 99
    assert t.block("weather", "Genauer Wert (1 Grad)", False, 0.20) is None
    assert st.kv_get("rueckblick:seit") == res["stand"]


def test_needs_enough_independent_events(tmp_path):
    st, _ = _new(tmp_path)
    for i in range(40):                                  # 40 Märkte, aber nur 5 Ereignisse (Bereiche desselben Tages)
        _fc(st, f"m{i}", 0.25, 0.45, 1, f"day{i % 5}")
    t = hindsight.Table(hindsight.compute(st, NOW))
    assert t.block("weather", "Genauer Wert (1 Grad)", False, 0.20) is None


def test_ignores_old_invalid_and_tiny_differences(tmp_path):
    st, _ = _new(tmp_path)
    for i in range(25):
        _fc(st, f"old{i}", 0.25, 0.45, 1, f"o{i}", days_ago=config.HINDSIGHT_DAYS + 2)   # zu alt
        _fc(st, f"inv{i}", 0.25, 0.45, 1, f"v{i}", valid=0)                              # nie gehandelt
        _fc(st, f"tiny{i}", 0.44, 0.45, 1, f"t{i}")                                      # unter 3 Pkt
    res = hindsight.compute(st, NOW)
    assert res["bereiche"] == []


def test_engine_refuses_model_bets_in_blocked_area_only(tmp_path):
    st, eng = _new(tmp_path)
    _fill(st)
    hindsight.compute(st, NOW)
    m = {"id": "sh", "question": Q.format("Shanghai", 21), "category": "weather", "event_id": "e", "detail": None,
         "end_date": NOW + timedelta(hours=20), "fee_rate": 0.0, "token_ids": [], "best_bid": 0.54, "best_ask": 0.55}
    ev = {"side": 1, "price": 0.55, "fee_ps": 0.0, "eff": 0.55, "q": 0.75, "edge": 0.20}
    o = {"market": m, "eval": ev, "group": "weather:e", "status": {}, "market_p": 0.45,
         "analysis": {"p_yes": 0.25, "lane": "model"}}
    acc = engine_mod.ACC["haupt_wetter"]
    assert eng.refuse(acc, o).startswith("Rückblick: „Nein“")
    # Basisraten haben ihren eigenen Wächter, feste Regeln kein Modell -> nicht betroffen
    assert eng.refuse(acc, {**o, "analysis": {"p_yes": 0.25, "lane": "base"}}) is None
    assert eng.refuse(acc, {**o, "analysis": {"p_yes": 0.45, "lane": "rule"}}) is None
    # „Ja“ auf denselben Markt mit kleiner Abweichung bleibt erlaubt
    yes = {**o, "eval": {**ev, "side": 0, "q": 0.55, "edge": 0.05}, "analysis": {"p_yes": 0.55, "lane": "model"}, "market_p": 0.50}
    assert eng.refuse(acc, yes) is None


def test_learning_agent_reports_changes(tmp_path):
    st, _ = _new(tmp_path)
    _fill(st)
    agent = LearningAgent(st, probability=None)
    assert agent.hindsight(NOW) >= 1
    ev = st.q("SELECT title FROM events WHERE type='rueckblick'")
    assert any(e["title"].startswith("Rückblick sperrt: Wetter") for e in ev)


def test_rejections_before_a_rule_change_are_not_counted(tmp_path):
    st, _ = _new(tmp_path)
    agent = LearningAgent(st, probability=None)
    base = {"champion": 1, "valid": 0, "category": "crypto", "p": 0.6, "market_p": 0.5, "outcome": 1, "market_id": "x",
            "horizon": "<6h", "key": "crypto.options@1", "cluster": "c"}
    rows = [{**base, "reject_reason": "Summe der Antworten = 130 %", "created_at": "2026-11-18T10:00:00+00:00"},
            {**base, "reject_reason": "Summe der Antworten = 125 %", "created_at": "2026-11-22T10:00:00+00:00"},
            {**base, "reject_reason": "Abweichung zu groß (40 Pkt)", "created_at": "2026-11-18T10:00:00+00:00"}]
    v = {x["grund"]: x for x in agent.errors(rows)["validierung"]}
    assert v["Summe der Antworten"]["n"] == 1 and v["Summe der Antworten"]["seit"].startswith("2026-11-21")
    assert v["Abweichung zu groß"]["n"] == 1 and v["Abweichung zu groß"]["seit"] is None


def test_report_shows_hindsight_table(tmp_path, monkeypatch):
    from polybot import reports
    from tests.test_agents import _engine
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    store, eng = _engine(tmp_path, [])
    _fill(store)
    hindsight.compute(store, NOW)
    eng.run_cycle()
    rid = reports.generate(eng.intel, NOW, kind="manuell")
    md = store.q1("SELECT markdown FROM reports WHERE id=?", (rid,))["markdown"]
    assert "Rückblick (seit 2.8)" in md and "| Wetter | Nein | 15–25 Pkt | 25 | 20 % | 55 % | -64 % |" in md
    assert "Gesperrt nach Wettart: Wetter · Genauer Wert (1 Grad) · „Nein“ · 15–25 Pkt" in md
