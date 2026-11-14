"""Agenten-Gerüst.

Ein Agent ist ein Programm-Modul mit einer klar umrissenen Aufgabe (kein Sprachmodell).
Jeder Agent hat einen Takt (interval), ein Zeitbudget (budget) und eine Priorität; der Koordinator
ruft ihn über `Registry.run` auf, misst die Dauer, fängt Fehler ab und führt die Statuszeile,
die das Dashboard in der KI-Zentrale zeigt.
"""
import json
import logging
import time
import traceback
from datetime import datetime, timezone

from ..sources import redact

log = logging.getLogger("polybot.agents")


class Agent:
    id = "agent"
    name = "Agent"
    role = ""
    interval = 0        # Sekunden zwischen zwei Läufen (0 = in jedem Takt)
    budget = 30         # Sekunden, die der Agent sich nehmen darf
    priority = 5        # 1 = zuerst

    def __init__(self, store):
        self.store = store

    def run(self, now, deadline):
        """Arbeit erledigen; gibt ein kleines dict als Zusammenfassung zurück. `deadline` = time.time()-Wert."""
        raise NotImplementedError

    @staticmethod
    def time_left(deadline):
        return deadline - time.time()


class Registry:
    def __init__(self, store):
        self.store = store
        self.agents = {}
        self.status = {}

    def add(self, agent):
        self.agents[agent.id] = agent
        self.status[agent.id] = {"id": agent.id, "name": agent.name, "role": agent.role, "interval": agent.interval,
                                 "budget": agent.budget, "priority": agent.priority, "state": "wartet", "task": "",
                                 "runs": 0, "errors": 0, "error_streak": 0, "skipped": 0, "last_start": None,
                                 "last_end": None, "last_ok": None, "last_duration": None, "avg_duration": None,
                                 "last_error": None, "last_error_at": None, "last_summary": None, "load_sec": 0.0,
                                 "overruns": 0}
        last = self.store.kv_get(f"agent_last:{agent.id}")
        self.status[agent.id]["_last_ts"] = float(last) if last else 0.0
        return agent

    def due(self, agent_id, now_ts=None):
        a, s = self.agents[agent_id], self.status[agent_id]
        return a.interval <= 0 or (now_ts or time.time()) - s["_last_ts"] >= a.interval

    def skip(self, agent_id, reason):
        s = self.status[agent_id]
        s["skipped"] += 1
        s["task"] = f"zurückgestellt: {reason}"

    def run(self, agent_id, now=None, task="", fn=None, persist=True, budget=None):
        """Agent (oder eine seiner Teilaufgaben `fn`) ausführen. Wirft nie – Fehler landen in der Statuszeile."""
        a, s = self.agents[agent_id], self.status[agent_id]
        now = now or datetime.now(timezone.utc)
        t0 = time.time()
        deadline = t0 + (budget or a.budget)
        s.update(state="läuft", task=task or a.role, last_start=now.isoformat(timespec="seconds"))
        ok, summary, err = True, None, None
        try:
            summary = (fn or a.run)(now, deadline)
        except Exception as e:  # noqa: BLE001 – ein Agent darf den Takt nie abbrechen
            ok = False
            err = redact(f"{type(e).__name__}: {e}")[:300]
            log.error("Agent %s: %s\n%s", agent_id, err, redact(traceback.format_exc())[-1500:])
        dur = time.time() - t0
        end = datetime.now(timezone.utc).isoformat(timespec="seconds")
        s["runs"] += 1
        s["last_end"], s["last_duration"] = end, round(dur, 2)
        s["avg_duration"] = round(dur if s["avg_duration"] is None else 0.8 * s["avg_duration"] + 0.2 * dur, 2)
        s["load_sec"] = round(s["load_sec"] + dur, 1)
        if dur > (budget or a.budget) * 1.5:
            s["overruns"] += 1
        if ok:
            s.update(state="ok", last_ok=end, last_summary=summary, error_streak=0, task="")
        else:
            s["errors"] += 1
            s["error_streak"] += 1
            s.update(state="fehler", last_error=err, last_error_at=end, task="")
        if fn is None:
            s["_last_ts"] = t0
            self.store.kv_set(f"agent_last:{agent_id}", t0)
        if persist:
            try:
                self.store.x("INSERT INTO agent_runs(agent, started_at, duration, ok, summary, error) VALUES(?,?,?,?,?,?)",
                             (agent_id, s["last_start"], round(dur, 2), 1 if ok else 0,
                              json.dumps(summary, ensure_ascii=False, default=str)[:1500] if summary is not None else None, err))
            except Exception as e:  # noqa: BLE001
                log.warning("agent_runs nicht schreibbar: %s", e)
        return summary if ok else None

    def view(self):
        total = sum(s["load_sec"] for s in self.status.values()) or 1.0
        out = []
        for s in sorted(self.status.values(), key=lambda x: x["priority"]):
            row = {k: v for k, v in s.items() if not k.startswith("_")}
            row["load_share"] = round(s["load_sec"] / total, 3)
            out.append(row)
        return out
