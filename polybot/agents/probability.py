"""Probability Agent: Modell-Register, Varianten je Markt und die unveränderliche Prognose-Historie.

Jedes Modell liefert pro Markt eine oder mehrere Varianten („Modell@Version“ -> Wahrscheinlichkeit).
Genau eine Variante je Kategorie ist aktiv (model_registry) und geht ins Paper-Trading; alle anderen
laufen als Herausforderer nur mit und werden später vom Learning Agent gegen das Ergebnis gemessen.
Ein Wechsel des aktiven Modells passiert nur über `activate` (Freigabe im Dashboard) und ist rückholbar.
"""
import hashlib
import json
import math
import re
from datetime import datetime, timezone

from . import Agent

DEFAULTS = {"crypto": ("crypto.options", "1"), "weather": ("weather.ens", "2"), "sports": ("sports.books", "1"),
            "base": ("base.calib", "1")}   # „base“ = alle Märkte ohne eigenes Datenmodell
HORIZONS = [(6, "<6h"), (24, "6-24h"), (72, "24-72h"), (float("inf"), ">72h")]
MODEL_INFO = {
    "crypto.options": "Krypto: erwartete Schwankung aus Deribit-Optionen (inkl. Smile)",
    "crypto.hist": "Krypto: Schwankung aus 30 Tagen Kurshistorie",
    "crypto.histprem": "Krypto: Kurshistorie mit Aufschlag (wenn keine Optionsdaten da sind)",
    "weather.ens": "Wetter: Ensemble aus ECMWF, GFS, ICON, enge Verteilung",
    "weather.wide": "Wetter: dasselbe Ensemble, doppelt so breite Verteilung",
    "weather.emp": "Wetter: Ensemble-Mittel + gelernte Stations-Korrektur und gemessene Streuung",
    "weather.live": "Wetter am Messtag: bisher gemessener Höchst-/Tiefstwert (METAR) + korrigierte Vorhersage für die restlichen Stunden",
    "weather.live.wide": "Wetter am Messtag: wie oben, doppelt so breite Verteilung",
    "sports.books": "Sport: faire Quote aus Buchmachern (Marge herausgerechnet)",
    "base.calib": "Alle übrigen Märkte: Basisraten – wie oft trat „Ja“ bei Märkten gleicher Art und gleichem Kurs wirklich ein",
    "crypto.v1": "Krypto: Prognosen aus der Zeit vor v2 (Modellstand nicht mitgespeichert)",
    "weather.v1": "Wetter: Prognosen aus der Zeit vor v2 (Modellstand nicht mitgespeichert)",
    "sports.v1": "Sport: Prognosen aus der Zeit vor v2 (Modellstand nicht mitgespeichert)",
}


LEGACY_VERSION = "alt"   # Prognosen aus der Zeit vor v2 (ohne Modell-Angabe gespeichert)
_DATE = re.compile(r"(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}")
_CITY = re.compile(r"temperature in (.+?) be ")
_COIN = re.compile(r"\b(Bitcoin|Ethereum|Solana|XRP|Dogecoin|BNB|Cardano|Hyperliquid|BTC|ETH|SOL|DOGE)\b")


def legacy_cluster(category, question):
    """Gruppe zusammenhängender Alt-Prognosen aus dem Fragetext (Stadt bzw. Coin + Stichtag); None, wenn nicht erkennbar."""
    d = _DATE.search(question or "")
    if category == "weather":
        c = _CITY.search(question or "")
        return f"alt:{c.group(1)}:{d.group(0)}" if c and d else None
    if category == "crypto":
        c = _COIN.search(question or "")
        return f"alt:{c.group(1)}:{d.group(0)}" if c and d else None
    return None


def horizon(hours):
    for limit, name in HORIZONS:
        if hours < limit:
            return name
    return ">72h"


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def lead_bucket(lead_h):
    return "0" if lead_h < 12 else "1" if lead_h < 36 else "2" if lead_h < 60 else "3"


def weather_emp(f, params):
    """Gelerntes Wetter-Modell: Normalverteilung um (Ensemble-Mittel + Stations-Korrektur) mit gemessener Streuung."""
    if not f or f.get("ens_mean") is None or (f.get("lo") is None and f.get("hi") is None):
        return None
    unit, kind = f["unit"], f["kind"]
    bias = params.get("bias", {}).get(f"{f['icao']}|{kind}")
    if bias is None:
        bias = params.get("bias_kind", {}).get(f"{unit}|{kind}", 0.0)
    sd = params.get("sd", {}).get(f"{unit}|{lead_bucket(f.get('lead_h', 24))}") or params.get("sd", {}).get(unit)
    if not sd or sd <= 0:
        return None
    mu = f["ens_mean"] + bias
    a = 0.0 if f.get("lo") is None else _ncdf((f["lo"] - mu) / sd)
    b = 1.0 if f.get("hi") is None else _ncdf((f["hi"] - mu) / sd)
    return max(0.0, b - a)


class ProbabilityAgent(Agent):
    id = "probability"
    name = "Probability"
    role = "Berechnet alle Modell-Varianten je Markt, wählt das aktive Modell und schreibt die Prognose-Historie"
    priority = 2
    budget = 25

    def __init__(self, store):
        super().__init__(store)
        for cat, (model, version) in DEFAULTS.items():
            if not store.q1("SELECT 1 FROM model_registry WHERE category=?", (cat,)):
                store.x("INSERT INTO model_registry(category, model, version, mix_w, activated_at, note) VALUES(?,?,?,?,?,?)",
                        (cat, model, version, 1.0, self._now(), "Ausgangsmodell"))
                store.x("INSERT INTO model_log(ts, category, action, model, version, mix_w, reason) VALUES(?,?,?,?,?,?,?)",
                        (self._now(), cat, "start", model, version, 1.0, "Ausgangsmodell"))
        self.reload()
        last = store.q1("SELECT hash FROM forecasts ORDER BY id DESC LIMIT 1")
        self._last_hash = last["hash"] if last else ""
        self.cycle = {"logged": 0, "variants": 0}
        self.legacy_imported = self.import_legacy()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def import_legacy(self):
        """Einmalig: die Prognosen aus der alten Tabelle `predictions` (v1.x) in die Historie übernehmen.

        Sie bekommen das Modell „<Bereich>.v1“ in Version „alt“, weil damals nicht mitgespeichert wurde, welcher
        Modellstand gerechnet hat. Gespeichert war je Markt die erste, ungemischte Modell-Prognose samt Marktpreis.
        Bereits bekannte Ergebnisse kommen mit. So beginnt die Auswertung nicht bei null.
        """
        if self.store.kv_get("legacy_import"):
            return 0
        rows, outcomes, prev = [], [], self._last_hash
        for r in self.store.q("SELECT * FROM predictions WHERE own_p IS NOT NULL AND market_p IS NOT NULL AND created_at IS NOT NULL "
                              "AND end_date IS NOT NULL ORDER BY created_at, market_id"):
            try:
                made = datetime.fromisoformat(r["created_at"])
                hours = (datetime.fromisoformat(r["end_date"]) - made).total_seconds() / 3600
            except (TypeError, ValueError):
                continue
            if not (0 < r["own_p"] < 1) or hours <= 0:
                continue
            model, hz = f"{r['category']}.v1", horizon(hours)
            h = hashlib.sha256(f"{prev}|{r['market_id']}|{model}|{LEGACY_VERSION}|{hz}|{r['created_at']}|{r['own_p']:.6f}|{r['market_p']:.6f}".encode()).hexdigest()[:32]
            rows.append((r["market_id"], r["category"], model, LEGACY_VERSION, hz, r["created_at"], round(r["own_p"], 6), round(r["market_p"], 6), None,
                         round(hours, 2), 1, 1, None, legacy_cluster(r["category"], r["question"]), '["übernommen aus v1.3"]', None, prev, h))
            prev = h
            if r["outcome"] is not None:
                outcomes.append((r["market_id"], r["outcome"], r["resolved_at"] or r["created_at"], "polymarket (v1.3)"))
        if rows:
            # Die Prüfsumme rechnet mit den gerundeten Werten, wie sie in der Tabelle stehen
            fixed, prev = [], self._last_hash
            for row in rows:
                h = hashlib.sha256(f"{prev}|{row[0]}|{row[2]}|{row[3]}|{row[4]}|{row[5]}|{row[6]:.6f}|{row[7]:.6f}".encode()).hexdigest()[:32]
                fixed.append(row[:16] + (prev, h))
                prev = h
            self.store.xmany("INSERT OR IGNORE INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, uncertainty, "
                             "hours_left, champion, valid, reject_reason, cluster, sources, features, prev_hash, hash) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", fixed)
            self._last_hash = prev
        if outcomes:
            self.store.xmany("INSERT OR IGNORE INTO outcomes(market_id, outcome, resolved_at, source) VALUES(?,?,?,?)", outcomes)
        self.store.kv_set("legacy_import", f"{self._now()}: {len(rows)} Prognosen, {len(outcomes)} Ergebnisse")
        return len(rows)

    def reload(self):
        self.reg = {r["category"]: r for r in self.store.q("SELECT * FROM model_registry")}
        self.emp = {}
        latest = self.store.q1("SELECT version, params FROM model_versions WHERE model='weather.emp' ORDER BY id DESC LIMIT 1")
        if latest:
            self.emp[latest["version"]] = json.loads(latest["params"])
        w = self.reg.get("weather")
        if w and w["model"] == "weather.emp" and w["version"] not in self.emp:
            row = self.store.q1("SELECT params FROM model_versions WHERE model='weather.emp' AND version=?", (w["version"],))
            if row:
                self.emp[w["version"]] = json.loads(row["params"])

    # ------------------------------------------------------------------ Auswahl des aktiven Modells
    def select(self, m, a, mkt_p):
        """Ergänzt gelernte Varianten und setzt a['p_yes'] auf die Variante des aktiven Modells."""
        cat = "base" if a.get("lane") == "base" else m["category"]
        clamp = lambda v: min(max(v, 0.001), 0.999)  # noqa: E731
        variants = a.setdefault("variants", {})
        if not variants:
            variants[a.get("model_id") or f"{cat}.modell@1"] = a["p_yes"]
        if cat == "weather" and a.get("features"):
            for ver, params in self.emp.items():
                p = weather_emp(a["features"], params)
                if p is not None:
                    variants[f"weather.emp@{ver}"] = clamp(p)
        reg = self.reg.get(cat)
        key = f"{reg['model']}@{reg['version']}" if reg else a.get("model_id")
        if key not in variants:  # aktives Modell hat für diesen Markt nichts geliefert -> Ersatz des Modells selbst
            key = a.get("model_id") if a.get("model_id") in variants else next(iter(variants))
        p = variants[key]
        a["p_model"] = p                  # reiner Modellwert vor einer Mischung – daran prüft die Validierung die Stimmigkeit
        w = reg["mix_w"] if reg and reg["mix_w"] is not None else 1.0
        if w < 0.999:
            p = w * p + (1 - w) * mkt_p
            model, ver = key.split("@", 1)
            key = f"{model}.mix@{ver}w{int(round(w * 100))}"
            variants[key] = clamp(p)
            a["info"] = f"{a.get('info', '')} · gemischt {w * 100:.0f} % Modell / {(1 - w) * 100:.0f} % Markt"
        a["p_yes"] = clamp(p)
        a["model_id"] = key
        return a

    # ------------------------------------------------------------------ Prognose-Historie
    def log(self, results, now):
        """Schreibt je Markt, Variante und Zeithorizont genau eine Zeile (nur Anfügen)."""
        if not results:
            return {"neu": 0}
        ids = [o["market"]["id"] for o in results]
        have = set()
        for i in range(0, len(ids), 800):
            chunk = ids[i:i + 800]
            for r in self.store.q("SELECT market_id, model, version, horizon FROM forecasts WHERE market_id IN (%s)"
                                  % ",".join("?" * len(chunk)), chunk):
                have.add((r["market_id"], r["model"], r["version"], r["horizon"]))
        stamp = now.isoformat(timespec="seconds")
        rows, prev = [], self._last_hash
        for o in results:
            m, a = o["market"], o["analysis"]
            hours = a.get("hours")
            if hours is None and m.get("end_date"):
                hours = (m["end_date"] - now).total_seconds() / 3600
            if hours is None or hours <= 0:
                continue
            hz = horizon(hours)
            champ = a.get("model_id")
            for key, p in a.get("variants", {}).items():
                model, _, version = key.partition("@")
                k = (m["id"], model, version or "1", hz)
                if k in have:
                    continue
                have.add(k)
                is_champ = key == champ
                h = hashlib.sha256(f"{prev}|{m['id']}|{model}|{version}|{hz}|{stamp}|{p:.6f}|{o['market_p']:.6f}".encode()).hexdigest()[:32]
                rows.append((m["id"], m["category"], model, version or "1", hz, stamp, round(p, 6), round(o["market_p"], 6),
                             round(a.get("uncertainty"), 4) if is_champ and a.get("uncertainty") is not None else None,
                             round(hours, 2), 1 if is_champ else 0, 0 if a.get("valid") is False else 1,
                             a.get("reject") if is_champ else None, o.get("group"),
                             json.dumps(a.get("sources"), ensure_ascii=False) if is_champ and a.get("sources") else None,
                             json.dumps(a.get("features"), ensure_ascii=False) if a.get("features") and (is_champ or model in ("weather.ens", "form.elo")) else None,
                             prev, h))
                prev = h
        if rows:
            self.store.xmany("INSERT INTO forecasts(market_id, category, model, version, horizon, created_at, p, market_p, uncertainty, "
                             "hours_left, champion, valid, reject_reason, cluster, sources, features, prev_hash, hash) "
                             "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._last_hash = prev
        self.cycle = {"logged": len(rows), "variants": sum(len(o["analysis"].get("variants", {})) for o in results)}
        return {"neu": len(rows), "märkte": len(results)}

    def log_extra(self, results, now):
        """Zusätzliche Prognosen außerhalb der Modell-Berechnung (Form-Modell, 2.11) – nur mitschreiben, nie gehandelt
        als aktives Modell; die Zählung des Laufs bleibt die der Modell-Berechnung."""
        keep = dict(self.cycle)
        out = self.log(results, now)
        self.cycle = {**keep, "form": out.get("neu", 0)}
        return out

    def verify_chain(self, from_id=0, limit=200000):
        """Prüfsummen-Kette nachrechnen. Gibt (ok, geprüfte Zeilen, erste fehlerhafte ID, letzte geprüfte ID) zurück."""
        prev_row = self.store.q1("SELECT hash FROM forecasts WHERE id<=? ORDER BY id DESC LIMIT 1", (from_id,)) if from_id else None
        prev = prev_row["hash"] if prev_row else ""
        n, last_id = 0, from_id
        for r in self.store.q("SELECT id, market_id, model, version, horizon, created_at, p, market_p, prev_hash, hash FROM forecasts "
                              "WHERE id>? ORDER BY id LIMIT ?", (from_id, limit)):
            want = hashlib.sha256(f"{prev}|{r['market_id']}|{r['model']}|{r['version']}|{r['horizon']}|{r['created_at']}|"
                                  f"{r['p']:.6f}|{r['market_p']:.6f}".encode()).hexdigest()[:32]
            if r["prev_hash"] != prev or r["hash"] != want:
                return False, n, r["id"], last_id
            prev, last_id, n = r["hash"], r["id"], n + 1
        return True, n, None, last_id

    # ------------------------------------------------------------------ Freigabe / Rückholung
    def activate(self, category, model, version, mix_w=1.0, reason=""):
        cur = self.reg.get(category) or {}
        self.store.x("INSERT INTO model_registry(category, model, version, mix_w, prev_model, prev_version, prev_mix_w, activated_at, note) "
                     "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(category) DO UPDATE SET model=excluded.model, version=excluded.version, "
                     "mix_w=excluded.mix_w, prev_model=excluded.prev_model, prev_version=excluded.prev_version, "
                     "prev_mix_w=excluded.prev_mix_w, activated_at=excluded.activated_at, note=excluded.note",
                     (category, model, version, mix_w, cur.get("model"), cur.get("version"), cur.get("mix_w"), self._now(), reason[:300]))
        self.store.x("INSERT INTO model_log(ts, category, action, model, version, mix_w, reason) VALUES(?,?,?,?,?,?,?)",
                     (self._now(), category, "aktiviert", model, version, mix_w, reason[:300]))
        self.reload()

    def rollback(self, category, reason="zurückgeholt"):
        cur = self.reg.get(category)
        if not cur or not cur.get("prev_model"):
            return False
        self.activate(category, cur["prev_model"], cur["prev_version"], cur["prev_mix_w"] if cur["prev_mix_w"] is not None else 1.0, reason)
        return True

    def run(self, now, deadline):
        return dict(self.cycle)

    def view(self):
        return {"aktiv": [{**r, "info": MODEL_INFO.get(r["model"], "")} for r in self.reg.values()],
                "modelle": MODEL_INFO,
                "verlauf": self.store.q("SELECT * FROM model_log ORDER BY id DESC LIMIT 40"),
                "versionen": self.store.q("SELECT id, model, version, n_samples, created_at, note FROM model_versions ORDER BY id DESC LIMIT 30")}
