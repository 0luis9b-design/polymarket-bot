"""Spiele (17.11.): höchstens eine Wette je Spiel und Konto; Konten werden nicht mehr pausiert.

Live-Befund 15./16.11.: Auf Färöer – Moldau wettete ein Konto auf „1:1“, „Unentschieden“ und „2:2“ – alles verloren.
Bei E-Sport kamen Sieger und einzelne Karten desselben Spiels zusammen."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, engine as engine_mod, polymarket, taxonomy  # noqa: E402
from polybot.engine import Engine, game_key  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _new(tmp_path):
    st = Store(str(tmp_path / "g.sqlite"))
    eng = Engine(st, odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None)
    eng.book_fn = None
    return st, eng


def _game_market(mid, q, ev_id, ev_slug, bid=0.03, ask=0.04, tags=("sports", "soccer")):
    raw = {"id": mid, "question": q, "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps([str(bid), str(1 - bid)]),
           "bestBid": bid, "bestAsk": ask, "liquidityNum": 20000, "volume24hr": 5000,
           "endDate": (NOW + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ"), "active": True, "closed": False,
           "acceptingOrders": True, "clobTokenIds": json.dumps([f"{mid}-y", f"{mid}-n"]),
           "tags": [{"slug": t, "label": t} for t in tags]}
    ev = {"id": ev_id, "title": "Faroe Islands vs. Moldova", "slug": ev_slug, "tags": raw["tags"]}
    m = polymarket.normalize_market(raw, ev, "sports")
    m.update(taxonomy.classify(m))
    m["fee_rate"] = 0.0
    return m


def test_game_key_joins_all_markets_of_one_game():
    a = {"event_slug": "fif-fro-mda-2026-11-16", "event_id": "1"}
    b = {"event_slug": "fif-fro-mda-2026-11-16-exact-score", "event_id": "2"}
    c = {"event_slug": "cs2-tt3-bar2-2026-11-14", "event_id": "3"}
    assert game_key(a) == game_key(b) == "spiel:fif-fro-mda-2026-11-16"
    assert game_key(c) != game_key(a)
    assert game_key({"event_slug": "some-event-without-date", "event_id": "77"}) == "spiel:77"


def test_only_one_bet_per_game_and_account(tmp_path):
    st, eng = _new(tmp_path)
    m1 = _game_market("x11", "Exact Score: Faroe Islands 1 - 1 Moldova?", "e-score", "fif-fro-mda-2026-11-16-exact-score")
    m2 = _game_market("x22", "Exact Score: Faroe Islands 2 - 2 Moldova?", "e-score", "fif-fro-mda-2026-11-16-exact-score")
    m3 = _game_market("drw", "Will Faroe Islands vs. Moldova end in a draw?", "e-main", "fif-fro-mda-2026-11-16")
    acc = engine_mod.ACC["aussenseiter_sport"]
    eng.do_longshots(acc, [m1, m2, m3], NOW)
    held = st.open_positions("aussenseiter_sport")
    assert len(held) == 1 and held[0]["grp"] == "spiel:fif-fro-mda-2026-11-16"
    # ein anderes Konto darf trotzdem wetten – jedes Konto hat seine eigene „eine Wette“
    assert not st.has_game("aussenseiter_politik", "spiel:fif-fro-mda-2026-11-16")
    # auch über das Ereignis: alte Wetten (vor dem Spiel-Schlüssel) zählen mit
    st.insert_position({"account": "haupt_sport", "market_id": "old", "event_id": "e-main", "category": "sports", "question": "q",
                        "side": 0, "side_name": "Yes", "price": 0.5, "shares": 2, "cost": 1, "fee": 0, "status": "open",
                        "opened_at": "2026-11-15T10:00:00+00:00", "grp": "spiel:e-main"})
    ev = {"side": 0, "price": 0.28, "fee_ps": 0.0, "eff": 0.28, "q": 0.45, "edge": 0.17}
    o = {"market": m3, "eval": ev, "group": game_key(m3), "status": {}, "analysis": {"p_yes": 0.45, "lane": "model"}}
    assert eng.open_position(engine_mod.ACC["haupt_sport"], o, 0.03) == "schon eine Wette auf dieses Spiel"


def test_accounts_are_not_paused_any_more(tmp_path):
    st, eng = _new(tmp_path)
    assert config.RISK_PAUSE is False
    st.add_cash(-300, config.START_BALANCE, "haupt_krypto")         # −30 %: früher Pause bis „Fortsetzen“
    eng.check_risk(NOW)
    eng.check_risk(NOW)
    assert not eng.paused("haupt_krypto")
    rows = st.q("SELECT title, detail, level FROM events WHERE type='risikobremse'")
    assert len(rows) == 1 and rows[0]["title"] == "Großer Verlust: Haupt · Krypto" and "handelt weiter" in rows[0]["detail"]
    assert rows[0]["level"] == "normal"


def test_existing_pauses_are_lifted_once(tmp_path):
    st = Store(str(tmp_path / "u.sqlite"))
    st.kv_set("paused:haupt_esport", "−25 % seit Start – pausiert bis „Fortsetzen“")
    st.kv_set("daystart:haupt_sport", json.dumps({"day": "2026-11-17", "equity": 1000, "locked": True}))
    st.kv_set("paused_state", json.dumps({"haupt_esport": "−25 %", "haupt_sport": "−8 %"}))
    st.add_cash(-300, config.START_BALANCE, "haupt_esport")
    eng = Engine(st, odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None)
    assert not eng.paused("haupt_esport") and not eng.paused("haupt_sport")
    assert st.kv_get("paused:haupt_esport") == "" and "locked" not in st.kv_json("daystart:haupt_sport")
    assert abs(float(st.kv_get("risk_base:haupt_esport")) - 700) < 1e-6      # falls die Pause je wieder eingeschaltet wird
    assert st.kv_get("unpause_v1")
