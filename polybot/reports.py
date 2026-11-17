"""Claude-Review-Bericht: zweimal pro Woche ein vollständiger Lagebericht aus gemessenen Werten.

Der Bot schreibt den Bericht selbst (ohne Sprachmodell, ohne bezahlte Schnittstelle), speichert ihn dauerhaft
und gibt ihn zusätzlich zeilenweise ins Railway-Log (Präfix „CLAUDE-REVIEW#<Nr>| “). Dort liest ihn die geplante
Claude-Sitzung und bespricht ihn mit Luis. Der Bericht ändert nichts am System – Empfehlungen sind Vorschläge.

Jede Zahl stammt aus der Datenbank. Wo nichts gemessen wurde, steht „keine Daten“.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, events, sources, taxonomy, version

log = logging.getLogger("polybot.bericht")
BERLIN = ZoneInfo("Europe/Berlin")
PREFIX = "CLAUDE-REVIEW"
CAT = {"crypto": "Krypto", "sports": "Sport", "weather": "Wetter"}
NA = "keine Daten"


def due(store, now):
    local = now.astimezone(BERLIN)
    if local.weekday() not in config.REPORT_DAYS or local.hour < config.REPORT_HOUR:
        return False
    return store.kv_get("report:last_day") != local.strftime("%Y-%m-%d")


def _f(v, digits=3, pct=False, sign=False, unit=""):
    """Zahl für den Bericht – oder ausdrücklich „keine Daten“ (dann auch ohne Einheit)."""
    if v is None:
        return NA
    if pct:
        return f"{v * 100:{'+' if sign else ''}.{digits}f} %"
    return f"{v:{'+' if sign else ''}.{digits}f}{unit}"


def _table(head, rows):
    if not rows:
        return ["Keine Daten."]
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def build(coord, now, since):
    """Gibt (Titel, Markdown, Daten, Empfehlungen) zurück."""
    st, eng = coord.store, coord.engine
    since_iso = since.isoformat(timespec="seconds")
    local = now.astimezone(BERLIN)
    days = max(1, round((now - since).total_seconds() / 86400))
    L, recs, data = [], [], {}
    add = L.append
    title = f"Claude-Review {local:%d.%m.%Y} (Zeitraum {since.astimezone(BERLIN):%d.%m.}–{local:%d.%m.})"
    add(f"# {title}")
    add("")
    add(f"Version {version.VERSION} · erstellt {local:%d.%m.%Y %H:%M} Uhr · alles Simulation mit Spielgeld, keine echten Einsätze.")
    add("")

    # 1 ---------------------------------------------------------------- Systemzustand
    agents = coord.registry.view()
    warn = st.q("SELECT level, type, title, ts, count FROM events WHERE ts>=? AND level IN ('hoch','kritisch') ORDER BY id DESC LIMIT 30", (since_iso,))
    runs = st.q("SELECT agent, COUNT(*) n, SUM(1-ok) fehler, AVG(duration) dauer FROM agent_runs WHERE started_at>=? GROUP BY agent", (since_iso,))
    cyc = next((r for r in runs if r["agent"] == "trader"), None)
    paused = {a["name"]: eng.state["paused"].get(a["id"]) for a in config.ACCOUNTS if eng.state["paused"].get(a["id"])} if eng else {}
    add("## 1. Allgemeiner Systemzustand")
    add(f"- Handels-Läufe im Zeitraum: {cyc['n'] if cyc else NA}" + (f", davon mit Fehler: {cyc['fehler']}, mittlere Dauer {cyc['dauer']:.0f} s" if cyc else ""))
    add(f"- Warnungen (hoch/kritisch): {len(warn)}")
    for w in warn[:8]:
        add(f"  - {w['ts'][:16].replace('T', ' ')} · {w['level']} · {w['title']}" + (f" (×{w['count']})" if w["count"] > 1 else ""))
    add(f"- Agenten mit Fehlern aktuell: {', '.join(a['name'] for a in agents if a['state'] == 'fehler') or 'keine'}")
    add(f"- Konten mit Risikobremse: {', '.join(f'{k} ({v})' for k, v in paused.items()) or 'keine'}")
    add("")
    data["warnungen"] = len(warn)

    # 2 ---------------------------------------------------------------- Neue Funktionen
    add("## 2. Neue Funktionen")
    new = [c for c in version.CHANGES if c[0] >= since.strftime("%Y-%m-%d")]
    for d, v, text in new:
        add(f"- {d} · v{v}: {text}")
    if not new:
        add("- Keine neuen Funktionen im Zeitraum.")
    add("")

    # 3 ---------------------------------------------------------------- Prognosequalität
    summ = coord.learning.summary() or {}
    models = summ.get("modelle") or []
    add("## 3. Prognosequalität")
    add(f"Prognosen mit bekanntem Ergebnis: {summ.get('n', 0)}. Brier und LogLoss: kleiner ist besser. "
        "Vorsprung = wie viel besser als der Marktpreis zum selben Zeitpunkt (negativ = schlechter). "
        "z unter −1,64 = gesichert besser als der Markt, über +1,64 = gesichert schlechter.")
    add("")
    rows = [[CAT.get(m["category"], m["category"]), m["model"] + (" ★" if m["active"] else ""), m["n"], m["clusters"], _f(m["brier"]),
             _f(m["brier_market"]), _f(m["skill"], 1, pct=True, sign=True), _f(m["z"], 2, sign=True), _f(m["hit"], 0, pct=True),
             _f(m["bias"], 1, pct=True, sign=True)] for m in models]
    L += _table(["Bereich", "Modell (★ aktiv)", "n", "Ereignisse", "Brier", "Brier Markt", "Vorsprung", "z", "Treffer", "Verzerrung"], rows)
    add("")
    active = [m for m in models if m["active"]]
    data["modelle"] = [{k: m[k] for k in ("category", "model", "n", "brier", "brier_market", "skill", "z")} for m in active]
    for m in active:
        name = CAT.get(m["category"], m["category"])
        if m["n"] >= 100 and m["z"] is not None and m["z"] > 1.64:
            recs.append(f"{name}: Das aktive Modell „{m['model']}“ ist gesichert schlechter als der Marktpreis (Brier {m['brier']:.3f} gegen "
                        f"{m['brier_market']:.3f}, z = {m['z']:+.1f}, n = {m['n']}). Handel in diesem Bereich aussetzen oder Modell wechseln.")
        elif m["n"] < 100:
            recs.append(f"{name}: erst {m['n']} Prognosen mit Ergebnis – für ein Urteil über das Modell zu wenig (ab etwa 100). Weiter sammeln, nichts ändern.")

    # 4 ---------------------------------------------------------------- Fehlerentwicklung
    add("## 4. Fehlerentwicklung")
    tl = summ.get("verlauf") or []
    cut = since.strftime("%Y-%m-%d")
    rows = []
    for cat in sorted({t["category"] for t in tl}):
        def agg(sel):
            n = sum(t["n"] for t in sel)
            return (n, sum(t["brier"] * t["n"] for t in sel) / n, sum(t["brier_market"] * t["n"] for t in sel) / n) if n else (0, None, None)
        a, b = agg([t for t in tl if t["category"] == cat and t["tag"] < cut]), agg([t for t in tl if t["category"] == cat and t["tag"] >= cut])
        rows.append([CAT.get(cat, cat), a[0], _f(a[1]), _f(a[2]), b[0], _f(b[1]), _f(b[2])])
    L += _table(["Bereich", "n davor", "Brier davor", "Markt davor", "n im Zeitraum", "Brier im Zeitraum", "Markt im Zeitraum"], rows)
    add("")

    # 5 + 6 ------------------------------------------------------------ Verbesserungen
    props = st.q("SELECT * FROM proposals WHERE created_at>=? OR decided_at>=? OR status='offen' ORDER BY id DESC", (since_iso, since_iso))
    exps = st.q("SELECT category, champion, challenger, n_oos, clusters_oos, brier_champ_oos, brier_chal_oos, z, passed, detail FROM experiments")
    mlog = st.q("SELECT * FROM model_log WHERE ts>=? AND action!='start' ORDER BY id", (since_iso,))
    add("## 5. Erfolgreiche Verbesserungen")
    good = [p for p in props if p["status"] == "freigegeben"]
    for p in good:
        add(f"- Freigegeben: {p['title']} ({(p['decided_at'] or '')[:10]})")
    for e in [e for e in exps if e["passed"]]:
        add(f"- Test bestanden: {CAT.get(e['category'], e['category'])} – „{e['challenger']}“ schlägt „{e['champion']}“ im späteren Teil "
            f"(Brier {_f(e['brier_chal_oos'])} gegen {_f(e['brier_champ_oos'])}, n = {e['n_oos']}).")
    for m in mlog:
        add(f"- Modell-Register: {m['ts'][:10]} · {CAT.get(m['category'], m['category'])} · {m['action']} · {m['model']}@{m['version']} · {m['reason'] or ''}")
    if not good and not any(e["passed"] for e in exps) and not mlog:
        add("- Keine im Zeitraum.")
    add("")
    add("## 6. Fehlgeschlagene Verbesserungen")
    failed = [e for e in exps if not e["passed"]]
    for e in sorted(failed, key=lambda e: e["category"])[:14]:
        why = "; ".join((json.loads(e["detail"] or "{}").get("reasons") or [])[:3])
        add(f"- {CAT.get(e['category'], e['category'])}: „{e['challenger']}“ gegen „{e['champion']}“ nicht bestanden – {why or 'ohne Angabe'} "
            f"(später: {_f(e['brier_chal_oos'])} gegen {_f(e['brier_champ_oos'])}, n = {e['n_oos']}).")
    for p in [p for p in props if p["status"] == "abgelehnt"]:
        add(f"- Abgelehnt: {p['title']}" + (f" – {p['note']}" if p["note"] else ""))
    if not failed and not any(p["status"] == "abgelehnt" for p in props):
        add("- Keine im Zeitraum.")
    add("")
    open_props = [p for p in props if p["status"] == "offen"]
    for p in open_props:
        recs.append(f"Offener Vorschlag #{p['id']} wartet auf Entscheidung: {p['title']} (Dashboard → Lernen).")

    # 7 ---------------------------------------------------------------- Muster
    add("## 7. Neue erkannte Muster")
    err = summ.get("fehler") or {}
    n_pat = 0
    for c in err.get("kategorien", []):
        for f in c["befunde"]:
            add(f"- {CAT.get(c['category'], c['category'])}: {f} (n = {c['n']}).")
            n_pat += 1
        if c["sicher"] >= 20:
            add(f"- {CAT.get(c['category'], c['category'])}: von {c['sicher']} sehr sicheren Prognosen (≤ 10 % oder ≥ 90 %) lagen {c['sicher_falsch']} falsch; "
                f"beim Markt {c['markt_sicher_falsch']} von {c['markt_sicher']}.")
            n_pat += 1
    for v in err.get("validierung", []):
        if v["n"] >= 10:
            better = "die Ablehnung war berechtigt (Markt lag näher)" if v["brier"] > v["brier_market"] else "das Modell lag hier näher als der Markt"
            add(f"- Prüfung „{v['grund']}“: {v['n']} abgelehnte Prognosen, Brier Modell {v['brier']:.3f} / Markt {v['brier_market']:.3f} – {better}.")
            n_pat += 1
            if v["brier"] < v["brier_market"] and v["n"] >= 50:
                recs.append(f"Die Prüfregel „{v['grund']}“ hat {v['n']} Prognosen gesperrt, bei denen das Modell besser lag als der Markt – Schwelle überdenken.")
    for s in (err.get("stationen") or [])[:3]:
        if s["brier"] > s["brier_market"]:
            add(f"- Wetter-Station {s['station']}: Modell {s['brier']:.3f} gegen Markt {s['brier_market']:.3f} (n = {s['n']}).")
            n_pat += 1
    if eng and getattr(eng, "base", None):
        for cat, why in sorted(eng.base.blocked.items()):
            add(f"- Basisraten-Wächter: {taxonomy.NAMES.get(cat, cat)} gesperrt – {why}.")
            n_pat += 1
        cells = [c for c in eng.base.cells_report() if abs(c["abw"]) >= 0.05]
        if cells:
            add("- Auffälligste Vergleichszellen der Basisraten (große Abweichungen deuten eher auf verzerrte Vergleichsdaten "
                "als auf echte Chancen; über 10 Pkt wird nicht gehandelt):")
            for c in cells[:5]:
                add(f"  - {c['thema']}, {c['stufe'].replace('p_', '')} vor Ende, Kurs {c['kurs']}: eingetreten {c['eingetreten'] * 100:.0f} % "
                    f"bei Ø Kurs {c['kurs_avg'] * 100:.0f} % (n = {c['n']})")
            n_pat += 1
    lab = (coord.strategy.view() or {}).get("latest") or {}
    for b in lab.get("kalibrierung") or []:
        if b["n"] >= 60 and abs(b["ist"] - b["p"]) >= 0.05:
            add(f"- Marktpreis 24 h vor Ende bei {b['von'] * 100:.0f}–{b['bis'] * 100:.0f} %: eingetreten in {b['ist'] * 100:.0f} % der Fälle (n = {b['n']}).")
            n_pat += 1
    if not n_pat:
        add("- Noch keine belastbaren Muster (zu wenige entschiedene Prognosen).")
    add("")

    # 8 ---------------------------------------------------------------- Datenquellen
    add("## 8. Datenquellenqualität")
    src = st.q("SELECT source, SUM(calls) calls, SUM(errors) errors, SUM(limited) limited, SUM(blocked) blocked, SUM(units) units, "
               "SUM(ms) ms, MAX(last_ok) last_ok FROM source_stats WHERE day>=? GROUP BY source ORDER BY calls DESC", (since.strftime("%Y-%m-%d"),))
    rows = []
    for s in src:
        cfg = sources.SOURCES.get(s["source"], {})
        rate = s["errors"] / s["calls"] if s["calls"] else None
        rows.append([cfg.get("name", s["source"]), s["calls"], _f(rate, 1, pct=True), s["limited"], s["blocked"],
                     f"{s['ms'] / s['calls']:.0f} ms" if s["calls"] else NA, (s["last_ok"] or NA)[:16].replace("T", " ")])
        if s["calls"] >= 50 and rate is not None and rate > 0.10:
            recs.append(f"Datenquelle {cfg.get('name', s['source'])}: Fehlerquote {rate * 100:.0f} % bei {s['calls']} Abrufen – Ursache prüfen.")
        if s["limited"]:
            recs.append(f"Datenquelle {cfg.get('name', s['source'])}: {s['limited']}× vom Anbieter gebremst (429) – Budget oder Takt senken.")
    L += _table(["Quelle", "Abrufe", "Fehlerquote", "gebremst (429)", "selbst zurückgehalten", "Antwortzeit", "zuletzt ok"], rows)
    add("")

    # 9 ---------------------------------------------------------------- Strategien
    add("## 9. Strategievergleich")
    add("Paper-Trading: jede Strategie hat je Thema ein eigenes Konto (je 1.000 $ Spielgeld). Zuerst je Strategie zusammengefasst:")
    add("")
    rows, accs = [], []
    if eng:
        accs = eng.accounts_overview()
        per = {r["account"]: r["gv"] or 0 for r in st.q("SELECT account, SUM(pnl) gv FROM positions WHERE status!='open' AND closed_at>=? "
                                                         "GROUP BY account", (since_iso,))}
        for sr in sorted(eng.strategy_summary(accs), key=lambda r: -r["pnl_total"]):
            ids = [a["id"] for a in accs if a.get("strategy") == sr["id"]]
            rows.append([sr["name"], sr["konten"], f"{sr['pnl_total']:+.2f} $", f"{sum(per.get(i, 0) for i in ids):+.2f} $",
                         sr["settled"], _f(sr["winrate"], 0, pct=True), sr["open_count"], sr["paused"]])
    L += _table(["Strategie", "Konten", "seit Start", "im Zeitraum", "entschieden", "Treffer", "offen", "pausiert"], rows)
    add("")
    if accs:
        topics = [(cat, name) for cat, _, name in config.TOPICS if any(a.get("topic") == cat for a in accs)]
        by = {(a.get("strategy"), a.get("topic")): a for a in accs}
        add("G/V seit Start je Konto (Strategie × Thema; „·“ = noch keine Wette, „–“ = gibt es nicht, P = pausiert):")
        add("")
        rows = []
        for sid, sname in config.STRATEGY_NAME.items():
            line = [sname]
            for cat, _ in topics:
                a = by.get((sid, cat))
                if not a:
                    line.append("–")
                elif not (a["open_count"] or a["closed_count"]):
                    line.append("·")
                else:
                    line.append(f"{a['pnl_total']:+.0f}{' P' if a['paused'] else ''}")
            rows.append(line)
        L += _table(["Strategie"] + [n for _, n in topics], rows)
        add("")
    add(f"Backtests auf entschiedenen Märkten (Datensatz: {(lab.get('datensatz') or {}).get('mit_kurs_24h', 0)} Märkte mit Kursverlauf; nur der spätere Prüfteil zählt):")
    add("")
    rows = [[s["name"], s["is"].get("n", 0), _f(s["is"].get("roi"), 1, pct=True, sign=True), s["oos"].get("n", 0),
             _f(s["oos"].get("roi"), 1, pct=True, sign=True), _f(s["oos"].get("t"), 1, sign=True),
             "ja" if s["bestanden"] else ("Vergleich" if s["baseline"] else "nein")] for s in lab.get("strategien") or []]
    L += _table(["Strategie", "Wetten früh", "je Wette früh", "Wetten spät", "je Wette spät", "t", "bestanden"], rows)
    if lab.get("hinweis"):
        add(lab["hinweis"])
    add("")

    # 10 --------------------------------------------------------------- Ressourcen
    add("## 10. Ressourcenverbrauch")
    res = coord.guardian.latest or {}
    hist = [h for h in st.kv_json("guardian:res", []) if h.get("rss_mb")]
    add(f"- Arbeitsspeicher: aktuell {_f(res.get('rss_mb'), 0, unit=' MB')}" + (f", Spitze {max(h['rss_mb'] for h in hist):.0f} MB (letzte {len(hist)} Messungen)" if hist else ""))
    add(f"- CPU-Anteil des Prozesses: {_f(res.get('cpu_pct'), 1, unit=' % eines Kerns')}")
    add(f"- Datenbank: {_f(res.get('db_mb'), 1, unit=' MB')} · Platte frei: {_f(res.get('disk_free_gb'), 2, unit=' GB')}")
    add("- Rechenzeit je Agent im Zeitraum:")
    for r in sorted(runs, key=lambda r: -(r["dauer"] or 0) * r["n"]):
        add(f"  - {r['agent']}: {r['n']} Läufe, zusammen {(r['dauer'] or 0) * r['n'] / 60:.1f} min, Fehler {r['fehler']}")
    today = {s["id"]: s for s in sources.snapshot()}
    for sid in ("open_meteo", "odds_api", "polymarket_gamma", "polymarket_clob"):
        s = today.get(sid)
        if s and s["day_budget"]:
            add(f"- Tagesbudget {s['name']}: {s['units']} von {s['day_budget']} verbraucht (heute)")
    add("- Externe Kosten: 0 € (nur kostenlose Schnittstellen; The Odds API im Gratis-Kontingent).")
    add("")

    # 11 --------------------------------------------------------------- Offene Probleme
    add("## 11. Offene technische Probleme")
    probs = []
    for a in agents:
        if a["state"] == "fehler":
            probs.append(f"Agent {a['name']}: {a['last_error']}")
        if a["overruns"] >= 3:
            probs.append(f"Agent {a['name']} hat sein Zeitbudget {a['overruns']}× deutlich überschritten.")
    for s in today.values():
        if s["paused_s"] > 0:
            probs.append(f"Datenquelle {s['name']} pausiert noch {s['paused_s'] // 60} min ({s['last_msg'] or 'Limit'}).")
    gaps = (lab.get("datensatz") or {}).get("lücken") or {}
    n_ds = (lab.get("datensatz") or {}).get("zeilen") or 0
    if n_ds and gaps.get("p_24h", 0) / n_ds > 0.3:
        probs.append(f"Backtest-Datensatz: bei {gaps['p_24h']} von {n_ds} Märkten fehlt der Kurs 24 h vor Ende (kein Handel in dem Fenster).")
    for k in ("kritisch",):
        for w in [w for w in warn if w["level"] == k][:5]:
            probs.append(f"Kritisches Ereignis {w['ts'][:16].replace('T', ' ')}: {w['title']}")
    for p in probs:
        add(f"- {p}")
    if not probs:
        add("- Keine bekannten.")
    add("")

    # 12 + 13 ---------------------------------------------------------- Empfehlungen
    for name, why in paused.items():
        if "seit Start" in (why or ""):
            recs.append(f"Konto „{name}“ steht seit dem Verlust-Limit still. Erst die Ursache klären (Abschnitt 3 und 7), dann im Dashboard entscheiden, ob es fortgesetzt wird.")
    add("## 12. Verbesserungsvorschläge")
    for r in recs:
        add(f"- {r}")
    if not recs:
        add("- Keine – die Messwerte geben im Moment keinen Anlass für eine Änderung.")
    add("")
    add("## 13. Empfehlungen für die nächsten Entwicklungsschritte")
    nxt = []
    if (summ.get("n") or 0) < 300:
        nxt.append(f"Datenbasis wachsen lassen: {summ.get('n', 0)} Prognosen mit Ergebnis. Modell-Entscheidungen erst ab etwa 100 je Bereich.")
    wv = st.q1("SELECT version, n_samples, created_at FROM model_versions WHERE model='weather.emp' ORDER BY id DESC LIMIT 1")
    if wv:
        nxt.append(f"Wetter: gelernte Parameter Version {wv['version']} (aus {wv['n_samples']} Messungen) laufen als Herausforderer mit – Ergebnis des Tests abwarten.")
    else:
        nxt.append("Wetter: Die Stations-Korrektur entsteht automatisch, sobald 30 entschiedene Tage/Stationen vorliegen.")
    if n_ds < 500:
        nxt.append(f"Strategie-Labor: Datensatz hat {n_ds} entschiedene Märkte; belastbar wird es ab etwa 500 mit Kursverlauf.")
    if open_props:
        nxt.append(f"{len(open_props)} Vorschlag/Vorschläge im Lernzentrum entscheiden.")
    nxt.append("Offene Punkte aus docs/DEVELOPMENT_ROADMAP.md (Abschnitt „Vorgemerkt“) mit Claude durchgehen.")
    for n in nxt:
        add(f"- {n}")
    add("")
    add("_Dieser Bericht wurde automatisch aus Messwerten erzeugt. Er ändert nichts am System._")
    data["empfehlungen"] = len(recs)
    return title, "\n".join(L), data, recs


def generate(coord, now=None, kind="geplant"):
    st = coord.store
    now = now or datetime.now(timezone.utc)
    last = st.q1("SELECT created_at FROM reports ORDER BY id DESC LIMIT 1")
    since = now - timedelta(days=4)
    if last:
        try:
            since = max(datetime.fromisoformat(last["created_at"]), now - timedelta(days=8))
        except ValueError:
            pass
    title, md, data, recs = build(coord, now, since)
    cur = st.x("INSERT INTO reports(created_at, kind, period_from, period_to, title, markdown, data) VALUES(?,?,?,?,?,?,?)",
               (now.isoformat(timespec="seconds"), kind, since.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"),
                title, md, json.dumps(data, ensure_ascii=False)))
    rid = cur.lastrowid
    open_texts = {r["text"] for r in st.q("SELECT text FROM recommendations WHERE status='offen'")}
    for r in recs:
        if r not in open_texts:
            st.x("INSERT INTO recommendations(report_id, text, updated_at) VALUES(?,?,?)", (rid, r, now.isoformat(timespec="seconds")))
    st.kv_set("report:last_day", now.astimezone(BERLIN).strftime("%Y-%m-%d"))
    log.info("%s#%d| BEGINN %s", PREFIX, rid, title)
    for line in md.split("\n"):
        log.info("%s#%d| %s", PREFIX, rid, line)
    log.info("%s#%d| ENDE", PREFIX, rid)
    events.emit(st, "normal", "bericht", f"Claude-Review-Bericht #{rid} ist fertig",
                f"{len(recs)} Empfehlung(en). Im Dashboard unter „Claude-Berichte“ – oder mit Claude besprechen.",
                key=f"bericht:{rid}", agent="coordinator")
    return rid
