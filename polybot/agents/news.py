"""News Research Agent: sammelt Schlagzeilen aus frei zugänglichen RSS-Feeds und ordnet sie Märkten zu.

Grundsätze
  - Jede Meldung behält Quelle, Link und Veröffentlichungszeit. Es wird nichts umgeschrieben oder erfunden.
  - Texte von außen sind Daten, keine Anweisungen: Sie werden gespeichert, verglichen und (maskiert) angezeigt – mehr nicht.
  - Die Zuordnung ist ein Stichwort-Abgleich, kein Textverständnis. Sie sagt „diese Meldung nennt dieselben Namen“,
    nicht „diese Meldung ändert die Wahrscheinlichkeit“. In die Prognosen fließt sie deshalb nicht ein.
  - Veröffentlichungszeit ≠ Ereigniszeit: gespeichert wird, wann die Meldung erschien und wann sie abgerufen wurde.
    Wann das beschriebene Ereignis stattfand, lässt sich aus einer Schlagzeile nicht verlässlich ablesen.
"""
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .. import config, http
from . import Agent

STOP = set("""will would could should there their about after before between during other which while where when what with
within without from into over under than then this that these those have has had been being more most less least first last
next week month year day days today tomorrow price prices above below higher lower least reach reaches reached hits before
january february march april june july august september october november december monday tuesday wednesday thursday friday
saturday sunday team game match season winner wins market temperature highest lowest city announce announces announced
does make made says said president minister election elections party government state states united world news report""".split())
HEDGE = ("reportedly", "rumour", "rumor", "sources say", "source says", "unconfirmed", "alleged", "allegedly", "claims",
         "claim", "could", "may ", "might", "expected to", "set to", "speculation", "considering")
TAG = re.compile(r"<[^>]+>")
WORD = re.compile(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'’\-]{2,}")


def parse_rss(data):
    """RSS 2.0 lesen. Dokumente mit DOCTYPE/ENTITY werden abgelehnt (Schutz vor XML-Tricks)."""
    if isinstance(data, str):
        data = data.encode("utf-8", "replace")
    head = data[:4000].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        raise ValueError("RSS mit DOCTYPE/ENTITY wird nicht gelesen")
    root = ET.fromstring(data)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith("http"):
            continue
        pub = None
        try:
            d = parsedate_to_datetime((it.findtext("pubDate") or "").strip())
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            pub = d.astimezone(timezone.utc)
        except (TypeError, ValueError):
            pass
        summary = html.unescape(TAG.sub(" ", it.findtext("description") or "")).strip()
        out.append({"title": html.unescape(title)[:300], "url": link.split("#")[0][:500], "summary": summary[:500], "published": pub})
    return out


def tokens(text):
    return {w.lower().strip("'’-") for w in WORD.findall(text or "")} - STOP


def names(question):
    """Eigennamen: großgeschriebene Wörter, die nicht am Satzanfang stehen (grobe, aber robuste Näherung)."""
    words = WORD.findall(question or "")
    out = set()
    for i, w in enumerate(words):
        if i > 0 and w[0].isupper() and len(w) >= 4 and w.lower() not in STOP:
            out.add(w.lower().strip("'’-"))
    return out


def match_score(market_names, market_tokens, news_tokens):
    """> 0 nur, wenn mindestens ein Eigenname und insgesamt zwei Begriffe übereinstimmen."""
    hit_names = market_names & news_tokens
    if not hit_names:
        return 0.0
    hits = {t for t in market_tokens & news_tokens if len(t) >= 4}
    if len(hits) < 2:
        return 0.0
    return round(len(hit_names) + 0.5 * (len(hits) - len(hit_names)), 2)


def is_hedged(text):
    t = f" {text.lower()} "
    return any(h in t for h in HEDGE)


class NewsAgent(Agent):
    id = "news"
    name = "News Research"
    role = "Sammelt Schlagzeilen (BBC-RSS), merkt Quelle und Zeit, ordnet sie per Stichwort den wichtigsten Märkten zu"
    interval = 1800
    budget = 40
    priority = 5

    def __init__(self, store, fetch=http.get_text):
        super().__init__(store)
        self.fetch = fetch

    def run(self, now, deadline):
        if not config.NEWS_ENABLED:
            return {"aus": True}
        stamp = now.isoformat(timespec="seconds")
        new, errors, feeds = 0, 0, 0
        fresh = []
        for name, url in config.NEWS_FEEDS:
            if self.time_left(deadline) < 8:
                break
            try:
                items = parse_rss(self.fetch(url))
            except Exception:  # noqa: BLE001 – ein Feed darf ausfallen
                errors += 1
                continue
            feeds += 1
            for it in items[:60]:
                pub = it["published"]
                if pub and pub < now - timedelta(days=7):
                    continue
                cur = self.store.x("INSERT OR IGNORE INTO news(source, title, url, summary, published, fetched_at, flags) VALUES(?,?,?,?,?,?,?)",
                                   (name, it["title"], it["url"], it["summary"], pub.isoformat(timespec="seconds") if pub else None, stamp,
                                    "unbestätigt" if is_hedged(it["title"]) else ""))
                if cur.rowcount:
                    new += 1
                    fresh.append({"id": cur.lastrowid, **it})
        linked = self.link(fresh) if fresh else 0
        self.refresh_counts(now)
        self.store.x("DELETE FROM news_links WHERE news_id IN (SELECT id FROM news WHERE fetched_at<?)",
                     ((now - timedelta(days=30)).isoformat(timespec="seconds"),))
        self.store.x("DELETE FROM news WHERE fetched_at<?", ((now - timedelta(days=30)).isoformat(timespec="seconds"),))
        return {"feeds": feeds, "fehler": errors, "neu": new, "zugeordnet": linked}

    def link(self, items, top=400):
        markets = self.store.q("SELECT id, question, event_title FROM markets WHERE status='open' ORDER BY priority DESC LIMIT ?", (top,))
        prepared = []
        for m in markets:
            text = f"{m['question']} {m['event_title'] or ''}"
            n = names(text)
            if n:
                prepared.append((m["id"], n, tokens(text)))
        rows = []
        for it in items:
            nt = tokens(f"{it['title']} {it['summary']}")
            scored = [(match_score(n, t, nt), mid) for mid, n, t in prepared]
            for score, mid in sorted((s for s in scored if s[0] > 0), reverse=True)[:8]:
                rows.append((it["id"], mid, score))
        if rows:
            self.store.xmany("INSERT OR IGNORE INTO news_links(news_id, market_id, score) VALUES(?,?,?)", rows)
        return len(rows)

    def refresh_counts(self, now):
        since = (now - timedelta(hours=48)).isoformat(timespec="seconds")
        self.store.x("UPDATE markets SET news_48h = (SELECT COUNT(*) FROM news_links l JOIN news n ON n.id=l.news_id "
                     "WHERE l.market_id=markets.id AND COALESCE(n.published, n.fetched_at)>=?) "
                     "WHERE status='open' AND (news_48h>0 OR id IN (SELECT market_id FROM news_links))", (since,))

    def stats(self):
        day = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat(timespec="seconds")
        return {"gesamt": self.store.scalar("SELECT COUNT(*) FROM news"),
                "neu_24h": self.store.scalar("SELECT COUNT(*) FROM news WHERE fetched_at>=?", (day,)),
                "zuordnungen": self.store.scalar("SELECT COUNT(*) FROM news_links"),
                "quellen": self.store.q("SELECT source, COUNT(*) n, MAX(published) letzte FROM news GROUP BY source ORDER BY n DESC")}
