"""Market Scanner Agent: Marktkatalog, stündlicher Preisverlauf, neue Märkte, auffällige Bewegungen.

Zwei Zuflüsse:
  ingest()  jeder Lauf – die Märkte der Modell-Kategorien (Krypto, Sport, Wetter), die der Handels-Lauf ohnehin holt
  run()     stündlich – je Kategorie die aktivsten Märkte von Polymarket (kleine /markets-Abrufe, 100 pro Seite)

Abdeckung ist bewusst „breit, aber nach Aktivität sortiert“: Polymarket führt zehntausende Märkte,
die meisten ohne Handel. Im Katalog landen je Kategorie die mit dem meisten 24-h-Volumen.
"""
import math
from datetime import datetime, timedelta, timezone

from .. import config, events, polymarket, taxonomy
from . import Agent

# (Kategorie im System, Tag-ID bei Polymarket) – IDs am 14.11.2026 live geprüft. Reihenfolge = Vorrang bei Mehrfach-Tags.
CATEGORY_TAGS = [
    ("crypto", 21), ("weather", 84), ("esports", 64), ("sports", 1), ("politics", 2), ("politics", 144),
    ("world", 100265), ("world", 101970), ("economy", 100328), ("economy", 120), ("economy", 107),
    ("tech", 1401), ("tech", 439), ("science", 74), ("culture", 596),
]
CATEGORY_NAMES = {"crypto": "Krypto", "weather": "Wetter", "sports": "Sport", "esports": "E-Sport", "politics": "Politik",
                  "world": "Welt", "economy": "Wirtschaft", "tech": "Technik", "science": "Wissenschaft", "culture": "Kultur", "other": "Sonstiges"}
MODELED = ("crypto", "sports", "weather")


def quality_flags(m, now):
    flags = []
    if not m.get("best_bid") or not m.get("best_ask"):
        flags.append("kein_orderbuch")
    elif m["best_ask"] - m["best_bid"] > 0.10:
        flags.append("spanne_gross")
    if (m.get("liquidity") or 0) < 500:
        flags.append("wenig_liquiditaet")
    if not m.get("end_date"):
        flags.append("kein_ende")
    elif m["end_date"] < now:
        flags.append("abgelaufen")
    return flags


def priority(m, flags, now, modeled, news=0):
    """0–100: wie viel Aufmerksamkeit verdient der Markt gerade (Aktivität, Nähe zur Entscheidung, Datenqualität)."""
    act = min(1.0, math.log10(1 + (m.get("volume24h") or 0)) / 6)
    liq = min(1.0, math.log10(1 + (m.get("liquidity") or 0)) / 6)
    if m.get("end_date"):
        h = (m["end_date"] - now).total_seconds() / 3600
        soon = 0.0 if h < 0 else 1.0 if h < 24 else 0.7 if h < 72 else 0.4 if h < 24 * 14 else 0.2
    else:
        soon = 0.1
    base = 0.40 * act + 0.20 * liq + 0.20 * soon + (0.10 if modeled else 0) + 0.10 * min(1.0, news / 3)
    return round(100 * base * max(0.0, 1 - 0.25 * len(flags)), 1)


class ScannerAgent(Agent):
    id = "scanner"
    name = "Market Scanner"
    role = "Hält den Marktkatalog aller Kategorien aktuell, speichert den Preisverlauf, erkennt neue Märkte und Bewegungen"
    interval = 3600
    budget = 90
    priority = 1

    def __init__(self, store, fetch_markets=polymarket.fetch_markets):
        super().__init__(store)
        self.fetch_markets = fetch_markets
        self._snap_hour = None
        self.last_ingest = {}

    # ------------------------------------------------------------------ Zufluss
    def ingest(self, markets, now, modeled=True, category=None):
        """Märkte in den Katalog übernehmen (anlegen oder aktualisieren) und einmal pro Stunde den Preis festhalten."""
        if not markets:
            return {"märkte": 0}
        stamp = now.isoformat(timespec="seconds")
        hour = now.strftime("%Y-%m-%dT%H:00")
        news = {r["id"]: r["news_48h"] for r in self.store.q("SELECT id, news_48h FROM markets WHERE news_48h>0")}
        rows, snaps = [], []
        for m in markets:
            flags = quality_flags(m, now)
            cat = category or m["category"]
            if not m.get("sub"):
                c = taxonomy.classify(m, None if cat in (None, "other") else cat)
                cat = c["category"] if cat in (None, "other") else cat
                m["sub"], m["detail"] = c["sub"], c["detail"]
            price = polymarket.mid_yes(m)
            rows.append((m["id"], m["question"][:400], cat, m.get("sub"), m.get("detail"), ",".join((m.get("tags") or [])[:12]), m.get("event_id"),
                         (m.get("event_title") or "")[:200], m.get("event_slug") or m.get("slug"),
                         m["end_date"].isoformat() if m.get("end_date") else None, stamp, stamp, round(price, 4),
                         m.get("best_bid"), m.get("best_ask"), m.get("liquidity"), m.get("volume"), m.get("volume24h"),
                         ",".join(flags), priority(m, flags, now, modeled, news.get(m["id"], 0)), 1 if modeled else 0))
            snaps.append((m["id"], hour, round(price, 4), m.get("best_bid"), m.get("best_ask"), m.get("volume24h"), m.get("liquidity")))
        self.store.xmany(
            "INSERT INTO markets(id, question, category, sub, detail, tags, event_id, event_title, slug, end_date, first_seen, last_seen, "
            "price, bid, ask, liquidity, volume, volume24h, quality, priority, modeled) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, price=excluded.price, bid=excluded.bid, ask=excluded.ask, "
            "liquidity=excluded.liquidity, volume=excluded.volume, volume24h=excluded.volume24h, quality=excluded.quality, "
            "priority=excluded.priority, end_date=excluded.end_date, status='open', "
            "modeled=MAX(markets.modeled, excluded.modeled), "
            "tags=CASE WHEN excluded.tags != '' THEN excluded.tags ELSE markets.tags END, "
            "category=CASE WHEN excluded.modeled=1 OR markets.modeled=0 THEN excluded.category ELSE markets.category END, "
            "sub=CASE WHEN excluded.modeled=1 OR markets.modeled=0 OR markets.sub IS NULL THEN excluded.sub ELSE markets.sub END, "
            "detail=CASE WHEN excluded.modeled=1 OR markets.modeled=0 OR markets.detail IS NULL THEN excluded.detail ELSE markets.detail END", rows)
        key = (hour, modeled, category)
        if not modeled or self._snap_hour != key:
            self.store.xmany("INSERT OR IGNORE INTO snapshots(market_id, ts, price, bid, ask, volume24h, liquidity) VALUES(?,?,?,?,?,?,?)", snaps)
            if modeled:
                self._snap_hour = key
        self.last_ingest = {"märkte": len(rows), "zeit": stamp}
        return {"märkte": len(rows)}

    # ------------------------------------------------------------------ stündlicher Durchlauf
    def run(self, now, deadline):
        if not config.SCANNER_ENABLED:
            return {"aus": True}
        known_before = self.store.scalar("SELECT COUNT(*) FROM markets WHERE modeled=0") or 0
        seen, per_cat, errors = set(), {}, 0
        pages = max(1, math.ceil(config.CATALOG_PER_CATEGORY / 100))
        for cat, tag_id in CATEGORY_TAGS:
            if self.time_left(deadline) < 8:
                break
            for page in range(pages):
                try:
                    raws = self.fetch_markets({"tag_id": tag_id, "related_tags": "true", "closed": "false", "active": "true", "include_tag": "true",
                                               "order": "volume24hr", "ascending": "false", "limit": 100, "offset": page * 100})
                except Exception:  # noqa: BLE001 – eine Kategorie darf ausfallen
                    errors += 1
                    break
                ms = []
                for raw in raws:
                    ev = (raw.get("events") or [{}])[0] if isinstance(raw.get("events"), list) and raw.get("events") else {}
                    m = polymarket.normalize_market(raw, ev, "other")
                    if m and not m["closed"] and m["id"] not in seen and "up or down" not in m["question"].lower():
                        seen.add(m["id"])
                        c = taxonomy.classify(m)          # Einordnung nach den Schlagwörtern des Marktes, nicht nach der Abfrage
                        if c["category"] == "other":
                            c = taxonomy.classify(m, cat)
                        m.update(c)
                        m["fee_rate"] = polymarket.fee_rate_for(raw, c["category"])
                        ms.append(m)
                # Modell-Kategorien kommen vollständig über den Handels-Lauf; hier nur ergänzen, nicht umhängen
                self.ingest(ms, now, modeled=False)
                for m in ms:
                    per_cat[m["category"]] = per_cat.get(m["category"], 0) + 1
                if len(raws) < 100:
                    break
        moves = self.update_moves(now)
        self.update_status(now)
        new = self.store.scalar("SELECT COUNT(*) FROM markets WHERE first_seen>=?", ((now - timedelta(hours=24)).isoformat(timespec="seconds"),))
        if known_before >= 100:
            self.announce_new(now)
        return {"katalog": per_cat, "fehler": errors, "bewegungen": moves, "neu_24h": new}

    def update_moves(self, now):
        """Veränderung gegenüber dem Stand vor 1 h und vor 24 h (aus dem eigenen Preisverlauf)."""
        h1 = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:00")
        h24 = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:00")
        self.store.x("UPDATE markets SET move_1h = price - (SELECT price FROM snapshots s WHERE s.market_id=markets.id AND s.ts=?), "
                     "move_24h = price - (SELECT price FROM snapshots s WHERE s.market_id=markets.id AND s.ts=?) WHERE status='open'", (h1, h24))
        soon = (now + timedelta(hours=6)).isoformat(timespec="seconds")
        rows = self.store.q("SELECT id, question, category, price, move_1h, volume24h, liquidity FROM markets WHERE status='open' "
                            "AND ABS(move_1h)>=? AND liquidity>=5000 AND price BETWEEN 0.03 AND 0.97 AND (end_date IS NULL OR end_date>?) "
                            "ORDER BY ABS(move_1h) DESC LIMIT 15", (config.MOVE_ALERT_PTS, soon))
        hour = now.strftime("%Y-%m-%dT%H")
        for r in rows:
            big = abs(r["move_1h"]) >= 2 * config.MOVE_ALERT_PTS and (r["volume24h"] or 0) >= 50000
            events.emit(self.store, "normal" if big else "info", "bewegung",
                        f"Kursbewegung {r['move_1h'] * 100:+.0f} Pkt in 1 h: {r['question'][:120]}",
                        f"Jetzt {r['price'] * 100:.0f} % · 24-h-Volumen {r['volume24h'] or 0:,.0f} $ · {CATEGORY_NAMES.get(r['category'], r['category'])}",
                        key=f"move:{r['id']}:{hour}", agent=self.id, telegram=big)
        return len(rows)

    def update_status(self, now):
        iso = lambda d: d.isoformat(timespec="seconds")  # noqa: E731
        self.store.x("UPDATE markets SET status='closed' WHERE status='open' AND end_date IS NOT NULL AND end_date<?", (iso(now - timedelta(hours=6)),))
        self.store.x("UPDATE markets SET status='closed' WHERE status='open' AND modeled=1 AND last_seen<?", (iso(now - timedelta(hours=3)),))
        self.store.x("UPDATE markets SET status='unbeobachtet' WHERE status='open' AND modeled=0 AND last_seen<?", (iso(now - timedelta(hours=48)),))

    def announce_new(self, now):
        since = (now - timedelta(minutes=70)).isoformat(timespec="seconds")
        for r in self.store.q("SELECT id, question, category, volume24h FROM markets WHERE first_seen>=? AND modeled=0 AND volume24h>=20000 "
                              "ORDER BY volume24h DESC LIMIT 5", (since,)):
            events.emit(self.store, "info", "neuer_markt", f"Neu im Katalog: {r['question'][:140]}",
                        f"{CATEGORY_NAMES.get(r['category'], r['category'])} · 24-h-Volumen {r['volume24h']:,.0f} $",
                        key=f"neu:{r['id']}", agent=self.id, telegram=False)

    def stats(self):
        st = self.store
        return {"gesamt": st.scalar("SELECT COUNT(*) FROM markets"),
                "offen": st.scalar("SELECT COUNT(*) FROM markets WHERE status='open'"),
                "mit_modell": st.scalar("SELECT COUNT(*) FROM markets WHERE status='open' AND modeled=1"),
                "kategorien": st.q("SELECT category, COUNT(*) n, SUM(volume24h) vol FROM markets WHERE status='open' GROUP BY category ORDER BY n DESC"),
                "preispunkte": st.scalar("SELECT COUNT(*) FROM snapshots")}
