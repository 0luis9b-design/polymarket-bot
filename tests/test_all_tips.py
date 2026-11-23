"""„Alles tippen“ (2.10, Wunsch 23.11.): bei jedem Spiel und Ereignis eine Wette auf den Favoriten laut Markt –
auch Tennis, Tischtennis, E-Sport –, um zu sehen, wo Wetten Plus oder Minus machen. Eigene Konten, feste Regeln bleiben."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config  # noqa: E402
from polybot.engine import Engine  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402
from tests.test_engine import CLOSES  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _game(mid, tags, outcomes, bid, ask, start_h=5, end_days=None, slug=None, liq=5000, q=None, smt="moneyline"):
    start = NOW + timedelta(hours=start_h)
    end = start + (timedelta(days=end_days) if end_days else timedelta(hours=3))
    slug = slug or f"{mid}-{start:%Y-%m-%d}"
    return {"id": mid, "question": q or " vs ".join(outcomes), "outcomes": json.dumps(outcomes),
            "outcomePrices": json.dumps([str((bid + ask) / 2), str(1 - (bid + ask) / 2)]), "bestBid": bid, "bestAsk": ask,
            "liquidityNum": liq, "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "gameStartTime": start.strftime("%Y-%m-%d %H:%M:%S+00"),
            "active": True, "closed": False, "acceptingOrders": True, "sportsMarketType": smt,
            "clobTokenIds": json.dumps([f"{mid}-0", f"{mid}-1"]), "tags": [{"slug": t, "label": t.title()} for t in tags],
            "events": [{"id": "ev-" + slug, "title": "Spiel " + mid, "slug": slug}]}


def _eng(tmp_path, by_tag):
    st = Store(str(tmp_path / "a.sqlite"))
    calls = []

    def fetch_markets(params):
        calls.append(params)
        if "tag_id" not in params:          # „weitere Märkte“ (Basisraten) – hier leer
            return []
        return by_tag.get(params["tag_id"], []) if params["offset"] == 0 else []
    eng = Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                 odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None, book_fn=None, fetch_markets=fetch_markets)
    eng.book_fn = None
    return st, eng, calls


def _bets(st, prefix="alles_"):
    return {p["market_id"]: p for p in st.open_positions() if p["account"].startswith(prefix)}


def test_tips_the_favourite_of_every_game_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALL_TIPS", True)
    monkeypatch.setattr(config, "MIN_WIN_P", 0.50)
    sports = [
        _game("t1", ["sports", "tennis"], ["Jules Leroux", "Lucas Hammond"], 0.25, 0.27),       # Favorit: Hammond (74 %)
        _game("t2", ["sports", "tennis"], ["A Okonkwo", "H Bilicic"], 0.05, 0.90),              # Orderbuch leer/weit -> nein
        _game("t3", ["sports", "tennis"], ["Start", "Schon"], 0.40, 0.42, start_h=-1),           # schon begonnen -> nein
        _game("t4", ["sports", "tennis"], ["Spaet", "Dran"], 0.40, 0.42, start_h=60),            # erst in 60 h -> noch nicht
        _game("t5", ["sports", "tennis"], ["Klar", "Aussen"], 0.94, 0.95),                       # Favorit kostet > 90 ¢ -> nein
        # Fußball: drei Ja/Nein-Märkte je Spiel -> eine Wette, „Ja“ auf den Favoriten
        _game("f1", ["sports", "soccer"], ["Yes", "No"], 0.61, 0.62, slug="el1-bra-bar-2026-11-24", q="Will Bradford win?"),
        _game("f2", ["sports", "soccer"], ["Yes", "No"], 0.24, 0.26, slug="el1-bra-bar-2026-11-24", q="Will Barnsley win?"),
        _game("f3", ["sports", "soccer"], ["Yes", "No"], 0.20, 0.22, slug="el1-bra-bar-2026-11-24", q="Will it be a draw?"),
        # ausgeglichenes Fußballspiel ohne Favoriten über 50 %: wahrscheinlichstes „Nein“
        _game("g1", ["sports", "soccer"], ["Yes", "No"], 0.35, 0.36, slug="el2-pvl-nth-2026-11-24", q="Will Port Vale win?"),
        _game("g2", ["sports", "soccer"], ["Yes", "No"], 0.34, 0.35, slug="el2-pvl-nth-2026-11-24", q="Will Northampton win?"),
        _game("g3", ["sports", "soccer"], ["Yes", "No"], 0.28, 0.29, slug="el2-pvl-nth-2026-11-24", q="Will it be a draw?"),
    ]
    tt = [_game("tt1", ["sports", "table-tennis"], ["Tianyang Li", "Finn Luu"], 0.55, 0.58, end_days=7),
          _game("tt2", ["sports", "table-tennis"], ["Leer", "Buch"], 0.30, 0.70, end_days=7)]   # 40 ¢ Spanne -> nein
    es = [_game("e1", ["esports", "counter-strike"], ["FaZe", "Fnatic"], 0.66, 0.67)]
    # live 23.11.: Märkte kamen ohne Tags – dann gilt der Tag, über den sie geholt wurden (nicht „Sonstiges“)
    sports.append(_game("n1", [], ["Ole Miss Rebels", "Clemson Tigers"], 0.67, 0.68))
    es.append(_game("n2", [], ["Esport Academy", "gothboiclique"], 0.57, 0.58))
    st, eng, calls = _eng(tmp_path, {"sports": sports + tt, "esports": es, "table-tennis": tt})
    assert eng.run_cycle()
    bets = _bets(st)
    assert set(bets) == {"t1", "f1", "g3", "tt1", "e1", "n1", "n2"}, set(bets)
    assert (bets["n1"]["account"], bets["n2"]["account"]) == ("alles_sport", "alles_esport")
    assert all(c.get("include_tag") == "true" for c in calls if "tag_id" in c)
    assert (bets["t1"]["side_name"], bets["t1"]["account"]) == ("Lucas Hammond", "alles_sport")
    assert (bets["f1"]["side_name"], bets["g3"]["side_name"]) == ("Yes", "No")
    assert (bets["tt1"]["side_name"], bets["e1"]["account"]) == ("Tianyang Li", "alles_esport")
    assert all(abs(p["cost"] - config.ALL_STAKE) < 0.01 and p["lane"] == "rule" for p in bets.values())
    assert "Favorit laut Markt – Lucas Hammond mit 74 %" in bets["t1"]["entry"]
    # die anderen Strategien wetten auf diese Spiele nicht (kein Modell, keine Buchmacher-Quoten)
    assert not [p for p in st.open_positions() if not p["account"].startswith("alles_")]
    # Liste nur alle 30 min neu; nächster Lauf: keine zweite Wette auf dieselben Spiele
    n_calls = len(calls)
    assert eng.run_cycle()
    assert set(_bets(st)) == set(bets) and len([c for c in calls[n_calls:] if "tag_id" in c]) == 0


def test_can_be_switched_off_and_respects_topic_switch(tmp_path, monkeypatch):
    games = {"sports": [_game("t1", ["sports", "tennis"], ["A", "B"], 0.25, 0.27)],
             "esports": [_game("e1", ["esports", "dota-2"], ["X", "Y"], 0.66, 0.67)]}
    monkeypatch.setattr(config, "ALL_TIPS", False)
    st, eng, _ = _eng(tmp_path, games)
    assert eng.run_cycle() and not _bets(st)
    monkeypatch.setattr(config, "ALL_TIPS", True)
    monkeypatch.setattr(config, "TRADE_SPORTS", False)
    monkeypatch.setattr(config, "ALL_STAKE", 3.0)
    assert eng.run_cycle()
    bets = _bets(st)
    assert set(bets) == {"e1"} and abs(bets["e1"]["cost"] - 3.0) < 0.01


def test_never_exact_score_and_other_strategies_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALL_TIPS", True)
    st, eng, _ = _eng(tmp_path, {})
    m = {"id": "x", "question": "Exact Score: Moldova 0 - 0 Faroe Islands?", "outcomes": ["Yes", "No"], "category": "sports",
         "accepting_orders": True, "end_date": NOW + timedelta(hours=5), "game_start": NOW + timedelta(hours=2),
         "liquidity": 5000, "best_bid": 0.30, "best_ask": 0.31, "event_slug": "fif-mda-fro-2026-11-24", "event_id": "e", "fee_rate": 0.0,
         "sports_market_type": "soccer_exact_score", "lane": "alles"}
    eng.do_all(config.ACCOUNTS[[a["id"] for a in config.ACCOUNTS].index("alles_sport")], [m], NOW)
    assert not _bets(st)
    # Außenseiter-Regel bleibt bei ihren bisherigen Märkten
    longshot = {**m, "question": "Will Moldova win?", "best_bid": 0.03, "best_ask": 0.04, "sports_market_type": "moneyline"}
    eng.do_longshots(config.ACCOUNTS[[a["id"] for a in config.ACCOUNTS].index("aussenseiter_sport")], [longshot], NOW)
    assert not st.open_positions("aussenseiter_sport")


def test_games_are_settled_soon_after_the_start(tmp_path):
    st, eng, _ = _eng(tmp_path, {})
    asked = []
    eng.fetch_by_ids = lambda ids: asked.extend(ids) or {}
    base = {"account": "alles_sport", "category": "sports", "question": "Li vs Luu", "side": 0, "side_name": "Tianyang Li",
            "price": 0.58, "shares": 8, "cost": 5, "fee": 0, "status": "open", "opened_at": NOW.isoformat(), "lane": "rule",
            "end_date": (NOW + timedelta(days=6)).isoformat()}
    st.insert_position({**base, "market_id": "played", "game_start": (NOW - timedelta(hours=3)).isoformat()})
    st.insert_position({**base, "market_id": "running", "game_start": (NOW - timedelta(minutes=30)).isoformat()})
    st.insert_position({**base, "market_id": "later", "game_start": (NOW + timedelta(hours=5)).isoformat()})
    eng.settle()
    assert asked == ["played"]     # Tischtennis endet offiziell 7 Tage nach Beginn – abgerechnet wird schon nach dem Spiel


def test_soonest_games_first_and_capped_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALL_TIPS", True)
    monkeypatch.setattr(config, "ALL_MAX_NEW", 1)
    games = [_game("late", ["sports", "tennis"], ["A", "B"], 0.25, 0.27, start_h=20),
             _game("soon", ["sports", "tennis"], ["C", "D"], 0.25, 0.27, start_h=2)]
    st, eng, _ = _eng(tmp_path, {"sports": games})
    assert eng.run_cycle() and set(_bets(st)) == {"soon"}
    assert eng.run_cycle() and set(_bets(st)) == {"soon", "late"}


def test_misfiled_first_bets_move_to_the_right_account_once(tmp_path):
    st, eng, _ = _eng(tmp_path, {})
    eng.cash("alles_sonstiges"), eng.cash("alles_sport")
    st.insert_position({"account": "alles_sonstiges", "market_id": "n1", "category": "other", "question": "Ole Miss vs Clemson",
                        "side": 0, "side_name": "Ole Miss Rebels", "price": 0.68, "shares": 7.35, "cost": 5, "fee": 0, "status": "open",
                        "opened_at": NOW.isoformat(), "lane": "rule"})
    st.add_cash(-5, config.START_BALANCE, "alles_sonstiges")
    m = {"id": "n1", "lane": "alles", "category": "sports", "sub": "Basketball", "detail": "NCAA"}
    eng.refile_all([m])
    p = st.open_positions("alles_sport")[0]
    assert (p["category"], p["sub"]) == ("sports", "Basketball") and not st.open_positions("alles_sonstiges")
    assert eng.cash("alles_sonstiges") == config.START_BALANCE and eng.cash("alles_sport") == config.START_BALANCE - 5
    eng.refile_all([m])                                   # nur einmal
    assert eng.cash("alles_sport") == config.START_BALANCE - 5


def test_one_sport_cannot_fill_the_account(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "ALL_TIPS", True)
    monkeypatch.setattr(config, "ALL_MAX_PER_SUB", 2)
    tt = [_game(f"tt{i}", ["sports", "table-tennis"], [f"A{i}", f"B{i}"], 0.60, 0.62, start_h=1 + i * 0.1, end_days=7) for i in range(5)]
    soccer = [_game("fb", ["sports", "soccer"], ["Yes", "No"], 0.61, 0.62, start_h=6, q="Will Bayern win?")]
    st, eng, _ = _eng(tmp_path, {"sports": tt + soccer})
    assert eng.run_cycle()
    subs = sorted(p["sub"] for p in _bets(st).values())
    assert subs == ["Fußball", "Tischtennis", "Tischtennis"], subs
