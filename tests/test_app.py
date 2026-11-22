"""Dashboard und Schnittstellen (Flask-Testclient, ohne Netz)."""
import base64
import importlib
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from polybot import migrations

HDR = {"X-Requested-With": "polybot"}


def _load(monkeypatch, tmp_path, user="", pw=""):
    monkeypatch.setenv("AUTOSTART", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_USER", user)
    monkeypatch.setenv("DASHBOARD_PASSWORD", pw)
    from polybot import config
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)
    return app_module


@pytest.fixture
def mod(tmp_path, monkeypatch):
    return _load(monkeypatch, tmp_path)


def _seed(mod):
    """Ein paar echte Zeilen über die normalen Wege anlegen (kein Schreiben an den Agenten vorbei)."""
    from tests.test_agents import NOW, RSS, _m, _o
    co, st = mod.coordinator, mod.store
    now = datetime.now(timezone.utc).replace(microsecond=0)
    m1 = _m("w1", cat="weather", question="Will the highest temperature in London be 12°C on November 15?", end=now + timedelta(hours=30))
    m2 = _m("c1", cat="crypto", bid=0.10, ask=0.12, question="Will Bitcoin be above $100,000?", end=now + timedelta(hours=30))
    co.scanner.ingest([m1, m2], now)
    res = [_o(m1, 0.45, {"weather.ens@2": 0.45, "weather.wide@1": 0.40}, model_id="weather.ens@2"),
           _o(m2, 0.70, {"crypto.options@1": 0.70}, model_id="crypto.options@1")]
    co.on_results(res, now)
    st.add_outcome("w1", 1.0)
    co.registry.run("learning", now)
    co.registry.run("guardian", now)
    return m1, m2


def test_pages_render_and_static_files(mod):
    c = mod.app.test_client()
    r = c.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and 'id="main"' in html and "/static/app.js" in html and "Claude-Berichte" in html
    assert "<script>" not in html                                            # kein eingebettetes Skript (strenge CSP)
    assert "/static/pages3.js" in html and "#/wetten" in html and "#/kalender" in html
    r = c.get("/paper")                                                      # alte Adresse führt in die Zentrale
    assert r.status_code == 302 and r.headers["Location"].endswith("/#/paper")
    for f in ("app.js", "pages1.js", "pages2.js", "pages3.js", "shell.js", "app.css", "shell.css"):
        assert c.get("/static/" + f).status_code == 200, f
    assert os.path.exists(os.path.join(mod.config.DATA_DIR, "polybot.sqlite"))


def test_security_headers_and_minimal_health(mod):
    c = mod.app.test_client()
    mod.engine.state["last_error"] = "RuntimeError: geheim apiKey=XYZ"
    r = c.get("/health")
    assert r.status_code == 200 and set(r.get_json()) == {"ok", "last_scan", "age_s"}      # keine Fehlertexte ohne Anmeldung
    r = c.get("/api/overview")
    assert "script-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
    assert c.post("/api/scan").status_code in (403, 404)                                    # alter Auslöser mit Schlüssel in der Adresse ist weg
    assert c.post("/api/scan", headers=HDR).status_code in (404, 405)


def test_write_calls_need_dashboard_header(mod):
    c = mod.app.test_client()
    for path in ("/api/settings", "/api/reports/generate", "/api/model/rollback", "/api/proposal/1/approve", "/api/recommendation/1",
                 "/api/account/haupt_krypto/resume"):
        assert c.post(path, json={}).status_code == 403, path


def test_login_throttle_blocks_guessing(tmp_path, monkeypatch):
    mod = _load(monkeypatch, tmp_path, "luis", "geheim")
    c = mod.app.test_client()

    def hdr(pw):
        return {"Authorization": "Basic " + base64.b64encode(f"luis:{pw}".encode()).decode()}
    assert c.get("/").status_code == 401                                                     # ohne Anmeldung: zählt nicht als Fehlversuch
    for i in range(10):
        assert c.get("/", headers=hdr(f"falsch{i}")).status_code == 401
    assert c.get("/", headers=hdr("geheim")).status_code == 429                              # jetzt auch mit richtigem Passwort gesperrt
    assert c.get("/health").status_code == 200
    mod._FAILS.clear()
    assert c.get("/", headers=hdr("geheim")).status_code == 200


def test_all_read_endpoints_on_empty_database(mod):
    """Leere Datenbank: jede Seite antwortet, und nichts wird erfunden."""
    c = mod.app.test_client()
    for path in ("/api/overview", "/api/markets", "/api/predictions", "/api/agents", "/api/learning", "/api/strategy", "/api/performance",
                 "/api/data", "/api/health", "/api/reports", "/api/settings", "/api/state", "/api/events"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.get_data(as_text=True)[:300])
    o = c.get("/api/overview").get_json()
    assert o["quality"] == [] and o["counts"]["prognosen"] == 0 and o["resources"] is None and o["tick"] is None
    assert c.get("/api/performance").get_json()["n"] == 0
    assert c.get("/api/strategy").get_json()["latest"] is None
    assert c.get("/api/market/gibtsnicht").status_code == 404
    assert c.get("/api/report/99").status_code == 404


def test_overview_markets_predictions_with_data(mod):
    _seed(mod)
    c = mod.app.test_client()
    o = c.get("/api/overview").get_json()
    assert o["counts"]["märkte_offen"] == 2 and o["counts"]["prognosen"] == 3 and o["counts"]["prognosen_mit_ergebnis"] == 1
    q = {x["category"]: x for x in o["quality"]}
    assert q["weather"]["n"] == 1 and abs(q["weather"]["brier"] - 0.3025) < 1e-9 and o["resources"]["rss_mb"] > 5
    m = c.get("/api/markets?q=Bitcoin&sort=price&dir=asc").get_json()
    assert m["total"] == 1 and m["rows"][0]["id"] == "c1" and m["rows"][0]["modeled"] == 1
    assert c.get("/api/markets?q=%25").get_json()["total"] == 0                               # % ist Suchtext, kein Platzhalter
    assert c.get("/api/markets?sort=id;DROP TABLE markets").get_json()["total"] == 2          # unbekannte Sortierung -> Standard
    assert c.get("/api/markets?cat=weather").get_json()["total"] == 1
    p = c.get("/api/predictions").get_json()
    assert p["total"] == 2 and p["counts"] == {"gesamt": 2, "varianten": 3, "abgelehnt": 1, "entschieden": 1}
    rej = c.get("/api/predictions?status=abgelehnt").get_json()["rows"]
    assert len(rej) == 1 and rej[0]["market_id"] == "c1" and "Abweichung" in rej[0]["reject_reason"]
    assert c.get("/api/predictions?status=entschieden").get_json()["rows"][0]["outcome"] == 1.0
    d = c.get("/api/market/w1").get_json()
    assert d["market"]["question"].startswith("Will the highest") and len(d["forecasts"]) == 2 and d["outcome"]["outcome"] == 1.0
    assert d["forecasts"][0]["sources"] == ["test"] and len(d["history"]) == 1
    a = c.get("/api/agents").get_json()
    assert {x["id"] for x in a["agents"]} == {"coordinator", "trader", "scanner", "probability", "validation", "news", "learning", "strategy", "guardian"}
    assert a["validation"]["letzter_lauf"]["gültig"] == 1 and len(a["contradictions"]) == 1


def test_settings_roundtrip_and_validation(mod):
    c = mod.app.test_client()
    spec = {s["key"]: s for s in c.get("/api/settings").get_json()["spec"]}
    assert spec["MAX_DIVERGENCE"]["min"] == 0.10 and spec["TRADE_WEATHER"]["type"] == "bool"
    assert not any("TOKEN" in k or "PASSWORD" in k or "KEY" in k for k in spec)
    r = c.post("/api/settings", json={"TRADE_WEATHER": False, "NOTIFY_DAILY_CAP": 12}, headers=HDR)
    assert r.status_code == 200 and len(r.get_json()["changed"]) == 2 and mod.config.TRADE_WEATHER is False
    assert c.post("/api/settings", json={"MAX_DIVERGENCE": 9}, headers=HDR).status_code == 400
    assert c.post("/api/settings", json={"DASHBOARD_PASSWORD": "x"}, headers=HDR).status_code == 400
    assert mod.store.scalar("SELECT COUNT(*) FROM events WHERE type='einstellung'") == 2


def test_proposal_approve_reject_and_rollback(mod):
    c, st, co = mod.app.test_client(), mod.store, mod.coordinator
    detail = json.dumps({"challenger": "weather.wide@1", "w": None})
    st.x("INSERT INTO proposals(created_at, kind, category, key, title, detail) VALUES('2026-11-14T00:00:00+00:00','modellwechsel','weather','k1','Test A',?)", (detail,))
    st.x("INSERT INTO proposals(created_at, kind, category, key, title, detail) VALUES('2026-11-14T00:00:00+00:00','mischung','sports','k2','Test B',?)",
         (json.dumps({"w": 0.5}),))
    assert c.post("/api/proposal/2/reject", json={"note": "nein"}, headers=HDR).status_code == 200
    assert co.probability.reg["sports"]["mix_w"] == 1.0
    assert c.post("/api/proposal/1/approve", json={}, headers=HDR).status_code == 200
    assert co.probability.reg["weather"]["model"] == "weather.wide"
    assert c.post("/api/proposal/1/approve", json={}, headers=HDR).status_code == 409        # nicht zweimal
    assert c.post("/api/proposal/1/loeschen", json={}, headers=HDR).status_code == 404
    lern = c.get("/api/learning").get_json()
    assert [p["status"] for p in lern["lernen"]["vorschläge"]] == ["abgelehnt", "freigegeben"]
    assert c.post("/api/model/rollback", json={"category": "weather"}, headers=HDR).status_code == 200
    assert co.probability.reg["weather"]["model"] == "weather.ens"
    assert c.post("/api/model/rollback", json={"category": "sports"}, headers=HDR).status_code == 409   # nichts zum Zurückholen
    assert c.post("/api/model/rollback", json={"category": "x"}, headers=HDR).status_code == 404


def test_report_generate_read_and_recommendations(mod):
    c, st = mod.app.test_client(), mod.store
    _seed(mod)
    r = c.post("/api/reports/generate", json={}, headers=HDR)
    assert r.status_code == 200
    rid = r.get_json()["id"]
    assert c.post("/api/reports/generate", json={}, headers=HDR).status_code == 429          # nicht im Minutentakt
    one = c.get(f"/api/report/{rid}").get_json()
    assert "## 13." in one["report"]["markdown"] and one["report"]["kind"] == "manuell"
    assert c.post(f"/api/report/{rid}", json={"status": "besprochen", "note": "mit Claude durchgegangen"}, headers=HDR).status_code == 200
    assert c.post(f"/api/report/{rid}", json={"status": "kaputt"}, headers=HDR).status_code == 400
    lst = c.get("/api/reports").get_json()
    assert lst["reports"][0]["status"] == "besprochen" and lst["reports"][0]["note"] == "mit Claude durchgegangen"
    st.x("INSERT INTO recommendations(report_id, text, updated_at) VALUES(?,?,?)", (rid, "Testempfehlung", "x"))
    rec = st.q1("SELECT id FROM recommendations WHERE text='Testempfehlung'")["id"]
    assert c.post(f"/api/recommendation/{rec}", json={"status": "erledigt", "note": "ok"}, headers=HDR).status_code == 200
    assert c.post(f"/api/recommendation/{rec}", json={"status": "weg"}, headers=HDR).status_code == 400
    assert c.post("/api/recommendation/9999", json={"status": "erledigt"}, headers=HDR).status_code == 404
    assert st.q1("SELECT status, note FROM recommendations WHERE id=?", (rec,)) == {"status": "erledigt", "note": "ok"}


def test_health_page_data(mod):
    _seed(mod)
    h = mod.app.test_client().get("/api/health").get_json()
    assert h["db"]["schema"] == migrations.LATEST and h["db"]["wal"] == "wal" and h["db"]["tabellen"]["forecasts"] == 3
    assert h["guardian"]["aktuell"]["rss_mb"] > 5 and h["guardian"]["kette"]["bis"] >= 1 and h["system"]["loop_alive"] is None
    from polybot import sources
    assert h["telegram"]["aktiv"] is False and sources.redact(json.dumps(h)) == json.dumps(h)   # nichts Geheimnis-Ähnliches in der Antwort


def test_loop_runs_cycle_and_stops(mod, monkeypatch):
    """Die Hintergrund-Schleife fährt einen Takt, zählt ihn und lässt sich sauber anhalten."""
    calls = []
    monkeypatch.setattr(mod.coordinator, "cycle", lambda: calls.append(1) or mod._stop.set())
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    mod._stop.clear()
    mod._loop()
    assert calls == [1] and mod._beat["cycles"] == 1 and mod._beat["start"] is None and mod._beat["end"] <= time.time()
    mod._stop.clear()


def test_paper_endpoints_history_detail_calendar(mod):
    c = mod.app.test_client()
    st = mod.store
    pid = st.insert_position({"account": "haupt_sport", "market_id": "s1", "category": "sports", "sub": "Fußball", "detail": "Champions League",
                              "question": "Will Bayern win?", "side": 0, "side_name": "Yes", "price": 0.5, "shares": 20, "cost": 10, "fee": 0,
                              "own_p": 0.62, "market_p": 0.5, "p_mid": 0.49, "edge": 0.12, "status": "won", "pnl": 10, "payout": 20,
                              "opened_at": "2026-11-10T10:00:00+00:00", "closed_at": "2026-11-11T21:00:00+00:00", "kind": "model", "lane": "model",
                              "entry": json.dumps({"p_unser_ausgang": 0.62, "begruendung": "Buchmacher 62 %"}),
                              "exit_reason": "Markt entschieden: „Yes“"})
    d = c.get("/api/bets?cat=sports&sub=Fu%C3%9Fball&detail=Champions%20League").get_json()
    assert d["total"] == 1 and d["rows"][0]["id"] == pid and d["tree"]["sports"]["subs"]["Fußball"]["n"] == 1
    assert c.get("/api/bets?cat=politics").get_json()["total"] == 0
    # Export als Tabelle (2.8): gleicher Filter, Excel-Format, Marktfragen gegen Formel-Einschleusung geschützt
    st.insert_position({"account": "haupt_sport", "market_id": "s2", "category": "sports", "question": "=HYPERLINK(\"x\");Bayern?",
                        "side": 1, "side_name": "No", "price": 0.4, "shares": 10, "cost": 4, "fee": 0, "status": "open",
                        "opened_at": "2026-11-12T10:00:00+00:00", "lane": "model"})
    r = c.get("/api/bets.csv?cat=sports&sub=Fu%C3%9Fball")
    assert r.status_code == 200 and r.mimetype == "text/csv" and "attachment" in r.headers["Content-Disposition"]
    rows = r.get_data(as_text=True).lstrip("\ufeff").splitlines()
    assert len(rows) == 2 and rows[0].startswith("Nr;Konto;Thema") and ";Will Bayern win?;Yes;eigenes Modell;10.11.2026 11:00;" in rows[1]
    assert ";0,5;0,62;0,49;0,12;10;20;gewonnen;10;" in rows[1]
    alle = c.get("/api/bets.csv?cat=sports").get_data(as_text=True)
    assert '"\'=HYPERLINK(""x"");Bayern?"' in alle and ";offen;" in alle
    b = c.get(f"/api/bet/{pid}").get_json()
    assert b["bet"]["entry"]["begruendung"] == "Buchmacher 62 %" and b["bet"]["pfad"] == "Sport › Fußball › Champions League"
    assert c.get("/api/bet/999999").status_code == 404 and c.get("/api/bet/abc").status_code == 404
    k = c.get("/api/calendar?monat=2026-11&tag=2026-11-11").get_json()
    assert [t["gv"] for t in k["tage"] if t["n"]] == [10] and k["geschlossen"][0]["id"] == pid
    p = c.get("/api/paper?account=haupt_sport").get_json()
    assert p["kennzahlen"]["kontowert"] is not None and p["kurve"]["art"] == "kontowert" and len(p["konten"]) == len(mod.config.ACCOUNTS)
    p = c.get("/api/paper?cat=sports").get_json()
    assert p["kennzahlen"]["gv"] == 10 and p["kennzahlen"]["trefferquote"] == 1.0 and p["aufteilung"]["stufe"] == "sub"
    assert c.get("/api/paper?account=x%27%20OR%201%3D1--").status_code == 200                # Eingaben gehen nur als Parameter in SQL
