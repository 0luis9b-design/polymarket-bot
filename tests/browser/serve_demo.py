"""Startet die Zentrale mit erfundenen Beispiel-Zeilen in einem Wegwerf-Verzeichnis – nur für die Browser-Tests.

Aufruf: python tests/browser/serve_demo.py <verzeichnis> <port>. Läuft nie auf Railway (keine Hintergrund-Schleife, kein Netz)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

os.environ.update(AUTOSTART="0", DATA_DIR=sys.argv[1], DASHBOARD_USER="", DASHBOARD_PASSWORD="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import app as A  # noqa: E402
from tests.test_agents import _m, _o  # noqa: E402

st, co = A.store, A.coordinator
now = datetime.now(timezone.utc).replace(microsecond=0)
ms = [_m("w1", cat="weather", question="Will the highest temperature in London be 12°C on November 15?", end=now + timedelta(hours=30)),
      _m("c1", cat="crypto", bid=0.10, ask=0.12, question="Will the price of Bitcoin be above $100,000 on November 20?", end=now + timedelta(hours=30))]
for h in (3, 2, 1, 0):
    co.scanner.ingest(ms, now - timedelta(hours=h))
co.on_results([_o(ms[0], 0.45, {"weather.ens@2": 0.45}, model_id="weather.ens@2"), _o(ms[1], 0.30, {"crypto.options@1": 0.30}, model_id="crypto.options@1")], now)
st.add_outcome("w1", 1.0)
KINDS = [("sports", "Fußball", "Champions League", "Will Bayern win?", "haupt_sport", "model"),
         ("politics", "USA", "Allgemein", "Will the bill pass by Friday?", "schwelle4_politik", "base"),
         ("weather", "Europa", "London", "Will the highest temperature in London be 12°C on November 15?", "haupt_wetter", "model"),
         ("crypto", "Bitcoin", "Kurs am Stichtag", "Will the price of Bitcoin be above $100,000 on November 20?", "haupt_krypto", "model")]
for i in range(24):
    cat, sub, det, q, acc, lane = KINDS[i % 4]
    opened = now - timedelta(days=i * 0.7, hours=2)
    done = i % 3 != 0
    status = ("won" if i % 2 else "lost") if done else "open"
    st.insert_position({"account": acc, "market_id": "w1" if cat == "weather" else f"x{i}", "category": cat, "sub": sub, "detail": det,
                        "question": q, "side": i % 2, "side_name": ["Yes", "No"][i % 2], "price": 0.4, "shares": 25, "cost": 10, "fee": 0.1,
                        "own_p": 0.55, "market_p": 0.4, "p_mid": 0.39, "edge": 0.15, "status": status, "pnl": (15 if status == "won" else -10) if done else None,
                        "opened_at": opened.isoformat(timespec="seconds"), "closed_at": (opened + timedelta(hours=20)).isoformat(timespec="seconds") if done else None,
                        "end_date": (opened + timedelta(hours=24)).isoformat(timespec="seconds"), "kind": "model", "lane": lane, "mark": 0.45,
                        "entry": json.dumps({"p_unser_ausgang": 0.55, "begruendung": "Beispiel", "pruefung": [["wert", True]]})})
from polybot import hindsight  # noqa: E402
for i in range(25):   # Rückblick (2.8): ein verlierender und ein gewinnender Bereich
    for mid, p, mkt, out in ((f"hn{i}", 0.25, 0.45, 1 if i % 5 else 0), (f"hy{i}", 0.55, 0.50, 1 if i % 3 else 0)):
        st.x("INSERT INTO markets(id, question, category) VALUES(?,?,?)", (mid, f"Will the highest temperature in City{i} be 13°C on November 20?", "weather"))
        st.x("INSERT INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, champion, valid, cluster) "
             "VALUES(?,?,?,?,?,?,?,?,1,1,?)", (mid, "weather", "weather.live", "1", "6-24h", (now - timedelta(days=2)).isoformat(), p, mkt, f"{mid}c"))
        st.add_outcome(mid, float(out))
hindsight.compute(st, now)
A.engine.state["paused"]["kelly_sport"] = "−8 % heute – pausiert bis morgen"   # damit die Liste „Pausierte Konten“ sichtbar ist
A.app.run(host="127.0.0.1", port=int(sys.argv[2]), use_reloader=False)
