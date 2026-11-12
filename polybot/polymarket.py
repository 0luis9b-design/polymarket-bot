"""Polymarket lesen (nur öffentliche Daten, kein Konto, kein Wallet).

Gamma-API: https://gamma-api.polymarket.com
Wichtig: outcomes, outcomePrices und clobTokenIds kommen als JSON-Text, nicht als Liste.
bestBid / bestAsk gelten für das ERSTE Outcome (bei Ja/Nein also für "Ja").
"""
import json
import logging
from datetime import datetime, timezone

from . import config
from .http import HttpError, get_json

GAMMA = "https://gamma-api.polymarket.com"
log = logging.getLogger("polybot.polymarket")


def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def _num(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_dt(v):
    if not v:
        return None
    s = str(v).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if len(s) == 10:  # nur Datum
        s += "T00:00:00+00:00"
    if "+" not in s[10:] and "-" not in s[10:]:
        s += "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fee_rate_for(raw, category):
    """Gebührensatz aus den Marktdaten lesen, sonst Standardsatz der Kategorie."""
    if raw.get("feesEnabled") is False:
        return 0.0
    sched = raw.get("feeSchedule")
    if isinstance(sched, str):
        sched = _loads(sched, None)
    if isinstance(sched, dict):
        for k, v in sched.items():
            if "rate" in k.lower() and "maker" not in k.lower() and "rebate" not in k.lower():
                r = _num(v)
                if r is not None and 0 <= r < 1:
                    return r
    return {"crypto": config.FEE_RATE_CRYPTO, "weather": config.FEE_RATE_WEATHER}.get(category, config.FEE_RATE_SPORTS)


def normalize_market(raw, event=None, category="other"):
    """Rohdaten in ein einheitliches dict umwandeln. Gibt None zurück, wenn unbrauchbar."""
    outcomes = _loads(raw.get("outcomes"), [])
    prices = [_num(p, None) for p in _loads(raw.get("outcomePrices"), [])]
    if len(outcomes) != 2 or len(prices) != 2 or None in prices:
        return None
    bid = _num(raw.get("bestBid"))
    ask = _num(raw.get("bestAsk"))
    ev = event or {}
    tags = [t.get("slug") for t in (ev.get("tags") or raw.get("tags") or []) if isinstance(t, dict) and t.get("slug")]
    return {
        "id": str(raw.get("id")),
        "question": raw.get("question") or "",
        "slug": raw.get("slug") or "",
        "description": raw.get("description") or "",
        "outcomes": [str(o) for o in outcomes],
        "prices": prices,
        "best_bid": bid,
        "best_ask": ask,
        "liquidity": _num(raw.get("liquidityNum"), _num(raw.get("liquidity"), 0.0)),
        "volume": _num(raw.get("volumeNum"), _num(raw.get("volume"), 0.0)),
        "end_date": parse_dt(raw.get("endDate") or raw.get("endDateIso")),
        "closed": bool(raw.get("closed")),
        "active": raw.get("active", True) is not False,
        "accepting_orders": raw.get("acceptingOrders", True) is not False,
        "resolution_status": raw.get("umaResolutionStatus") or "",
        "sports_market_type": raw.get("sportsMarketType") or "",
        "game_start": parse_dt(raw.get("gameStartTime") or ev.get("startTime")),
        "group_title": raw.get("groupItemTitle") or "",
        "event_id": str(ev.get("id") or ""),
        "event_title": ev.get("title") or "",
        "event_slug": ev.get("slug") or "",
        "tags": tags,
        "category": category,
        "fee_rate": fee_rate_for(raw, category),
        "token_ids": [str(t) for t in _loads(raw.get("clobTokenIds"), [])],
        "neg_risk": bool(ev.get("negRisk") or ev.get("enableNegRisk")),
        "event_n_markets": len(ev.get("markets") or []),
        "min_size": _num(raw.get("orderMinSize"), 5.0),
    }


def quote(m, side):
    """Kaufkurs (Ask) und Verkaufskurs (Bid) für Outcome 0 oder 1.

    Kauf von Outcome 1 ("Nein") kostet 1 − bestBid von Outcome 0.
    """
    bid, ask = m["best_bid"], m["best_ask"]
    p0 = m["prices"][0]
    if bid is None or ask is None or ask <= 0 or bid <= 0 or ask < bid:
        # Kein brauchbares Orderbuch: konservativ 1 Cent Aufschlag
        bid, ask = max(p0 - 0.01, 0.001), min(p0 + 0.01, 0.999)
    if side == 0:
        return ask, bid
    return 1 - bid, 1 - ask


def mid_yes(m):
    bid, ask = m["best_bid"], m["best_ask"]
    if bid and ask and 0 < bid <= ask < 1:
        return (bid + ask) / 2
    return m["prices"][0]


def fetch_events(tag_slug, max_pages=8, page_size=100, extra=None):
    """Alle offenen Ereignisse zu einem Tag holen (mit Seitenweise-Abruf)."""
    out = []
    params = {"tag_slug": tag_slug, "closed": "false", "active": "true", "limit": page_size}
    if extra:
        params.update(extra)
    try:
        for page in range(max_pages):
            data = get_json(f"{GAMMA}/events", params={**params, "offset": page * page_size})
            items = data if isinstance(data, list) else (data.get("data") or data.get("events") or [])
            out.extend(items)
            if len(items) < page_size:
                break
        return out
    except HttpError as e:
        if e.status not in (400, 422):
            raise
        log.info("offset abgelehnt, nutze keyset-Abruf")
    out, cursor = [], None
    for _ in range(max_pages):
        p = dict(params)
        if cursor:
            p["after_cursor"] = cursor
        data = get_json(f"{GAMMA}/events/keyset", params=p)
        items = data if isinstance(data, list) else (data.get("data") or data.get("events") or [])
        out.extend(items)
        cursor = None if isinstance(data, list) else data.get("next_cursor")
        if not cursor or not items:
            break
    return out


def markets_from_events(events, category):
    out = []
    for ev in events:
        for raw in ev.get("markets") or []:
            m = normalize_market(raw, ev, category)
            if m and not m["closed"] and m["active"]:
                out.append(m)
    return out


def fetch_markets_by_ids(ids):
    """Märkte (auch geschlossene) für die Abrechnung holen."""
    result = {}
    ids = [str(i) for i in ids if i]
    for i in range(0, len(ids), 40):
        chunk = ids[i:i + 40]
        try:
            data = get_json(f"{GAMMA}/markets", params=[("id", x) for x in chunk] + [("limit", len(chunk))])
            for raw in data if isinstance(data, list) else data.get("data", []):
                result[str(raw.get("id"))] = raw
        except HttpError as e:
            log.warning("Sammelabruf fehlgeschlagen (%s), einzeln weiter", e)
        for x in chunk:
            if x in result:
                continue
            try:
                result[x] = get_json(f"{GAMMA}/markets/{x}")
            except HttpError as e:
                log.warning("Markt %s nicht abrufbar: %s", x, e)
    return result


def resolution(raw):
    """Gibt den Index des Gewinner-Outcomes zurück, 'void' bei 50/50, oder None wenn noch offen."""
    if not raw or not raw.get("closed"):
        return None
    prices = [_num(p, None) for p in _loads(raw.get("outcomePrices"), [])]
    if len(prices) != 2 or None in prices:
        return None
    status = (raw.get("umaResolutionStatus") or "").lower()
    if status and status not in ("resolved", "settled"):
        # z. B. "proposed" oder "disputed": noch nicht endgültig
        if not (max(prices) >= 0.999 and min(prices) <= 0.001):
            return None
    if prices[0] >= 0.99 and prices[1] <= 0.01:
        return 0
    if prices[1] >= 0.99 and prices[0] <= 0.01:
        return 1
    if abs(prices[0] - 0.5) < 0.01 and abs(prices[1] - 0.5) < 0.01:
        return "void"
    return None


# ---------------------------------------------------------------- Orderbuch (CLOB)
CLOB = "https://clob.polymarket.com"


def fetch_book(token_id):
    """Orderbuch eines Outcome-Tokens: (asks aufsteigend, bids absteigend) als [(preis, menge)]."""
    data = get_json(f"{CLOB}/book", params={"token_id": token_id}, retries=1)
    asks = sorted(((float(a["price"]), float(a["size"])) for a in data.get("asks") or []), key=lambda x: x[0])
    bids = sorted(((float(b["price"]), float(b["size"])) for b in data.get("bids") or []), key=lambda x: -x[0])
    return asks, bids


def fill_buy(asks, budget, fee_rate, q=None, min_edge=0.0):
    """Kauft für höchstens `budget` $ (inkl. Gebühr) über die Ask-Stufen.

    Stoppt an der Stufe, ab der der Vorsprung (q − Preis − Gebühr) unter min_edge fällt.
    Gibt (anteile, kosten_inkl_gebuehr, gebuehr, durchschnittskurs) zurück.
    """
    shares = cost = fee = 0.0
    left = budget
    for price, size in asks:
        fps = fee_rate * price * (1 - price)
        if q is not None and q - (price + fps) < min_edge:
            break
        per_share = price + fps
        take = min(size, left / per_share)
        if take <= 0:
            break
        shares += take
        cost += take * per_share
        fee += take * fps
        left -= take * per_share
        if left <= 1e-9:
            break
    avg = (cost - fee) / shares if shares else None
    return shares, cost, fee, avg


def fill_sell(bids, shares, fee_rate, min_price=0.0):
    """Verkauft `shares` über die Bid-Stufen. Gibt (verkauft, erloes_nach_gebuehr, gebuehr, durchschnittskurs)."""
    sold = proceeds = fee = 0.0
    left = shares
    for price, size in bids:
        if price < min_price:
            break
        take = min(size, left)
        fps = fee_rate * price * (1 - price)
        sold += take
        proceeds += take * (price - fps)
        fee += take * fps
        left -= take
        if left <= 1e-9:
            break
    avg = (proceeds + fee) / sold if sold else None
    return sold, proceeds, fee, avg
