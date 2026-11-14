"""Echte, gekürzte API-Antworten (aufgenommen am 14.11.2026) durch die echten Auswerter schicken.

Fällt hier etwas um, hat sich wahrscheinlich ein Format bei Polymarket oder aviationweather.gov geändert –
oder unser Code liest es anders als gedacht. Die Dateien liegen in tests/fixtures/ und sind reine Testdaten."""
import json
import os
from datetime import datetime, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import crypto_model, metar, polymarket, taxonomy  # noqa: E402
from polybot.agents.strategy import horizon_prices  # noqa: E402
from polybot.engine import Engine  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

HERE = os.path.join(os.path.dirname(__file__), "fixtures")


def _load(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return json.load(f)


PM = _load("polymarket_samples.json")


def _norm(raw):
    ev = raw["events"][0] if raw.get("events") else None
    m = polymarket.normalize_market(raw, ev, "other")
    m.update(taxonomy.classify(m))
    m["fee_rate"] = polymarket.fee_rate_for(raw, m["category"])
    return m


def test_open_markets_are_read_and_classified():
    ms = {m["id"]: m for m in map(_norm, PM["open"])}
    paths = {i: taxonomy.path(m) for i, m in ms.items()}
    assert paths["6889024"] == "E-Sport › League of Legends › LoL Worlds 2026"
    assert paths["7027599"] == "Sport › Tennis › WTA"                                        # Liga aus dem Kürzel des Ereignisses
    assert paths["6446616"] == "Sport › Basketball › NBA"
    assert paths["6496387"] == "Sport › Kampfsport › UFC"
    assert paths["6891215"] == "Krypto › Bitcoin › Fällt auf einen Kurs"
    assert paths["6282802"].startswith("Welt › Nahost")
    assert paths["6771160"] == "Kultur › Tweets"
    nba = ms["6446616"]
    assert nba["outcomes"] == ["Mavericks", "Cavaliers"] and (nba["best_bid"], nba["best_ask"]) == (0.65, 0.66)
    assert nba["fee_rate"] == 0.05 and ms["6282802"]["fee_rate"] == 0.0                       # feesEnabled: false
    assert nba["end_date"] == datetime(2026, 11, 15, 0, 30, tzinfo=timezone.utc)
    assert nba["game_start"] == datetime(2026, 11, 15, 0, 30, tzinfo=timezone.utc)            # Format „… 00:30:00+00“
    assert nba["tag_labels"]["nba"] == "NBA" and nba["event_title"] == "Mavericks vs. Cavaliers"


def test_closed_markets_resolve_and_open_proposed_does_not():
    for raw in PM["closed"]:
        assert polymarket.resolution(raw) == 1, raw["question"]
        assert polymarket.parse_dt(raw["closedTime"]).tzinfo is not None                    # „2026-11-14 19:48:18.481845+00“
    wta = next(r for r in PM["open"] if r["id"] == "7027599")
    assert polymarket.resolution(wta) is None                                                 # noch nicht geschlossen


def test_crypto_event_ladder_parses_for_the_model():
    ev = PM["event"]
    ms = [polymarket.normalize_market(r, ev, "crypto") for r in ev["markets"]]
    parsed = [crypto_model.parse_question(m["question"]) for m in ms]
    assert [p["kind"] for p in parsed] == ["above"] * 3 and [p["k1"] for p in parsed] == [66000, 68000, 70000]
    assert all(taxonomy.classify(m, "crypto")["detail"] == "Kurs am Stichtag" for m in ms)
    assert [round(polymarket.mid_yes(m), 4) for m in ms] == [0.1525, 0.0295, 0.0175]


def test_other_markets_from_recorded_page(tmp_path):
    st = Store(str(tmp_path / "f.sqlite"))
    eng = Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: ([1.0] * 721, "Test"),
                 odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None, book_fn=None,
                 fetch_markets=lambda p: PM["open"] if p["offset"] == 0 else [])
    out = eng.collect_others(datetime(2026, 11, 14, 22, 4, tzinfo=timezone.utc), set())
    assert len(out) == len(PM["open"]) and {m["lane"] for m in out} == {"base"}
    assert {m["category"] for m in out} == {"esports", "sports", "crypto", "world", "culture"}


def test_price_history_gives_horizon_prices_without_looking_ahead():
    h = sorted((x["t"], x["p"]) for x in _load("clob_prices_history.json")["history"])
    end = h[-1][0] + 3600
    p = horizon_prices(h, end)
    # je Stufe der letzte Kurs VOR dem Zeitpunkt (Punkte liegen hier 3 h auseinander)
    assert (p["p_1h"], p["p_6h"], p["p_24h"]) == (0.184, 0.0455, 0.0095)
    assert p["p_72h"] is None and p["p_7d"] is None                                           # keine Kurse so weit vorne
    # Markt schon vor dem Ende geschlossen: spätere Stufen bleiben leer
    assert horizon_prices(h, end, closed_ts=end - 5 * 3600)["p_1h"] is None


def test_recorded_metar_parses_and_gives_local_day_extremes():
    raw = _load("metar_klga_eglc.json")
    res = metar._fetch(["KLGA", "EGLC"], lambda url, params=None, **kw: raw)
    assert len(res["KLGA"]) == 4 and len(res["EGLC"]) == 8
    e = metar.day_extremes(res["KLGA"], "America/New_York", "2026-11-14", "F")
    assert e["n"] == 4 and abs(e["max"] - 46.04) < 1e-9                                       # 7,8 °C
    e = metar.day_extremes(res["EGLC"], "Europe/London", "2026-11-14", "C")
    assert (e["max"], e["min"]) == (7.0, 4.0)
