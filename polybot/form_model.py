"""Form-Modell: Wie stark sind beide Seiten – nach ihren bisherigen Ergebnissen, gewichtet nach der Stärke der Gegner?

Wunsch (23.11.): direkter Vergleich, Form, letzte Spiele angepasst an die Gegner, Verletzungen – kostenlos.
Eine Elo-Wertung ist genau „Ergebnisse angepasst an die Gegnerstärke“: Wer einen starken Gegner schlägt, gewinnt viele
Punkte, wer gegen einen schwachen verliert, verliert viele. Aus dem Unterschied der Punkte folgt die Siegchance.

Quellen (alle kostenlos):
- Fußball: ClubElo (api.clubelo.com, ohne Schlüssel) – Spielplan der nächsten Tage mit Wahrscheinlichkeiten je
  Tordifferenz, inklusive Heimvorteil. Daraus Sieg/Unentschieden/Niederlage.
- Alle Duelle „Name gegen Name“ (Tennis, Tischtennis, Basketball, Eishockey, MMA, E-Sport …): eigene Elo-Wertung aus
  den Ergebnissen entschiedener Polymarket-Sieger-Märkte (gesammelt je Sportart, bis FORM_DAYS zurück).
- Verletzte/Gesperrte (API-Football, nur wenn Luis einen eigenen Gratis-Schlüssel einträgt): angezeigt und gespeichert.
  Sie ändern die Wahrscheinlichkeit (noch) nicht – erst wird gemessen, ob sie helfen.

Ehrlich: Buchmacher und Märkte kennen all das auch. Deshalb wird das Modell gemessen (Prognose-Historie „form.elo“,
Bericht) und handelt nur in eigenen Konten („Form (Elo)“), nicht in den Hauptstrategien.
"""
import csv
import io
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

from . import config, polymarket, taxonomy
from .http import HttpError, get_json, get_text
from .sports_model import name_score, split_title

log = logging.getLogger("polybot.form")
MODEL_ID = "form.elo@1"
API_FOOTBALL = "https://v3.football.api-sports.io"
YES_NO = ("yes", "no", "ja", "nein")


def k_factor(n):
    """Neue Spieler bewegen sich schnell, erfahrene langsam (Faustregel aus der Tennis-Elo von FiveThirtyEight)."""
    return 250 / (n + 5) ** 0.4


def expected(ra, rb):
    return 1 / (1 + 10 ** ((rb - ra) / 400))


def _key(name):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split())


def parse_clubelo(text):
    """ClubElo-Spielplan (CSV) -> [{date, home, away, p: {home, "Draw", away}}].

    Spalten „GD=x“, „GD<-5“, „GD>5“ (Wahrscheinlichkeit je Tordifferenz aus Sicht der Heimelf); ersatzweise „R:h-a“."""
    out = []
    rows = list(csv.DictReader(io.StringIO(text.decode("utf-8", "replace") if isinstance(text, bytes) else text)))
    for r in rows:
        home, away, day = (r.get("Home") or "").strip(), (r.get("Away") or "").strip(), (r.get("Date") or "").strip()
        if not home or not away or not day:
            continue
        ph = pd = pa = 0.0
        found = False
        for col, val in r.items():
            if not col or val in (None, ""):
                continue
            try:
                v = float(val)
            except ValueError:
                continue
            c = col.strip()
            gd = None
            if c.startswith("GD"):
                m = re.match(r"GD\s*([<>=])\s*(-?\d+)", c)
                if m:
                    gd = int(m.group(2)) + (-1 if m.group(1) == "<" else 1 if m.group(1) == ">" else 0)
            elif c.startswith("R:"):
                m = re.match(r"R:(\d+)-(\d+)", c)
                if m:
                    gd = int(m.group(1)) - int(m.group(2))
            if gd is None:
                continue
            found = True
            if gd > 0:
                ph += v
            elif gd < 0:
                pa += v
            else:
                pd += v
        tot = ph + pd + pa
        if found and tot > 0.5:
            out.append({"date": day, "home": home, "away": away, "p": {home: ph / tot, "Draw": pd / tot, away: pa / tot}})
    return out


class Form:
    def __init__(self, store, fetch_markets=None, fetch_text=get_text, fetch_json=get_json):
        self.store = store
        self.fetch_markets = fetch_markets
        self.fetch_text = fetch_text
        self.fetch_json = fetch_json
        self._ratings = (None, None)      # (Anzahl Ergebnisse, Wertung)
        self._fixtures = (None, [])       # (Zeitpunkt, Spielplan)
        self._by_day = {}
        self._inj = {}                    # Tag -> {Team: [(Spieler, Art, Grund)]}
        self._match = {}                  # (Ereignis, Tag) -> passendes ClubElo-Spiel (Namensvergleich ist teuer)

    # ------------------------------------------------------------------ Ergebnisse sammeln
    def _window(self, lo, hi, off, now):
        """Eine Seite je Tag (Sport, E-Sport) – gibt (neue Ergebnisse, Abrufe, weitere Seite vorhanden?) zurück."""
        added = pages = 0
        more = False
        for slug in [t.partition(":")[0] for t in config.ALL_TAGS if t.partition(":")[0] in ("sports", "esports")]:
            tid = polymarket.tag_id(slug) if self.fetch_markets is polymarket.fetch_markets else slug
            if not tid:
                continue
            raws = self.fetch_markets({"tag_id": tid, "sports_market_types": "moneyline", "closed": "true", "include_tag": "true",
                                       "limit": 100, "offset": off, "end_date_min": lo.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                       "end_date_max": hi.strftime("%Y-%m-%dT%H:%M:%SZ")})
            pages += 1
            added += self._store_results(raws, slug, now)
            more = more or len(raws) >= 100
        return added, pages, more

    def collect(self, now):
        """Entschiedene Sieger-Märkte sammeln – die neuesten zuerst, weil sie für die heutige Stärke am meisten zählen.

        1) Jede Stunde die letzten 36 Stunden (auch Märkte, deren offizielles Ende noch kommt: Tischtennis + 7 Tage).
        2) Mit den übrigen Abrufen rückwärts Tag für Tag bis FORM_DAYS (Zeiger kv form:back). Die Reihenfolge des
           Sammelns ist egal: Die Wertung wird immer in zeitlicher Reihenfolge der Spiele gerechnet."""
        if not self.fetch_markets:
            return 0
        added = pages = off = 0
        recent = now - timedelta(hours=36)
        while pages < config.FORM_PAGES // 2:
            a, p, more = self._window(recent, now + timedelta(days=8), off, now)
            added, pages, off = added + a, pages + p, off + 100
            if not more:
                break
        st = self.store.kv_json("form:back", None) or {}
        back = datetime.fromisoformat(st["back"]) if st.get("back") else recent
        off = int(st.get("off", 0))
        while pages < config.FORM_PAGES and back > now - timedelta(days=config.FORM_DAYS):
            a, p, more = self._window(back - timedelta(days=1), back, off, now)
            added, pages = added + a, pages + p
            if more:
                off += 100
            else:
                back, off = back - timedelta(days=1), 0
        self.store.kv_set("form:back", json.dumps({"back": back.isoformat(timespec="seconds"), "off": off}))
        return added

    def _store_results(self, raws, slug, now):
        rows = []
        for raw in raws:
            ev = raw["events"][0] if isinstance(raw.get("events"), list) and raw.get("events") else None
            m = polymarket.normalize_market(raw, ev, "other")
            if not m or len(m["outcomes"]) != 2 or m["outcomes"][0].lower() in YES_NO:
                continue
            res = polymarket.resolution(raw)
            if res not in (0, 1):
                continue
            c = taxonomy.classify(m)
            if c["category"] not in config.GAME_TOPICS:
                c = taxonomy.classify(m, "esports" if slug == "esports" else "sports")
            played = m.get("game_start") or m.get("end_date")
            rows.append((m["id"], c["category"], c["sub"], played.isoformat(timespec="seconds") if played else None,
                         m["outcomes"][0], m["outcomes"][1], res, m.get("event_slug"), now.isoformat(timespec="seconds")))
        if rows:
            self.store.xmany("INSERT OR IGNORE INTO match_results(market_id, category, sport, played_at, a, b, winner, slug, added_at) "
                             "VALUES(?,?,?,?,?,?,?,?,?)", rows)
        return len(rows)

    # ------------------------------------------------------------------ Elo-Wertung
    def ratings(self):
        """{(Bereich, Sportart): {Name: [Wertung, Spiele]}} – neu gerechnet, sobald neue Ergebnisse da sind."""
        n = self.store.scalar("SELECT COUNT(*) FROM match_results")
        if self._ratings[0] == n:
            return self._ratings[1]
        table = defaultdict(dict)
        for r in self.store.q("SELECT category, sport, a, b, winner FROM match_results WHERE played_at IS NOT NULL ORDER BY played_at, market_id"):
            t = table[(r["category"], r["sport"])]
            a, b = t.setdefault(_key(r["a"]), [1500.0, 0]), t.setdefault(_key(r["b"]), [1500.0, 0])
            ea = expected(a[0], b[0])
            sa = 1.0 if r["winner"] == 0 else 0.0
            a[0] += k_factor(a[1]) * (sa - ea)
            b[0] += k_factor(b[1]) * ((1 - sa) - (1 - ea))
            a[1] += 1
            b[1] += 1
        self._ratings = (n, dict(table))
        return self._ratings[1]

    def two_way(self, m):
        """Siegchance der ersten Seite bei „Name gegen Name“ – nur, wenn beide genug gewertete Spiele haben."""
        t = self.ratings().get((m["category"], m.get("sub")))
        if not t:
            return None
        a, b = t.get(_key(m["outcomes"][0])), t.get(_key(m["outcomes"][1]))
        if not a or not b or min(a[1], b[1]) < config.FORM_MIN_GAMES:
            return None
        p = expected(a[0], b[0])
        info = (f"Stärke-Wertung {m.get('sub')}: {m['outcomes'][0]} {a[0]:.0f} ({a[1]} Spiele) gegen "
                f"{m['outcomes'][1]} {b[0]:.0f} ({b[1]} Spiele) → {p * 100:.0f} % für {m['outcomes'][0]}")
        return p, info, {"elo_a": round(a[0]), "elo_b": round(b[0]), "spiele_a": a[1], "spiele_b": b[1]}, \
            ["Eigene Elo-Wertung aus entschiedenen Polymarket-Spielen"]

    # ------------------------------------------------------------------ Fußball (ClubElo)
    def fixtures(self, now):
        ts, fx = self._fixtures
        # erfolgreich: 6 h behalten; Fehler (live 23.11.: Zeitüberschreitung): nach 1 h erneut versuchen
        if ts and (now - ts).total_seconds() < (6 * 3600 if fx else 3600):
            return fx
        try:
            fx = parse_clubelo(self.fetch_text(config.CLUBELO_URL, timeout=40))
            self.store.kv_set("form:clubelo", json.dumps({"stand": now.isoformat(timespec="seconds"), "spiele": len(fx)}))
        except (HttpError, ValueError, TypeError) as e:
            log.info("ClubElo nicht abrufbar: %s", e)
            self.store.kv_set("form:clubelo", json.dumps({"stand": now.isoformat(timespec="seconds"), "spiele": len(fx or []),
                                                          "fehler": str(e)[:120]}, ensure_ascii=False))
            fx = fx or []
        self._fixtures = (now, fx)
        self._match = {}
        by_day = defaultdict(list)
        for f in fx:
            by_day[f["date"]].append(f)
        self._by_day = dict(by_day)
        return fx

    def football(self, m, now):
        teams = split_title(m.get("event_title")) or split_title(m.get("question"))
        start = m.get("game_start")
        if not teams or not start:
            return None
        self.fixtures(now)
        ck = (teams, start.date())
        if ck not in self._match:
            best, best_s = None, 0.0
            for d in (-1, 0, 1):   # ClubElo nennt nur den Tag; Anstoß kurz nach Mitternacht UTC liegt am Vortag
                for f in self._by_day.get((start.date() + timedelta(days=d)).isoformat(), []):
                    s = max(min(name_score(teams[0], f["home"]), name_score(teams[1], f["away"])),
                            min(name_score(teams[0], f["away"]), name_score(teams[1], f["home"])))
                    if s > best_s:
                        best, best_s = f, s
            self._match[ck] = best if best_s >= 0.75 else None
        best = self._match[ck]
        if not best:
            return None
        q = m["question"]
        if m["outcomes"][0].lower() in YES_NO:
            if "draw" in q.lower():
                p, label = best["p"]["Draw"], "Unentschieden"
            else:
                hit = re.search(r"will (.+?) win", q, flags=re.I)
                if not hit:
                    return None
                label = hit.group(1)
                side = max((best["home"], best["away"]), key=lambda t: name_score(label, t))
                if name_score(label, side) < 0.75:
                    return None
                p = best["p"][side]
        else:
            return None   # Zwei-Wege-Markt bei einem Spiel mit Unentschieden
        info = (f"ClubElo: {best['home']} – {best['away']} · {label} {p * 100:.0f} % "
                f"(Heimsieg {best['p'][best['home']] * 100:.0f} %, Remis {best['p']['Draw'] * 100:.0f} %, "
                f"Auswärtssieg {best['p'][best['away']] * 100:.0f} %)")
        return p, info, {"heim": best["home"], "gast": best["away"]}, ["ClubElo (Stärke-Wertung europäischer Klubs)"]

    # ------------------------------------------------------------------ Verletzte/Gesperrte (API-Football)
    def injuries_refresh(self, now):
        """Alle 3 Stunden die Ausfälle für heute und morgen (2 Abrufe). Nur mit eigenem Schlüssel."""
        if not config.API_FOOTBALL_KEY:
            return 0
        last = self.store.kv_get("form:inj_last")
        if last and (now - datetime.fromisoformat(last)).total_seconds() < 3 * 3600:
            return 0
        n = 0
        for d in (now.date(), now.date() + timedelta(days=1)):
            try:
                data = self.fetch_json(f"{API_FOOTBALL}/injuries", params={"date": d.isoformat()}, retries=1,
                                       headers={"x-apisports-key": config.API_FOOTBALL_KEY})
            except HttpError as e:
                log.info("API-Football nicht abrufbar: %s", e)
                break
            if isinstance(data, dict) and data.get("errors"):
                self.store.kv_set("form:inj_error", json.dumps(data.get("errors"), ensure_ascii=False)[:300])
            rows = []
            for it in (data.get("response") if isinstance(data, dict) else None) or []:
                pl, tm, fx, lg = it.get("player") or {}, it.get("team") or {}, it.get("fixture") or {}, it.get("league") or {}
                if not pl.get("name") or not tm.get("name"):
                    continue
                rows.append((d.isoformat(), tm["name"], pl["name"], pl.get("type") or "", pl.get("reason") or "",
                             lg.get("name") or "", str(fx.get("id") or ""), now.isoformat(timespec="seconds")))
            if rows:
                self.store.xmany("INSERT OR REPLACE INTO injuries(day, team, player, kind, reason, league, fixture_id, fetched_at) "
                                 "VALUES(?,?,?,?,?,?,?,?)", rows)
            n += len(rows)
        self.store.kv_set("form:inj_last", now.isoformat(timespec="seconds"))
        self._inj = {}
        return n

    def injuries_for(self, team, day):
        if day not in self._inj:
            by = defaultdict(list)
            for r in self.store.q("SELECT team, player, kind, reason FROM injuries WHERE day=?", (day,)):
                by[r["team"]].append((r["player"], r["kind"], r["reason"]))
            self._inj[day] = dict(by)
        ck = ("team", team, day)
        if ck not in self._inj:
            best, best_s = None, 0.0
            for t in self._inj[day]:
                s = name_score(team, t)
                if s > best_s:
                    best, best_s = t, s
            self._inj[ck] = self._inj[day][best] if best and best_s >= 0.75 else None
        return self._inj[ck]

    # ------------------------------------------------------------------ Prognose
    def analyze(self, m, now):
        """Prognose des Form-Modells für einen Markt – oder None."""
        if m["category"] not in config.GAME_TOPICS or not m.get("accepting_orders"):
            return None
        start = m.get("game_start")
        if not start or start <= now:
            return None
        smt = (m.get("sports_market_type") or "moneyline").lower()
        if smt != "moneyline" or len(m["outcomes"]) != 2 or polymarket.is_exact_score(m):
            return None
        if m["outcomes"][0].lower() in YES_NO:
            got = self.football(m, now) if m["category"] == "sports" else None
        else:
            got = self.two_way(m)
        if not got:
            return None
        p, info, feats, srcs = got
        p = min(max(p, 0.01), 0.99)
        hours = (start - now).total_seconds() / 3600
        feats = {**feats, "sportart": m.get("sub"), "stunden": round(hours, 1)}
        if m["category"] == "sports" and config.API_FOOTBALL_KEY:
            teams = split_title(m.get("event_title")) or split_title(m.get("question"))
            if teams:
                miss = [self.injuries_for(t, start.date().isoformat()) for t in teams]
                feats.update({"ausfaelle_a": len(miss[0]) if miss[0] is not None else None,
                              "ausfaelle_b": len(miss[1]) if miss[1] is not None else None})
                parts = [f"{t}: {len(x)} ({', '.join(pl for pl, _, _ in x[:3])}{' …' if len(x) > 3 else ''})"
                         for t, x in zip(teams, miss) if x]
                if parts:
                    info += " · Ausfälle laut API-Football – " + "; ".join(parts) + " (fließen noch nicht in die Rechnung ein)"
                    srcs = srcs + ["API-Football (Verletzte/Gesperrte)"]
        return {"p_yes": p, "info": info, "hours": hours, "model": "Form-Modell (Elo)", "model_id": MODEL_ID, "lane": "form",
                "variants": {MODEL_ID: p}, "uncertainty": 0.08, "sources": srcs, "features": feats}

    # ------------------------------------------------------------------ Auswertung
    def status(self):
        rows = self.store.q("SELECT category, sport, COUNT(*) n FROM match_results GROUP BY category, sport ORDER BY n DESC")
        rated = {k: sum(1 for v in t.values() if v[1] >= config.FORM_MIN_GAMES) for k, t in self.ratings().items()}
        return {"ergebnisse": [{"thema": r["category"], "sportart": r["sport"], "spiele": r["n"],
                                "bewertet": rated.get((r["category"], r["sport"]), 0)} for r in rows],
                "clubelo": self.store.kv_json("form:clubelo", None), "zeiger": self.store.kv_json("form:cursor", None),
                "verletzte": self.store.scalar("SELECT COUNT(*) FROM injuries"), "verletzte_schluessel": bool(config.API_FOOTBALL_KEY),
                "verletzte_fehler": self.store.kv_get("form:inj_error")}


def evaluate(store, min_events=1):
    """Treffsicherheit des Form-Modells gegen den Marktpreis je Sportart (entschiedene Prognosen)."""
    rows = store.q("SELECT f.category, COALESCE(m.sub, '–') sub, f.p, f.market_p, f.cluster, f.market_id, o.outcome "
                   "FROM forecasts f JOIN outcomes o ON o.market_id=f.market_id LEFT JOIN markets m ON m.id=f.market_id "
                   "WHERE f.model='form.elo' AND f.market_p IS NOT NULL")
    by = defaultdict(list)
    for r in rows:
        by[(r["category"], r["sub"])].append(r)
    out = []
    for (cat, sub), rs in by.items():
        ev = {r["cluster"] or r["market_id"] for r in rs}
        if len(ev) < min_events:
            continue
        b_mod = sum((r["p"] - r["outcome"]) ** 2 for r in rs) / len(rs)
        b_mkt = sum((r["market_p"] - r["outcome"]) ** 2 for r in rs) / len(rs)
        out.append({"thema": cat, "sportart": sub, "n": len(rs), "ereignisse": len(ev), "brier_modell": b_mod, "brier_markt": b_mkt})
    out.sort(key=lambda x: -x["n"])
    return out
