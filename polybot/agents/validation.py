"""Validation Agent: prüft jede Prognose, bevor sie ins Paper-Trading geht.

Eine abgelehnte Prognose wird trotzdem gespeichert (mit Grund) – so lässt sich später messen,
ob die Ablehnung richtig war. Gehandelt wird sie nicht.

Regeln
  wert        Wahrscheinlichkeit ist eine Zahl zwischen 0 und 1
  ende        der Markt läuft noch
  frische     die Daten hinter der Prognose sind nicht zu alt
  abweichung  Modell und liquider Markt liegen nicht weiter auseinander als MAX_DIVERGENCE.
              Eine riesige Abweichung ist fast immer ein Modellfehler, kein Vorsprung
              (Beleg: Wetter-Wetten vom 12./13.11.2026).
  summe       bei Ereignissen mit genau einer richtigen Antwort ergeben die Modell-Werte zusammen etwa 100 %
"""
import time
from collections import Counter, defaultdict

from .. import config
from . import Agent

# Wann eine Prüfregel zuletzt geändert wurde. Die Auswertung „War die Ablehnung berechtigt?“ zählt nur Ablehnungen ab
# diesem Zeitpunkt – sonst bleibt eine Empfehlung zu einer längst geänderten Regel offen (Bericht #4: 659 alte Fälle).
RULE_SINCE = {"Summe der Antworten": "2026-11-21T15:00:00+00:00"}   # 2.6: nur noch bei Summe 85–120 %

MAX_AGE = {"crypto": 20 * 60, "sports": 8 * 3600, "weather": 8 * 3600}


class ValidationAgent(Agent):
    id = "validation"
    name = "Validation"
    role = "Prüft Prognosen auf Datenfrische, Plausibilität und Stimmigkeit; sperrt Unsicheres fürs Handeln"
    priority = 3
    budget = 10

    def __init__(self, store):
        super().__init__(store)
        self.cycle = {}
        self.cycle_by_cat = {}
        self.total = Counter()

    def check(self, results, now):
        counts = Counter()
        self._by_cat = defaultdict(Counter)
        by_event = defaultdict(list)
        for o in results:
            m, a = o["market"], o["analysis"]
            checks, reason = [], None
            p = a.get("p_yes")
            if p is None or p != p or not (0 < p < 1):
                reason = "Wert ungültig"
            checks.append(("wert", reason is None))
            hours = a.get("hours")
            if reason is None and hours is not None and hours <= 0:
                reason = "Markt läuft nicht mehr"
            checks.append(("ende", reason is None))
            if reason is None:
                ts = a.get("data_ts")
                limit = MAX_AGE.get(m["category"])
                if ts and limit and time.time() - ts > limit:
                    reason = f"Daten veraltet ({(time.time() - ts) / 3600:.1f} h)"
                checks.append(("frische", reason is None))
            if reason is None:
                spread = (m["best_ask"] - m["best_bid"]) if (m.get("best_ask") and m.get("best_bid")) else None
                liquid = (m.get("liquidity") or 0) >= config.MIN_LIQUIDITY and spread is not None and spread <= config.MAX_SPREAD
                div = abs(p - o["market_p"])
                if liquid and div > config.MAX_DIVERGENCE:
                    reason = f"Abweichung unplausibel ({div * 100:.0f} Pkt gegen liquiden Markt)"
                checks.append(("abweichung", reason is None))
            a["checks"] = checks
            if reason:
                self._reject(o, reason, counts, self._by_cat)
            else:
                a["valid"] = True
                if m.get("neg_risk") and m.get("event_id"):
                    by_event[m["event_id"]].append(o)
        # Summen-Prüfung über vollständige Ereignisse
        for eid, group in by_event.items():
            n = group[0]["market"].get("event_n_markets") or 0
            if n < 2 or len(group) != n:
                continue
            # Nur prüfen, wenn auch der Markt die Antworten als „genau eine trifft zu“ behandelt (Kurse zusammen ≈ 100 %).
            # Live-Befund 18./21.11.: Polymarket markiert auch Kurs-Leitern („Bitcoin über 64k / 66k / 68k“, „Was erreicht
            # Bitcoin?“) als zusammengehörig. Dort können mehrere Antworten eintreffen – die Summe liegt weit über 100 %.
            # Die Regel sperrte 653 Prognosen, bei denen das Modell besser lag als der Markt (Brier 0,080 gegen 0,112).
            mkt = [o.get("market_p") for o in group]
            if any(x is None for x in mkt) or not (0.85 <= sum(mkt) <= 1.20):
                continue
            # am reinen Modellwert gemessen: nach einer freigegebenen Mischung mit dem Marktpreis würde sonst die
            # (oft ungenaue) Summe der Marktkurse geprüft statt die Stimmigkeit des Modells
            total = sum(o["analysis"].get("p_model", o["analysis"]["p_yes"]) for o in group)
            ok = 0.85 <= total <= 1.15
            for o in group:
                o["analysis"]["checks"].append(("summe", ok))
                if not ok:
                    self._reject(o, f"Summe der Antworten = {total * 100:.0f} %", counts, self._by_cat)
        counts["geprüft"] = len(results)
        counts["gültig"] = sum(1 for o in results if o["analysis"].get("valid"))
        self.cycle = dict(counts)
        self.cycle_by_cat = {c: dict(v) for c, v in self._by_cat.items()}
        self.total.update({k: v for k, v in counts.items() if k not in ("geprüft", "gültig")})
        return self.cycle

    @staticmethod
    def _reject(o, reason, counts, by_cat=None):
        a = o["analysis"]
        if a.get("valid") is False:
            return
        a["valid"] = False
        a["reject"] = reason
        o["base"].append(f"Prüfung: {reason}")
        short = reason.split(" (")[0].split(" =")[0]
        counts[short] += 1
        if by_cat is not None:
            by_cat[o["market"]["category"]][short] += 1

    def run(self, now, deadline):
        return dict(self.cycle)
