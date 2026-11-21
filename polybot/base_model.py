"""Basisraten-Modell: eigene Wahrscheinlichkeit für Märkte, für die es kein eigenes Datenmodell gibt.

Frage: „Wenn Märkte dieser Art bei diesem Kurs so kurz vor dem Ende standen – wie oft ist ‚Ja‘ wirklich eingetreten?“
Antwort aus dem eigenen Datensatz entschiedener Polymarket-Märkte (Tabelle `resolved`, aufgebaut vom Strategy Agent).

Rechnung je Markt
  1. Zeitpunkt: die nächstliegende Stufe 1 h / 6 h / 24 h / 72 h / 7 Tage vor dem Ende (länger laufende Märkte: keine Prognose).
  2. Kursbereich: in welchem von 10 Bereichen der Ja-Kurs liegt (an den Rändern feiner, dort ist die Verzerrung am größten).
  3. Abweichung = Anteil eingetretener „Ja“ minus durchschnittlicher Kurs in diesem Bereich, gemessen NUR an Märkten
     desselben Themas, zur Null hin gestaucht (50 gedachte Märkte ohne Abweichung).
  4. Prognose = Marktpreis + Abweichung. Unsicherheit = Standardfehler der Abweichung; gehandelt wird nur, wenn die
     Abweichung mindestens 1,64 Standardfehler groß ist (siehe engine.do_entries).

Ein „guter Grund“ (seit 17.11., nach den Verlusten mit E-Sport und Fußball-Ergebnissen am 15./16.11.) – alles muss stimmen:
  - nur echte Ja/Nein-Fragen; nie ein genaues Ergebnis eines Spiels; nicht mehr, wenn das Spiel schon läuft
  - (17.11. abends, Wunsch Luis: Spiele wieder erlaubt – aber nur mit Gewinnchance ≥ MIN_WIN_P und einer Wette je Spiel)
  - mindestens BASE_MIN_N_CAT Vergleichsmärkte aus demselben Thema (nicht geliehen aus anderen Themen)
  - das Muster gilt in den älteren UND den neueren Vergleichsmärkten (sonst Zufall) und widerspricht nicht allen Themen
  - die Rohabweichung ist höchstens BASE_MAX_DEV groß – mehr ist fast immer ein Fehler in den Vergleichsdaten
    (Beispiel: ein genaues Fußball-Ergebnis, das eintrifft, wird viel mehr gehandelt und landet eher im Datensatz)
  - im Thema haben sich die Basisraten bisher nicht als schlechter als der Markt erwiesen (Wächter, aus der Prognose-Historie;
    seit 2.5 sperrt er schon bei „wahrscheinlich schlechter“, z ≥ BASE_GUARD_Z = 1,0, und gibt das Thema selbst wieder frei)

Ohne Blick in die Zukunft: Der Datensatz enthält nur Märkte, die vor dem jetzigen Zeitpunkt entschieden wurden, und
deren Kurse zu festen Zeitpunkten vor dem geplanten Ende.
"""
import math
import time

from . import config, polymarket, taxonomy

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
        self.cells = {}       # (kategorie|None, spalte, bereich[, hälfte]) -> [n, summe Kurs, summe Ergebnis]
        self.n_rows = 0
        self.blocked = {}     # Thema -> Grund (Wächter)

    def refresh(self, force=False):
        if not force and time.time() - self._t < self.max_age:
            return
        cells = {}
        rows = self.store.q("SELECT category, outcome, p_1h, p_6h, p_24h, p_72h, p_7d FROM resolved WHERE outcome IS NOT NULL "
                            "AND sel=? ORDER BY end_date, market_id", (config.DATASET_SEL,))
        total = {}
        for r in rows:
            total[r["category"]] = total.get(r["category"], 0) + 1
        seen = {}
        for r in rows:
            cat = r["category"]
            i = seen.get(cat, 0)
            seen[cat] = i + 1
            half = 0 if i < total[cat] / 2 else 1          # ältere / neuere Hälfte des Themas
            for col, _ in HORIZONS:
                p = r[col]
                if p is None:
                    continue
                b = bin_of(p)
                for key in ((None, col, b), (cat, col, b), (cat, col, b, half)):
                    c = cells.setdefault(key, [0, 0.0, 0.0])
                    c[0] += 1
                    c[1] += p
                    c[2] += r["outcome"]
        self.cells, self.n_rows, self._t = cells, len(rows), time.time()
        self.blocked = self.guard()

    def guard(self):
        """Wächter: Themen, in denen die Basisraten bisher gesichert schlechter waren als der Marktpreis."""
        out = {}
        summ = self.store.kv_json("learn:summary", None) if hasattr(self.store, "kv_json") else None
        for m in (summ or {}).get("modelle") or []:
            if m.get("model") != MODEL_ID or (m.get("n") or 0) < config.BASE_GUARD_MIN_N:
                continue
            z, ev = m.get("z"), m.get("clusters") or 0
            worse = (m.get("brier") or 0) > (m.get("brier_market") or 0)
            if z is not None and z >= config.BASE_GUARD_Z and worse and ev >= 5:
                sure = "gesichert" if z >= config.BASE_MIN_Z else "wahrscheinlich"
                out[m["category"]] = (f"Basisraten waren in diesem Thema bisher {sure} schlechter als der Markt "
                                      f"(Brier {m.get('brier', 0):.3f} gegen {m.get('brier_market', 0):.3f}, z = {z:+.2f}, {m['n']} Prognosen)")
        return out

    def cell(self, cat, col, b, half=None):
        key = (cat, col, b) if half is None else (cat, col, b, half)
        n, sp, sy = self.cells.get(key, (0, 0.0, 0.0))
        if not n:
            return 0, None, None
        return n, sp / n, sy / n

    def estimate(self, cat, hours, p):
        """(Abweichung, Standardfehler, Beschreibung, Merkmale) oder (None, None, Grund)."""
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
        n_c, p_c, y_c = self.cell(cat, col, b)
        need = config.BASE_MIN_N_CAT
        name = taxonomy.NAMES.get(cat, cat)
        if n_c < need:
            return None, None, f"zu wenige Vergleichsmärkte im eigenen Thema ({n_c}/{need})"
        raw = y_c - p_c
        if abs(raw) > config.BASE_MAX_DEV:
            return None, None, (f"Abweichung unplausibel groß ({raw * 100:+.0f} Pkt) – vermutlich verzerrte Vergleichsdaten")
        raw_g = y_g - p_g
        if raw * raw_g < 0 and abs(raw_g) >= 0.01:
            return None, None, "Muster widerspricht allen anderen Themen"
        halves = []
        for h in (0, 1):
            n_h, p_h, y_h = self.cell(cat, col, b, h)
            if n_h < config.BASE_HALF_MIN_N or (y_h - p_h) * raw <= 0 or abs(y_h - p_h) < 0.01:
                return None, None, "Muster nicht stabil (ältere und neuere Vergleichsmärkte stimmen nicht überein)"
            halves.append((n_h, y_h - p_h))
        d = raw * n_c / (n_c + SHRINK)
        se = max(0.005, math.sqrt(max(y_c * (1 - y_c), 0.01) / n_c))
        horizon = {"p_1h": "1 h", "p_6h": "6 h", "p_24h": "24 h", "p_72h": "3 Tage", "p_7d": "7 Tage"}[col]
        desc = (f"Grund: {n_c} vergleichbare {name}-Märkte ({horizon} vor Ende, Ja-Kurs {bin_label(b)}) – "
                f"eingetreten {y_c * 100:.1f} % bei Ø Kurs {p_c * 100:.1f} %; ältere Hälfte {halves[0][1] * 100:+.1f} Pkt, "
                f"neuere {halves[1][1] * 100:+.1f} Pkt (alle Themen {n_g}: {raw_g * 100:+.1f} Pkt)")
        return d, se, desc, {"col": col, "bin": b, "n_all": n_g, "n_cat": n_c, "roh": round(raw, 4),
                             "haelften": [round(x[1], 4) for x in halves]}

    def cells_report(self, min_n=30, top=8):
        """Die auffälligsten Vergleichszellen je Thema (für den Claude-Bericht): wo weichen Kurs und Ergebnis am stärksten ab?"""
        self.refresh()
        rows = []
        for key, (n, sp, sy) in self.cells.items():
            if len(key) != 3 or key[0] is None or n < min_n:
                continue
            cat, col, b = key
            rows.append({"thema": taxonomy.NAMES.get(cat, cat), "stufe": col, "kurs": bin_label(b), "n": n,
                         "kurs_avg": sp / n, "eingetreten": sy / n, "abw": sy / n - sp / n})
        return sorted(rows, key=lambda r: -abs(r["abw"]))[:top]

    def analyze(self, m, now):
        if polymarket.is_exact_score(m):
            return {"skip": "genaues Ergebnis – wird nie gewettet"}
        if m.get("category") in config.GAME_TOPICS and m.get("game_start") and m["game_start"] <= now:
            return {"skip": "Spiel läuft schon"}
        outs = [str(o).lower() for o in (m.get("outcomes") or [])]
        if len(outs) != 2:
            return {"skip": "nicht genau zwei Ausgänge"}
        if outs[0] not in ("yes", "ja"):
            return {"skip": "keine Ja/Nein-Frage"}
        self.refresh()
        if m.get("category") in self.blocked:
            return {"skip": "Basisraten in diesem Thema gesperrt (Wächter)"}
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
                "z_quelle": "nur eigenes Thema",
                "sources": [f"Eigener Datensatz entschiedener Polymarket-Märkte ({self.n_rows} Märkte)"],
                "data_ts": time.time(), "hours": hours,
                "features": {"market_p": round(p, 4), "abweichung": round(d, 4), "se": round(se, 4), **feat},
                "group": f"{m['category']}:{m.get('event_id') or m['id']}"}
