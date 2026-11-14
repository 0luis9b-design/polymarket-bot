"""Central Intelligence Coordinator: steuert den Takt, verteilt das Zeitbudget und führt die Ergebnisse zusammen.

Ein Takt (alle SCAN_INTERVAL_MIN Minuten):
  1. Paper-Trading-Lauf (Märkte holen → Modelle → Prüfung → Prognose-Historie → virtuelle Wetten).
     Scanner, Probability und Validation arbeiten dabei als Teilschritte im selben Lauf – so wird jeder Markt
     nur einmal abgerufen und berechnet (keine doppelte Arbeit, keine Nachrichten zwischen Agenten nötig).
  2. Hintergrund-Agenten, sofern fällig, in Reihenfolge ihrer Priorität und nur solange das Zeitbudget reicht.
     Wer wiederholt scheitert, wird zurückgestuft (immer längere Pausen) statt den Takt aufzuhalten.
  3. Zweimal pro Woche: Claude-Review-Bericht.

Die Agenten sind Programm-Module, keine Sprachmodelle – das Budget für externe Dienste ist 0 €.
"""
import logging
import time
from datetime import datetime, timezone

from .. import config, events, reports, settings, sources
from . import Agent, Registry
from .guardian import GuardianAgent
from .learning import LearningAgent
from .news import NewsAgent
from .probability import ProbabilityAgent
from .scanner import ScannerAgent
from .strategy import StrategyAgent
from .validation import ValidationAgent


log = logging.getLogger("polybot.takt")


class _Self(Agent):
    id = "coordinator"
    name = "Central Coordinator"
    role = "Steuert den Takt, verteilt das Zeitbudget, führt Ergebnisse zusammen, erkennt Widersprüche, schreibt die Berichte"
    priority = 0
    budget = 30

    def run(self, now, deadline):
        return {}


class _Trader(Agent):
    id = "trader"
    name = "Paper Trading"
    role = "Bewertet die Märkte mit Modell und setzt virtuelle Wetten auf 11 Spielgeld-Konten (keine echten Einsätze)"
    priority = 1
    budget = 150

    def run(self, now, deadline):
        return {}


class Coordinator:
    def __init__(self, store, engine=None, fetch_markets=None, fetch_history=None, fetch_text=None):
        self.store = store
        self.engine = engine
        settings.load(store)
        sources.load(store)
        r = self.registry = Registry(store)
        r.add(_Self(store))
        r.add(_Trader(store))
        self.scanner = r.add(ScannerAgent(store, **({"fetch_markets": fetch_markets} if fetch_markets else {})))
        self.probability = r.add(ProbabilityAgent(store))
        self.validation = r.add(ValidationAgent(store))
        self.news = r.add(NewsAgent(store, **({"fetch": fetch_text} if fetch_text else {})))
        self.learning = r.add(LearningAgent(store, self.probability))
        kw = {k: v for k, v in (("fetch_markets", fetch_markets), ("fetch_history", fetch_history)) if v}
        self.strategy = r.add(StrategyAgent(store, **kw))
        self.guardian = r.add(GuardianAgent(store, r, self.probability))
        self.background = sorted([self.scanner, self.news, self.learning, self.strategy, self.guardian], key=lambda a: a.priority)
        self._backoff = {}
        self.last_tick = {}
        self.contradictions = []
        self.started = time.time()

    # ------------------------------------------------------------------ Teilschritte im Handels-Lauf
    def on_markets(self, markets, now):
        def ingest(n, d):
            out = self.scanner.ingest([m for m in markets if m.get("lane") != "base"], now)
            others = [m for m in markets if m.get("lane") == "base"]
            if others:
                out["weitere"] = self.scanner.ingest(others, now, modeled=False)["märkte"]
            return out
        self.registry.run("scanner", now, task="Märkte des Handels-Laufs in den Katalog übernehmen", fn=ingest, persist=False)

    def select(self, m, a, mkt_p):
        try:
            return self.probability.select(m, a, mkt_p)
        except Exception:  # noqa: BLE001 – dann bleibt die Prognose des Modells unverändert
            return a

    def on_results(self, results, now):
        self.registry.run("validation", now, task="Prognosen prüfen", fn=lambda n, d: self.validation.check(results, now))
        self.registry.run("probability", now, task="Prognose-Historie fortschreiben", fn=lambda n, d: self.probability.log(results, now))
        self.contradictions = self._contradictions(results)

    @staticmethod
    def _contradictions(results):
        """Modell und liquider Markt auf entgegengesetzten Seiten – zum Nachsehen, nicht zum Handeln."""
        out = []
        for o in results:
            p, mp, m = o["analysis"]["p_yes"], o["market_p"], o["market"]
            if (m.get("liquidity") or 0) >= config.MIN_LIQUIDITY and ((p >= 0.65 and mp <= 0.35) or (p <= 0.35 and mp >= 0.65)):
                out.append({"id": m["id"], "question": m["question"], "category": m["category"], "p": round(p, 3), "market_p": round(mp, 3),
                            "valid": o["analysis"].get("valid", True), "reject": o["analysis"].get("reject")})
        return sorted(out, key=lambda x: -abs(x["p"] - x["market_p"]))[:25]

    # ------------------------------------------------------------------ Takt
    def cycle(self, now=None):
        """Ein kompletter Takt. Wirft nie."""
        t0 = time.time()
        now = now or datetime.now(timezone.utc)
        deadline = t0 + config.CYCLE_BUDGET_SEC
        if self.engine is not None:
            self.registry.run("trader", now, task="Märkte bewerten und Paper-Trading", fn=lambda n, d: self._trade(), budget=_Trader.budget)
        ran, skipped = self.tick(now, deadline)
        if reports.due(self.store, now) and self.engine is not None:
            rid = self.registry.run("coordinator", now, task="Claude-Review-Bericht schreiben",
                                    fn=lambda n, d: {"bericht": reports.generate(self, now)})
            if rid:
                ran.append("bericht")
        try:
            sources.flush(self.store)
        except Exception:  # noqa: BLE001
            pass
        self.last_tick = {"zeit": now.isoformat(timespec="seconds"), "dauer": round(time.time() - t0, 1), "gelaufen": ran,
                          "zurückgestellt": skipped, "budget": config.CYCLE_BUDGET_SEC}
        self._log_tick(ran, skipped)
        return self.last_tick

    def _log_tick(self, ran, skipped):
        """Eine Zeile pro Takt ins Log: Das Dashboard ist passwortgeschützt, das Log ist der Weg, auf dem der
        Claude-Supervisor (und jeder, der die Railway-Logs liest) den Zustand der Agenten sieht."""
        try:
            st = self.registry.status
            parts = [f"{aid} {st[aid]['last_duration']:.0f}s" if aid in st and st[aid]["last_duration"] is not None else aid for aid in ran]
            failing = [f"{s['id']}: {s['last_error']}" for s in st.values() if s["state"] == "fehler"]
            snap = sources.snapshot()
            paused = [f"{x['name']} ({x['paused_s'] // 60:.0f} min)" for x in snap if x["paused_s"] > 0]
            held = [f"{x['name']} {x['blocked']}×" for x in snap if x["blocked"]]
            c = self.counts()
            v = dict(self.validation.cycle or {})
            checked, valid = v.pop("geprüft", 0), v.pop("gültig", 0)
            by_cat = getattr(self.validation, "cycle_by_cat", {}) or {}
            check = f"{checked} geprüft, {valid} gültig" + "".join(
                f", {cat}: " + "/".join(f"{k} {n}" for k, n in sorted(rs.items(), key=lambda x: -x[1])) for cat, rs in sorted(by_cat.items()))
            models = ", ".join(f"{r['model']}@{r['version']}" + ("" if r["mix_w"] is None or r["mix_w"] >= 0.999 else f" (Mischung {r['mix_w'] * 100:.0f} % Modell)")
                               for _, r in sorted(self.probability.reg.items()))
            paused_acc = sorted(k for k, r in (self.engine.state.get("paused") or {}).items() if r) if self.engine is not None else []
            log.info("Takt %.0f s · gelaufen: %s · zurückgestellt: %s · Agenten mit Fehler: %s · pausierte Quellen: %s · "
                     "heute vom Budget gebremst: %s · Prüfung: %s · aktive Modelle: %s · pausierte Konten: %s · "
                     "Bestand: %s Märkte offen (%s mit Modell), %s Prognosen, %s Ergebnisse, %s im Datensatz, %s Schlagzeilen",
                     self.last_tick["dauer"], ", ".join(parts) or "–", ", ".join(skipped) or "–", "; ".join(failing) or "keine",
                     ", ".join(paused) or "keine", ", ".join(held) or "nichts", check, models, ", ".join(paused_acc) or "keine", c["märkte_offen"], c["märkte_mit_modell"], c["prognosen"], c["ergebnisse"],
                     c["entschiedene_märkte_datensatz"], c["nachrichten"])
        except Exception:  # noqa: BLE001 – eine Logzeile darf nie stören
            pass

    def _trade(self):
        eng = self.engine
        if not eng.run_cycle():
            return {"übersprungen": "Lauf läuft schon"}
        if eng.state.get("last_error"):
            raise RuntimeError(eng.state["last_error"])
        return {"märkte": eng.state.get("markets_seen"), "bewertet": len(eng.state.get("opportunities") or []),
                "dauer": eng.state.get("duration"), "prüfung": self.validation.cycle}

    def tick(self, now, deadline):
        ran, skipped = [], []
        ts = time.time()
        for agent in self.background:
            aid = agent.id
            if not self.registry.due(aid, ts):
                continue
            if self._backoff.get(aid, 0) > ts:
                self.registry.skip(aid, "zurückgestuft nach wiederholten Fehlern")
                skipped.append(aid)
                continue
            left = deadline - time.time()
            if left < min(agent.budget, 15) and aid != "guardian":
                self.registry.skip(aid, "Zeitbudget des Takts aufgebraucht")
                skipped.append(aid)
                continue
            self.registry.run(aid, now, budget=max(5.0, min(agent.budget, left)) if aid != "guardian" else agent.budget)
            ran.append(aid)
            streak = self.registry.status[aid]["error_streak"]
            if streak >= 3:  # 5, 10, 20 … höchstens 60 Minuten Pause
                self._backoff[aid] = time.time() + min(3600, 300 * 2 ** (streak - 3))
            else:
                self._backoff.pop(aid, None)
        return ran, skipped

    # ------------------------------------------------------------------ Zusammenführung fürs Dashboard
    def counts(self):
        st = self.store
        return {"märkte_katalog": st.scalar("SELECT COUNT(*) FROM markets"),
                "märkte_offen": st.scalar("SELECT COUNT(*) FROM markets WHERE status='open'"),
                "märkte_mit_modell": st.scalar("SELECT COUNT(*) FROM markets WHERE status='open' AND modeled=1"),
                "prognosen": st.scalar("SELECT COUNT(*) FROM forecasts"),
                "prognosen_märkte": st.scalar("SELECT COUNT(DISTINCT market_id) FROM forecasts"),
                "ergebnisse": st.scalar("SELECT COUNT(*) FROM outcomes"),
                "prognosen_mit_ergebnis": st.scalar("SELECT COUNT(*) FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id WHERE f.champion=1"),
                "entschiedene_märkte_datensatz": st.scalar("SELECT COUNT(*) FROM resolved"),
                "nachrichten": st.scalar("SELECT COUNT(*) FROM news"),
                "berichte": st.scalar("SELECT COUNT(*) FROM reports")}

    def change_settings(self, changes):
        done = settings.update(self.store, changes)
        for key, old, new in done:
            events.emit(self.store, "info", "einstellung", f"Einstellung geändert: {key}", f"{old} → {new}", agent="coordinator", telegram=False)
        return done
