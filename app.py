"""Polymarket-Spielgeld-Bot – Webserver + Hintergrund-Schleife.

Start lokal:   python app.py
Railway:       gunicorn app:app (siehe railway.json) – die Schleife startet beim Import.
"""
import logging
import os
import threading
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request

from polybot import config
from polybot.engine import Engine
from polybot.store import Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("polybot")

app = Flask(__name__)
store = Store(os.path.join(config.DATA_DIR, "polybot.sqlite"))
engine = Engine(store)
_started = False
_start_lock = threading.Lock()


def _loop():
    time.sleep(3)
    store.log("Bot gestartet (Spielgeld). Intervall: %.0f min" % config.SCAN_INTERVAL_MIN)
    while True:
        engine.run_cycle()
        time.sleep(config.SCAN_INTERVAL_MIN * 60)


def _auto_start():
    global _started
    with _start_lock:
        if _started or not config.AUTOSTART:
            return
        _started = True
        threading.Thread(target=_loop, name="polybot-loop", daemon=True).start()


_auto_start()  # muss beim Import laufen, damit es auch unter Gunicorn startet


def _pct(v, digits=1):
    return "–" if v is None else f"{v * 100:.{digits}f} %".replace(".", ",")


def _usd(v):
    return "–" if v is None else f"{v:,.2f} $".replace(",", "X").replace(".", ",").replace("X", ".")


def _susd(v):
    return "–" if v is None else ("+" if v >= 0 else "−") + _usd(abs(v))


def _cent(v):
    return "–" if v is None else f"{v * 100:.1f} ¢".replace(".", ",")


def _dt(v):
    if not v:
        return "–"
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return str(v)
    from zoneinfo import ZoneInfo
    return d.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m. %H:%M")


app.jinja_env.filters.update(pct=_pct, usd=_usd, susd=_susd, cent=_cent, dt=_dt)


def _dashboard_data():
    st = engine.state
    return {
        "now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": engine.stats(),
        "state": {k: st.get(k) for k in ("last_scan", "last_error", "running", "duration", "markets_seen",
                                          "skips", "opportunities", "scans")},
        "sources": engine.status(),
        "open": store.open_positions(),
        "closed": store.closed_positions(5000),
        "equity": store.equity_history(),
        "logs": store.recent_log(150),
        "cfg": {
            "start": config.START_BALANCE, "interval": config.SCAN_INTERVAL_MIN,
            "min_edge_crypto": config.MIN_EDGE_CRYPTO, "min_edge_sports": config.MIN_EDGE_SPORTS,
            "stake_mode": config.STAKE_MODE, "stake_min": config.STAKE_MIN_PCT, "stake_max": config.STAKE_MAX_PCT,
            "max_event": config.MAX_EVENT_PCT, "max_exposure": config.MAX_EXPOSURE_PCT,
            "leagues": config.SPORTS_LEAGUES,
        },
    }


@app.route("/")
def index():
    return render_template("index.html", data=_dashboard_data())


@app.route("/api/state")
def api_state():
    return jsonify(_dashboard_data())


@app.route("/api/scan", methods=["POST"])
def api_scan():
    if request.args.get("key") != os.environ.get("SCAN_KEY", "") or not os.environ.get("SCAN_KEY"):
        return jsonify(error="SCAN_KEY fehlt oder falsch"), 403
    threading.Thread(target=engine.run_cycle, daemon=True).start()
    return jsonify(ok=True)


@app.route("/health")
def health():
    return jsonify(ok=True, last_scan=engine.state["last_scan"], error=engine.state["last_error"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
