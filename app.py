"""Polymarket-Spielgeld-Bot – Webserver + Hintergrund-Schleife.

Start lokal:   python app.py
Railway:       gunicorn app:app (siehe railway.json) – die Schleife startet beim Import.
"""
import atexit
import json
import logging
import os
import signal
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import base64
import binascii
import hashlib
import secrets

from flask import Flask, Response, jsonify, render_template, request

from polybot import config, events, notify, reports, settings, sources, version, weather_model
from polybot.agents.coordinator import Coordinator
from polybot.agents.scanner import CATEGORY_NAMES
from polybot.engine import Engine
from polybot.polymarket import parse_dt
from polybot.sources import redact
from polybot.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polybot")

app = Flask(__name__)

# ---------------------------------------------------------------- Zugangsschutz
# Wie beim XAUUSD-Bot: HTTP-Basic-Auth über DASHBOARD_USER und DASHBOARD_PASSWORD.
# Sind beide nicht gesetzt, bleibt der Schutz aus (sonst sperrt man sich nach einem Deploy
# ohne Variablen selbst aus) – das wird aber laut ins Log geschrieben.
# /health bleibt immer frei, weil Railway darüber prüft, ob der Dienst lebt.
FREIE_PFADE = {"/health", "/favicon.ico"}


def _abdruck(wert):
    return hashlib.sha256(wert.encode("utf-8")).digest()


_USER = os.environ.get("DASHBOARD_USER", "")
_PASS = os.environ.get("DASHBOARD_PASSWORD", "")
AUTH_AKTIV = bool(_USER and _PASS)
_USER_H, _PASS_H = _abdruck(_USER), _abdruck(_PASS)
if AUTH_AKTIV:
    log.info("Zugangsschutz aktiv (HTTP Basic Auth, Benutzer %r)", _USER)
elif _USER or _PASS:
    log.warning("UNGESCHUETZT: Es ist nur %s gesetzt – Basic Auth braucht DASHBOARD_USER und DASHBOARD_PASSWORD.",
                "DASHBOARD_USER" if _USER else "DASHBOARD_PASSWORD")
else:
    log.warning("UNGESCHUETZT: DASHBOARD_USER und DASHBOARD_PASSWORD sind nicht gesetzt.")


def _berechtigt(header):
    if not header:
        return False
    art, _, wert = header.partition(" ")
    if art.lower() != "basic" or not wert:
        return False
    try:
        klar = base64.b64decode(wert, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    benutzer, trenner, passwort = klar.partition(":")
    if not trenner:
        return False
    # beide Vergleiche laufen immer, damit die Antwortzeit nichts verrät
    ok_u = secrets.compare_digest(_abdruck(benutzer), _USER_H)
    ok_p = secrets.compare_digest(_abdruck(passwort), _PASS_H)
    return ok_u & ok_p


# Schutz vor Durchprobieren: nach 10 falschen Anmeldungen in 10 Minuten wird die Adresse 10 Minuten abgewiesen.
_FAILS = defaultdict(deque)
_FAIL_LIMIT, _FAIL_WINDOW = 10, 600


def _client():
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or "?")[:64]


@app.before_request
def _zugangsschutz():
    if request.path in FREIE_PFADE:
        return None
    if AUTH_AKTIV:
        ip, now = _client(), time.time()
        fails = _FAILS[ip]
        while fails and now - fails[0] > _FAIL_WINDOW:
            fails.popleft()
        if len(fails) >= _FAIL_LIMIT:
            return Response("Zu viele Fehlversuche. Bitte in 10 Minuten erneut versuchen.", 429, {"Retry-After": "600"}, mimetype="text/plain")
        if not _berechtigt(request.headers.get("Authorization", "")):
            if request.headers.get("Authorization"):
                fails.append(now)
                if len(fails) == _FAIL_LIMIT:
                    log.warning("Anmeldung: %d Fehlversuche von einer Adresse – 10 Minuten gesperrt", _FAIL_LIMIT)
            if len(_FAILS) > 5000:
                _FAILS.clear()
            return Response("Zugang nur mit Anmeldung.", 401,
                            {"WWW-Authenticate": 'Basic realm="Polymarket-Bot", charset="UTF-8"'}, mimetype="text/plain")
    # Schreibende Aufrufe nur von der eigenen Seite: Der Browser schickt die Anmeldung auch bei fremden Seiten mit,
    # einen eigenen Kopf kann eine fremde Seite aber nicht setzen.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != "polybot":
        return jsonify(error="Aufruf nur aus dem Dashboard möglich"), 403
    return None


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


@app.after_request
def _sicherheits_kopf(resp):
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if request.path.startswith("/api/") or request.path in ("/", "/paper"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


store = Store(os.path.join(config.DATA_DIR, "polybot.sqlite"))
weather_model.attach(store)   # Wetter-Vorhersagen aus der Zeit vor dem Neustart weiterverwenden
coordinator = Coordinator(store)
engine = Engine(store, intel=coordinator)
STARTED = time.time()
_started = False
_start_lock = threading.Lock()
_stop = threading.Event()
_beat = {"start": None, "end": None, "cycles": 0, "restarts": 0, "thread": None}
_loop_lock_file = None


def _single_loop():
    """Nur ein Prozess darf die Schleife fahren (Schutz, falls je mehr als ein Gunicorn-Worker läuft)."""
    global _loop_lock_file
    try:
        import fcntl
        f = open(os.path.join(config.DATA_DIR, "loop.lock"), "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _loop_lock_file = f
        return True
    except BlockingIOError:
        return False
    except Exception:  # noqa: BLE001 – kein Datei-Lock verfügbar (z. B. Windows): laufen lassen
        return True


def _loop():
    time.sleep(3)
    store.log("Bot gestartet (Spielgeld, v%s). Intervall: %.0f min" % (version.VERSION, config.SCAN_INTERVAL_MIN))
    while not _stop.is_set():
        _beat["start"] = time.time()
        try:
            coordinator.cycle()
        except Exception as e:  # noqa: BLE001 – der Takt darf nie sterben
            log.exception("Takt abgebrochen")
            events.emit(store, "hoch", "takt", "Takt mit Fehler abgebrochen", redact(f"{type(e).__name__}: {e}"), key="takt-fehler")
        _beat["end"] = time.time()
        _beat["start"] = None
        _beat["cycles"] += 1
        waited = 0.0
        while waited < config.SCAN_INTERVAL_MIN * 60 and not _stop.is_set():  # in kleinen Schritten: Änderungen am Intervall greifen sofort
            _stop.wait(5)
            waited += 5


def _start_loop():
    t = threading.Thread(target=_loop, name="polybot-loop", daemon=True)
    _beat["thread"] = t
    t.start()


def _watchdog():
    """Überwacht die Schleife: meldet Stillstand, startet einen abgestürzten Faden neu und beendet den Prozess,
    wenn ein Takt völlig festhängt (Gunicorn startet ihn dann frisch) – höchstens einmal in 3 Stunden."""
    limit = config.WATCHDOG_MIN * 60
    alerted = False
    prev = parse_dt(engine.state.get("last_scan"))  # war der Bot vor dem Start lange aus (Absturz statt Deploy)?
    down_before = prev and (datetime.now(timezone.utc) - prev).total_seconds() > limit
    started = time.time()
    while not _stop.is_set():
        _stop.wait(60)
        last = parse_dt(engine.state.get("last_scan"))
        now = datetime.now(timezone.utc)
        age = (now - last).total_seconds() if last else time.time() - started
        if down_before and last and last > prev:
            mins = (last - prev).total_seconds() / 60
            events.emit(store, "hoch", "wiederherstellung", "Polymarket-Bot läuft wieder", f"Er war etwa {mins:.0f} min aus (Neustart).", agent="guardian")
            store.log(f"Wachhund: Bot war {mins:.0f} min aus und läuft wieder")
            down_before = False
        t = _beat["thread"]
        if t is not None and not t.is_alive() and not _stop.is_set():
            _beat["restarts"] += 1
            events.emit(store, "kritisch", "neustart", "Hintergrund-Schleife war beendet – neu gestartet",
                        f"Neustart Nr. {_beat['restarts']} seit Prozessstart.", key="loop-tot", agent="guardian", dedupe_h=1)
            _start_loop()
        hang = time.time() - _beat["start"] if _beat["start"] else 0
        if hang > config.SELF_RESTART_MIN * 60 and time.time() - float(store.kv_get("selfrestart_at") or 0) > 3 * 3600:
            store.kv_set("selfrestart_at", time.time())
            events.emit(store, "kritisch", "neustart", "Takt hängt – Prozess wird neu gestartet",
                        f"Ein Takt läuft seit {hang / 60:.0f} min. Der Dienst startet sich jetzt selbst neu.", key="selbst-neustart", agent="guardian")
            store.log(f"Wachhund: Takt hängt seit {hang / 60:.0f} min – Selbst-Neustart", "error")
            time.sleep(3)  # Telegram-Versand abwarten
            os.kill(os.getpid(), signal.SIGTERM)
        if age > limit and not alerted and time.time() - started > limit:
            alerted = True
            msg = f"Seit {age / 60:.0f} min kein fertiger Lauf" + (f". Letzter Fehler: {engine.state['last_error']}"
                                                                    if engine.state.get("last_error") else "")
            store.log(f"Wachhund: {msg}", "error")
            events.emit(store, "kritisch", "stillstand", "Polymarket-Bot hängt", msg, key="stillstand", agent="guardian", dedupe_h=2)
        elif alerted and age < limit:
            alerted = False
            store.log("Wachhund: läuft wieder")
            events.emit(store, "hoch", "wiederherstellung", "Polymarket-Bot läuft wieder", agent="guardian")


def _shutdown():
    _stop.set()
    try:
        sources.flush(store)
        store.x("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001
        pass


def _auto_start():
    global _started
    with _start_lock:
        if _started or not config.AUTOSTART:
            return
        _started = True
        if not _single_loop():
            log.warning("Ein anderer Prozess fährt die Schleife bereits – dieser Prozess bedient nur das Dashboard.")
            return
        atexit.register(_shutdown)
        _start_loop()
        threading.Thread(target=_watchdog, name="polybot-watchdog", daemon=True).start()


_auto_start()  # muss beim Import laufen, damit es auch unter Gunicorn startet


# ---------------------------------------------------------------- Hilfen
def _iso(dt=None):
    return (dt or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _int(name, default, lo, hi):
    try:
        return max(lo, min(hi, int(request.args.get(name, default))))
    except (TypeError, ValueError):
        return default


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _system_state():
    st = engine.state
    last = parse_dt(st.get("last_scan"))
    age = (datetime.now(timezone.utc) - last).total_seconds() if last else None
    agents = coordinator.registry.view()
    bad = [a["name"] for a in agents if a["state"] == "fehler"]
    if st.get("last_error") or (age is not None and age > config.WATCHDOG_MIN * 60):
        status = "fehler"
    elif bad or any(s["paused_s"] > 0 for s in sources.snapshot()):
        status = "warnung"
    else:
        status = "ok" if last else "startet"
    return {"status": status, "last_cycle": st.get("last_scan"), "age_s": round(age) if age is not None else None,
            "running": bool(st.get("running")), "cycle_duration": st.get("duration"), "last_error": st.get("last_error"),
            "interval_min": config.SCAN_INTERVAL_MIN, "uptime_s": round(time.time() - STARTED), "cycles": _beat["cycles"],
            "agents_failing": bad, "loop_alive": bool(_beat["thread"] and _beat["thread"].is_alive()) if config.AUTOSTART else None}


def _paper_data():
    st = engine.state
    return {
        "now": _iso(),
        "stats": engine.stats(),
        "accounts": engine.accounts_overview(),
        "state": {k: st.get(k) for k in ("last_scan", "last_error", "running", "duration", "markets_seen",
                                          "skips", "opportunities", "scans", "arbs", "weights", "paused")},
        "sources": engine.status(),
        "open": store.open_positions(),
        "closed": store.closed_positions(8000),
        "orders": store.recent_orders(300),
        "equity": {a["id"]: store.equity_history(a["id"]) for a in config.ACCOUNTS},
        "logs": store.recent_log(200),
        "registry": {r["category"]: {"model": r["model"], "version": r["version"], "mix_w": r["mix_w"]} for r in coordinator.probability.reg.values()},
        "cfg": {
            "start": config.START_BALANCE, "interval": config.SCAN_INTERVAL_MIN,
            "min_edge_crypto": config.MIN_EDGE_CRYPTO, "min_edge_sports": config.MIN_EDGE_SPORTS,
            "min_edge_weather": config.MIN_EDGE_WEATHER, "calib_min_n": config.CALIB_MIN_N,
            "stake_mode": config.STAKE_MODE, "stake_min": config.STAKE_MIN_PCT, "stake_max": config.STAKE_MAX_PCT,
            "max_event": config.MAX_EVENT_PCT, "max_group": config.MAX_GROUP_PCT, "max_exposure": config.MAX_EXPOSURE_PCT,
            "leagues": config.SPORTS_LEAGUES,
        },
    }


# ---------------------------------------------------------------- Seiten
@app.route("/")
def index():
    return render_template("index.html", version=version.VERSION, refresh=config.DASH_REFRESH_SEC)


@app.route("/paper")
def paper():
    return render_template("paper.html", data=_paper_data(), version=version.VERSION)


@app.route("/favicon.ico")
def favicon():
    return app.send_static_file("icon.svg")


@app.route("/health")
def health():
    # bewusst ohne Fehlertexte: dieser Pfad ist ohne Anmeldung erreichbar
    last = parse_dt(engine.state.get("last_scan"))
    return jsonify(ok=True, last_scan=engine.state["last_scan"],
                   age_s=round((datetime.now(timezone.utc) - last).total_seconds()) if last else None)


# ---------------------------------------------------------------- API: Lage
@app.route("/api/state")
def api_state():
    return jsonify(_paper_data())


@app.route("/api/overview")
def api_overview():
    summ = coordinator.learning.summary() or {}
    reg = coordinator.probability.reg
    quality = []
    for m in summ.get("bereiche") or []:   # alle Prognosen, nach denen entschieden wurde – nicht nur die des gerade aktiven Modells
        row = {k: m.get(k) for k in ("category", "n", "clusters", "brier", "brier_market", "logloss", "logloss_market", "skill", "z", "hit", "bias", "alt")}
        r = reg.get(m["category"])
        row["model"] = f"{r['model']}@{r['version']}" if r else None
        quality.append(row)
    day = _iso(datetime.now(timezone.utc) - timedelta(hours=24))
    movers = store.q("SELECT id, question, category, price, move_1h, volume24h FROM markets WHERE status='open' AND move_1h IS NOT NULL "
                     "AND liquidity>=2000 AND price BETWEEN 0.03 AND 0.97 ORDER BY ABS(move_1h) DESC LIMIT 6")
    return jsonify({
        "now": _iso(), "version": version.VERSION, "system": _system_state(), "counts": coordinator.counts(),
        "quality": quality, "quality_stand": summ.get("stand"),
        "learning": {"n": summ.get("n"), "stand": summ.get("stand"), "experimente": store.scalar("SELECT COUNT(*) FROM experiments"),
                     "bestanden": store.scalar("SELECT COUNT(*) FROM experiments WHERE passed=1"),
                     "offene_vorschläge": store.scalar("SELECT COUNT(*) FROM proposals WHERE status='offen'"),
                     "modellversionen": store.scalar("SELECT COUNT(*) FROM model_versions"),
                     "datensatz": store.scalar("SELECT COUNT(*) FROM resolved")},
        "activity": {"movers": [m for m in movers if abs(m["move_1h"]) >= 0.02],
                     "neu_24h": store.scalar("SELECT COUNT(*) FROM markets WHERE first_seen>=?", (day,)),
                     "volumen_24h": store.scalar("SELECT SUM(volume24h) FROM markets WHERE status='open'"),
                     "prognosen_24h": store.scalar("SELECT COUNT(*) FROM forecasts WHERE created_at>=? AND champion=1", (day,)),
                     "abgelehnt_24h": store.scalar("SELECT COUNT(*) FROM forecasts WHERE created_at>=? AND champion=1 AND valid=0", (day,))},
        "events": events.recent(store, 14),
        "warnings": events.recent(store, 8, min_level="hoch", hours=48),
        "resources": coordinator.guardian.latest or None,
        "agents": [{k: a[k] for k in ("id", "name", "state", "last_end", "task")} for a in coordinator.registry.view()],
        "accounts": [{k: a[k] for k in ("id", "name", "equity", "pnl_total", "open_count", "paused")} for a in engine.accounts_overview()],
        "tick": coordinator.last_tick or None,
        "refresh": config.DASH_REFRESH_SEC,
    })


MARKET_SORT = {"priority": "priority", "volume24h": "volume24h", "liquidity": "liquidity", "move_1h": "ABS(move_1h)", "move_24h": "ABS(move_24h)",
               "end_date": "end_date", "price": "price", "last_seen": "last_seen", "first_seen": "first_seen", "news": "news_48h"}


@app.route("/api/markets")
def api_markets():
    cond, args = [], []
    q = (request.args.get("q") or "").strip()[:80]
    if q:
        cond.append("question LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    cat = request.args.get("cat")
    if cat and cat != "all":
        cond.append("category=?")
        args.append(cat[:20])
    status = request.args.get("status", "open")
    if status in ("open", "closed", "unbeobachtet"):
        cond.append("status=?")
        args.append(status)
    if request.args.get("modeled") == "1":
        cond.append("modeled=1")
    flag = request.args.get("flag")
    if flag == "ok":
        cond.append("(quality IS NULL OR quality='')")
    elif flag == "mangel":
        cond.append("quality!=''")
    elif flag == "news":
        cond.append("news_48h>0")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    sort = MARKET_SORT.get(request.args.get("sort"), "priority")
    direction = "ASC" if request.args.get("dir") == "asc" else "DESC"
    per, page = _int("per", 50, 10, 200), _int("page", 1, 1, 100000)
    total = store.scalar(f"SELECT COUNT(*) FROM markets{where}", args)
    rows = store.q(f"SELECT id, question, category, price, bid, ask, liquidity, volume24h, move_1h, move_24h, quality, priority, modeled, status, "
                   f"end_date, last_seen, first_seen, news_48h FROM markets{where} ORDER BY {sort} IS NULL, {sort} {direction}, id LIMIT ? OFFSET ?",
                   args + [per, (page - 1) * per])
    cats = store.q("SELECT category, COUNT(*) n FROM markets WHERE status='open' GROUP BY category ORDER BY n DESC")
    return jsonify(total=total, page=page, per=per, rows=rows, categories=cats, names=CATEGORY_NAMES,
                   catalog=coordinator.scanner.stats(), last_sweep=coordinator.registry.status["scanner"]["last_ok"])


@app.route("/api/market/<mid>")
def api_market(mid):
    m = store.q1("SELECT * FROM markets WHERE id=?", (mid,))
    fcs = store.q("SELECT id, model, version, horizon, created_at, p, market_p, uncertainty, hours_left, champion, valid, reject_reason, cluster, "
                  "sources, features FROM forecasts WHERE market_id=? ORDER BY id", (mid,))
    if not m and not fcs:
        return jsonify(error="Markt nicht im Katalog"), 404
    for f in fcs:
        for k in ("sources", "features"):
            try:
                f[k] = json.loads(f[k]) if f[k] else None
            except ValueError:
                f[k] = None
    news = store.q("SELECT n.title, n.url, n.source, n.published, n.fetched_at, n.flags, l.score FROM news_links l JOIN news n ON n.id=l.news_id "
                   "WHERE l.market_id=? ORDER BY COALESCE(n.published, n.fetched_at) DESC LIMIT 12", (mid,))
    pos = store.q("SELECT account, side_name, price, cost, status, pnl, opened_at, closed_at FROM positions WHERE market_id=? ORDER BY id", (mid,))
    names = {a["id"]: a["name"] for a in config.ACCOUNTS}
    for p in pos:
        p["account"] = names.get(p["account"], p["account"])
    live = next((o for o in engine.state.get("opportunities") or [] if o["id"] == mid), None)
    return jsonify(market=m, history=store.q("SELECT ts, price, volume24h FROM snapshots WHERE market_id=? ORDER BY ts", (mid,)),
                   forecasts=fcs, outcome=store.q1("SELECT outcome, resolved_at, source FROM outcomes WHERE market_id=?", (mid,)),
                   news=news, positions=pos, live=live, names=CATEGORY_NAMES)


@app.route("/api/predictions")
def api_predictions():
    cond, args = ["f.champion=1"], []
    cat = request.args.get("cat")
    if cat and cat != "all":
        cond.append("f.category=?")
        args.append(cat[:20])
    status = request.args.get("status", "all")
    if status == "offen":
        cond.append("o.outcome IS NULL")
    elif status == "entschieden":
        cond.append("o.outcome IS NOT NULL")
    elif status == "abgelehnt":
        cond.append("f.valid=0")
    q = (request.args.get("q") or "").strip()[:80]
    if q:
        cond.append("m.question LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    where = " WHERE " + " AND ".join(cond)
    base = "FROM forecasts f LEFT JOIN markets m ON m.id=f.market_id LEFT JOIN outcomes o ON o.market_id=f.market_id"
    per, page = _int("per", 50, 10, 200), _int("page", 1, 1, 100000)
    order = "ABS(f.p - o.outcome) DESC, f.id DESC" if request.args.get("sort") == "fehler" and status == "entschieden" else \
        "ABS(f.p - f.market_p) DESC, f.id DESC" if request.args.get("sort") == "abstand" else "f.id DESC"
    rows = store.q(f"SELECT f.id, f.market_id, f.category, f.model, f.version, f.horizon, f.created_at, f.p, f.market_p, f.uncertainty, f.valid, "
                   f"f.reject_reason, m.question, m.end_date, o.outcome {base}{where} ORDER BY {order} LIMIT ? OFFSET ?", args + [per, (page - 1) * per])
    return jsonify(total=store.scalar(f"SELECT COUNT(*) {base}{where}", args), page=page, per=per, rows=rows,
                   counts={"gesamt": store.scalar("SELECT COUNT(*) FROM forecasts WHERE champion=1"),
                           "varianten": store.scalar("SELECT COUNT(*) FROM forecasts"),
                           "abgelehnt": store.scalar("SELECT COUNT(*) FROM forecasts WHERE champion=1 AND valid=0"),
                           "entschieden": store.scalar("SELECT COUNT(*) FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id WHERE f.champion=1")})


# ---------------------------------------------------------------- API: Intelligenz
@app.route("/api/agents")
def api_agents():
    runs = store.q("SELECT agent, started_at, duration, ok, summary, error FROM agent_runs ORDER BY id DESC LIMIT 60")
    return jsonify(agents=coordinator.registry.view(), tick=coordinator.last_tick or None, contradictions=coordinator.contradictions,
                   validation={"letzter_lauf": coordinator.validation.cycle, "seit_start": dict(coordinator.validation.total)},
                   runs=runs, system=_system_state())


@app.route("/api/learning")
def api_learning():
    return jsonify(lernen=coordinator.learning.view(), modelle=coordinator.probability.view(),
                   kriterien={"min_oos": config.EXPERIMENT_MIN_OOS, "min_cluster": config.EXPERIMENT_MIN_CLUSTERS,
                              "min_gewinn": config.EXPERIMENT_MIN_GAIN, "min_z": config.EXPERIMENT_MIN_Z},
                   last_run=coordinator.registry.status["learning"]["last_ok"])


@app.route("/api/proposal/<int:pid>/<action>", methods=["POST"])
def api_proposal(pid, action):
    if action not in ("approve", "reject"):
        return jsonify(error="unbekannte Aktion"), 404
    ok, msg = coordinator.learning.decide(pid, action == "approve", _json_body().get("note", ""))
    return (jsonify(ok=True), 200) if ok else (jsonify(error=msg), 409)


@app.route("/api/model/rollback", methods=["POST"])
def api_rollback():
    cat = str(_json_body().get("category", ""))
    if cat not in coordinator.probability.reg:
        return jsonify(error="unbekannter Bereich"), 404
    if not coordinator.probability.rollback(cat, "im Dashboard zurückgeholt"):
        return jsonify(error="Es gibt keine frühere Version"), 409
    events.emit(store, "normal", "modell", f"Modell zurückgeholt: {cat}", "Im Dashboard ausgelöst.", agent="learning", telegram=False)
    return jsonify(ok=True)


@app.route("/api/strategy")
def api_strategy():
    return jsonify(**coordinator.strategy.view(), names=CATEGORY_NAMES, last_run=coordinator.registry.status["strategy"]["last_ok"])


@app.route("/api/performance")
def api_performance():
    summ = coordinator.learning.summary() or {}
    quality = store.q("SELECT substr(created_at, 1, 10) tag, category, COUNT(*) n, SUM(valid) gueltig FROM forecasts WHERE champion=1 "
                      "GROUP BY tag, category ORDER BY tag")
    return jsonify(stand=summ.get("stand"), n=summ.get("n", 0), modelle=summ.get("modelle") or [], bereiche=summ.get("bereiche") or [],
                   verlauf=summ.get("verlauf") or [],
                   kalibrierung=summ.get("kalibrierung") or {}, fehler=summ.get("fehler") or {}, datenqualitaet=quality,
                   versionen=store.q("SELECT * FROM model_log ORDER BY id"))


# ---------------------------------------------------------------- API: Betrieb
@app.route("/api/data")
def api_data():
    since = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%d")
    w = weather_model.status
    return jsonify(
        sources=sources.snapshot(),
        history=store.q("SELECT source, day, calls, errors, limited, blocked, units FROM source_stats WHERE day>=? ORDER BY day", (since,)),
        weather={"stationen": len(w), "ok": sum(1 for v in w.values() if str(v).startswith("OK")),
                 "fehler": {k: str(v)[:120] for k, v in w.items() if not str(v).startswith("OK")}},
        engine=engine.status(), catalog=coordinator.scanner.stats(), news=coordinator.news.stats(),
        feeds=[name for name, _ in config.NEWS_FEEDS],
        gaps={"prognosen_ohne_quelle": store.scalar("SELECT COUNT(*) FROM forecasts WHERE champion=1 AND sources IS NULL"),
              "märkte_ohne_orderbuch": store.scalar("SELECT COUNT(*) FROM markets WHERE status='open' AND quality LIKE '%kein_orderbuch%'"),
              "märkte_ohne_ende": store.scalar("SELECT COUNT(*) FROM markets WHERE status='open' AND quality LIKE '%kein_ende%'"),
              "märkte_offen": store.scalar("SELECT COUNT(*) FROM markets WHERE status='open'"),
              "übersprungen": engine.state.get("skips") or {}})


TABLES = ["markets", "snapshots", "forecasts", "outcomes", "positions", "orders", "predictions", "resolved", "backtests", "experiments",
          "proposals", "news", "events", "agent_runs", "reports", "log"]


@app.route("/api/health")
def api_health():
    env = {k: os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_SERVICE_NAME", "RAILWAY_REPLICA_REGION", "RAILWAY_DEPLOYMENT_ID",
                                          "RAILWAY_GIT_COMMIT_SHA", "RAILWAY_GIT_BRANCH", "RAILWAY_VOLUME_MOUNT_PATH") if os.environ.get(k)}
    if env.get("RAILWAY_GIT_COMMIT_SHA"):
        env["RAILWAY_GIT_COMMIT_SHA"] = env["RAILWAY_GIT_COMMIT_SHA"][:10]
    return jsonify(system=_system_state(), guardian=coordinator.guardian.view(),
                   db={"tabellen": {t: store.scalar(f"SELECT COUNT(*) FROM {t}") for t in TABLES}, "schema": store.schema_version,
                       "wal": store.scalar("PRAGMA journal_mode")},
                   threads=sorted(t.name for t in threading.enumerate()),
                   loop={"cycles": _beat["cycles"], "restarts": _beat["restarts"], "running_since": _beat["start"], "last_end": _beat["end"],
                         "self_restart_min": config.SELF_RESTART_MIN, "watchdog_min": config.WATCHDOG_MIN, "budget_sec": config.CYCLE_BUDGET_SEC},
                   errors=[l for l in store.recent_log(400) if l["level"] == "error"][:40],
                   events=events.recent(store, 40, min_level="hoch"),
                   telegram={"aktiv": notify.enabled(), "status": notify.last_status, **notify.stats},
                   railway=env or None, auth=AUTH_AKTIV, version=version.VERSION, python=sys.version.split()[0])


@app.route("/api/events")
def api_events():
    level = request.args.get("level")
    return jsonify(rows=events.recent(store, _int("limit", 100, 10, 500), min_level=level if level in settings.LEVELS else None))


@app.route("/api/reports")
def api_reports():
    return jsonify(reports=store.q("SELECT id, created_at, kind, period_from, period_to, title, status, note FROM reports ORDER BY id DESC LIMIT 60"),
                   recommendations=store.q("SELECT * FROM recommendations ORDER BY (status='offen') DESC, id DESC LIMIT 200"),
                   plan={"tage": config.REPORT_DAYS, "stunde": config.REPORT_HOUR})


@app.route("/api/report/<int:rid>", methods=["GET", "POST"])
def api_report(rid):
    r = store.q1("SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        return jsonify(error="Bericht nicht gefunden"), 404
    if request.method == "POST":
        body = _json_body()
        status = body.get("status", r["status"])
        if status not in ("offen", "besprochen", "abgeschlossen"):
            return jsonify(error="unbekannter Status"), 400
        store.x("UPDATE reports SET status=?, note=? WHERE id=?", (status, str(body.get("note", r["note"] or ""))[:4000], rid))
        return jsonify(ok=True)
    r.pop("data", None)
    return jsonify(report=r, recommendations=store.q("SELECT * FROM recommendations WHERE report_id=? ORDER BY id", (rid,)))


_report_lock = threading.Lock()


@app.route("/api/reports/generate", methods=["POST"])
def api_report_generate():
    last = store.q1("SELECT created_at FROM reports ORDER BY id DESC LIMIT 1")
    if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last["created_at"])).total_seconds() < 600:
        return jsonify(error="Der letzte Bericht ist keine 10 Minuten alt."), 429
    if not _report_lock.acquire(blocking=False):
        return jsonify(error="Ein Bericht wird gerade erstellt."), 429
    try:
        rid = reports.generate(coordinator, kind="manuell")
    finally:
        _report_lock.release()
    return jsonify(ok=True, id=rid)


@app.route("/api/recommendation/<int:rid>", methods=["POST"])
def api_recommendation(rid):
    body = _json_body()
    status = body.get("status")
    if status not in ("offen", "erledigt", "verworfen"):
        return jsonify(error="unbekannter Status"), 400
    cur = store.x("UPDATE recommendations SET status=?, note=?, updated_at=? WHERE id=?", (status, str(body.get("note", ""))[:1000], _iso(), rid))
    return (jsonify(ok=True), 200) if cur.rowcount else (jsonify(error="nicht gefunden"), 404)


@app.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    if request.method == "POST":
        try:
            done = coordinator.change_settings(_json_body())
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True, changed=[{"key": k, "old": o, "new": n} for k, o, n in done])
    spec = [{"key": k, "type": t, "min": lo if t not in ("choice", "bool") else None, "max": hi, "choices": lo if t == "choice" else None,
             "group": g, "desc": d, "value": getattr(config, k)} for k, (t, lo, hi, g, d) in settings.SPEC.items()]
    fixed = {"Startguthaben je Konto": f"{config.START_BALANCE:.0f} $", "Konten": len(config.ACCOUNTS),
             "Mindest-Vorsprung Krypto / Sport / Wetter": f"{config.MIN_EDGE_CRYPTO * 100:.0f} / {config.MIN_EDGE_SPORTS * 100:.0f} / {config.MIN_EDGE_WEATHER * 100:.0f} Punkte",
             "Einsatz": f"{config.STAKE_MIN_PCT * 100:.0f}–{config.STAKE_MAX_PCT * 100:.0f} % des Kontos",
             "Risikobremse": f"−{config.RISK_DAY_LOSS_PCT * 100:.0f} % am Tag, −{config.RISK_TOTAL_LOSS_PCT * 100:.0f} % gesamt",
             "Ligen": ", ".join(config.SPORTS_LEAGUES), "Berichtstage": "Mittwoch und Sonntag" if config.REPORT_DAYS == [2, 6] else str(config.REPORT_DAYS),
             "Sicherungen": f"täglich, die letzten {config.BACKUP_KEEP}",
             "Aufbewahrung": f"Preisverlauf stündlich {config.RETAIN_SNAPSHOT_DAYS} Tage (danach täglich), Ereignisse {config.RETAIN_EVENT_DAYS} Tage"}
    return jsonify(spec=spec, fixed=fixed, models=coordinator.probability.view()["aktiv"],
                   sources=[{"id": s["id"], "name": s["name"], "use": s["use"], "limit": s["limit"], "day_budget": s["day_budget"]} for s in sources.snapshot()],
                   secrets={"Telegram": notify.enabled(), "Dashboard-Anmeldung": AUTH_AKTIV, "Buchmacher-Quoten (The Odds API)": bool(engine.odds.enabled)})


@app.route("/api/account/<aid>/resume", methods=["POST"])
def api_resume(aid):
    if aid not in {a["id"] for a in config.ACCOUNTS}:
        return jsonify(error="unbekanntes Konto"), 404
    engine.resume(aid)
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
