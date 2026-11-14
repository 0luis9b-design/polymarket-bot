"""Einordnung jedes Marktes in drei Stufen: Bereich → Unterbereich → Detail.

Beispiele:  Sport → Fußball → Champions League · Krypto → Bitcoin → Kurs am Stichtag · Wetter → Europa → London
            Politik → USA → Midterms · E-Sport → Counter-Strike 2 → (Turnier)

Grundlage sind die Schlagwörter (Tags), die Polymarket an jeden Markt hängt. Ihre Reihenfolge ist nicht verlässlich
(mal steht „sports“ vorne, mal hinten) – deshalb wird nur nach bekannten Schlagwörtern gesucht, in fester Rangfolge.
Unbekannte Schlagwörter werden als Detail übernommen (Polymarkets Bezeichnung, als Text – nie als Anweisung).
Für Krypto und Wetter kommt die Einordnung aus der Marktfrage selbst (Coin, Station), weil die Modelle sie ohnehin lesen.
"""
import re

NAMES = {"crypto": "Krypto", "sports": "Sport", "weather": "Wetter", "esports": "E-Sport", "politics": "Politik", "world": "Welt",
         "economy": "Wirtschaft", "tech": "Technik", "science": "Wissenschaft", "culture": "Kultur", "other": "Sonstiges"}
OTHER = "Allgemein"          # Detail, wenn nichts Genaueres bekannt ist

# Schlagwörter ohne Aussage über das Thema (Sortierhilfen, Turnierphasen, Werbe-Aktionen)
GENERIC = {
    "sports", "games", "recurring", "hide-from-new", "featured", "trending", "new", "today", "daily", "weekly", "monthly",
    "yearly", "neg-risk", "multi-strikes", "breaking", "all", "props", "knockout-stage", "final", "semifinal", "semi-finals",
    "quarterfinal", "quarter-finals", "group-stage", "playoffs", "regular-season", "parent-for-derivative", "earn-4", "main-election",
    "post-election", "close", "rankings", "global", "best-of-2026", "matchday", "ucl-matchday", "nba-tip-off", "build-your-map",
    "build-your-own-map", "map", "competitive-races", "votes", "election", "debut", "roster-changes",
}
_PROMO = re.compile(r"^(rewards?|earn)-|^global-rates$", re.I)

# ---------------------------------------------------------------- Bereich (Rangfolge zählt)
WEATHER_TAGS = {"weather", "daily-temperature", "highest-temperature", "lowest-temperature"}
ESPORT_GAMES = [  # (Schlagwörter, Name)
    ({"counter-strike-2", "cs2", "counter-strike"}, "Counter-Strike 2"),
    ({"league-of-legends", "lol"}, "League of Legends"),
    ({"dota-2"}, "Dota 2"),
    ({"valorant", "val"}, "Valorant"),
    ({"rainbow-six-siege"}, "Rainbow Six"),
    ({"call-of-duty"}, "Call of Duty"),
    ({"rocket-league"}, "Rocket League"),
    ({"overwatch"}, "Overwatch"),
]
SPORT_KINDS = [  # (Schlagwörter, Unterbereich) – Ligen zählen mit, falls „soccer“ o. Ä. fehlt
    ({"soccer", "ucl", "champions-league", "uel", "europa-league", "uecl", "europa-conference-league", "uefa-nations-league",
      "epl", "premier-league", "bundesliga", "bundesliga-2", "la-liga", "laliga", "la-liga-2", "serie-a", "serie-b", "ligue-1",
      "ligue-2", "efl-championship", "scottish-premiership", "soccer-el1", "soccer-el2", "mls", "eredivisie", "brazil-serie-a",
      "brazil-serie-b", "mex", "colombia-primera-a", "tur", "concacaf-nations-league", "world-cup", "fifa-world-cup", "afcq"}, "Fußball"),
    ({"basketball", "nba", "wnba", "ncaa-basketball", "ncaab", "cbb", "euroleague", "bkbsl"}, "Basketball"),
    ({"nfl", "cfb", "ncaaf", "american-football"}, "American Football"),
    ({"hockey", "nhl", "khl"}, "Eishockey"),
    ({"tennis", "atp", "wta"}, "Tennis"),
    ({"ufc", "mma", "boxing", "pfl"}, "Kampfsport"),
    ({"baseball", "mlb", "kbo", "npb"}, "Baseball"),
    ({"cricket", "international-cricket", "ipl"}, "Cricket"),
    ({"golf", "pga", "liv-golf"}, "Golf"),
    ({"f1", "formula-1", "formula1", "nascar", "motorsport", "motogp", "indycar"}, "Motorsport"),
    ({"rugby"}, "Rugby"),
    ({"darts"}, "Darts"),
    ({"chess"}, "Schach"),
    ({"cycling"}, "Radsport"),
    ({"olympics"}, "Olympia"),
    ({"table-tennis"}, "Tischtennis"),
]
LEAGUES = {  # Detail für Sport
    "ucl": "Champions League", "champions-league": "Champions League", "uel": "Europa League", "europa-league": "Europa League",
    "uecl": "Conference League", "europa-conference-league": "Conference League", "uefa-nations-league": "Nations League",
    "epl": "Premier League", "premier-league": "Premier League", "bundesliga": "Bundesliga", "bundesliga-2": "2. Bundesliga",
    "la-liga": "La Liga", "laliga": "La Liga", "la-liga-2": "La Liga 2", "serie-a": "Serie A", "serie-b": "Serie B",
    "ligue-1": "Ligue 1", "ligue-2": "Ligue 2", "efl-championship": "Championship", "scottish-premiership": "Schottland",
    "soccer-el1": "League One", "soccer-el2": "League Two", "mls": "MLS", "eredivisie": "Eredivisie",
    "brazil-serie-a": "Brasilien Série A", "brazil-serie-b": "Brasilien Série B", "mex": "Liga MX",
    "colombia-primera-a": "Kolumbien Primera A", "tur": "Süper Lig", "concacaf-nations-league": "CONCACAF Nations League",
    "world-cup": "Weltmeisterschaft", "fifa-world-cup": "Weltmeisterschaft", "afcq": "WM-Qualifikation Asien",
    "nba": "NBA", "wnba": "WNBA", "ncaa-basketball": "College Basketball", "ncaab": "College Basketball", "cbb": "College Basketball",
    "euroleague": "EuroLeague", "bkbsl": "Türkei BSL", "nfl": "NFL", "cfb": "College Football", "ncaaf": "College Football",
    "nhl": "NHL", "khl": "KHL", "mlb": "MLB", "ufc": "UFC", "pfl": "PFL", "atp": "ATP", "wta": "WTA", "f1": "Formel 1",
    "formula-1": "Formel 1", "nascar": "NASCAR", "motogp": "MotoGP", "ipl": "IPL",
}
# Bereiche ohne eigenes Modell: (Schlagwörter, Bereich) in Rangfolge
TOPIC_ORDER = [
    ({"tweets-markets", "mention-markets"}, "culture"),
    ({"elections", "us-presidential-election", "midterms", "primaries", "world-elections", "global-elections", "congress",
      "senate-elections", "2028-election", "house-of-representatives"}, "politics"),
    ({"fed", "fomc", "fed-rates", "economy", "economic-policy", "macro-indicators", "commodities", "oil", "gold", "inflation", "ipo",
      "ipos", "finance", "business", "stocks", "equities", "finance-updown"}, "economy"),
    ({"geopolitics", "world", "ukraine", "russia", "iran", "israel", "middle-east", "china", "taiwan", "venezuela"}, "world"),
    ({"science", "climate-science", "climate", "fda", "vaccine", "health", "pandemic"}, "science"),
    ({"tech", "ai", "big-tech", "openai", "anthropic", "nvidia", "spacex", "space", "apple", "google", "tesla", "llm"}, "tech"),
    ({"politics", "trump", "us-politics", "trump-presidency"}, "politics"),
    ({"pop-culture", "culture", "movies", "music", "celebrities", "awards", "oscars", "youtube", "netflix", "video-games",
      "box-office", "streamer"}, "culture"),
]
SUBS = {  # Unterbereich je Bereich: (Schlagwörter, Name) in Rangfolge
    "politics": [({"midterms", "senate-elections", "us-presidential-election", "2028-election", "congress", "primaries",
                   "united-states", "trump", "us-politics", "trump-presidency", "house-of-representatives", "cabinet-shakeup"}, "USA"),
                 ({"elections", "world-elections", "global-elections"}, "Wahlen weltweit")],
    "economy": [({"fed", "fomc", "fed-rates", "economic-policy"}, "Notenbank"),
                ({"ipo", "ipos", "anthropic-ipo", "openai-ipo"}, "Börsengänge"),
                ({"commodities", "oil", "gold"}, "Rohstoffe"),
                ({"macro-indicators", "inflation", "cpi", "jobs"}, "Konjunkturdaten"),
                ({"stocks", "equities", "finance-updown"}, "Aktien und Indizes"),
                ({"big-tech", "business", "nvidia"}, "Unternehmen")],
    "world": [({"iran", "israel", "middle-east", "israel-x-iran", "strait-of-hormuz"}, "Nahost"),
              ({"ukraine", "russia", "putin", "zelensky", "zelenskyy"}, "Russland und Ukraine"),
              ({"china", "taiwan"}, "China und Taiwan"),
              ({"venezuela", "latin-america", "mexico"}, "Lateinamerika")],
    "science": [({"fda", "vaccine", "cancer", "health", "pandemic"}, "Medizin"),
                ({"climate-science", "climate"}, "Klima"),
                ({"space", "spacex", "nasa"}, "Raumfahrt")],
    "tech": [({"ai", "openai", "anthropic", "llm", "gpt-releases", "ai-releases", "claude"}, "Künstliche Intelligenz"),
             ({"space", "spacex"}, "Raumfahrt"),
             ({"big-tech", "nvidia", "google", "apple", "tesla", "x"}, "Tech-Konzerne")],
    "culture": [({"tweets-markets"}, "Tweets"),
                ({"mention-markets"}, "Erwähnungen"),
                ({"movies", "box-office", "netflix", "oscars", "top-netflix"}, "Film und Serien"),
                ({"music", "album"}, "Musik"),
                ({"youtube", "streamer", "mrbeast"}, "YouTube und Streamer"),
                ({"video-games", "gta-vi", "gta"}, "Videospiele"),
                ({"awards", "time-poty"}, "Auszeichnungen"),
                ({"celebrities"}, "Prominente")],
}
COINS = [  # (Schlagwörter, Wörter in der Frage, Name)
    ({"bitcoin", "btc"}, ("bitcoin", "btc"), "Bitcoin"),
    ({"ethereum", "eth"}, ("ethereum", "eth "), "Ethereum"),
    ({"solana", "sol"}, ("solana",), "Solana"),
    ({"xrp", "ripple"}, ("xrp", "ripple"), "XRP"),
    ({"dogecoin", "doge"}, ("dogecoin", "doge"), "Dogecoin"),
    ({"bnb"}, ("bnb",), "BNB"),
    ({"hyperliquid", "hype"}, ("hyperliquid",), "Hyperliquid"),
    ({"cardano"}, ("cardano",), "Cardano"),
]
CRYPTO_KINDS = {"above": "Kurs am Stichtag", "below": "Kurs am Stichtag", "between": "Kursbereich am Stichtag",
                "touch": "Erreicht einen Kurs", "touch_down": "Fällt auf einen Kurs"}
REGIONS = {"Europe": "Europa", "America": "Amerika", "Asia": "Asien", "Africa": "Afrika", "Australia": "Ozeanien", "Pacific": "Ozeanien"}
# Für ältere Wetten ohne Stationsangabe: Stadt → Region (Städte der Polymarket-Temperaturmärkte)
_CITIES = {
    "Europa": "london, paris, munich, berlin, madrid, moscow, istanbul, ankara, helsinki, warsaw, amsterdam, rome, milan, vienna, "
              "stockholm, dublin, zurich, lisbon, athens, prague, oslo, copenhagen, brussels, kyiv",
    "Asien": "tokyo, seoul, hong kong, chongqing, shanghai, beijing, shenzhen, guangzhou, wuhan, chengdu, singapore, manila, "
             "kuala lumpur, lucknow, karachi, delhi, mumbai, bangkok, jakarta, taipei, jeddah, riyadh, dubai, tel aviv, tehran",
    "Amerika": "new york, chicago, los angeles, miami, dallas, atlanta, seattle, houston, denver, phoenix, boston, san francisco, "
               "toronto, vancouver, mexico city, buenos aires, sao paulo, são paulo, lima, bogota, santiago",
    "Afrika": "cape town, johannesburg, lagos, cairo, nairobi",
    "Ozeanien": "sydney, melbourne, wellington, auckland",
}
CITY_REGION = {c.strip(): r for r, cs in _CITIES.items() for c in cs.split(",")}


def _pretty(slug, labels):
    lab = (labels or {}).get(slug)
    if lab and lab.strip():
        return lab.strip()[:60]
    return " ".join(w.capitalize() if not w.isupper() else w for w in slug.replace("_", "-").split("-"))[:60]


def _first(tags, table):
    for keys, name in table:
        if tags & keys:
            return name, keys
    return None, set()


def category_of(tags, question=""):
    """Bereich aus den Schlagwörtern. Gibt „other“ zurück, wenn nichts passt."""
    t = set(tags)
    if t & WEATHER_TAGS or re.search(r"(highest|lowest) temperature in ", question or "", re.I):
        return "weather"
    if "esports" in t or any(t & keys for keys, _ in ESPORT_GAMES):
        return "esports"
    if t & {"crypto", "crypto-prices"}:
        return "crypto"
    if "sports" in t or ("games" in t and any(t & keys for keys, _ in SPORT_KINDS)):
        return "sports"
    for keys, cat in TOPIC_ORDER:
        if t & keys:
            return cat
    return "other"


def _detail_from_tags(tags_ordered, used, labels):
    for s in tags_ordered:
        if s in used or s in GENERIC or _PROMO.search(s) or s in NAMES:
            continue
        return _pretty(s, labels)
    return OTHER


def classify(m, category=None):
    """dict(category, sub, detail) für einen Markt (normalisiertes dict aus polymarket.normalize_market).

    `category` erzwingt den Bereich (bei Märkten, die das Handelsmodell schon einer Kategorie zugeordnet hat)."""
    tags_ordered = [str(x).lower() for x in (m.get("tags") or []) if x]
    tags = set(tags_ordered)
    labels = m.get("tag_labels") or {}
    q = m.get("question") or ""
    cat = category or m.get("category")
    if cat in (None, "", "other") or cat not in NAMES:
        cat = category_of(tags, q)
    sub, detail = OTHER, OTHER

    if cat == "crypto":
        low = " " + q.lower() + " "
        sub = next((name for keys, words, name in COINS if tags & keys or any(w in low for w in words)), "Weitere Coins")
        if "up or down" in low or "up-or-down" in tags:
            detail = "Hoch oder runter"
        else:
            try:
                from .crypto_model import parse_question as parse
                p = parse(q)
            except Exception:  # noqa: BLE001
                p = None
            detail = CRYPTO_KINDS.get((p or {}).get("kind"), "Erreicht einen Kurs" if "hit-price" in tags else OTHER)
    elif cat == "weather":
        try:
            from .weather_model import STATIONS, parse_question, station_for
            pq = parse_question(q) or {}
            icao = station_for(m)
        except Exception:  # noqa: BLE001
            pq, icao, STATIONS = {}, None, {}
        city = (pq.get("city") or "").strip()
        detail = city[:1].upper() + city[1:] if city else OTHER
        if icao and icao in STATIONS:
            sub = REGIONS.get(STATIONS[icao][2].split("/")[0], OTHER)
        elif city:
            low = city.lower()
            sub = next((r for c, r in CITY_REGION.items() if low == c or low.startswith(c)), OTHER)
    elif cat == "sports":
        sub, used = _first(tags, SPORT_KINDS)
        sub = sub or OTHER
        league = next((LEAGUES[s] for s in tags_ordered if s in LEAGUES), None)
        if not league:  # ältere Wetten ohne Schlagwörter: Liga steckt im Kürzel des Ereignisses („ucl-bay-psg-…“)
            prefix = (m.get("event_slug") or m.get("slug") or "").split("-")[0].lower()
            league = LEAGUES.get(prefix)
            if league and sub == OTHER:
                sub = _first({prefix}, SPORT_KINDS)[0] or OTHER
        detail = league or _detail_from_tags(tags_ordered, used | set(LEAGUES), labels)
    elif cat == "esports":
        sub, used = _first(tags, ESPORT_GAMES)
        sub = sub or OTHER
        detail = _detail_from_tags(tags_ordered, used | {"esports"}, labels)
    else:
        sub, used = _first(tags, SUBS.get(cat, []))
        if not sub:
            sub = _detail_from_tags(tags_ordered, set().union(*[k for k, _ in TOPIC_ORDER]), labels)
            used = {s for s in tags_ordered if _pretty(s, labels) == sub}
        detail = _detail_from_tags(tags_ordered, used | set().union(*[k for k, _ in TOPIC_ORDER]), labels)
        if detail == sub:
            detail = OTHER
    return {"category": cat, "sub": sub or OTHER, "detail": detail or OTHER}


def path(c):
    """Lesbarer Pfad, z. B. „Sport › Fußball › Champions League“."""
    parts = [NAMES.get(c.get("category"), c.get("category") or "?"), c.get("sub"), c.get("detail")]
    return " › ".join(p for p in parts if p and p != OTHER)
