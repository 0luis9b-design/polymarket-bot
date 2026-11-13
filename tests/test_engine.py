import json
import math
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, polymarket  # noqa: E402
from polybot.engine import Engine, confidence, evaluate, fee_per_share, kelly_fraction  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

CLOSES = [100000.0 * (1 + 0.004 * ((i % 2) * 2 - 1)) for i in range(720)] + [100000.0]


def test_kelly_and_fee():
    assert kelly_fraction(0.6, 0.5) == (0.6 - 0.5) / 0.5
    assert kelly_fraction(0.4, 0.5) == 0
    assert abs(fee_per_share(0.07, 0.5) - 0.0175) < 1e-12


def _end(now, days=2):
    day = (now + timedelta(days=days)).astimezone(ZoneInfo("America/New_York")).replace(hour=12, minute=0, second=0)
    return day.astimezone(timezone.utc)


def _market(mid, strike, end, bid, ask, q="above"):
    question = (f"Will the price of Bitcoin be above ${strike:,} on {end:%B} {end.day}?" if q == "above" else q)
    return {"id": mid, "question": question,
            "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps([str(bid), str(1 - bid)]),
            "bestBid": bid, "bestAsk": ask, "liquidityNum": 10000, "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active": True, "closed": False, "acceptingOrders": True,
            "clobTokenIds": json.dumps([f"{mid}-y", f"{mid}-n"])}


def _engine(tmp_path, events, resolved=None, books=None):
    resolved = resolved if resolved is not None else {}
    store = Store(str(tmp_path / "t.sqlite"))
    eng = Engine(store, fetch_events=lambda tag, **kw: events if tag == "crypto" else [],
                 fetch_by_ids=lambda ids: {i: resolved[i] for i in ids if i in resolved},
                 closes_fn=lambda a: (CLOSES, "Test"), odds_client=OddsClient(store, api_key=""),
                 chain_fn=lambda a, n: None,
                 book_fn=(lambda t: books[t]) if books is not None else None)
    if books is None:
        eng.book_fn = None
    return store, eng


def test_full_cycle_all_accounts(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    markets = [
        _market("m1", 100000, end, 0.20, 0.22),   # Bot ~50 % -> Ja ist billig -> kaufen
        _market("m2", 120000, end, 0.01, 0.02),   # extrem -> nicht handeln
    ]
    events = [{"id": "e1", "title": "Bitcoin above ___?", "slug": "bitcoin-above", "markets": markets}]
    resolved = {}
    store, eng = _engine(tmp_path, events, resolved)
    assert eng.run_cycle()
    assert eng.state["last_error"] is None, eng.state["last_error"]
    pos = store.open_positions("haupt_krypto")
    assert any(p["market_id"] == "m1" and p["side"] == 0 for p in pos), eng.state["opportunities"]
    assert not any(p["market_id"] == "m2" for p in pos)
    assert all(19.9 <= p["cost"] <= 60.1 for p in pos), [p["cost"] for p in pos]  # 2–6 % von 1.000 $
    assert abs(store.cash(config.START_BALANCE, "haupt_krypto") - (config.START_BALANCE - sum(p["cost"] for p in pos))) < 1e-6
    # Testkonten: jedes Modell-Konto kauft für sich, das Arbitrage-Konto nicht
    accs = {p["account"] for p in store.open_positions()}
    assert {"haupt_krypto", "schwelle4", "schwelle8", "fest10", "halten", "kelly"} <= accs
    assert "arbitrage" not in accs
    fest = store.open_positions("fest10")[0]
    assert abs(fest["cost"] - 10.0) < 1e-6

    # Zweiter Lauf eröffnet nichts doppelt
    n = len(store.open_positions())
    eng.run_cycle()
    assert len(store.open_positions()) == n

    # Abrechnung: m1 Ja gewinnt
    p1 = next(p for p in pos if p["market_id"] == "m1")
    for p in store.open_positions():
        store.update_position(p["id"], end_date=(now - timedelta(hours=2)).isoformat())
    resolved["m1"] = {**markets[0], "closed": True, "outcomePrices": '["1", "0"]', "umaResolutionStatus": "resolved"}
    eng.settle()
    closed = store.closed_positions(account="haupt_krypto")
    assert closed[0]["status"] == "won"
    assert abs(closed[0]["pnl"] - (p1["shares"] - p1["cost"])) < 1e-3
    st = eng.stats()
    assert st["wins"] == 1 and st["closed_count"] == 1
    assert len(eng.accounts_overview()) == len(config.ACCOUNTS)


def test_group_limit_coin_and_day(tmp_path):
    """Zwei Märkte am selben Coin+Stichtag zählen zusammen – max. 10 % des Kontos."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    q2 = f"Will the price of Bitcoin be between $99,000 and $101,000 on {end:%B} {end.day}?"
    ms = [_market("a", 100000, end, 0.20, 0.22), _market("b", 0, end, 0.05, 0.06, q=q2),
          _market("c", 99000, end, 0.30, 0.31)]
    events = [{"id": "e1", "markets": ms[:1] + ms[2:]}, {"id": "e2", "markets": ms[1:2]}]
    store, eng = _engine(tmp_path, events)
    eng.run_cycle()
    pos = store.open_positions("haupt_krypto")
    assert len({p["grp"] for p in pos}) == 1
    assert sum(p["cost"] for p in pos) <= config.START_BALANCE * config.MAX_GROUP_PCT + 1e-6


def test_orderbook_fill_and_thin_book(tmp_path):
    asks = [(0.22, 50.0), (0.25, 50.0), (0.40, 1000.0)]
    shares, cost, fee, avg = polymarket.fill_buy(asks, 1000, 0.07, q=0.45, min_edge=0.06)
    assert shares == 100.0  # stoppt vor 40 ¢, weil dort kein Vorsprung mehr
    assert 0.22 < avg < 0.25
    sold, proceeds, fee2, avg2 = polymarket.fill_sell([(0.5, 30.0), (0.45, 100.0)], 50, 0.0, min_price=0.4)
    assert sold == 50 and abs(avg2 - (30 * 0.5 + 20 * 0.45) / 50) < 1e-12

    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    m = _market("m1", 100000, end, 0.20, 0.22)
    books = {"m1-y": ([(0.22, 10.0)], [(0.20, 100.0)]), "m1-n": ([(0.80, 100.0)], [(0.78, 100.0)])}
    store, eng = _engine(tmp_path, [{"id": "e1", "markets": [m]}], books=books)
    eng.run_cycle()
    assert store.open_positions("haupt_krypto") == []  # 10 Anteile × 22 ¢ = 2,20 $ < 2 % des Kontos
    assert eng.state["opportunities"][0]["status"] == "Orderbuch zu dünn"


def test_early_exit_takes_profit(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    m = _market("m1", 100000, end, 0.20, 0.22)
    events = [{"id": "e1", "markets": [m]}]
    store, eng = _engine(tmp_path, events)
    eng.run_cycle()
    p = next(p for p in store.open_positions("haupt_krypto"))
    store.update_position(p["id"], opened_at=(now - timedelta(hours=2)).isoformat())
    # Markt zieht auf 49/50 Cent – fair laut Bot ~49 % -> Gewinn mitnehmen
    events[0]["markets"] = [_market("m1", 100000, end, 0.50, 0.51)]
    eng.run_cycle()
    sold = [c for c in store.closed_positions(account="haupt_krypto") if c["status"] == "sold"]
    assert sold and sold[0]["pnl"] > 0
    # Konto "Ohne Ausstieg" hält weiter
    assert store.open_positions("halten")


def test_arbitrage_bucket_sum(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    qs = [f"Will the price of Bitcoin be less than $98,000 on {end:%B} {end.day}?",
          f"Will the price of Bitcoin be between $98,000 and $102,000 on {end:%B} {end.day}?",
          f"Will the price of Bitcoin be greater than $102,000 on {end:%B} {end.day}?"]
    asks = [0.20, 0.45, 0.25]  # Summe 0,90 -> 10 Cent sicher (abzüglich Gebühr)
    ms = [_market(f"b{i}", 0, end, a - 0.01, a, q=q) for i, (a, q) in enumerate(zip(asks, qs))]
    books = {}
    for i, a in enumerate(asks):
        books[f"b{i}-y"] = ([(a, 500.0)], [(a - 0.01, 500.0)])
        books[f"b{i}-n"] = ([(1 - a + 0.01, 500.0)], [(1 - a, 500.0)])
    events = [{"id": "ev", "title": "Bitcoin price?", "negRisk": True, "markets": ms}]
    store, eng = _engine(tmp_path, events, books=books)
    eng.run_cycle()
    arb = [p for p in store.open_positions("arbitrage") if p["kind"] == "arb"]
    assert len(arb) == 3 and len({p["shares"] for p in arb}) == 1
    cost = sum(p["cost"] for p in arb)
    assert arb[0]["shares"] > cost  # sichere Auszahlung (1 $ pro Paket) > Einsatz
    assert eng.state["arbs"][0]["status"]["arbitrage"] == "eröffnet"


def test_arbitrage_ladder():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    store = Store(":memory:")
    eng = Engine(store, odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None)
    lo = polymarket.normalize_market(_market("l", 100000, end, 0.40, 0.41), {"id": "e"}, "crypto")
    hi = polymarket.normalize_market(_market("h", 102000, end, 0.50, 0.51), {"id": "e"}, "crypto")
    arbs = eng.find_arbs([lo, hi], now)
    assert arbs and arbs[0]["type"] == "Stufen-Widerspruch" and arbs[0]["cost"] < 1


def test_confidence_stake_range():
    assert confidence(0.30, 0.06, 0.06) == 0
    assert abs(confidence(0.90, 0.20, 0.06) - 1) < 1e-12
    assert 0 < confidence(0.60, 0.11, 0.06) < 1


def test_evaluate_picks_better_side():
    m = {"best_bid": 0.70, "best_ask": 0.72, "prices": [0.71, 0.29], "fee_rate": 0.0}
    ev = evaluate(m, 0.5)  # Bot: 50 %, Markt 71 % -> "Nein" für 0.30 kaufen
    assert ev["side"] == 1 and abs(ev["price"] - 0.30) < 1e-9 and ev["edge"] > 0.19


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.sqlite")
    db = sqlite3.connect(path)
    db.executescript("""CREATE TABLE positions (id INTEGER PRIMARY KEY AUTOINCREMENT, market_id TEXT, event_id TEXT,
        category TEXT, question TEXT, slug TEXT, event_slug TEXT, side INTEGER, side_name TEXT, shares REAL, price REAL,
        fee REAL, cost REAL, own_p REAL, market_p REAL, edge REAL, info TEXT, opened_at TEXT, end_date TEXT,
        status TEXT DEFAULT 'open', mark REAL, payout REAL, pnl REAL, closed_at TEXT);
        INSERT INTO positions(market_id, side, shares, price, cost, status) VALUES ('x', 0, 10, 0.4, 4, 'open');
        CREATE TABLE equity (ts TEXT PRIMARY KEY, equity REAL, cash REAL);
        INSERT INTO equity VALUES ('2026-11-12T10:00', 999, 990);""")
    db.commit()
    db.close()
    st = Store(path)
    assert st.open_positions("haupt")[0]["kind"] == "model"
    assert st.equity_history("haupt")[0]["equity"] == 999


def test_options_chain_flat_vol_matches_lognormal():
    from polybot import options
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    rows = []
    for exp in ("13NOV26", "14NOV26", "16NOV26"):
        for k in range(90000, 110001, 2000):
            for kind in ("C", "P"):
                rows.append({"instrument_name": f"BTC-{exp}-{k}-{kind}", "mark_iv": 50.0, "underlying_price": 100000.0})
    ch = options.Chain(rows, "BTC-", now)
    assert ch.ok
    hours = 48
    sd = 0.5 * math.sqrt(hours / (365 * 24))
    exp = 0.5 * (1 + math.erf((math.log(100000 / 102000) - sd * sd / 2) / sd / math.sqrt(2)))
    assert abs(ch.prob_above(100000, 102000, hours) - exp) < 0.003
    assert options.parse_instrument("XRP_USDC-27NOV26-1d08-P")[1] == 1.08



def test_split_moves_old_main_account(tmp_path):
    store = Store(str(tmp_path / "s.sqlite"))
    base = {"event_id": "e", "question": "q", "slug": "", "event_slug": "", "side": 0, "side_name": "Yes",
            "shares": 10, "price": 0.5, "fee": 0, "own_p": 0.6, "market_p": 0.5, "edge": 0.1, "info": "",
            "opened_at": "2026-11-12T10:00:00+00:00", "end_date": "2026-11-14T10:00:00+00:00", "account": "haupt"}
    store.insert_position({**base, "market_id": "c1", "category": "crypto", "cost": 30.0, "status": "open"})
    store.insert_position({**base, "market_id": "w1", "category": "weather", "cost": 40.0, "status": "lost",
                           "pnl": -40.0, "payout": 0.0, "closed_at": "2026-11-13T06:00:00+00:00"})
    store.insert_position({**base, "market_id": "w2", "category": "weather", "cost": 20.0, "status": "open"})
    eng = Engine(store, odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None)
    assert [p["market_id"] for p in store.open_positions("haupt_krypto")] == ["c1"]
    assert {p["market_id"] for p in store.open_positions("haupt_wetter")} == {"w2"}
    assert store.closed_positions(account="haupt_wetter")[0]["market_id"] == "w1"
    assert abs(eng.cash("haupt_krypto") - 970) < 1e-9
    assert abs(eng.cash("haupt_wetter") - (1000 - 40 - 20)) < 1e-9
    assert abs(eng.cash("haupt_sport") - 1000) < 1e-9
    assert store.open_positions("haupt") == []
    Engine(store, odds_client=OddsClient(store, api_key=""), chain_fn=lambda a, n: None)  # zweiter Start: nichts doppelt
    assert abs(eng.cash("haupt_wetter") - 940) < 1e-9


def test_category_accounts_only_trade_their_area(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = _end(now)
    events = [{"id": "e1", "markets": [_market("m1", 100000, end, 0.20, 0.22)]}]
    store, eng = _engine(tmp_path, events)
    eng.run_cycle()
    assert store.open_positions("haupt_krypto")
    assert store.open_positions("haupt_sport") == [] and store.open_positions("haupt_wetter") == []
