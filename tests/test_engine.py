import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config  # noqa: E402
from polybot.engine import Engine, evaluate, fee_per_share, kelly_fraction  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402


def test_kelly_and_fee():
    assert kelly_fraction(0.6, 0.5) == (0.6 - 0.5) / 0.5
    assert kelly_fraction(0.4, 0.5) == 0
    assert abs(fee_per_share(0.07, 0.5) - 0.0175) < 1e-12


def _market(mid, strike, end, bid, ask):
    return {"id": mid, "question": f"Will the price of Bitcoin be above ${strike:,} on {end:%B} {end.day}?",
            "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps([str(bid), str(1 - bid)]),
            "bestBid": bid, "bestAsk": ask, "liquidityNum": 10000, "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active": True, "closed": False, "acceptingOrders": True}


def test_full_cycle(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    # Stichtag ~2 Tage entfernt, 12:00 New York
    from zoneinfo import ZoneInfo
    day = (now + timedelta(days=2)).astimezone(ZoneInfo("America/New_York")).replace(hour=12, minute=0, second=0)
    end = day.astimezone(timezone.utc)
    closes = [100000.0 * (1 + 0.004 * ((i % 2) * 2 - 1)) for i in range(720)] + [100000.0]
    markets = [
        _market("m1", 100000, end, 0.20, 0.22),   # Bot ~50 % -> Ja ist billig -> kaufen
        _market("m2", 120000, end, 0.01, 0.02),   # extrem -> nicht handeln
        _market("m3", 98000, end, 0.95, 0.97),    # Bot ~85 % -> Nein billig? prüfen wir nur Ablauf
    ]
    events = [{"id": "e1", "title": "Bitcoin above ___?", "slug": "bitcoin-above", "markets": markets}]
    resolved = {}

    store = Store(str(tmp_path / "t.sqlite"))
    eng = Engine(store, fetch_events=lambda tag, **kw: events if tag == "crypto" else [],
                 fetch_by_ids=lambda ids: {i: resolved[i] for i in ids if i in resolved},
                 closes_fn=lambda a: (closes, "Test"), odds_client=OddsClient(store, api_key=""))
    assert eng.run_cycle()
    assert eng.state["last_error"] is None, eng.state["last_error"]
    pos = store.open_positions()
    assert any(p["market_id"] == "m1" and p["side"] == 0 for p in pos), eng.state["opportunities"]
    assert not any(p["market_id"] == "m2" for p in pos)
    total_cost = sum(p["cost"] for p in pos)
    assert total_cost <= config.START_BALANCE * config.MAX_EVENT_PCT + 1e-6
    assert abs(store.cash(config.START_BALANCE) - (config.START_BALANCE - total_cost)) < 1e-6

    # Zweiter Lauf eröffnet nichts doppelt
    eng.run_cycle()
    assert len(store.open_positions()) == len(pos)

    # Abrechnung: m1 Ja gewinnt
    p1 = next(p for p in pos if p["market_id"] == "m1")
    store.update_position(p1["id"], end_date=(now - timedelta(hours=2)).isoformat())
    resolved["m1"] = {**markets[0], "closed": True, "outcomePrices": '["1", "0"]', "umaResolutionStatus": "resolved"}
    eng.settle()
    closed = store.closed_positions()
    assert closed[0]["status"] == "won"
    assert abs(closed[0]["pnl"] - (p1["shares"] - p1["cost"])) < 1e-3
    st = eng.stats()
    assert st["wins"] == 1 and st["closed_count"] == 1


def test_evaluate_picks_better_side():
    m = {"best_bid": 0.70, "best_ask": 0.72, "prices": [0.71, 0.29], "fee_rate": 0.0}
    ev = evaluate(m, 0.5)  # Bot: 50 %, Markt 71 % -> "Nein" für 0.30 kaufen
    assert ev["side"] == 1 and abs(ev["price"] - 0.30) < 1e-9 and ev["edge"] > 0.19
