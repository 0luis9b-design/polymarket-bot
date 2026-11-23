"""Form-Modell (2.11, Freigabe 23.11.: „Du kannst alles kostenlose umsetzen“): Stärke-Wertung (Elo) aus Ergebnissen,
Fußball von ClubElo, Verletzte/Gesperrte von API-Football (nur mit eigenem Schlüssel). Erst messen, eigene Konten."""
import json
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("AUTOSTART", "0")

from polybot import config, form_model, sources  # noqa: E402
from polybot.store import Store  # noqa: E402
from tests.test_all_tips import _eng, _game  # noqa: E402

NOW = datetime.now(timezone.utc).replace(microsecond=0)
DAY = (NOW + timedelta(hours=5)).strftime("%Y-%m-%d")
GD = ["GD<-5", "GD=-5", "GD=-4", "GD=-3", "GD=-2", "GD=-1", "GD=0", "GD=1", "GD=2", "GD=3", "GD=4", "GD=5", "GD>5"]
CLUBELO = ("Date,Country,Home,Away," + ",".join(GD) + "\n"
           f"{DAY},GER,Bayern,Dortmund,0,0,0,0.01,0.04,0.15,0.20,0.25,0.20,0.10,0.04,0.01,0\n").encode()


def _closed(mid, a, b, winner, sport_tag, days_ago):
    start = NOW - timedelta(days=days_ago)
    return {"id": mid, "question": f"{a} vs {b}", "outcomes": json.dumps([a, b]),
            "outcomePrices": json.dumps(["1", "0"] if winner == 0 else ["0", "1"]), "closed": True, "active": True,
            "endDate": (start + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "gameStartTime": start.strftime("%Y-%m-%d %H:%M:%S+00"),
            "sportsMarketType": "moneyline", "clobTokenIds": json.dumps([f"{mid}-0", f"{mid}-1"]),
            "tags": [{"slug": t, "label": t.title()} for t in ["sports", sport_tag]],
            "events": [{"id": "ev" + mid, "title": f"{a} vs {b}", "slug": f"{mid}-{start:%Y-%m-%d}"}]}


def test_clubelo_fixtures_are_parsed_from_goal_differences():
    fx = form_model.parse_clubelo(CLUBELO)
    assert len(fx) == 1 and (fx[0]["home"], fx[0]["away"]) == ("Bayern", "Dortmund")
    p = fx[0]["p"]
    assert abs(p["Bayern"] - 0.60) < 1e-9 and abs(p["Draw"] - 0.20) < 1e-9 and abs(p["Dortmund"] - 0.20) < 1e-9
    alt = form_model.parse_clubelo(b"Date,Country,Home,Away,R:1-0,R:0-0,R:0-1\n2026-11-24,ENG,Arsenal,Chelsea,0.5,0.3,0.2\n")
    assert abs(alt[0]["p"]["Arsenal"] - 0.5) < 1e-9 and form_model.parse_clubelo(b"kaputt\n1,2\n") == []


def test_elo_from_results_needs_enough_games(tmp_path):
    st = Store(str(tmp_path / "f.sqlite"))
    f = form_model.Form(st)
    rows = []
    for i in range(8):   # A schlägt B achtmal, C hat nur 2 Spiele
        rows.append((f"r{i}", "sports", "Tennis", (NOW - timedelta(days=10 - i)).isoformat(), "Alpha One", "Beta Two", 0, None, NOW.isoformat()))
    rows += [("c1", "sports", "Tennis", NOW.isoformat(), "Gamma", "Beta Two", 0, None, NOW.isoformat()),
             ("c2", "sports", "Tennis", NOW.isoformat(), "Gamma", "Alpha One", 1, None, NOW.isoformat())]
    st.xmany("INSERT INTO match_results(market_id, category, sport, played_at, a, b, winner, slug, added_at) VALUES(?,?,?,?,?,?,?,?,?)", rows)
    m = {"category": "sports", "sub": "Tennis", "outcomes": ["Beta Two", "Alpha One"]}
    p, info, feats, _ = f.two_way(m)
    assert p < 0.25 and feats["spiele_b"] == 9 and "Stärke-Wertung Tennis" in info      # B ist klar schwächer
    assert f.two_way({**m, "outcomes": ["Gamma", "Alpha One"]}) is None                    # Gamma: nur 2 Spiele
    assert f.two_way({**m, "sub": "Tischtennis"}) is None                                  # andere Sportart, eigene Wertung


def test_results_are_collected_day_by_day(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORM_DAYS", 3)
    raws = [_closed("x1", "Alpha One", "Beta Two", 0, "tennis", 2), _closed("x2", "Ping", "Pong", 1, "table-tennis", 2)]
    st = Store(str(tmp_path / "c.sqlite"))
    asked = []

    def fetch(params):
        asked.append(params)
        lo = datetime.strptime(params["end_date_min"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        hi = datetime.strptime(params["end_date_max"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        if params["tag_id"] != "sports" or params["offset"]:
            return []
        return [r for r in raws if lo <= datetime.strptime(r["endDate"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc) <= hi]
    f = form_model.Form(st, fetch_markets=fetch)
    assert f.collect(NOW) == 2
    got = {r["market_id"]: r for r in st.q("SELECT * FROM match_results")}
    assert (got["x1"]["sport"], got["x1"]["winner"], got["x2"]["sport"], got["x2"]["winner"]) == ("Tennis", 0, "Tischtennis", 1)
    assert all(p["closed"] == "true" and p["sports_market_types"] == "moneyline" for p in asked)
    back = datetime.fromisoformat(st.kv_json("form:back")["back"])
    assert back <= NOW - timedelta(days=3) + timedelta(hours=12)                                # rückwärts bis FORM_DAYS
    # die letzten 36 Stunden kommen zuerst, und zwar mit Spielen, deren offizielles Ende noch kommt (Tischtennis)
    first = asked[0]
    assert first["end_date_min"] <= (NOW - timedelta(hours=35)).strftime("%Y-%m-%dT%H:%M:%SZ") and \
        first["end_date_max"] >= (NOW + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    asked.clear()
    assert f.collect(NOW) == 0 and len(asked) == 2                                              # danach nur noch die letzten 36 h


def _bayern(bid, ask):
    return _game("fcb", ["sports", "soccer", "bundesliga"], ["Yes", "No"], bid, ask, slug=f"bun-fcb-bvb-{DAY}",
                 q="Will FC Bayern München win?") | {"events": [{"id": "e-fcb", "title": "FC Bayern München vs. Borussia Dortmund",
                                                                   "slug": f"bun-fcb-bvb-{DAY}"}]}


def test_engine_logs_form_forecasts_and_bets_only_with_clear_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "FORM_TIPS", True)
    st, eng, _ = _eng(tmp_path, {"sports": [_bayern(0.50, 0.52)]})
    eng.form.fetch_text = lambda url, **kw: CLUBELO
    assert eng.run_cycle()
    fc = st.q("SELECT model, champion, p, features FROM forecasts WHERE model='form.elo'")
    assert len(fc) == 1 and fc[0]["champion"] == 0 and abs(fc[0]["p"] - 0.60) < 1e-6 and "heim" in fc[0]["features"]
    pos = st.open_positions("form_sport")
    assert len(pos) == 1 and pos[0]["side_name"] == "Yes" and pos[0]["lane"] == "form" and abs(pos[0]["cost"] - config.FORM_STAKE) < 0.01
    assert "ClubElo: Bayern – Dortmund · FC Bayern München 60 %" in pos[0]["entry"]
    # Markt schon bei 57 ¢: nur 3 Punkte Vorsprung -> keine Wette
    (tmp_path / "b").mkdir()
    st2, eng2, _ = _eng(tmp_path / "b", {"sports": [_bayern(0.56, 0.57)]})
    eng2.form.fetch_text = lambda url, **kw: CLUBELO
    assert eng2.run_cycle() and not st2.open_positions("form_sport")
    assert len(st2.q("SELECT 1 FROM forecasts WHERE model='form.elo'")) == 1           # gemessen wird trotzdem


def test_injuries_are_shown_but_do_not_change_the_probability(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "geheim-12345678")
    monkeypatch.setenv("API_FOOTBALL_KEY", "geheim-12345678")
    st = Store(str(tmp_path / "i.sqlite"))
    calls = []

    def fetch_json(url, params=None, retries=1, headers=None):
        calls.append((url, params, headers))
        return {"errors": [], "response": [
            {"player": {"name": "M. Neuer", "type": "Missing Fixture", "reason": "Knee Injury"}, "team": {"name": "Bayern München"},
             "fixture": {"id": 1}, "league": {"name": "Bundesliga"}}]}
    f = form_model.Form(st, fetch_text=lambda url, **kw: CLUBELO, fetch_json=fetch_json)
    assert f.injuries_refresh(NOW) == 2 and f.injuries_refresh(NOW) == 0                    # 2 Tage, dann 3 h Pause
    assert all("geheim" not in json.dumps(p) and h == {"x-apisports-key": "geheim-12345678"} for _, p, h in calls)
    m = {"id": "fcb", "category": "sports", "sub": "Fußball", "accepting_orders": True, "outcomes": ["Yes", "No"],
         "question": "Will FC Bayern München win?", "event_title": "FC Bayern München vs. Borussia Dortmund",
         "game_start": NOW + timedelta(hours=5), "sports_market_type": "moneyline"}
    a = f.analyze(m, NOW)
    assert abs(a["p_yes"] - 0.60) < 1e-9 and a["features"]["ausfaelle_a"] == 1 and "M. Neuer" in a["info"]
    assert "API-Football (Verletzte/Gesperrte)" in a["sources"]
    assert sources.redact("Fehler mit geheim-12345678 im Text") == "Fehler mit <geheim> im Text"


def test_evaluation_compares_model_and_market(tmp_path):
    st = Store(str(tmp_path / "e.sqlite"))
    for i, (p, mkt, out) in enumerate([(0.8, 0.6, 1), (0.3, 0.5, 0), (0.7, 0.55, 1)]):
        st.x("INSERT INTO markets(id, question, category, sub) VALUES(?,?,?,?)", (f"m{i}", "A vs B", "sports", "Tennis"))
        st.x("INSERT INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, champion, valid, cluster) "
             "VALUES(?,?,?,?,?,?,?,?,0,1,?)", (f"m{i}", "sports", "form.elo", "1", "6-24h", NOW.isoformat(), p, mkt, f"g{i}"))
        st.add_outcome(f"m{i}", float(out))
    ev = form_model.evaluate(st)
    assert ev[0]["sportart"] == "Tennis" and ev[0]["n"] == 3 and ev[0]["brier_modell"] < ev[0]["brier_markt"]


def test_report_shows_form_model_status(tmp_path, monkeypatch):
    from polybot import reports
    from tests.test_agents import _engine
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    store, eng = _engine(tmp_path, [])
    store.xmany("INSERT INTO match_results(market_id, category, sport, played_at, a, b, winner, slug, added_at) VALUES(?,?,?,?,?,?,?,?,?)",
                [("r1", "sports", "Tennis", NOW.isoformat(), "A", "B", 0, None, NOW.isoformat())])
    eng.run_cycle()
    rid = reports.generate(eng.intel, NOW, kind="manuell")
    md = store.q1("SELECT markdown FROM reports WHERE id=?", (rid,))["markdown"]
    assert "Form-Modell (seit 2.11, Elo): gesammelte Ergebnisse je Sportart – Sport · Tennis 1 (0 mit Wertung)" in md
    assert "Verletzte/Gesperrte: aus (kein Schlüssel API_FOOTBALL_KEY eingetragen)" in md
