"""Rückblick: Hätte eine Wette in diesem Bereich Geld gebracht?

Warum (Bericht #4, 22.11.): Die Wetter- und Krypto-Prognosen waren im Schnitt genauer als der Marktpreis, die Wetten
darauf haben trotzdem deutlich verloren (Wetter: 19 % gewonnen bei 40 % Preis, n = 298). Gewettet wird nur dort, wo
Modell und Markt weit auseinanderliegen – und genau dort lag meist der Markt richtig (z. B. „Nein“ auf den Temperatur-
Bereich, den der Markt für den wahrscheinlichsten hält). Ein guter Durchschnitt sagt darüber nichts.

Was passiert: Für jede entschiedene Prognose der gehandelten Variante (letzte HINDSIGHT_DAYS Tage, nur gültige) wird
nachgerechnet, wie eine Wette auf die Seite des Modells zum damaligen Marktpreis ausgegangen wäre:
Gewinn je Anteil = gewonnen (1/0) − Preis. Gruppiert nach Thema · Wettart · Seite · Abweichung. Die Sicherheit wird über
unabhängige Ereignisse gerechnet (11 Temperatur-Bereiche eines Tages zählen als ein Ereignis), nicht über Einzelzeilen.

Gesperrt wird ein Bereich nur, wenn dort genug Ereignisse vorliegen und der Verlust statistisch gesichert ist. Es
entscheidet der genaueste Bereich mit genug Daten (erst mit Wettart, sonst das ganze Thema). Die Prognosen werden auch
ohne Wetten weiter aufgezeichnet; wird ein Bereich besser, ist er beim nächsten Rückblick von selbst wieder offen.
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import config, taxonomy

KV = "learn:rueckblick"
# Abweichung der gewählten Seite vom Marktpreis (Punkte)
BUCKETS = ((0.03, 0.08, "3–8 Pkt"), (0.08, 0.15, "8–15 Pkt"), (0.15, 0.25, "15–25 Pkt"), (0.25, 9.0, "über 25 Pkt"))
ALL = "alle Wettarten"


def bucket(diff):
    for lo, hi, name in BUCKETS:
        if lo <= diff < hi:
            return name
    return None


def _z(by_cluster):
    """Mittelwert, z-Wert und Zahl der Ereignisse; negativ = Wetten hätten verloren."""
    means = [sum(v) / len(v) for v in by_cluster.values()]
    n = len(means)
    if n < 2:
        return (means[0] if means else 0.0), None, n
    mean = sum(means) / n
    sd = statistics.stdev(means)
    if sd == 0:
        return mean, (0.0 if mean == 0 else math.copysign(99.0, mean)), n
    return mean, mean / (sd / math.sqrt(n)), n


def compute(store, now=None):
    """Rechnet den Rückblick und legt ihn in kv ab. Gibt die Tabelle zurück."""
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=config.HINDSIGHT_DAYS)).isoformat(timespec="seconds")
    rows = store.q(
        "SELECT f.market_id, f.category, f.p, f.market_p, f.cluster, o.outcome, m.question, m.detail "
        "FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id LEFT JOIN markets m ON m.id=f.market_id "
        "WHERE f.champion=1 AND f.valid=1 AND f.market_p IS NOT NULL AND f.created_at>=?", (since,))
    cells = defaultdict(lambda: {"gain": defaultdict(list), "n": 0, "won": 0, "price": 0.0})
    for r in rows:
        d = r["p"] - r["market_p"]
        if abs(d) < config.HINDSIGHT_MIN_DIFF or r["outcome"] not in (0, 1, 0.0, 1.0):
            continue
        yes = d > 0
        price = r["market_p"] if yes else 1 - r["market_p"]
        won = r["outcome"] if yes else 1 - r["outcome"]
        b = bucket(abs(d))
        bt = taxonomy.bet_type(r["question"], r["category"], r["detail"]) if r["question"] else None
        cl = r["cluster"] or r["market_id"]
        for key in ((r["category"], bt, "Ja" if yes else "Nein", b), (r["category"], ALL, "Ja" if yes else "Nein", b)):
            if key[1] is None:
                continue
            c = cells[key]
            c["gain"][cl].append(won - price)
            c["n"] += 1
            c["won"] += won
            c["price"] += price
    out = []
    for (cat, bt, side, b), c in cells.items():
        mean, z, clusters = _z(c["gain"])
        blocked = bool(clusters >= config.HINDSIGHT_MIN_CLUSTERS and z is not None and z <= -config.HINDSIGHT_Z and mean < 0)
        out.append({"thema": cat, "wettart": bt, "seite": side, "abweichung": b, "n": c["n"], "ereignisse": clusters,
                    "gewonnen": c["won"] / c["n"], "preis": c["price"] / c["n"], "gewinn_je_anteil": mean,
                    "rendite": (c["won"] - c["price"]) / c["price"] if c["price"] else None,
                    "z": z, "gesperrt": blocked})
    out.sort(key=lambda x: (x["thema"], x["wettart"] != ALL, x["wettart"], x["seite"], [b[2] for b in BUCKETS].index(x["abweichung"])))
    res = {"stand": now.isoformat(timespec="seconds"), "tage": config.HINDSIGHT_DAYS, "prognosen": len(rows), "bereiche": out}
    store.kv_set(KV, json.dumps(res, ensure_ascii=False))
    if not store.kv_get("rueckblick:seit"):
        store.kv_set("rueckblick:seit", res["stand"])   # ab hier gelten die neuen Regeln (für die Berichts-Empfehlungen)
    return res


def load(store):
    return store.kv_json(KV, None)


class Table:
    """Schneller Nachschlag für die Engine: Ist diese Wette in einem gesperrten Bereich?"""

    def __init__(self, data):
        self.stand = (data or {}).get("stand")
        self.cells = {(c["thema"], c["wettart"], c["seite"], c["abweichung"]): c for c in (data or {}).get("bereiche", [])}

    def lookup(self, category, bet_type, side_yes, diff):
        """Genauester Bereich mit genug Ereignissen – oder None."""
        b = bucket(diff)
        if not b:
            return None
        side = "Ja" if side_yes else "Nein"
        for bt in (bet_type, ALL):
            c = self.cells.get((category, bt, side, b))
            if c and c["ereignisse"] >= config.HINDSIGHT_MIN_CLUSTERS:
                return c
        return None

    def block(self, category, bet_type, side_yes, diff):
        """Grund als Text, wenn gesperrt – sonst None."""
        if not config.HINDSIGHT:
            return None
        c = self.lookup(category, bet_type, side_yes, diff)
        if not c:
            return None
        # z erneut gegen die aktuelle Einstellung prüfen (Schwelle im Dashboard änderbar)
        if c["z"] is None or c["z"] > -config.HINDSIGHT_Z or c["gewinn_je_anteil"] >= 0:
            return None
        art = "" if c["wettart"] == ALL else f" · {c['wettart']}"
        return (f"Rückblick: „{c['seite']}“ mit {c['abweichung']} Abweichung{art} hätte verloren "
                f"({c['gewonnen'] * 100:.0f} % gewonnen bei {c['preis'] * 100:.0f} % Preis, {c['ereignisse']} Ereignisse)")


def summary_lines(data, limit=12):
    """Kurzfassung für Bericht und Telegram: gesperrte Bereiche und die besten offenen."""
    if not data:
        return []
    cells = data.get("bereiche", [])
    blocked = [c for c in cells if c["gesperrt"]]
    lines = []
    for c in sorted(blocked, key=lambda c: c["z"] or 0)[:limit]:
        art = "" if c["wettart"] == ALL else f" · {c['wettart']}"
        lines.append(f"{config.TOPIC_NAME.get(c['thema'], c['thema'])}{art} · „{c['seite']}“ · {c['abweichung']}: {c['gewonnen'] * 100:.0f} % gewonnen bei "
                     f"{c['preis'] * 100:.0f} % Preis ({c['ereignisse']} Ereignisse, z = {c['z']:.1f})")
    return lines
