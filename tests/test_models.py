import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from polybot import crypto_model as cm
from polybot import sports_model as sm
from polybot.polymarket import normalize_market, quote, resolution


@pytest.mark.parametrize("q,exp", [
    ("Will the price of Bitcoin be above $104,000 on November 14?", ("BTC", "above", 104000)),
    ("Will the price of Ethereum be less than $3,000 on November 14?", ("ETH", "below", 3000)),
    ("Will the price of Solana be between $150 and $160 on November 14?", ("SOL", "between", 150)),
    ("Will Bitcoin reach $120,000 in November?", ("BTC", "touch", 120000)),
    ("Will Bitcoin dip to $90,000 in November?", ("BTC", "touch_down", 90000)),
    ("Will Bitcoin hit $150k by June 30, 2027?", ("BTC", "touch", 150000)),
    ("Will XRP be greater than $3.50 on November 20?", ("XRP", "above", 3.5)),
])
def test_parse(q, exp):
    p = cm.parse_question(q)
    assert (p["asset"], p["kind"], p["k1"]) == exp


@pytest.mark.parametrize("q", [
    "Bitcoin Up or Down - November 11, 3PM ET",
    "Will MicroStrategy buy more Bitcoin?",
    "Will Trump win?",
    "Will Bitcoin hashprice be greater than $40 for the week of November 9?",
])
def test_parse_unsupported(q):
    assert cm.parse_question(q) is None


def test_target_time_noon_et():
    end = datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)
    t = cm.target_time("Will the price of Bitcoin be above $104,000 on November 14?", end)
    assert t == datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)  # 12:00 EST = 17:00 UTC
    end2 = datetime(2026, 7, 14, 23, 59, tzinfo=timezone.utc)
    t2 = cm.target_time("Will the price of Bitcoin be above $104,000 on July 14?", end2)
    assert t2 == datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)  # 12:00 EDT = 16:00 UTC


def test_probabilities_sane():
    sd = 0.03
    assert 0.48 < cm.prob_above(100, 100, sd) < 0.5
    assert cm.prob_above(100, 90, sd) > 0.99
    assert cm.prob_above(100, 110, sd) < 0.01
    # monoton fallend im Strike
    vals = [cm.prob_above(100, k, sd) for k in range(90, 111)]
    assert all(a >= b for a, b in zip(vals, vals[1:]))
    # Bereich = Differenz
    p = cm.probability({"kind": "between", "k1": 98, "k2": 102}, 100, 0.01, 9)
    assert abs(p - (cm.prob_above(100, 98, 0.03) - cm.prob_above(100, 102, 0.03))) < 1e-12
    # Berühren ist wahrscheinlicher als am Ende darüber
    assert cm.prob_touch_up(100, 105, sd) > cm.prob_above(100, 105, sd)
    assert cm.probability({"kind": "touch", "k1": 95}, 100, 0.01, 9) == cm.prob_touch_down(100, 95, 0.03)


def test_mixture_variance_is_one():
    assert abs(sum(w * f * f for w, f in cm.MIXTURE) - 1) < 1e-12


def test_hourly_vol():
    import random
    random.seed(1)
    closes = [100.0]
    for _ in range(720):
        closes.append(closes[-1] * math.exp(random.gauss(0, 0.005)))
    v = cm.hourly_vol(closes)
    assert 0.0042 < v < 0.0058


def _raw_market(**kw):
    base = {"id": "1", "question": "Will the price of Bitcoin be above $100,000 on November 14?",
            "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps(["0.40", "0.60"]),
            "bestBid": 0.39, "bestAsk": 0.41, "liquidityNum": 5000, "endDate": "2026-11-14T17:00:00Z",
            "active": True, "closed": False, "acceptingOrders": True}
    base.update(kw)
    return base


def test_quote_both_sides():
    m = normalize_market(_raw_market(), {"id": "e1", "title": "Bitcoin above ___ on November 14?"}, "crypto")
    assert quote(m, 0) == (0.41, 0.39)
    ask1, bid1 = quote(m, 1)
    assert abs(ask1 - 0.61) < 1e-9 and abs(bid1 - 0.59) < 1e-9
    assert m["fee_rate"] == 0.07


def test_resolution():
    assert resolution(_raw_market(closed=True, outcomePrices='["1","0"]', umaResolutionStatus="resolved")) == 0
    assert resolution(_raw_market(closed=True, outcomePrices='["0","1"]')) == 1
    assert resolution(_raw_market(closed=False, outcomePrices='["0","1"]')) is None
    assert resolution(_raw_market(closed=True, outcomePrices='["0.5","0.5"]')) == "void"


def test_crypto_analyze():
    now = datetime(2026, 11, 13, 17, 0, tzinfo=timezone.utc)
    m = normalize_market(_raw_market(), {"id": "e1"}, "crypto")
    closes = [100000.0 * (1 + 0.004 * ((i % 2) * 2 - 1)) for i in range(720)] + [100000.0]
    a = cm.analyze(m, now=now, closes_fn=lambda asset: (closes, "Test"), chain_fn=lambda a, n: None)
    assert 0.4 < a["p_yes"] < 0.5
    assert abs(a["hours"] - 24) < 1e-6


# ---------------------------------------------------------------- Sport

def test_devig_and_names():
    p = sm.devig([1.9, 1.9])
    assert p == [0.5, 0.5]
    assert sm.name_score("Lakers", "Los Angeles Lakers") >= 0.9
    assert sm.name_score("Man City", "Manchester City") >= 0.75
    assert sm.name_score("Bayern München", "Bayern Munich") >= 0.75
    assert sm.name_score("Lakers", "Boston Celtics") < 0.5


class FakeStore:
    def __init__(self):
        self.kv = {}

    def kv_get(self, k, d=None):
        return self.kv.get(k, d)

    def kv_set(self, k, v):
        self.kv[k] = v


def _odds_event(start):
    return {"home_team": "Los Angeles Lakers", "away_team": "Boston Celtics", "commence_time": start,
            "bookmakers": [
                {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Los Angeles Lakers", "price": 2.5}, {"name": "Boston Celtics", "price": 1.6}]}]},
                {"key": "bet365", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Los Angeles Lakers", "price": 2.45}, {"name": "Boston Celtics", "price": 1.57}]}]},
                {"key": "unibet", "markets": [{"key": "h2h", "outcomes": [
                    {"name": "Los Angeles Lakers", "price": 2.4}, {"name": "Boston Celtics", "price": 1.58}]}]},
            ]}


def test_sports_analyze_moneyline():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    start = "2026-11-13T00:30:00Z"
    calls = []

    def fetch(url, params=None, want_headers=False):
        calls.append(url)
        return [_odds_event(start)], {"x-requests-remaining": "480"}

    client = sm.OddsClient(FakeStore(), api_key="x", fetch=fetch)
    raw = {"id": "9", "question": "Lakers vs. Celtics", "outcomes": '["Lakers", "Celtics"]',
           "outcomePrices": '["0.33", "0.67"]', "bestBid": 0.32, "bestAsk": 0.34, "liquidityNum": 20000,
           "endDate": "2026-11-13T04:00:00Z", "gameStartTime": "2026-11-13 00:30:00+00", "sportsMarketType": "moneyline"}
    ev = {"id": "e9", "title": "Lakers vs. Celtics", "slug": "nba-lal-bos-2026-11-12", "tags": [{"slug": "nba"}]}
    m = normalize_market(raw, ev, "sports")
    a = sm.analyze(m, client, now=now)
    assert "p_yes" in a, a
    assert 0.37 < a["p_yes"] < 0.40  # Pinnacle fair ~0.39
    sm.analyze(m, client, now=now)
    assert len(calls) == 1  # Cache greift


def test_sports_soccer_yes_no():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    ev_odds = {"home_team": "Arsenal", "away_team": "Chelsea", "commence_time": "2026-11-12T20:00:00Z",
               "bookmakers": [{"key": k, "markets": [{"key": "h2h", "outcomes": [
                   {"name": "Arsenal", "price": 2.0}, {"name": "Chelsea", "price": 4.0}, {"name": "Draw", "price": 3.6}]}]}
                   for k in ("pinnacle", "a", "b")]}
    client = sm.OddsClient(FakeStore(), api_key="x", fetch=lambda *a, **k: ([ev_odds], {}))
    ev = {"id": "e", "title": "Arsenal FC vs. Chelsea FC", "slug": "epl-ars-che-2026-11-12", "tags": [{"slug": "epl"}]}
    base = {"outcomes": '["Yes", "No"]', "outcomePrices": '["0.5", "0.5"]', "endDate": "2026-11-12T22:00:00Z",
            "gameStartTime": "2026-11-12 20:00:00+00"}
    win = normalize_market({**base, "id": "1", "question": "Will Arsenal FC win on 2026-11-12?"}, ev, "sports")
    draw = normalize_market({**base, "id": "2", "question": "Will Arsenal FC vs. Chelsea FC end in a draw?"}, ev, "sports")
    pw = sm.analyze(win, client, now=now)["p_yes"]
    pd = sm.analyze(draw, client, now=now)["p_yes"]
    tot = 1 / 2 + 1 / 4 + 1 / 3.6
    assert abs(pw - 0.5 / tot) < 1e-6 and abs(pd - (1 / 3.6) / tot) < 1e-6


def test_sports_without_key():
    client = sm.OddsClient(FakeStore(), api_key="")
    assert not client.enabled


def test_odds_daily_budget():
    st = FakeStore()
    n = {"c": 0}

    def fetch(url, params=None, want_headers=False):
        n["c"] += 1
        return [], {"x-requests-remaining": "45"}

    client = sm.OddsClient(st, api_key="x", fetch=fetch)
    st.kv["odds_remaining"] = "45"
    budget = client.daily_budget()
    assert budget >= 1
    for i in range(budget + 5):
        client.events(f"sport_{i}")
    assert n["c"] == budget
    assert "Tagesbudget" in client.status


def test_nhl_two_way_ignores_three_way_books():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    ev_odds = {"home_team": "Carolina Hurricanes", "away_team": "Seattle Kraken", "commence_time": "2026-11-12T17:00:00Z",
               "bookmakers": [
                   {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "Carolina Hurricanes", "price": 1.4}, {"name": "Seattle Kraken", "price": 3.0}]}]},
                   {"key": "x", "markets": [{"key": "h2h", "outcomes": [
                       {"name": "Carolina Hurricanes", "price": 1.9}, {"name": "Seattle Kraken", "price": 4.5},
                       {"name": "Draw", "price": 4.2}]}]}]}
    client = sm.OddsClient(FakeStore(), api_key="x", fetch=lambda *a, **k: ([ev_odds], {}))
    raw = {"id": "1", "question": "Hurricanes vs. Kraken", "outcomes": '["Hurricanes", "Kraken"]',
           "outcomePrices": '["0.72", "0.28"]', "endDate": "2026-11-12T20:00:00Z",
           "gameStartTime": "2026-11-12 17:00:00+00", "sportsMarketType": "moneyline"}
    m = normalize_market(raw, {"id": "e", "title": "Hurricanes vs. Kraken", "tags": [{"slug": "nhl"}]}, "sports")
    a = sm.analyze(m, client, now=now)
    assert abs(a["p_yes"] - (1 / 1.4) / (1 / 1.4 + 1 / 3.0)) < 1e-9
