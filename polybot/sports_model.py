"""Eigene Wahrscheinlichkeit für Sport-Märkte aus Buchmacher-Quoten.

Idee: Profi-Buchmacher (v. a. Pinnacle) setzen sehr genaue Quoten. Rechnet man ihre
Marge heraus, erhält man eine faire Wahrscheinlichkeit. Weicht Polymarket deutlich
davon ab, ist das eine Chance.

Quelle: The Odds API (https://the-odds-api.com), Gratis-Plan 500 Abrufe/Monat.
Jeder Abruf (1 Liga, Markt h2h, Region eu) kostet 1 Guthaben-Punkt.
"""
import json
import logging
import re
import statistics
import time
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher

from . import config
from .http import HttpError, get_json
from .polymarket import parse_dt

log = logging.getLogger("polybot.sports")
ODDS = "https://api.the-odds-api.com/v4"

# Polymarket-Tag  ->  Odds-API-Liga
LEAGUES = {
    "nba": "basketball_nba",
    "wnba": "basketball_wnba",
    "ncaab": "basketball_ncaab",
    "cbb": "basketball_ncaab",
    "euroleague": "basketball_euroleague",
    "nfl": "americanfootball_nfl",
    "cfb": "americanfootball_ncaaf",
    "ncaaf": "americanfootball_ncaaf",
    "nhl": "icehockey_nhl",
    "mlb": "baseball_mlb",
    "epl": "soccer_epl",
    "premier-league": "soccer_epl",
    "la-liga": "soccer_spain_la_liga",
    "laliga": "soccer_spain_la_liga",
    "bundesliga": "soccer_germany_bundesliga",
    "bundesliga-2": "soccer_germany_bundesliga2",
    "serie-a": "soccer_italy_serie_a",
    "ligue-1": "soccer_france_ligue_one",
    "ucl": "soccer_uefa_champs_league",
    "champions-league": "soccer_uefa_champs_league",
    "uel": "soccer_uefa_europa_league",
    "europa-league": "soccer_uefa_europa_league",
    "uecl": "soccer_uefa_europa_conference_league",
    "mls": "soccer_usa_mls",
    "eredivisie": "soccer_netherlands_eredivisie",
    "ufc": "mma_mixed_martial_arts",
    "mma": "mma_mixed_martial_arts",
}

STOP = {"fc", "cf", "afc", "sc", "ac", "the", "club", "de", "cd", "ud", "sv", "fk", "bc", "vs", "v", "and", "1", "04", "05", "09"}


ALIASES = {
    "man utd": "manchester united", "man united": "manchester united", "man city": "manchester city",
    "spurs": "tottenham hotspur", "wolves": "wolverhampton wanderers", "psg": "paris saint germain",
    "inter": "inter milan", "internazionale": "inter milan", "atletico": "atletico madrid",
    "nottm forest": "nottingham forest", "gladbach": "borussia monchengladbach",
    "leverkusen": "bayer leverkusen", "munchen": "munich", "la": "los angeles", "ny": "new york",
}


def norm(name):
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = " ".join(s.split())
    for k, v in ALIASES.items():
        s = re.sub(rf"\b{k}\b", v, s)
    return [w for w in s.split() if w and w not in STOP]


def _tok_match(x, y):
    return x == y or (min(len(x), len(y)) >= 3 and (x.startswith(y) or y.startswith(x)))


def name_score(a, b):
    """0..1 – wie gut passen zwei Teamnamen zusammen (z. B. 'Lakers' ~ 'Los Angeles Lakers')."""
    ta, tb = norm(a), norm(b)
    if not ta or not tb:
        return 0.0
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return 1.0
    if sa <= sb or sb <= sa:
        return 0.92
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    hits = sum(1 for x in short if any(_tok_match(x, y) for y in long_))
    inter = hits / len(short)
    ratio = SequenceMatcher(None, " ".join(ta), " ".join(tb)).ratio()
    # Ähnlichkeit der Schreibweise zählt nur, wenn sie sehr hoch ist (sonst: Man United ≠ Man City)
    return max(inter * 0.9, ratio if ratio >= 0.88 else 0.0)


def devig(prices):
    """Dezimalquoten -> faire Wahrscheinlichkeiten (Marge proportional herausgerechnet)."""
    inv = [1 / p for p in prices if p and p > 1]
    if len(inv) != len(prices):
        return None
    tot = sum(inv)
    return [x / tot for x in inv]


def consensus(event, n_outcomes=None):
    """Faire Wahrscheinlichkeit je Outcome-Name. Pinnacle zählt doppelt, sonst Median.

    n_outcomes=2: nur Buchmacher mit Zwei-Wege-Quote (inkl. Verlängerung, z. B. NHL bei Polymarket),
    n_outcomes=3: nur Drei-Wege-Quoten (Fußball mit Unentschieden).
    """
    per_name = {}
    books = 0
    pin = False
    for bm in event.get("bookmakers") or []:
        for mk in bm.get("markets") or []:
            if mk.get("key") != "h2h":
                continue
            outs = mk.get("outcomes") or []
            if n_outcomes and len(outs) != n_outcomes:
                continue
            pin = pin or bm.get("key") == "pinnacle"
            fair = devig([o.get("price") for o in outs])
            if not fair:
                continue
            books += 1
            weight = 2 if bm.get("key") == "pinnacle" else 1
            for o, p in zip(outs, fair):
                per_name.setdefault(o["name"], []).extend([p] * weight)
    if books < config.SPORTS_MIN_BOOKS and not pin:
        return None, books
    if not per_name:
        return None, books
    med = {k: statistics.median(v) for k, v in per_name.items()}
    tot = sum(med.values())
    return {k: v / tot for k, v in med.items()}, books


class OddsClient:
    def __init__(self, store, api_key=None, fetch=get_json):
        self.store = store
        self.key = api_key if api_key is not None else config.ODDS_API_KEY
        self.fetch = fetch
        self.cache = {}  # sport -> (zeit, events)
        self.status = "kein ODDS_API_KEY gesetzt – Sport-Modul aus" if not self.key else "bereit"

    @property
    def enabled(self):
        return bool(self.key)

    def credits_left(self):
        v = self.store.kv_get("odds_remaining")
        return int(float(v)) if v not in (None, "") else None

    @staticmethod
    def _day_key():
        return "odds_used_" + datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def used_today(self):
        return int(float(self.store.kv_get(self._day_key(), 0) or 0))

    def daily_budget(self):
        """Restguthaben gleichmäßig auf die restlichen Tage des Monats verteilen."""
        if config.ODDS_DAILY_BUDGET > 0:
            return config.ODDS_DAILY_BUDGET
        left = self.credits_left()
        if left is None:
            return 10  # erster Tag: Guthaben noch unbekannt
        now = datetime.now(timezone.utc)
        nxt = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
        days = max(1, (nxt - now).days + 1)
        return max(1, (left - config.ODDS_CREDIT_RESERVE) // days)

    def events(self, sport, max_age=None):
        """max_age (Sekunden) überschreibt das normale Intervall – z. B. für Schlusskurse kurz vor Anpfiff."""
        if not self.enabled:
            return []
        hit = self.cache.get(sport)
        if hit is None:  # nach einem Neustart: gespeicherte Quoten nutzen statt Guthaben zu verbrauchen
            saved = self.store.kv_json(f"odds_cache:{sport}") if hasattr(self.store, "kv_json") else None
            if saved and "t" in saved:
                hit = self.cache[sport] = (saved["t"], saved.get("data") or [])
        limit = max_age if max_age is not None else config.ODDS_REFRESH_MIN * 60
        if hit and time.time() - hit[0] < limit:
            return hit[1]
        left = self.credits_left()
        if left is not None and left <= config.ODDS_CREDIT_RESERVE:
            self.status = f"Guthaben fast leer ({left}) – warte auf Monatsreset"
            return hit[1] if hit else []
        used, budget = self.used_today(), self.daily_budget()
        if used >= budget:
            self.status = f"Tagesbudget aufgebraucht ({used}/{budget}) – nutze letzte Quoten"
            return hit[1] if hit else []
        try:
            data, headers = self.fetch(
                f"{ODDS}/sports/{sport}/odds",
                params={"apiKey": self.key, "regions": config.ODDS_REGIONS, "markets": "h2h",
                        "oddsFormat": "decimal", "dateFormat": "iso"},
                want_headers=True)
        except HttpError as e:
            self.status = f"Fehler bei {sport}: {str(e)[:120]}"
            if e.status in (401, 403):
                self.key = ""
            return hit[1] if hit else []
        self.store.kv_set(self._day_key(), self.used_today() + 1)
        rem = headers.get("x-requests-remaining") if headers else None
        if rem is not None:
            self.store.kv_set("odds_remaining", rem)
        self.cache[sport] = (time.time(), data or [])
        try:
            self.store.kv_set(f"odds_cache:{sport}", json.dumps({"t": time.time(), "data": data or []}))
        except (TypeError, ValueError):
            pass
        self.status = (f"OK – Restguthaben {rem if rem is not None else '?'} · heute "
                       f"{self.used_today()}/{self.daily_budget()} Abrufe")
        return data or []


def league_for(market):
    tags = [str(t).lower() for t in market.get("tags") or []]
    for t in tags:  # zuerst die gewählten Ligen, damit z. B. "bundesliga-2" nicht als "bundesliga" gilt
        if t in config.SPORTS_LEAGUES and t in LEAGUES:
            return LEAGUES[t]
    for t in tags:
        if t in LEAGUES:
            return LEAGUES[t]
    slug = market.get("event_slug") or ""
    prefix = slug.split("-")[0]
    return LEAGUES.get(prefix)


def split_title(title):
    parts = re.split(r"\s+(?:vs\.?|v\.?|@|at)\s+", title or "", flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), re.sub(r"\s*[-–(].*$", "", parts[1]).strip()
    return None


def find_event(events, team_a, team_b, start):
    best, best_s = None, 0.0
    for ev in events:
        h, a = ev.get("home_team", ""), ev.get("away_team", "")
        s1 = min(name_score(team_a, h), name_score(team_b, a))
        s2 = min(name_score(team_a, a), name_score(team_b, h))
        s = max(s1, s2)
        ct = parse_dt(ev.get("commence_time"))
        if start and ct and abs((ct - start).total_seconds()) > 8 * 3600:
            continue
        if s > best_s:
            best, best_s = ev, s
    return (best, best_s) if best_s >= 0.75 else (None, best_s)


def map_prob(fair, name):
    best, best_s = None, 0.0
    for k, v in fair.items():
        s = name_score(name, k)
        if s > best_s:
            best, best_s = v, s
    return best if best_s >= 0.75 else None


def analyze(market, odds_client, now=None):
    now = now or datetime.now(timezone.utc)
    if not odds_client.enabled:
        return {"skip": "Sport-Modul aus (kein API-Key)"}
    smt = (market.get("sports_market_type") or "").lower()
    if smt and smt not in ("moneyline", "h2h", "winner"):
        return {"skip": f"Markttyp {smt} nicht unterstützt"}
    start = market.get("game_start")
    if not start:
        return {"skip": "keine Anstoßzeit"}
    hours = (start - now).total_seconds() / 3600
    if hours < 0.1:
        return {"skip": "Spiel läuft schon"}
    if hours > config.SPORTS_LOOKAHEAD_H:
        return {"skip": "Spiel erst später"}
    sport = league_for(market)
    if not sport:
        return {"skip": "Liga nicht zugeordnet"}
    teams = split_title(market.get("event_title")) or split_title(market.get("question"))
    if not teams:
        return {"skip": "Teams nicht erkannt"}
    events = odds_client.events(sport)
    ev, score = find_event(events, teams[0], teams[1], start)
    if not ev:
        return {"skip": f"kein Buchmacher-Spiel gefunden ({score:.2f})"}
    outs = [o.lower() for o in market["outcomes"]]
    yes_no = outs[0] in ("yes", "ja")
    if yes_no:
        fair, books = consensus(ev, 3 if sport.startswith("soccer") else None)
    else:
        fair, books = consensus(ev, 2)  # Polymarket-Sieger-Markt mit zwei Teams: Verlängerung zählt
    if not fair:
        return {"skip": "zu wenige passende Buchmacher-Quoten"}

    q = market["question"]
    if yes_no:
        ql = q.lower()
        if "draw" in ql or "unentschieden" in ql:
            p = next((v for k, v in fair.items() if k.lower() == "draw"), None)
            label = "Unentschieden"
        else:
            m = re.search(r"will (.+?) win", q, flags=re.I)
            if not m:
                return {"skip": "Ja/Nein-Frage nicht erkannt"}
            label = m.group(1)
            p = map_prob({k: v for k, v in fair.items() if k.lower() != "draw"}, label)
    else:
        if any(k.lower() == "draw" for k in fair):
            return {"skip": "Zwei-Wege-Markt bei Spiel mit Remis"}
        label = market["outcomes"][0]
        p = map_prob(fair, label)
    if p is None:
        return {"skip": "Outcome nicht zugeordnet"}
    info = (f"{ev.get('home_team')} – {ev.get('away_team')} · faire Quote {label}: {p * 100:.1f} % "
            f"({books} Buchmacher) · Anstoß in {hours:.0f} h")
    return {"p_yes": min(max(p, 0.001), 0.999), "info": info, "hours": hours, "books": books}
