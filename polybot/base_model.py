"""Basisraten-Modell: eigene Wahrscheinlichkeit für Märkte, für die es kein eigenes Datenmodell gibt.

Frage: „Wenn Märkte dieser Art bei diesem Kurs so kurz vor dem Ende standen – wie oft ist ‚Ja‘ wirklich eingetreten?“
Antwort aus dem eigenen Datensatz entschiedener Polymarket-Märkte (Tabelle `resolved`, aufgebaut vom Strategy Agent).

Rechnung je Markt
  1. Zeitpunkt: die nächstliegende Stufe 1 h / 6 h / 24 h / 72 h / 7 Tage vor dem Ende (länger laufende Märkte: keine Prognose).
  2. Kursbereich: in welchem von 10 Bereichen der Ja-Kurs liegt (an den Rändern feiner, dort ist die Verzerrung am größten).
  3. Abweichung = Anteil eingetretener „Ja“ minus durchschnittlicher Kurs in diesem Bereich – erst über alle Bereiche
     (alle Kategorien), dann für die Kategorie des Marktes. Beides wird zur Null hin gestaucht, solange wenige Märkte
     da sind (je 50 gedachte Märkte ohne Abweichung). Wenig Daten → Prognose ≈ Marktpreis → kein Handel.
  4. Prognose = Marktpreis + Abweichung. Unsicherheit = Standardfehler der Abweichung; gehandelt wird nur, wenn die
     Abweichung mindestens 1,64 Standardfehler groß ist (siehe engine.do_entries).

Ohne Blick in die Zukunft: Der Datensatz enthält nur Märkte, die vor dem jetzigen Zeitpunkt entschieden wurden, und
deren Kurse zu festen Zeitpunkten vor dem geplanten Ende.
"""
import math
import time

from . import polymarket, taxonomy

HORIZONS = [("p_1h", 1.0), ("p_6h", 6.0), ("p_24h", 24.0), ("p_72h", 72.0), ("p_7d", 168.0)]
EDGES = [0.0, 0.03, 0.07, 0.15, 0.30, 0.50, 0.70, 0.85, 0.93, 0.97, 1.0001]
SHRINK = 50          # gedachte Märkte ohne Abweichung (stauchen kleine Stichproben zur Null)
MIN_N = 30           # so viele Vergleichsmärkte braucht ein Kursbereich mindestens (über alle Kategorien)
MAX_HOURS = 8 * 24   # weiter vorne hat der Datensatz keine Kurse
MODEL_ID = "base.calib@1"


def horizon_col(hours):
    """Nächstliegende Stufe (gemessen im Verhältnis, nicht in Stunden: 30 h liegt näher an 24 h als an 72 h)."""
    h = max(hours, 0.25)
    return min(HORIZONS, key=lambda x: abs(math.log(h / x[1])))[0]


def bin_of(p):
    for i in range(len(EDGES) - 1):
        if EDGES[i] <= p < EDGES[i + 1]:
            return i
    return len(EDGES) - 2


def bin_label(i):
    return f"{EDGES[i] * 100:.0f}–{min(EDGES[i + 1], 1) * 100:.0f} %"


class BaseRates:
    def __init__(self, store, max_age=900):
        self.store = store
        self.max_age = max_age
        self._t = 0.0
        self.cells = {}       # (kategorie|None, spalte, bereich) -> [n, summe Kurs, summe Ergebnis]
        self.n_rows = 0

    def refresh(self, force=False):
        if not force and time.time() - self._t < self.max_age:
            return
        cells = {}
        rows = self.store.q("SELECT category, outcome, p_1h, p_6h, p_24h, p_72h, p_7d FROM resolved WHERE outcome IS NOT NULL")
        for r in rows:
            for col, _ in HORIZONS:
                p = r[col]
                if p is None:
                    continue
                b = bin_of(p)
                for key in ((None, col, b), (r["category"], col, b)):
                    c = cells.setdefault(key, [0, 0.0, 0.0])
                    c[0] += 1
                    c[1] += p
                    c[2] += r["outcome"]
        self.cells, self.n_rows, self._t = cells, len(rows), time.time()

    def cell(self, cat, col, b):
        n, sp, sy = self.cells.get((cat, col, b), (0, 0.0, 0.0))
        if not n:
            return 0, None, None
        return n, sp / n, sy / n

    def estimate(self, cat, hours, p):
        """(Abweichung, Standardfehler, Beschreibung) oder (None, None, Grund)."""
        self.refresh()
        if hours is None or hours <= 0.2:
            return None, None, "Markt endet gleich"
        if hours > MAX_HOURS:
            return None, None, "Restlaufzeit über 8 Tage – dafür hat der Datensatz keine Vergleichskurse"
        col = horizon_col(hours)
        b = bin_of(p)
        n_g, p_g, y_g = self.cell(None, col, b)
        if n_g < MIN_N:
            return None, None, f"zu wenige Vergleichsmärkte ({n_g}/{MIN_N})"
        d_g = (y_g - p_g) * n_g / (n_g + SHRINK)
        n_c, p_c, y_c = self.cell(cat, col, b)
        d = d_g if not n_c else (n_c * (y_c - p_c) + SHRINK * d_g) / (n_c + SHRINK)
        # Standardfehler: aus der Kategorie, wenn sie genug Märkte hat, sonst aus allen
        n_se, y_se = (n_c, y_c) if n_c >= MIN_N else (n_g, y_g)
        se = max(0.005, math.sqrt(max(y_se * (1 - y_se), 0.01) / n_se))
        horizon = {"p_1h": "1 h", "p_6h": "6 h", "p_24h": "24 h", "p_72h": "3 Tage", "p_7d": "7 Tage"}[col]
        desc = (f"Vergleich: Märkte {horizon} vor Ende mit Ja-Kurs {bin_label(b)} – alle Bereiche {n_g} "
                f"(eingetreten {y_g * 100:.1f} % bei Ø Kurs {p_g * 100:.1f} %)")
        if n_c:
            desc += f", {taxonomy.NAMES.get(cat, cat)} {n_c} (eingetreten {y_c * 100:.1f} % bei Ø Kurs {p_c * 100:.1f} %)"
        return d, se, desc, {"col": col, "bin": b, "n_all": n_g, "n_cat": n_c}

    def analyze(self, m, now):
        if m["outcomes"][0].lower() not in ("yes", "ja"):
            return {"skip": "kein Ja/Nein-Markt"}
        if not m.get("best_bid") or not m.get("best_ask"):
            return {"skip": "kein Orderbuch"}
        if not m.get("end_date"):
            return {"skip": "kein Enddatum"}
        hours = (m["end_date"] - now).total_seconds() / 3600
        p = polymarket.mid_yes(m)
        res = self.estimate(m["category"], hours, p)
        if res[0] is None:
            return {"skip": res[2].split(" (")[0]}
        d, se, desc, feat = res
        q = min(max(p + d, 0.005), 0.995)
        return {"p_yes": q, "p_model": q, "model": "Basisraten", "model_id": MODEL_ID, "lane": "base",
                "variants": {MODEL_ID: q}, "uncertainty": round(se, 4), "z": (d / se) if se else 0.0,
                "info": f"Markt {p * 100:.1f} % · {desc} → eigene Schätzung {q * 100:.1f} %",
                "sources": [f"Eigener Datensatz entschiedener Polymarket-Märkte ({self.n_rows} Märkte)"],
                "data_ts": time.time(), "hours": hours,
                "features": {"market_p": round(p, 4), "abweichung": round(d, 4), "se": round(se, 4), **feat},
                "group": f"{m['category']}:{m.get('event_id') or m['id']}"}
