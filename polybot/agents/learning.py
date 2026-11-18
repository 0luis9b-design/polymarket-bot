"""Learning Agent: misst die Prognosen, sucht Fehlermuster und schlägt Verbesserungen vor – ohne selbst etwas zu aktivieren.

Ablauf (stündlich):
  1. Kennzahlen je Modell, Kategorie und Zeithorizont (Brier, LogLoss, Treffer, Verzerrung) – immer neben dem
     Marktpreis vom Prognose-Zeitpunkt und der 50-%-Baseline.
  2. Fehleranalyse: Über-/Unterschätzung, übermäßige Sicherheit, schwache Stationen, Nutzen der Validierung.
  3. Wetter: Stations-Korrektur und gemessene Streuung als neue Parameter-Version (Herausforderer „weather.emp“).
  4. Experimente: jeder Herausforderer gegen das aktive Modell, getrennt nach Zeit (erste 60 % / letzte 40 %).
  5. Vorschlag nur, wenn die späteren Daten klar für den Herausforderer sprechen. Aktiviert wird erst nach Freigabe.

Statistik-Hinweis: Prognosen desselben Ereignisses hängen zusammen (11 Temperatur-Bereiche eines Tages).
Deshalb wird die Sicherheit eines Unterschieds über Ereignis-Gruppen („cluster“) gerechnet, nicht über Einzelzeilen.
"""
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import config, events
from . import Agent

HORIZON_ORDER = ["<6h", "6-24h", "24-72h", ">72h"]
MIX_WEIGHTS = (0.75, 0.5, 0.25, 0.0)


def traded_key(reg):
    """(Schlüssel der gehandelten Variante, Schlüssel des reinen Modells) für einen Registereintrag – oder (None, None)."""
    if not reg:
        return None, None
    base = f"{reg['model']}@{reg['version']}"
    w = reg["mix_w"] if reg.get("mix_w") is not None else 1.0
    if w >= 0.999:
        return base, base
    return f"{reg['model']}.mix@{reg['version']}w{int(round(w * 100))}", base
CAT_NAMES = {"crypto": "Krypto", "sports": "Sport", "weather": "Wetter"}


def brier(rows, key="p"):
    return sum((r[key] - r["outcome"]) ** 2 for r in rows) / len(rows) if rows else None


def logloss(rows, key="p"):
    if not rows:
        return None
    s = 0.0
    for r in rows:
        p = min(max(r[key], 0.001), 0.999)
        s -= r["outcome"] * math.log(p) + (1 - r["outcome"]) * math.log(1 - p)
    return s / len(rows)


def paired_z(rows, a="p", b="market_p"):
    """Wie sicher ist a besser als b? Negativ = a besser. Gerechnet über Gruppen zusammenhängender Prognosen."""
    by = defaultdict(list)
    for r in rows:
        by[r.get("cluster") or r["market_id"]].append((r[a] - r["outcome"]) ** 2 - (r[b] - r["outcome"]) ** 2)
    means = [sum(v) / len(v) for v in by.values()]
    n = len(means)
    if n < 2:
        return None, n
    sd = statistics.stdev(means)
    if sd == 0:
        return 0.0, n
    return (sum(means) / n) / (sd / math.sqrt(n)), n


def summarize(rows):
    """Kennzahlen einer Gruppe von Prognosen mit Ergebnis."""
    if not rows:
        return None
    n = len(rows)
    b, bm = brier(rows), brier(rows, "market_p")
    z, clusters = paired_z(rows)
    hits = sum(1 for r in rows if (r["p"] >= 0.5) == (r["outcome"] >= 0.5))
    return {"n": n, "clusters": clusters, "brier": b, "brier_market": bm, "brier_half": 0.25,
            "logloss": logloss(rows), "logloss_market": logloss(rows, "market_p"),
            "hit": hits / n, "bias": sum(r["p"] - r["outcome"] for r in rows) / n,
            "skill": (1 - b / bm) if bm else None, "z": z}


def calibration(rows, key="p"):
    out = []
    for i in range(10):
        lo, hi = i / 10, (i + 1) / 10
        sel = [r for r in rows if lo <= r[key] < hi or (i == 9 and r[key] == 1.0)]
        if sel:
            out.append({"von": lo, "bis": hi, "n": len(sel), "p": sum(r[key] for r in sel) / len(sel),
                        "ist": sum(r["outcome"] for r in sel) / len(sel)})
    return out


def time_split(rows, share=0.6):
    """Teilt nach Zeit an Gruppen-Grenzen: frühe Gruppen = Vergleichsteil, späte = Out-of-Sample."""
    first = {}
    for r in rows:
        c = r.get("cluster") or r["market_id"]
        if c not in first or r["created_at"] < first[c]:
            first[c] = r["created_at"]
    order = sorted(first, key=lambda c: first[c])
    cut = set(order[:int(len(order) * share)])
    early = [r for r in rows if (r.get("cluster") or r["market_id"]) in cut]
    late = [r for r in rows if (r.get("cluster") or r["market_id"]) not in cut]
    return early, late


class LearningAgent(Agent):
    id = "learning"
    name = "Learning"
    role = "Misst alle Prognosen gegen Ergebnis und Markt, analysiert Fehler, testet Herausforderer und schlägt Freigaben vor"
    interval = 3600
    budget = 60
    priority = 4

    def __init__(self, store, probability):
        super().__init__(store)
        self.probability = probability

    # ------------------------------------------------------------------ Daten
    def rows(self, days=120):
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        rows = self.store.q(
            "SELECT f.id, f.market_id, f.category, f.model, f.version, f.horizon, f.created_at, f.p, f.market_p, f.champion, "
            "f.valid, f.reject_reason, f.cluster, o.outcome, o.resolved_at FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id "
            "WHERE f.created_at>=? AND f.market_p IS NOT NULL", (since,))
        for r in rows:
            r["key"] = f"{r['model']}@{r['version']}"
        return rows

    # ------------------------------------------------------------------ Hauptlauf
    def run(self, now, deadline):
        rows = self.rows()
        summary = {"stand": now.isoformat(timespec="seconds"), "n": len(rows), "modelle": self.metrics(rows), "bereiche": self.areas(rows),
                   "kalibrierung": self.calibrations(rows), "fehler": self.errors(rows), "verlauf": self.timeline(rows)}
        new_version = self.learn_weather(now) if self.time_left(deadline) > 10 else None
        exps = self.experiments(rows, now) if self.time_left(deadline) > 5 else []
        summary["experimente"] = len(exps)
        self.store.kv_set("learn:summary", json.dumps(summary, ensure_ascii=False))
        return {"prognosen_mit_ergebnis": len(rows), "experimente": len(exps), "neue_wetter_parameter": new_version,
                "offene_vorschläge": self.store.scalar("SELECT COUNT(*) FROM proposals WHERE status='offen'")}

    def summary(self):
        return self.store.kv_json("learn:summary", None)

    # ------------------------------------------------------------------ 1. Kennzahlen
    def metrics(self, rows):
        groups = defaultdict(list)
        for r in rows:
            groups[(r["category"], r["key"])].append(r)
        reg = self.probability.reg
        out = []
        for (cat, key), rs in sorted(groups.items()):
            s = summarize(rs)
            traded, base = traded_key(reg.get(cat))
            # „aktiv“ = genau die Variante, mit der jetzt gehandelt wird. Ist eine Mischung freigegeben, ist das die Mischung
            # mit dem freigegebenen Anteil; das reine Modell darunter heißt „Grundlage“ (bis 2.4 trugen beide den Stern).
            s.update(category=cat, model=key, active=key == traded, grundlage=bool(base and key == base and key != traded),
                     gehandelt=sum(1 for r in rs if r["champion"]),
                     horizonte={h: summarize([r for r in rs if r["horizon"] == h]) for h in HORIZON_ORDER
                                if any(r["horizon"] == h for r in rs)},
                     nur_gueltig=summarize([r for r in rs if r["valid"]]))
            out.append(s)
        return out

    def areas(self, rows):
        """Je Bereich: alle Prognosen, nach denen tatsächlich entschieden wurde (auch die aus der Zeit vor v2)."""
        out = []
        for cat in sorted({r["category"] for r in rows}):
            rs = [r for r in rows if r["category"] == cat and r["champion"]]
            if rs:
                s = summarize(rs)
                models = sorted({r["key"] for r in rs})
                s.update(category=cat, modelle=models, alt=sum(1 for r in rs if r["version"] == "alt"))
                out.append(s)
        return out

    def calibrations(self, rows):
        out = {}
        for cat in sorted({r["category"] for r in rows}):
            champ = [r for r in rows if r["category"] == cat and r["champion"]]
            out[cat] = {"modell": calibration(champ), "markt": calibration(champ, "market_p"), "n": len(champ)}
        return out

    def timeline(self, rows):
        by = defaultdict(list)
        for r in rows:
            if r["champion"]:
                by[(r["resolved_at"][:10], r["category"])].append(r)
        return [{"tag": d, "category": c, "n": len(rs), "brier": brier(rs), "brier_market": brier(rs, "market_p"),
                 "logloss": logloss(rs), "logloss_market": logloss(rs, "market_p")}
                for (d, c), rs in sorted(by.items())]

    # ------------------------------------------------------------------ 2. Fehleranalyse
    def errors(self, rows):
        champ = [r for r in rows if r["champion"]]
        out = {"kategorien": [], "schlimmste": [], "stationen": [], "validierung": []}
        for cat in sorted({r["category"] for r in champ}):
            rs = [r for r in champ if r["category"] == cat]
            bias = sum(r["p"] - r["outcome"] for r in rs) / len(rs)
            sure_wrong = [r for r in rs if (r["p"] >= 0.9 and r["outcome"] == 0) or (r["p"] <= 0.1 and r["outcome"] == 1)]
            sure = [r for r in rs if r["p"] >= 0.9 or r["p"] <= 0.1]
            mkt_sure = [r for r in rs if r["market_p"] >= 0.9 or r["market_p"] <= 0.1]
            mkt_wrong = [r for r in mkt_sure if (r["market_p"] >= 0.9 and r["outcome"] == 0) or (r["market_p"] <= 0.1 and r["outcome"] == 1)]
            findings = []
            if len(rs) >= 30:
                if bias > 0.03:
                    findings.append(f"systematische Überschätzung (+{bias * 100:.1f} Pkt)")
                elif bias < -0.03:
                    findings.append(f"systematische Unterschätzung ({bias * 100:.1f} Pkt)")
                if sure and mkt_sure and len(sure) >= 20 and len(sure_wrong) / len(sure) > 2 * max(len(mkt_wrong) / len(mkt_sure), 0.01):
                    findings.append("übermäßige Sicherheit: sehr sichere Prognosen liegen deutlich öfter falsch als die des Marktes")
                s = summarize(rs)
                if s["skill"] is not None and s["skill"] < -0.05 and (s["z"] or 0) > 1.64:
                    findings.append("klar schlechter als der Marktpreis")
            out["kategorien"].append({"category": cat, "n": len(rs), "bias": bias, "sicher": len(sure), "sicher_falsch": len(sure_wrong),
                                      "markt_sicher": len(mkt_sure), "markt_sicher_falsch": len(mkt_wrong), "befunde": findings})
        worst = sorted(champ, key=lambda r: -((r["p"] - r["outcome"]) ** 2))[:12]
        names = {}
        if worst:
            ids = [r["market_id"] for r in worst]
            for q in self.store.q("SELECT id, question FROM markets WHERE id IN (%s)" % ",".join("?" * len(ids)), ids):
                names[q["id"]] = q["question"]
        out["schlimmste"] = [{"market_id": r["market_id"], "frage": names.get(r["market_id"], r["market_id"]), "category": r["category"],
                              "modell": r["key"], "p": r["p"], "markt": r["market_p"], "ergebnis": r["outcome"], "horizont": r["horizon"]}
                             for r in worst]
        # Wetter je Station
        st = defaultdict(list)
        for f in self.store.q("SELECT f.p, f.market_p, f.features, o.outcome FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id "
                              "WHERE f.category='weather' AND f.champion=1 AND f.features IS NOT NULL"):
            try:
                st[json.loads(f["features"])["icao"]].append(f)
            except (ValueError, KeyError):
                continue
        rows_st = [{"station": k, "n": len(v), "brier": brier(v), "brier_market": brier(v, "market_p")} for k, v in st.items() if len(v) >= 10]
        out["stationen"] = sorted(rows_st, key=lambda r: (r["brier_market"] or 0) - (r["brier"] or 0))[:10]
        # War die Validierung berechtigt? Abgelehnte Prognosen: Modell gegen Markt
        rej = defaultdict(list)
        for r in champ:
            if not r["valid"]:
                rej[(r["reject_reason"] or "ohne Grund").split(" (")[0].split(" =")[0]].append(r)
        for reason, rs in sorted(rej.items(), key=lambda x: -len(x[1])):
            out["validierung"].append({"grund": reason, "n": len(rs), "brier": brier(rs), "brier_market": brier(rs, "market_p")})
        return out

    # ------------------------------------------------------------------ 3. Wetter-Parameter lernen
    def weather_residuals(self):
        """Beobachtete Temperatur (aus dem gewonnenen Bereich) minus Ensemble-Mittel zum Prognose-Zeitpunkt."""
        res = []
        for f in self.store.q("SELECT f.features, f.created_at FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id "
                              "WHERE f.category='weather' AND f.features IS NOT NULL AND o.outcome=1 AND f.model IN ('weather.ens','weather.emp')"):
            try:
                x = json.loads(f["features"])
            except ValueError:
                continue
            if x.get("lo") is None or x.get("hi") is None or x.get("ens_mean") is None:
                continue  # offene Rand-Bereiche („≤ 5 °C“) sagen nicht, wie warm es genau war
            res.append({"icao": x["icao"], "kind": x["kind"], "unit": x["unit"], "lead": x.get("lead_h", 24),
                        "r": (x["lo"] + x["hi"]) / 2 - x["ens_mean"], "date": x.get("date"), "at": f["created_at"]})
        return res

    def learn_weather(self, now, min_n=30):
        from .probability import lead_bucket
        res = self.weather_residuals()
        if len(res) < min_n:
            return None
        last = self.store.q1("SELECT version, n_samples, created_at FROM model_versions WHERE model='weather.emp' ORDER BY id DESC LIMIT 1")
        if last and (last["created_at"][:10] == now.strftime("%Y-%m-%d") or len(res) < last["n_samples"] * 1.2):
            return None
        by_kind, by_station = defaultdict(list), defaultdict(list)
        for x in res:
            by_kind[f"{x['unit']}|{x['kind']}"].append(x["r"])
            by_station[f"{x['icao']}|{x['kind']}"].append(x["r"])
        bias_kind = {k: sum(v) / len(v) for k, v in by_kind.items()}
        # Stations-Korrektur zur Mitte geschrumpft: wenige Tage -> wenig Vertrauen (n / (n + 5))
        bias = {}
        for k, v in by_station.items():
            icao, kind = k.split("|")
            unit = next(x["unit"] for x in res if x["icao"] == icao)
            g = bias_kind.get(f"{unit}|{kind}", 0.0)
            w = len(v) / (len(v) + 5)
            bias[k] = round(w * (sum(v) / len(v)) + (1 - w) * g, 3)
        sd_all, sd_lead = defaultdict(list), defaultdict(list)
        for x in res:
            b = bias.get(f"{x['icao']}|{x['kind']}", 0.0)
            sd_all[x["unit"]].append((x["r"] - b) ** 2)
            sd_lead[f"{x['unit']}|{lead_bucket(x['lead'])}"].append((x["r"] - b) ** 2)
        floor = {"C": 0.6, "F": 1.1}
        sd = {u: round(max(floor.get(u, 0.6), math.sqrt(sum(v) / len(v)) * 1.1), 3) for u, v in sd_all.items()}
        for k, v in sd_lead.items():
            if len(v) >= 20:
                sd[k] = round(max(floor.get(k[0], 0.6), math.sqrt(sum(v) / len(v)) * 1.1), 3)
        version = str(int(last["version"]) + 1) if last else "1"
        params = {"bias": bias, "bias_kind": {k: round(v, 3) for k, v in bias_kind.items()}, "sd": sd, "n": len(res)}
        self.store.x("INSERT INTO model_versions(model, version, params, n_samples, created_at, note) VALUES(?,?,?,?,?,?)",
                     ("weather.emp", version, json.dumps(params), len(res), now.isoformat(timespec="seconds"),
                      f"aus {len(res)} entschiedenen Tagen/Stationen; Streuung " + ", ".join(f"{k}: {v}" for k, v in sd.items() if '|' not in k)))
        self.probability.reload()
        events.emit(self.store, "info", "lernen", f"Neue Wetter-Parameter (Version {version}) aus {len(res)} Messungen",
                    "Läuft als Herausforderer „weather.emp“ mit – ohne Einfluss aufs Handeln, bis eine Freigabe erfolgt.",
                    agent=self.id, telegram=False)
        return version

    # ------------------------------------------------------------------ 4./5. Experimente und Vorschläge
    def experiments(self, rows, now):
        """Herausforderer gegen das, womit jetzt gehandelt wird.

        Seit 2.5: Ist eine Mischung freigegeben (z. B. 50 % Modell), ist sie der Maßstab – nicht mehr das reine Modell.
        Bis 2.4 wurde immer gegen das reine Modell gemessen; dann bestand dieselbe Mischung nach ihrer Freigabe sofort
        wieder und wurde erneut vorgeschlagen (16.–18.11.: neun Wetter-Vorschläge, fünfmal dasselbe Modell aktiviert).
        """
        out = []
        for cat, reg in self.probability.reg.items():
            champ_key = f"{reg['model']}@{reg['version']}"
            w_cur = reg["mix_w"] if reg.get("mix_w") is not None else 1.0
            label = champ_key if w_cur >= 0.999 else f"{champ_key} ({int(round(w_cur * 100))} % Modell)"
            cat_rows = [r for r in rows if r["category"] == cat]
            # gehandelte Prognose = Mischung mit dem freigegebenen Anteil; das reine Modell bleibt unter „p_raw“
            champ = {(r["market_id"], r["horizon"]): {**r, "p_raw": r["p"], "p": w_cur * r["p"] + (1 - w_cur) * r["market_p"]}
                     for r in cat_rows if r["key"] == champ_key}
            if len(champ) < 20:
                continue
            challengers = defaultdict(dict)
            for r in cat_rows:
                if r["key"] != champ_key and ".mix@" not in r["key"] and r["version"] != "alt":   # Alt-Prognosen sind kein wählbares Modell
                    challengers[r["key"]][(r["market_id"], r["horizon"])] = r
            tests = []
            for key, rs in challengers.items():
                pairs = [{**champ[k], "chal": rs[k]["p"]} for k in rs if k in champ]
                tests.append((key, "modellwechsel", pairs))
            weights = MIX_WEIGHTS + ((1.0,) if w_cur < 0.999 else ())
            for w in weights:
                if abs(w - w_cur) < 0.005:
                    continue                                   # das ist schon freigegeben – nicht gegen sich selbst testen
                name = "Marktpreis" if w == 0 else ("Reines Modell (ohne Mischung)" if w == 1.0 else f"Mischung {int(w * 100)} % Modell")
                tests.append((name, "mischung", [{**r, "chal": w * r["p_raw"] + (1 - w) * r["market_p"], "w": w} for r in champ.values()]))
            for key, kind, pairs in tests:
                if len(pairs) < 20:
                    continue
                res = self.compare(pairs)
                res.update(category=cat, champion=label, challenger=key, kind=kind)
                self.store.x(
                    "INSERT INTO experiments(created_at, category, champion, challenger, n_is, n_oos, clusters_oos, brier_champ_oos, "
                    "brier_chal_oos, brier_market_oos, z, passed, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT(category, champion, challenger) DO UPDATE SET created_at=excluded.created_at, n_is=excluded.n_is, "
                    "n_oos=excluded.n_oos, clusters_oos=excluded.clusters_oos, brier_champ_oos=excluded.brier_champ_oos, "
                    "brier_chal_oos=excluded.brier_chal_oos, brier_market_oos=excluded.brier_market_oos, z=excluded.z, "
                    "passed=excluded.passed, detail=excluded.detail",
                    (now.isoformat(timespec="seconds"), cat, label, key, res["n_is"], res["n_oos"], res["clusters_oos"],
                     res["brier_champ_oos"], res["brier_chal_oos"], res["brier_market_oos"], res["z"], 1 if res["passed"] else 0,
                     json.dumps(res, ensure_ascii=False)))
                out.append(res)
            # Alte Ergebnisse gegen einen früheren Stand (anderes Modell oder anderer Mischanteil) gelten nicht mehr
            self.store.x("DELETE FROM experiments WHERE category=? AND created_at<?", (cat, now.isoformat(timespec="seconds")))
            # Nur der beste bestandene Herausforderer wird vorgeschlagen (sonst stapeln sich Vorschläge)
            passed = [r for r in out if r["category"] == cat and r["passed"]]
            if passed:
                self.propose(min(passed, key=lambda r: r["brier_chal_oos"]), now)
            self.check_regression(cat, reg, cat_rows, now)
        return out

    @staticmethod
    def compare(pairs):
        """Herausforderer gegen aktives Modell, früher Teil und späterer Teil getrennt."""
        early, late = time_split(pairs)

        def part(rs):
            if not rs:
                return None, None, None
            return brier(rs), brier(rs, "chal"), brier(rs, "market_p")
        bi, ci, mi = part(early)
        bo, co, mo = part(late)
        z, clusters = paired_z(late, "chal", "p") if late else (None, 0)
        gain = ((bo - co) / bo) if bo else None
        reasons = []
        if len(late) < config.EXPERIMENT_MIN_OOS:
            reasons.append(f"zu wenige spätere Prognosen ({len(late)}/{config.EXPERIMENT_MIN_OOS})")
        if clusters < config.EXPERIMENT_MIN_CLUSTERS:
            reasons.append(f"zu wenige unabhängige Ereignisse ({clusters}/{config.EXPERIMENT_MIN_CLUSTERS})")
        if gain is None or gain < config.EXPERIMENT_MIN_GAIN:
            reasons.append("nicht deutlich besser" if gain is not None and gain >= 0 else "schlechter")
        if z is None or z > -config.EXPERIMENT_MIN_Z:
            reasons.append("Unterschied statistisch nicht klar")
        if bi is not None and ci is not None and ci > bi:
            reasons.append("im früheren Teil schlechter (Hinweis auf Zufall)")
        return {"n_is": len(early), "n_oos": len(late), "clusters_oos": clusters, "brier_champ_is": bi, "brier_chal_is": ci,
                "brier_market_is": mi, "brier_champ_oos": bo, "brier_chal_oos": co, "brier_market_oos": mo, "z": z, "gain": gain,
                "passed": not reasons, "reasons": reasons, "w": pairs[0].get("w") if pairs else None}

    def propose(self, res, now):
        cat = res["category"]
        key = f"{res['kind']}:{cat}:{res['challenger']}"
        if self.store.q1("SELECT 1 FROM proposals WHERE key=? AND status='offen'", (key,)):
            return None
        # Abgelehnt? Dann frühestens nach PROPOSAL_REJECT_DAYS Tagen wieder fragen (bis 2.4: schon in der nächsten Stunde)
        cut = (now - timedelta(days=config.PROPOSAL_REJECT_DAYS)).isoformat(timespec="seconds")
        if self.store.q1("SELECT 1 FROM proposals WHERE key=? AND status='abgelehnt' AND decided_at>=?", (key, cut)):
            return None
        # Schon freigegeben und seitdem nichts am aktiven Modell geändert? Dann ist es kein neuer Vorschlag.
        reg = self.probability.reg.get(cat) or {}
        if res["kind"] == "mischung" and abs((reg.get("mix_w") if reg.get("mix_w") is not None else 1.0) - (res.get("w") or 0)) < 0.005:
            return None
        name = CAT_NAMES.get(cat, cat)
        gain = f"{res['gain'] * 100:.1f} %"
        if res["kind"] == "mischung" and res["w"] == 0:
            title = f"{name}: Der Marktpreis ist klar genauer als das eigene Modell – Modell für den Handel stilllegen"
        elif res["kind"] == "mischung" and res["w"] >= 0.999:
            title = f"{name}: Mischung aufheben – wieder das reine Modell „{res['champion'].split(' (')[0]}“ nutzen"
        elif res["kind"] == "mischung":
            w_cur = reg.get("mix_w") if reg.get("mix_w") is not None else 1.0
            now_txt = "" if w_cur >= 0.999 else f", bisher {int(round(w_cur * 100))} %"
            title = f"{name}: Prognose mit dem Marktpreis mischen ({int(res['w'] * 100)} % Modell{now_txt})"
        else:
            title = f"{name}: Modell „{res['challenger']}“ aktivieren (statt „{res['champion']}“)"
        cur = self.store.x("INSERT INTO proposals(created_at, kind, category, key, title, detail) VALUES(?,?,?,?,?,?)",
                           (now.isoformat(timespec="seconds"), res["kind"], cat, key, title, json.dumps(res, ensure_ascii=False)))
        events.emit(self.store, "normal", "vorschlag", f"Vorschlag zur Freigabe: {title}",
                    f"In {res['n_oos']} späteren Prognosen ({res['clusters_oos']} Ereignisse) Brier {gain} besser. "
                    f"Freigabe oder Ablehnung im Dashboard unter „Lernen“.", key=f"vorschlag:{key}", agent=self.id)
        return cur.lastrowid

    def check_regression(self, cat, reg, cat_rows, now):
        """Ist das aktive Modell seit seiner Aktivierung klar schlechter als sein Vorgänger?"""
        if not reg.get("prev_model") or not reg.get("activated_at"):
            return
        cur_key, prev_key = f"{reg['model']}@{reg['version']}", f"{reg['prev_model']}@{reg['prev_version']}"
        cur = {(r["market_id"], r["horizon"]): r for r in cat_rows if r["key"] == cur_key and r["created_at"] >= reg["activated_at"]}
        prev = {(r["market_id"], r["horizon"]): r for r in cat_rows if r["key"] == prev_key and r["created_at"] >= reg["activated_at"]}
        pairs = [{**cur[k], "chal": prev[k]["p"]} for k in cur if k in prev]
        if len(pairs) < config.EXPERIMENT_MIN_OOS:
            return
        z, clusters = paired_z(pairs, "p", "chal")  # positiv = aktives Modell schlechter
        if z is not None and z >= config.EXPERIMENT_MIN_Z and clusters >= config.EXPERIMENT_MIN_CLUSTERS:
            key = f"zurueck:{cat}:{cur_key}"
            if not self.store.q1("SELECT 1 FROM proposals WHERE key=? AND status='offen'", (key,)):
                title = f"{CAT_NAMES.get(cat, cat)}: Rückfall erkannt – zurück zu „{prev_key}“"
                self.store.x("INSERT INTO proposals(created_at, kind, category, key, title, detail) VALUES(?,?,?,?,?,?)",
                             (now.isoformat(timespec="seconds"), "zurueck", cat, key, title,
                              json.dumps({"n": len(pairs), "clusters": clusters, "z": z, "brier_aktiv": brier(pairs),
                                          "brier_vorgaenger": brier(pairs, "chal")})))
                events.emit(self.store, "hoch", "rueckfall", title,
                            f"Seit der Aktivierung in {len(pairs)} Prognosen schlechter als der Vorgänger.", key=key, agent=self.id)

    def decide(self, proposal_id, approve, note=""):
        """Freigabe oder Ablehnung aus dem Dashboard. Nur hier ändert sich das aktive Modell."""
        p = self.store.q1("SELECT * FROM proposals WHERE id=?", (proposal_id,))
        if not p or p["status"] != "offen":
            return False, "Vorschlag nicht (mehr) offen"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if approve:
            d = json.loads(p["detail"] or "{}")
            reg = self.probability.reg.get(p["category"]) or {}
            reason = f"Freigabe Vorschlag #{p['id']}"
            if p["kind"] == "modellwechsel":
                model, _, version = d["challenger"].partition("@")
                self.probability.activate(p["category"], model, version, 1.0, reason)
            elif p["kind"] == "mischung":
                self.probability.activate(p["category"], reg.get("model"), reg.get("version"), float(d.get("w", 1.0)), reason)
            elif p["kind"] == "zurueck":
                self.probability.rollback(p["category"], reason)
        self.store.x("UPDATE proposals SET status=?, decided_at=?, note=? WHERE id=?",
                     ("freigegeben" if approve else "abgelehnt", now, str(note)[:500], proposal_id))
        events.emit(self.store, "info", "vorschlag", f"Vorschlag #{proposal_id} {'freigegeben' if approve else 'abgelehnt'}: {p['title']}",
                    str(note)[:300], agent=self.id, telegram=False)
        return True, "ok"

    def view(self):
        return {"zusammenfassung": self.summary(),
                "experimente": [dict(json.loads(e["detail"]), id=e["id"], created_at=e["created_at"])
                                for e in self.store.q("SELECT * FROM experiments ORDER BY category, passed DESC, brier_chal_oos")],
                "vorschläge": [dict(p, detail=json.loads(p["detail"] or "{}"))
                               for p in self.store.q("SELECT * FROM proposals ORDER BY (status='offen') DESC, id DESC LIMIT 40")]}
