"""Alle Märkte im Paper-Trading: Einordnung in Stufen, Basisraten-Modell, Konten je Thema,
Einstiegs-Daten je Wette, Historie, Detail und Kalender."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import base_model, config, paperview, taxonomy  # noqa: E402
from polybot.engine import Engine  # noqa: E402
from polybot.sports_model import OddsClient  # noqa: E402
from polybot.store import Store  # noqa: E402

from tests.test_engine import CLOSES  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------- Einordnung
def _c(tags, q="x", cat=None, **kw):
    return taxonomy.classify({"tags": tags, "question": q, **kw}, cat)


def test_taxonomy_three_levels_from_real_tag_combinations():
    # Schlagwort-Kombinationen wie am 14.11.2026 live bei Polymarket gesehen (Reihenfolge wechselt)
    assert _c(["ucl-matchday", "ucl", "soccer", "sports", "champions-league"]) == \
        {"category": "sports", "sub": "Fußball", "detail": "Champions League"}
    assert _c(["sports", "games", "soccer", "uefa-nations-league"])["detail"] == "Nations League"
    assert _c(["sports", "nba", "games", "basketball"]) == {"category": "sports", "sub": "Basketball", "detail": "NBA"}
    assert _c(["sports", "cfb", "games"]) == {"category": "sports", "sub": "American Football", "detail": "College Football"}
    assert _c(["tennis", "sports", "games"])["sub"] == "Tennis"
    # E-Sport-Märkte tragen auch „sports“ – sie gehören trotzdem zu E-Sport
    c = _c(["esports", "league-of-legends", "games", "sports", "lol-worlds-2026", "knockout-stage", "final"], tag_labels={"lol-worlds-2026": "LoL Worlds 2026"})
    assert c == {"category": "esports", "sub": "League of Legends", "detail": "LoL Worlds 2026"}
    assert _c(["pop-culture", "politics", "tweets-markets", "recurring"])["sub"] == "Tweets"
    assert _c(["primaries", "2028-election", "united-states", "politics", "elections"])[("sub")] == "USA"
    assert _c(["fed", "fomc", "trump", "economy", "fed-rates"]) == {"category": "economy", "sub": "Notenbank", "detail": "Allgemein"}
    assert _c(["iran", "israel", "geopolitics", "diplomacy-ceasefire"], tag_labels={"diplomacy-ceasefire": "Iran Ceasefire"}) == \
        {"category": "world", "sub": "Nahost", "detail": "Iran Ceasefire"}
    assert _c(["music", "pop-culture", "christmas", "nfl", "celebrities"])["category"] == "culture"   # NFL-Schlagwort ohne „sports“
    assert _c([])["category"] == "other"


def test_taxonomy_crypto_weather_and_old_bets_without_tags():
    assert _c([], "Will the price of Ethereum be between $1,600 and $1,700 on November 20?", "crypto") == \
        {"category": "crypto", "sub": "Ethereum", "detail": "Kursbereich am Stichtag"}
    assert _c(["crypto", "bitcoin", "hit-price"], "Will Bitcoin reach $70,000 November 9-15?")["detail"] == "Erreicht einen Kurs"
    c = _c([], "Will the highest temperature in London be 12°C on November 15?", "weather", description="London City Airport (EGLC)")
    assert c == {"category": "weather", "sub": "Europa", "detail": "London"}
    # Wette von vor v2: nur das Kürzel des Ereignisses verrät die Liga
    assert _c([], "Will Bayern win?", "sports", event_slug="ucl-bay-psg-2026-11-05") == \
        {"category": "sports", "sub": "Fußball", "detail": "Champions League"}
    assert taxonomy.path({"category": "sports", "sub": "Fußball", "detail": "Allgemein"}) == "Sport › Fußball"


# ---------------------------------------------------------------- Basisraten
def _resolved(st, n, cat, p, yes, col="p_24h", start=0, spread=True):
    """n entschiedene Märkte mit Kurs p; `yes` davon mit „Ja“ – gleichmäßig über die Zeit verteilt (spread)
    oder alle in der älteren Hälfte."""
    yes_idx = {int(k * n / yes) for k in range(yes)} if (spread and yes) else set(range(yes))
    rows = []
    for i in range(n):
        r = {"p_1h": None, "p_6h": None, "p_24h": None, "p_72h": None, "p_7d": None}
        r[col] = p
        end = (datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(hours=start + i)).isoformat(timespec="seconds")
        rows.append((f"r{cat}{col}{start + i}", "q", cat, end, 1.0 if i in yes_idx else 0.0,
                     r["p_1h"], r["p_6h"], r["p_24h"], r["p_72h"], r["p_7d"]))
    st.xmany("INSERT INTO resolved(market_id, question, category, end_date, outcome, p_1h, p_6h, p_24h, p_72h, p_7d) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)


def _bm(mid="pm1", cat="politics", bid=0.09, ask=0.11, hours=20, outcomes=("Yes", "No")):
    return {"id": mid, "question": "Will X happen?", "category": cat, "outcomes": list(outcomes), "best_bid": bid, "best_ask": ask,
            "end_date": NOW + timedelta(hours=hours), "event_id": "ev1", "liquidity": 20000}


def test_base_rates_need_data_from_the_own_topic_and_shrink(tmp_path):
    st = Store(str(tmp_path / "b.sqlite"))
    br = base_model.BaseRates(st)
    assert br.analyze(_bm(), NOW) == {"skip": "zu wenige Vergleichsmärkte"}
    _resolved(st, 40, "culture", 0.10, 0)               # genug für „alle Themen“, aber nichts im eigenen Thema Politik
    br.refresh(force=True)
    assert br.analyze(_bm(), NOW) == {"skip": "zu wenige Vergleichsmärkte im eigenen Thema"}
    _resolved(st, 120, "politics", 0.10, 4)             # jetzt 120 Politik-Märkte, 3,3 % „Ja“ bei Kurs 10 %
    br.refresh(force=True)
    a = br.analyze(_bm(), NOW)
    # Rohabweichung −6,7 Pkt; gestaucht mit 50 gedachten Märkten bleiben −4,7
    assert 0.045 < a["p_yes"] < 0.06 and a["lane"] == "base" and a["model_id"] == "base.calib@1"
    assert a["info"].count("Grund:") == 1 and "ältere Hälfte" in a["info"] and a["features"]["n_cat"] == 120
    assert br.analyze(_bm(hours=24 * 9), NOW)["skip"].startswith("Restlaufzeit über 8 Tage")
    assert br.analyze(_bm(outcomes=("A", "B", "C")), NOW) == {"skip": "nicht genau zwei Ausgänge"}
    assert br.analyze(_bm(outcomes=("Bayern", "Paris")), NOW) == {"skip": "keine Ja/Nein-Frage"}
    assert base_model.horizon_col(30) == "p_24h" and base_model.horizon_col(50) == "p_72h" and base_model.horizon_col(0.5) == "p_1h"


def test_base_rates_for_games_but_never_exact_scores(tmp_path):
    """Live 15./16.11.: Färöer – Moldau „1:1“ zu 33 % statt 14 % geschätzt. Genaue Ergebnisse treffen zu selten – nie wetten.
    Spiele selbst sind wieder erlaubt (Wunsch 17.11. abends), aber nicht mehr, wenn sie schon laufen."""
    st = Store(str(tmp_path / "b.sqlite"))
    _resolved(st, 400, "sports", 0.10, 8)
    br = base_model.BaseRates(st)
    assert "p_yes" in br.analyze(_bm(cat="sports"), NOW)
    exact = {**_bm(cat="sports"), "question": "Exact Score: Faroe Islands 1 - 1 Moldova?"}
    assert br.analyze(exact, NOW) == {"skip": "genaues Ergebnis – wird nie gewettet"}
    live = {**_bm(cat="sports"), "game_start": NOW - timedelta(minutes=5)}
    assert br.analyze(live, NOW) == {"skip": "Spiel läuft schon"}


def test_base_rates_find_longshot_bias_with_clear_significance(tmp_path):
    st = Store(str(tmp_path / "b.sqlite"))
    _resolved(st, 400, "politics", 0.10, 8)              # Kurs 10 %, eingetreten nur 2 %
    br = base_model.BaseRates(st)
    a = br.analyze(_bm(), NOW)
    assert abs(a["p_yes"] - 0.0289) < 0.002 and a["z"] < -5
    assert a["features"]["n_cat"] == 400 and "eingetreten 2,0 %" in a["info"].replace(".", ",")
    # anderes Thema ohne eigene Daten: keine geliehene Abweichung mehr
    assert br.analyze(_bm(cat="culture"), NOW) == {"skip": "zu wenige Vergleichsmärkte im eigenen Thema"}


def test_base_rates_reject_implausible_unstable_and_failed_topics(tmp_path):
    st = Store(str(tmp_path / "b.sqlite"))
    _resolved(st, 600, "politics", 0.10, 18)             # 3 % statt 10 %
    _resolved(st, 100, "economy", 0.10, 30)              # 30 % statt 10 %: so viel ist fast immer ein Datenfehler
    _resolved(st, 100, "tech", 0.10, 8, spread=False)    # ältere Märkte +6 Pkt, neuere −10 Pkt: kein verlässliches Muster
    _resolved(st, 100, "culture", 0.10, 19)              # +9 Pkt – widerspricht allen Themen zusammen (−1,7 Pkt)
    br = base_model.BaseRates(st)
    assert br.analyze(_bm(cat="economy"), NOW) == {"skip": "Abweichung unplausibel groß"}
    assert br.analyze(_bm(cat="tech"), NOW) == {"skip": "Muster nicht stabil"}
    assert br.analyze(_bm(cat="culture"), NOW) == {"skip": "Muster widerspricht allen anderen Themen"}
    assert "p_yes" in br.analyze(_bm(), NOW)
    # Wächter: in der Prognose-Historie waren die Basisraten im Thema Politik gesichert schlechter als der Markt
    st.kv_set("learn:summary", json.dumps({"modelle": [
        {"category": "politics", "model": "base.calib@1", "n": 40, "clusters": 12, "z": 2.4, "brier": 0.30, "brier_market": 0.22},
        {"category": "world", "model": "base.calib@1", "n": 40, "clusters": 12, "z": 0.9, "brier": 0.23, "brier_market": 0.22}]}))
    br.refresh(force=True)
    assert br.analyze(_bm(), NOW) == {"skip": "Basisraten in diesem Thema gesperrt (Wächter)"}
    assert "world" not in br.blocked and "schlechter als der Markt" in br.blocked["politics"]
    assert br.cells_report()[0]["thema"] == "Wirtschaft"                       # größte Abweichung zuerst (für den Bericht)


# ---------------------------------------------------------------- Handel mit allen Märkten
def _raw(mid, tags, bid=0.09, ask=0.11, hours=20, q="Will X happen?"):
    end = NOW + timedelta(hours=hours)
    return {"id": mid, "question": q, "outcomes": json.dumps(["Yes", "No"]), "outcomePrices": json.dumps([str(bid), str(1 - bid)]),
            "bestBid": bid, "bestAsk": ask, "liquidityNum": 20000, "volume24hr": 5000, "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "active": True, "closed": False, "acceptingOrders": True, "clobTokenIds": json.dumps([f"{mid}-y", f"{mid}-n"]),
            "tags": [{"slug": t, "label": t.title()} for t in tags], "events": [{"id": "ev-" + mid, "title": "Ereignis " + mid, "slug": "ev-" + mid}]}


def _eng(tmp_path, raws):
    st = Store(str(tmp_path / "e.sqlite"))
    pages = {0: raws}
    eng = Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
                 odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None, book_fn=None,
                 fetch_markets=lambda params: pages.get(params["offset"], []))
    eng.book_fn = None
    return st, eng


def test_other_markets_are_collected_classified_and_deduplicated(tmp_path):
    raws = [_raw("p1", ["politics", "elections", "united-states"]), _raw("p1", ["politics"]),
            _raw("u1", ["crypto"], q="Bitcoin Up or Down - 5 min"), _raw("s1", ["sports", "soccer", "ucl"])]
    st, eng = _eng(tmp_path, raws)
    out = eng.collect_others(NOW, {"s1"})
    assert [m["id"] for m in out] == ["p1"]                                      # doppelt, 5-Minuten-Markt und bekannte ID raus
    m = out[0]
    assert (m["category"], m["sub"], m["lane"]) == ("politics", "USA", "base")
    # offene Basisraten-Wette auf einem Markt, der nicht mehr unter den aktivsten ist: wird einzeln nachgeladen
    st.insert_position({"account": "haupt_politik", "market_id": "old1", "category": "politics", "question": "q", "side": 0, "side_name": "Yes",
                        "price": 0.5, "shares": 2, "cost": 1, "fee": 0, "status": "open", "opened_at": "2026-11-14T10:00:00+00:00", "lane": "base"})
    eng.fetch_by_ids = lambda ids: {i: _raw(i, ["politics"]) for i in ids}
    assert [m["id"] for m in eng.collect_others(NOW, {"s1"})] == ["p1", "old1"]


def test_topic_accounts_trade_base_rates_and_store_entry(tmp_path):
    raws = [_raw("p1", ["politics", "elections", "united-states"]), _raw("c1", ["pop-culture", "movies"], bid=0.49, ask=0.51)]
    st, eng = _eng(tmp_path, raws)
    _resolved(st, 400, "politics", 0.10, 8)
    eng.base.refresh(force=True)
    assert eng.run_cycle()
    pos = st.open_positions("haupt_politik")
    assert [p["market_id"] for p in pos] == ["p1"]                                # Kultur-Markt: keine Vergleichsmärkte für 50 %
    p = pos[0]
    assert p["side"] == 1 and p["lane"] == "base" and p["model"] == "base.calib@1"
    assert (p["category"], p["sub"]) == ("politics", "USA")
    assert abs(p["own_p"] - 0.971) < 0.003 and abs(p["p_mid"] - 0.90) < 1e-6 and p["hours_left"] > 19
    e = json.loads(p["entry"])
    assert e["spur"] == "Basisraten" and abs(e["p_unser_ausgang"] - p["own_p"]) < 1e-3 and e["z"] < -5
    assert e["einordnung"] == "Politik › USA" and "Eigener Datensatz" in e["quellen"][0] and e["kontowert"] == config.START_BALANCE
    # Nur Konten des Themas Politik wetten auf den Politik-Markt – jede Strategie mit ihrem eigenen Politik-Konto
    holders = {p["account"] for p in st.open_positions() if p["market_id"] == "p1"}
    assert holders and all(a.endswith("_politik") for a in holders), holders
    assert {"haupt_politik", "schwelle4_politik", "fest10_politik", "kelly_politik", "halten_politik"} <= holders
    assert abs(st.open_positions("fest10_politik")[0]["cost"] - 10.0) < 1e-6
    assert not [p for p in st.open_positions() if p["market_id"] == "c1"]
    # Prognose-Historie und Katalog kennen den Markt mit Einordnung, aber ohne „eigenes Modell“
    assert st.q1("SELECT model, champion FROM forecasts WHERE market_id='p1'") == {"model": "base.calib", "champion": 1}
    assert st.q1("SELECT category, sub, modeled FROM markets WHERE id='p1'") == {"category": "politics", "sub": "USA", "modeled": 0}
    assert not st.q("SELECT 1 FROM predictions WHERE market_id='p1'")            # alte Tabelle nur für eigene Modelle


def test_base_lane_needs_statistical_support_and_can_be_switched_off(tmp_path, monkeypatch):
    raws = [_raw("p1", ["politics", "elections"])]
    st, eng = _eng(tmp_path, raws)
    _resolved(st, 60, "politics", 0.10, 4)               # Abweichung da, aber unsicher
    eng.base.refresh(force=True)
    eng.run_cycle()
    assert not st.open_positions("haupt_politik")
    o = next(x for x in eng.state["opportunities"] if x["id"] == "p1")
    assert o["status"] in ("Abweichung statistisch nicht gesichert", "Vorsprung zu klein") and o["lane"] == "base"
    monkeypatch.setattr(config, "TRADE_BASE", False)
    assert eng.collect_markets(NOW) == []                                         # ausgeschaltet: keine weiteren Märkte


# ---------------------------------------------------------------- Historie, Detail, Kalender
def _pos(st, **kw):
    base = {"account": "haupt_sport", "market_id": "m", "event_id": "e", "category": "sports", "sub": "Fußball", "detail": "Champions League",
            "question": "Will Bayern win?", "side": 0, "side_name": "Yes", "price": 0.5, "shares": 20, "cost": 10, "fee": 0, "own_p": 0.6,
            "market_p": 0.5, "edge": 0.1, "status": "open", "opened_at": "2026-11-14T10:00:00+00:00", "end_date": "2026-11-20T10:00:00+00:00",
            "kind": "model", "mark": 0.55}
    base.update(kw)
    return st.insert_position(base)


def test_bets_filter_by_levels_status_and_tree(tmp_path):
    st = Store(str(tmp_path / "v.sqlite"))
    _pos(st, market_id="a")
    _pos(st, market_id="b", detail="Bundesliga", status="won", pnl=10, closed_at="2026-11-14T20:00:00+00:00")
    _pos(st, market_id="c", sub="Basketball", detail="NBA", status="lost", pnl=-10, closed_at="2026-11-14T21:00:00+00:00")
    _pos(st, market_id="d", category="politics", sub="USA", detail="Allgemein", status="sold", pnl=-2, closed_at="2026-11-14T22:00:00+00:00",
         account="schwelle4_politik")
    r = paperview.bets(st, {"cat": "sports", "sub": "Fußball"})
    assert r["total"] == 2 and {x["market_id"] for x in r["rows"]} == {"a", "b"}
    assert paperview.bets(st, {"cat": "sports", "sub": "Fußball", "detail": "Champions League"})["total"] == 1
    assert [x["market_id"] for x in paperview.bets(st, {"status": "verloren"})["rows"]] == ["d", "c"]     # verkauft mit Verlust zählt mit
    assert paperview.bets(st, {"status": "gewonnen"})["summe"]["gv"] == 10
    assert paperview.bets(st, {"account": "schwelle4_politik"})["total"] == 1
    assert paperview.bets(st, {"strat": "schwelle4"})["total"] == 1 and paperview.bets(st, {"strat": "haupt"})["total"] == 3
    assert paperview.tree(st, {"strat": "schwelle4"}) == {"politics": {"n": 1, "subs": {"USA": {"n": 1, "details": {"Allgemein": 1}}}}}
    assert paperview.single_account({"strat": "schwelle4", "cat": "politics"}) == "schwelle4_politik"
    assert paperview.single_account({"strat": "arbitrage", "cat": "politics"}) is None          # gibt es nicht
    assert paperview.single_account({"strat": "haupt", "cat": "sports", "sub": "Fußball"}) is None
    assert paperview.bets(st, {"q": "bayern"})["total"] == 4 and paperview.bets(st, {"q": "100%"})["total"] == 0
    t = paperview.tree(st)
    assert t["sports"]["n"] == 3 and t["sports"]["subs"]["Fußball"]["details"] == {"Champions League": 1, "Bundesliga": 1}
    row = paperview.bets(st, {"cat": "sports", "status": "offen"})["rows"][0]
    assert row["pfad"] == "Sport › Fußball › Champions League" and abs(row["gv_offen"] - 1.0) < 1e-9 and row["account_name"] == "Haupt · Sport"


def test_calendar_counts_days_in_german_time(tmp_path):
    st = Store(str(tmp_path / "v.sqlite"))
    _pos(st, market_id="a", status="won", pnl=5, closed_at="2026-11-14T22:30:00+00:00")     # 23:30 in Berlin -> 14.
    _pos(st, market_id="b", status="lost", pnl=-3, closed_at="2026-11-14T23:30:00+00:00")   # 00:30 in Berlin -> 15.
    _pos(st, market_id="c", status="won", pnl=4, closed_at="2026-11-15T09:00:00+00:00", category="politics", sub="USA")
    c = paperview.calendar(st, {"monat": "2026-11"})
    days = {d["tag"]: d for d in c["tage"]}
    assert days["2026-11-14"]["gv"] == 5 and days["2026-11-15"]["gv"] == 1 and days["2026-11-15"]["n"] == 2
    assert days["2026-11-14"]["eroeffnet"] == 3 and c["summe"]["plus_tage"] == 2 and c["summe"]["gv"] == 6
    c = paperview.calendar(st, {"monat": "2026-11", "cat": "sports", "tag": "2026-11-15"})
    assert {d["tag"]: d["gv"] for d in c["tage"] if d["n"]} == {"2026-11-14": 5, "2026-11-15": -3}
    assert [x["market_id"] for x in c["geschlossen"]] == ["b"] and c["eroeffnet"] == []
    assert paperview.calendar(st, {"monat": "kaputt"})["monat"] == datetime.now(paperview.BERLIN).strftime("%Y-%m")


def test_bet_detail_shows_entry_and_flips_prices_to_our_side(tmp_path):
    st = Store(str(tmp_path / "v.sqlite"))
    pid = _pos(st, market_id="x", side=1, side_name="No", own_p=0.8, entry=json.dumps({"p_unser_ausgang": 0.8, "begruendung": "Test"}))
    st.x("INSERT INTO snapshots(market_id, ts, price) VALUES('x', '2026-11-14T10:00', 0.3)")
    d = paperview.bet_detail(st, pid, [])
    assert d["bet"]["entry"]["begruendung"] == "Test" and abs(d["prices"][0]["p"] - 0.7) < 1e-9
    assert paperview.bet_detail(st, 9999) is None


def test_overview_kpis_and_split(tmp_path):
    st = Store(str(tmp_path / "v.sqlite"))
    _pos(st, market_id="a", status="won", pnl=10, own_p=0.7, closed_at="2026-11-14T20:00:00+00:00")
    _pos(st, market_id="b", status="lost", pnl=-10, own_p=0.5, closed_at="2026-11-14T21:00:00+00:00")
    _pos(st, market_id="c", category="politics", sub="USA")
    o = paperview.overview(st, None, {"cat": "sports"})
    k = o["kennzahlen"]
    assert k["trefferquote"] == 0.5 and abs(k["erwartet"] - 0.6) < 1e-9 and k["gv"] == 0 and k["offen"] == 0
    assert o["aufteilung"]["stufe"] == "sub" and o["aufteilung"]["rows"][0]["name"] == "Fußball"
    assert o["kurve"]["art"] == "gv" and [p[1] for p in o["kurve"]["punkte"]] == [10, 0]


def test_old_bets_get_classified_once(tmp_path):
    st = Store(str(tmp_path / "o.sqlite"))
    _pos(st, market_id="old", sub=None, detail=None, event_slug="epl-ars-che-2026-11-01", question="Will Arsenal win?")
    _pos(st, market_id="old2", sub=None, detail=None, category="weather", question="Will the highest temperature in Paris be 9°C on November 12?")
    Engine(st, fetch_events=lambda tag, **kw: [], fetch_by_ids=lambda ids: {}, closes_fn=lambda a: (CLOSES, "Test"),
           odds_client=OddsClient(st, api_key=""), chain_fn=lambda a, n: None, book_fn=None)
    rows = {r["market_id"]: r for r in st.q("SELECT market_id, sub, detail, lane FROM positions")}
    assert (rows["old"]["sub"], rows["old"]["detail"], rows["old"]["lane"]) == ("Fußball", "Premier League", "model")
    assert (rows["old2"]["sub"], rows["old2"]["detail"]) == ("Europa", "Paris")
    assert st.kv_get("taxonomy_v1")
