"""Konten je Strategie × Thema (14.11.): Aufbau, einmaliger Umzug der alten Konten, Zusammenfassungen."""
import json
import os

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, engine as engine_mod  # noqa: E402
from polybot.engine import Engine  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402


def _new(store):
    return Engine(store, odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None)


def test_every_strategy_has_one_account_per_topic():
    ids = [a["id"] for a in config.ACCOUNTS]
    assert len(ids) == len(set(ids))
    topics = [t[0] for t in config.TOPICS]
    for s in config.STRATEGIES:
        mine = [a for a in config.ACCOUNTS if a["strategy"] == s["id"]]
        expected = list(config.MODEL_TOPICS) if s["id"] == "arbitrage" else topics
        assert [a["topic"] for a in mine] == expected, s["id"]
        assert all(a["cats"] == [a["topic"]] and a["id"] == f"{s['id']}_{config.TOPIC_CODE[a['topic']]}" for a in mine)
    assert len(ids) == 8 * 11 + 3
    # die bisherigen Hauptkonten behalten ihre Kennung (Verlauf, Telegram, Lesezeichen)
    assert {"haupt_krypto", "haupt_sport", "haupt_wetter"} <= set(ids) and ids[0] == "haupt_krypto"
    # Arbitrage nur dort, wo der Bot ganze Ereignisse kennt
    assert {a["id"] for a in config.ACCOUNTS if a["arb"]} == {"haupt_krypto", "haupt_sport", "haupt_wetter",
                                                              "arbitrage_krypto", "arbitrage_sport", "arbitrage_wetter"}
    # die Schwelle in der Beschreibung passt zum Thema
    acc = {a["id"]: a for a in config.ACCOUNTS}
    assert "ab 6 Pkt (eigenes Modell)" in acc["haupt_krypto"]["desc"] and "Basisraten, statistisch gesichert" in acc["haupt_politik"]["desc"]
    assert "Arbitrage" not in acc["haupt_politik"]["desc"]


def test_telegram_accounts_accept_patterns(monkeypatch):
    monkeypatch.setattr(config, "NOTIFY_ACCOUNTS", ["haupt_*"])
    assert config.notify_account("haupt_politik") and not config.notify_account("schwelle4_politik")
    monkeypatch.setattr(config, "NOTIFY_ACCOUNTS", ["haupt_krypto", "kelly_sport"])
    assert config.notify_account("kelly_sport") and not config.notify_account("haupt_sport")


def _pos(store, account, mid, cat, cost, status="open", pnl=None):
    row = {"account": account, "market_id": mid, "event_id": "e", "category": cat, "question": "q " + mid, "side": 0, "side_name": "Yes",
           "shares": cost * 2, "price": 0.5, "cost": cost, "fee": 0, "own_p": 0.6, "market_p": 0.5, "edge": 0.1, "status": status,
           "opened_at": "2026-11-12T10:00:00+00:00", "end_date": "2026-11-20T10:00:00+00:00", "kind": "model"}
    if status != "open":
        row.update(pnl=pnl, payout=cost + pnl, closed_at="2026-11-13T10:00:00+00:00")
    return store.insert_position(row)


def test_old_accounts_move_to_strategy_topic_accounts_once(tmp_path):
    store = Store(str(tmp_path / "m.sqlite"))
    store.kv_set("split_v1", "2026-11-13")                       # alte Aufteilung des Hauptkontos ist schon gelaufen
    store.kv_set("cash:haupt_krypto", 950.0)                       # bestehendes Hauptkonto mit eigenem Stand
    store.kv_set("started_at:schwelle4", "2026-11-10T09:00:00+00:00")
    store.kv_set("paused_state", json.dumps({"kelly": "−25 % seit Start", "haupt_krypto": None}))
    _pos(store, "schwelle4", "c1", "crypto", 20)                                  # offen
    _pos(store, "schwelle4", "s1", "sports", 10, "won", 5.0)                      # +5
    _pos(store, "schwelle4", "s2", "sports", 50, "lost", -50.0)                   # −50
    _pos(store, "haupt_weitere", "p1", "politics", 10)                            # offen
    _pos(store, "haupt_weitere", "b1", "crypto", 6, "lost", -6.0)                 # Basisraten-Wette im Thema Krypto
    _pos(store, "aussenseiter", "w1", "weather", 20, "won", 1.0)
    _pos(store, "haupt_krypto", "k1", "crypto", 30)                               # gehört schon dem richtigen Konto
    with store.lock:
        store.db.execute("INSERT INTO orders(account, market_id, category, question, side, side_name, price, stake, status, created_at) "
                         "VALUES('limit','o1','crypto','q',0,'Yes',0.4,20,'open','2026-11-14T10:00:00+00:00')")
        store.db.commit()
    eng = _new(store)

    assert {p["market_id"] for p in store.open_positions("schwelle4_krypto")} == {"c1"}
    assert {p["market_id"] for p in store.closed_positions(account="schwelle4_sport")} == {"s1", "s2"}
    assert [p["market_id"] for p in store.open_positions("haupt_politik")] == ["p1"]
    assert [p["market_id"] for p in store.closed_positions(account="haupt_krypto")] == ["b1"]
    assert [p["market_id"] for p in store.closed_positions(account="aussenseiter_wetter")] == ["w1"]
    assert [o["market_id"] for o in store.open_orders("limit_krypto")] == ["o1"]
    assert not store.q("SELECT 1 FROM positions WHERE account IN ('schwelle4','haupt_weitere','aussenseiter')")
    # Bargeld: 1.000 $ + G/V der abgeschlossenen − Einsatz der offenen; bestehende Konten behalten ihren Stand
    assert abs(eng.cash("schwelle4_krypto") - 980) < 1e-9
    assert abs(eng.cash("schwelle4_sport") - 955) < 1e-9
    assert abs(eng.cash("haupt_politik") - 990) < 1e-9
    assert abs(eng.cash("aussenseiter_wetter") - 1001) < 1e-9
    assert abs(eng.cash("haupt_krypto") - 944) < 1e-9
    assert abs(eng.cash("kelly_sport") - 1000) < 1e-9                           # unberührt
    # Herkunft und Verlustgrenze: neue Konten zählen ab dem übernommenen Wert, der Start bleibt erhalten
    assert store.kv_get("started_at:schwelle4_krypto") == "2026-11-10T09:00:00+00:00"
    assert abs(float(store.kv_get("risk_base:schwelle4_sport")) - 955) < 1e-6
    assert store.kv_get("risk_base:haupt_krypto") is None
    assert "kelly" not in store.kv_json("paused_state", {})
    assert os.path.exists(tmp_path / "backups" / "vor-kontoaufteilung.sqlite")
    # zweiter Start: nichts doppelt
    _new(store)
    assert abs(eng.cash("schwelle4_sport") - 955) < 1e-9 and abs(eng.cash("haupt_krypto") - 944) < 1e-9


def test_fresh_database_needs_no_move(tmp_path):
    store = Store(str(tmp_path / "f.sqlite"))
    _new(store)
    assert store.kv_get("split_v2") and not os.path.exists(tmp_path / "backups" / "vor-kontoaufteilung.sqlite")


def test_overview_and_strategy_summary(tmp_path):
    store = Store(str(tmp_path / "o.sqlite"))
    eng = _new(store)
    _pos(store, "schwelle8_politik", "p1", "politics", 10, "won", 4.0)
    store.add_cash(4.0, config.START_BALANCE, "schwelle8_politik")      # −10 Einsatz, +14 Auszahlung
    _pos(store, "schwelle8_sport", "s1", "sports", 20)
    store.add_cash(-20.0, config.START_BALANCE, "schwelle8_sport")
    store.update_position(store.open_positions("schwelle8_sport")[0]["id"], mark=0.6)   # 40 Anteile × 0,60 = 24 $
    rows = {a["id"]: a for a in eng.accounts_overview()}
    assert len(rows) == len(config.ACCOUNTS)
    assert abs(rows["schwelle8_politik"]["pnl_total"] - 4) < 1e-9 and rows["schwelle8_politik"]["winrate"] == 1.0
    assert abs(rows["schwelle8_sport"]["equity"] - 1004) < 1e-9 and rows["schwelle8_sport"]["open_count"] == 1
    # gleiche Zahlen wie die Einzelrechnung
    assert abs(rows["schwelle8_sport"]["equity"] - eng.equity("schwelle8_sport")) < 1e-9
    summ = {s["id"]: s for s in eng.strategy_summary(eng.accounts_overview())}
    assert [s["id"] for s in eng.strategy_summary(eng.accounts_overview())] == [s["id"] for s in config.STRATEGIES]
    assert summ["schwelle8"]["konten"] == 11 and abs(summ["schwelle8"]["pnl_total"] - 8) < 1e-9 and summ["schwelle8"]["aktiv"] == 2
    assert summ["arbitrage"]["konten"] == 3
    # Analyse-Status zeigt das Hauptkonto des Themas
    o = {"market": {"category": "politics"}, "analysis": {"lane": "base"}}
    assert engine_mod.Engine.main_for(o) == "haupt_politik"


def test_daily_summary_groups_strategies(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from polybot import notify
    store = Store(str(tmp_path / "d.sqlite"))
    eng = _new(store)
    now = datetime(2026, 11, 14, 21, 30, tzinfo=timezone.utc)
    row = {"account": "kelly_politik", "market_id": "p1", "event_id": "e", "category": "politics", "question": "q", "side": 0,
           "side_name": "Yes", "shares": 20, "price": 0.5, "cost": 10, "fee": 0, "status": "won", "pnl": 10.0, "payout": 20,
           "opened_at": "2026-11-14T10:00:00+00:00", "closed_at": "2026-11-14T20:00:00+00:00", "kind": "model"}
    store.insert_position(row)
    store.insert_position({**row, "account": "haupt_sport", "market_id": "s1", "pnl": -3.0, "status": "lost", "payout": 0})
    sent = []
    monkeypatch.setattr(notify, "enabled", lambda: True)
    monkeypatch.setattr(notify, "send", lambda text, *a, **k: sent.append(text))
    eng.daily_summary(now)
    msg = sent[0]
    assert "Haupt · Sport: heute -3.00 $" in msg and "Haupt · Politik" not in msg      # unberührte Hauptkonten fehlen
    assert "Viertel-Kelly: +10.00 $ (1 abgerechnet)" in msg and msg.count("\n") < 20
