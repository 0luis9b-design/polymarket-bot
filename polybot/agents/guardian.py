"""System Guardian Agent: überwacht Ressourcen, Datenquellen, Agenten und die Datenbank.

Er repariert nichts „heimlich“: Jede Maßnahme (Sicherung, Aufräumen, erkannter Ausfall, Wiederherstellung)
landet als Ereignis im Dashboard; Wichtiges zusätzlich in Telegram. Der Neustart eines hängenden Prozesses
liegt bei app.py (Wachhund) – der Guardian liefert dafür die Messwerte.
"""
import json
import os
import shutil
import threading
import time
from datetime import timedelta

from .. import config, events, sources
from . import Agent

RAM_WARN_MB = 1500
DISK_WARN_FREE = 0.10


def read_resources(data_dir, db_path):
    """Messwerte des laufenden Prozesses. Was das System nicht hergibt, bleibt None (wird als „keine Daten“ angezeigt)."""
    out = {"rss_mb": None, "mem_limit_mb": None, "cpu_sec": None, "threads": threading.active_count(),
           "disk_total_gb": None, "disk_free_gb": None, "db_mb": None, "load1": None, "cpus": os.cpu_count()}
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    out["rss_mb"] = round(int(line.split()[1]) / 1024, 1)
    except OSError:
        pass
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = open(path).read().strip()
            if raw.isdigit() and int(raw) < 1 << 50:
                out["mem_limit_mb"] = round(int(raw) / 1048576)
            break
        except OSError:
            continue
    t = os.times()
    out["cpu_sec"] = round(t.user + t.system, 2)
    try:
        du = shutil.disk_usage(data_dir)
        out["disk_total_gb"], out["disk_free_gb"] = round(du.total / 1e9, 2), round(du.free / 1e9, 2)
    except OSError:
        pass
    try:
        size = sum(os.path.getsize(db_path + ext) for ext in ("", "-wal") if os.path.exists(db_path + ext))
        out["db_mb"] = round(size / 1048576, 2)
    except OSError:
        pass
    try:
        out["load1"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    return out


class GuardianAgent(Agent):
    id = "guardian"
    name = "System Guardian"
    role = "Überwacht Speicher, CPU, Platte, Datenquellen und Agenten; sichert täglich die Datenbank und räumt alte Daten auf"
    interval = 0
    budget = 30
    priority = 9

    def __init__(self, store, registry=None, probability=None):
        super().__init__(store)
        self.registry = registry
        self.probability = probability
        self._cpu = None
        self._down_sources = set(store.kv_json("guardian:down_sources", []))
        self._down_agents = set()
        self.latest = {}

    def run(self, now, deadline):
        res = read_resources(config.DATA_DIR, self.store.path)
        # CPU-Anteil seit dem letzten Lauf (ein Kern = 100 %)
        if self._cpu and res["cpu_sec"] is not None and time.time() > self._cpu[0]:
            res["cpu_pct"] = round(100 * (res["cpu_sec"] - self._cpu[1]) / (time.time() - self._cpu[0]), 1)
        else:
            res["cpu_pct"] = None
        self._cpu = (time.time(), res["cpu_sec"])
        res["ts"] = now.isoformat(timespec="seconds")
        self.latest = res
        hist = self.store.kv_json("guardian:res", [])
        hist.append({k: res[k] for k in ("ts", "rss_mb", "cpu_pct", "db_mb", "threads")})
        self.store.kv_set("guardian:res", json.dumps(hist[-288:]))
        out = {"ram_mb": res["rss_mb"], "cpu_pct": res["cpu_pct"], "db_mb": res["db_mb"]}
        self.check_resources(res)
        out["quellen_gestört"] = self.check_sources()
        out["agenten_gestört"] = self.check_agents()
        day = now.strftime("%Y-%m-%d")
        if self.store.kv_get("guardian:backup_day") != day and now.hour >= 3:
            out["sicherung"] = self.backup(day)
        if self.store.kv_get("guardian:prune_day") != day and self.time_left(deadline) > 10:
            out["aufgeräumt"] = self.prune(now)
            self.store.kv_set("guardian:prune_day", day)
        if self.time_left(deadline) > 8:
            out["kette"] = self.check_chain(now)
        week = now.strftime("%G-W%V")
        if self.store.kv_get("guardian:check_week") != week and self.time_left(deadline) > 10:
            out["integrität"] = self.integrity()
            self.store.kv_set("guardian:check_week", week)
        return out

    # ------------------------------------------------------------------ Prüfungen
    def check_resources(self, res):
        if res["rss_mb"] and res["rss_mb"] > RAM_WARN_MB:
            events.emit(self.store, "hoch", "ressourcen", f"Arbeitsspeicher hoch: {res['rss_mb']:.0f} MB",
                        f"Warnschwelle {RAM_WARN_MB} MB. Üblich sind 200–300 MB.", key="ram", agent=self.id)
        if res["disk_total_gb"] and res["disk_free_gb"] is not None and res["disk_free_gb"] / res["disk_total_gb"] < DISK_WARN_FREE:
            events.emit(self.store, "hoch", "ressourcen", f"Speicherplatz knapp: {res['disk_free_gb']:.2f} GB frei",
                        f"von {res['disk_total_gb']:.2f} GB", key="disk", agent=self.id, dedupe_h=24)

    def check_sources(self):
        down = set()
        for s in sources.snapshot():
            if s["streak"] >= 5 or (s["paused_s"] > 0 and s["limited"]):
                down.add(s["id"])
                if s["id"] not in self._down_sources:
                    why = "Limit erreicht (429)" if s["paused_s"] > 0 and s["limited"] else f"{s['streak']} Fehler in Folge"
                    events.emit(self.store, "hoch", "quelle", f"Datenquelle gestört: {s['name']}",
                                f"{why}. Letzte Meldung: {s['last_msg'] or '–'}. Betroffene Prognosen werden ausgesetzt, nicht geraten.",
                                key=f"quelle:{s['id']}", agent=self.id)
        for sid in self._down_sources - down:
            name = sources.SOURCES.get(sid, {}).get("name", sid)
            events.emit(self.store, "normal", "quelle", f"Datenquelle wieder erreichbar: {name}", agent=self.id, key=f"quelle-ok:{sid}", dedupe_h=1)
        if down != self._down_sources:
            self._down_sources = down
            self.store.kv_set("guardian:down_sources", json.dumps(sorted(down)))
        return len(down)

    def check_agents(self):
        if not self.registry:
            return 0
        down = set()
        for s in self.registry.view():
            if s["error_streak"] >= 3:
                down.add(s["id"])
                if s["id"] not in self._down_agents:
                    events.emit(self.store, "hoch", "agent", f"Agent „{s['name']}“ scheitert wiederholt",
                                f"{s['error_streak']} Fehlläufe in Folge. Letzter Fehler: {s['last_error']}", key=f"agent:{s['id']}", agent=self.id)
        for aid in self._down_agents - down:
            events.emit(self.store, "normal", "agent", f"Agent „{self.registry.agents[aid].name}“ läuft wieder", agent=self.id,
                        key=f"agent-ok:{aid}", dedupe_h=1)
        self._down_agents = down
        return len(down)

    def backup(self, day):
        try:
            name, size = self.store.backup(config.BACKUP_KEEP)
        except Exception as e:  # noqa: BLE001
            events.emit(self.store, "hoch", "sicherung", "Tägliche Sicherung fehlgeschlagen", f"{type(e).__name__}: {e}", key="backup-fail", agent=self.id)
            return "fehlgeschlagen"
        self.store.kv_set("guardian:backup_day", day)
        self.store.kv_set("guardian:backup_last", json.dumps({"datei": os.path.basename(name), "mb": round(size / 1048576, 2), "tag": day}))
        events.emit(self.store, "info", "sicherung", f"Datenbank gesichert ({size / 1048576:.1f} MB)",
                    f"{os.path.basename(name)} · die letzten {config.BACKUP_KEEP} Tage bleiben erhalten", agent=self.id, telegram=False)
        return os.path.basename(name)

    def prune(self, now):
        """Aufbewahrungsregeln. Prognose-Historie, Ergebnisse, Wetten und Berichte werden nie gelöscht."""
        iso = lambda days: (now - timedelta(days=days)).isoformat(timespec="seconds")  # noqa: E731
        snap_cut = (now - timedelta(days=config.RETAIN_SNAPSHOT_DAYS)).strftime("%Y-%m-%dT%H:00")
        n = {}
        # stündliche Preispunkte: nach der Frist bleibt nur noch der Punkt um 00:00 Uhr je Tag
        n["preispunkte"] = self.store.x("DELETE FROM snapshots WHERE ts<? AND substr(ts, 12, 2)!='00'", (snap_cut,)).rowcount
        n["ereignisse"] = self.store.x("DELETE FROM events WHERE ts<?", (iso(config.RETAIN_EVENT_DAYS),)).rowcount
        n["agentenläufe"] = self.store.x("DELETE FROM agent_runs WHERE started_at<?", (iso(config.RETAIN_RUN_DAYS),)).rowcount
        n["quellenstatistik"] = self.store.x("DELETE FROM source_stats WHERE day<?", (iso(90)[:10],)).rowcount
        # Katalog-Märkte ohne Modell, die seit 60 Tagen nicht mehr gesehen wurden und zu denen nichts gespeichert ist
        n["katalog"] = self.store.x("DELETE FROM markets WHERE modeled=0 AND last_seen<? AND id NOT IN (SELECT market_id FROM forecasts) "
                                    "AND id NOT IN (SELECT market_id FROM snapshots)", (iso(60),)).rowcount
        if sum(n.values()):
            events.emit(self.store, "info", "aufräumen", "Alte Betriebsdaten aufgeräumt",
                        ", ".join(f"{k}: {v}" for k, v in n.items() if v), agent=self.id, telegram=False)
        return n

    def check_chain(self, now):
        """Prüfsummen der Prognose-Historie nachrechnen (laufend nur die neuen Zeilen, wöchentlich alles)."""
        if not self.probability:
            return None
        week = now.strftime("%G-W%V")
        full = self.store.kv_get("guardian:chain_week") != week
        start = 0 if full else int(self.store.kv_get("guardian:chain_id") or 0)
        ok, n, bad, last = self.probability.verify_chain(start)
        if not ok:
            events.emit(self.store, "kritisch", "integrität", "Prognose-Historie wurde verändert",
                        f"Die Prüfsumme stimmt ab Zeile {bad} nicht mehr. Die Auswertungen sind ab dort nicht mehr vertrauenswürdig.",
                        key="kette", agent=self.id, dedupe_h=24)
            return f"FEHLER ab Zeile {bad}"
        self.store.kv_set("guardian:chain_id", last)
        if full:
            self.store.kv_set("guardian:chain_week", week)
        self.store.kv_set("guardian:chain_ok", json.dumps({"ts": now.isoformat(timespec="seconds"), "bis": last}))
        return f"ok (+{n})"

    def integrity(self):
        try:
            res = self.store.scalar("PRAGMA quick_check")
        except Exception as e:  # noqa: BLE001
            res = f"{type(e).__name__}: {e}"
        self.store.kv_set("guardian:integrity", json.dumps({"ergebnis": res, "ts": time.time()}))
        if res != "ok":
            events.emit(self.store, "kritisch", "integrität", "Datenbank-Prüfung meldet Fehler", str(res)[:500], key="dbcheck", agent=self.id)
        return res

    def view(self):
        folder = os.path.join(os.path.dirname(self.store.path), "backups")
        backups = []
        if os.path.isdir(folder):
            for f in sorted(os.listdir(folder), reverse=True)[:12]:
                p = os.path.join(folder, f)
                backups.append({"datei": f, "mb": round(os.path.getsize(p) / 1048576, 2), "zeit": os.path.getmtime(p)})
        return {"aktuell": self.latest or read_resources(config.DATA_DIR, self.store.path),
                "verlauf": self.store.kv_json("guardian:res", []), "sicherungen": backups,
                "integrität": self.store.kv_json("guardian:integrity", None), "kette": self.store.kv_json("guardian:chain_ok", None),
                "schema": self.store.schema_version, "schwellen": {"ram_mb": RAM_WARN_MB, "platte_frei": DISK_WARN_FREE}}
