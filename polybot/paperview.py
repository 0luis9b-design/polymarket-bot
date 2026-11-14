"""Daten für die Paper-Trading-Seiten: Übersicht, Wetten-Historie, Detail einer Wette, Kalender.

Alle Abfragen filtern gleich (Konto, Bereich › Unterbereich › Detail, Status, Suche, Zeitraum), damit Übersicht,
Historie und Kalender dieselben Wetten meinen. Alle Zahlen kommen aus der Datenbank – nichts wird geschätzt.
Tagesgrenzen gelten in deutscher Zeit.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, taxonomy

BERLIN = ZoneInfo("Europe/Berlin")
ACC_NAMES = {a["id"]: a["name"] for a in config.ACCOUNTS}
STRAT_ACCOUNTS = {}
for _a in config.ACCOUNTS:
    STRAT_ACCOUNTS.setdefault(_a.get("strategy") or _a["id"], []).append(_a["id"])
STATUS = {
    "offen": "status='open'",
    "entschieden": "status!='open'",
    "gewonnen": "(status='won' OR (status='sold' AND pnl>=0))",
    "verloren": "(status='lost' OR (status='sold' AND pnl<0))",
    "verkauft": "status='sold'",
    "storniert": "status='void'",
}
SORT = {"eroeffnet": "opened_at", "geschlossen": "closed_at", "ende": "end_date", "gv": "pnl", "einsatz": "cost",
        "chance": "own_p", "vorsprung": "edge"}
LIST_COLS = ("id, account, market_id, category, sub, detail, question, side, side_name, price, own_p, market_p, p_mid, edge, cost, shares, "
             "status, pnl, mark, opened_at, closed_at, end_date, kind, lane, model, event_slug, uncertainty, hours_left")


def choices():
    """Auswahllisten für die Filter: Strategien, Themen und alle Konten."""
    return {"strategies": [{"id": x["id"], "name": x["name"], "desc": x.get("rules", "")} for x in config.STRATEGIES],
            "topics": [{"id": cat, "name": name} for cat, _, name in config.TOPICS],
            "accounts": [{"id": a["id"], "name": a["name"], "strategy": a.get("strategy"), "topic": a.get("topic")} for a in config.ACCOUNTS]}


def single_account(args):
    """Ein einzelnes Konto ist gemeint: direkt gewählt oder Strategie + Thema (ohne feinere Filter)."""
    acc = args.get("account")
    if acc and acc != "alle":
        return acc if acc in ACC_NAMES else None
    strat, cat = args.get("strat"), args.get("cat")
    if strat and cat and not args.get("sub") and not args.get("detail"):
        code = config.TOPIC_CODE.get(cat)
        aid = f"{strat}_{code}" if code else None
        return aid if aid in ACC_NAMES else None
    return None


def day_of(iso):
    """Kalendertag (deutsche Zeit) eines ISO-Zeitstempels."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(BERLIN).strftime("%Y-%m-%d")


def _utc_bounds(day_from, day_to):
    """[Beginn von day_from, Beginn des Tages nach day_to) in UTC – für Abfragen auf ISO-Zeitstempel."""
    a = datetime.strptime(day_from, "%Y-%m-%d").replace(tzinfo=BERLIN).astimezone(timezone.utc)
    b = (datetime.strptime(day_to, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=BERLIN).astimezone(timezone.utc)
    return a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds")


def _valid_day(v):
    try:
        datetime.strptime(v, "%Y-%m-%d")
        return v
    except (TypeError, ValueError):
        return None


def where(args, status=True, search=True):
    """Gemeinsamer Filter. args: dict mit account, strat (Strategie), cat, sub, detail, status, q, von, bis, spur."""
    cond, params = [], []
    acc = args.get("account")
    if acc and acc != "alle":
        cond.append("account=?")
        params.append(acc[:40])
    strat = args.get("strat")
    if strat and strat != "alle":
        ids = STRAT_ACCOUNTS.get(strat) or ["–"]
        cond.append(f"account IN ({','.join('?' * len(ids))})")
        params += ids
    for key, col in (("cat", "category"), ("sub", "sub"), ("detail", "detail")):
        v = args.get(key)
        if v:
            cond.append(f"COALESCE({col}, ?)=?")
            params += [taxonomy.OTHER if col != "category" else "other", v[:80]]
    lane = args.get("spur")
    if lane in ("model", "base", "rule", "arb", "maker"):
        cond.append("COALESCE(lane, 'model')=?")
        params.append(lane)
    if status and args.get("status") in STATUS:
        cond.append(STATUS[args["status"]])
    if search:
        q = (args.get("q") or "").strip()[:80]
        if q:
            cond.append("question LIKE ? ESCAPE '\\'")
            params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    von, bis = _valid_day(args.get("von")), _valid_day(args.get("bis"))
    if von or bis:
        a, b = _utc_bounds(von or "2000-01-01", bis or "2100-01-01")
        cond.append("opened_at>=? AND opened_at<?")
        params += [a, b]
    return (" WHERE " + " AND ".join(cond)) if cond else "", params


def tree(store, args=None):
    """Bereich › Unterbereich › Detail mit Anzahl Wetten – nur was es wirklich gibt (für die Filter).
    Berücksichtigt nur Konto/Strategie, nicht den Bereich selbst (sonst gäbe es nichts mehr zu wählen)."""
    if isinstance(args, str):
        args = {"account": args}
    args = args or {}
    w, p = where({"account": args.get("account"), "strat": args.get("strat")}, status=False, search=False)
    rows = store.q(f"SELECT category, COALESCE(sub, ?) sub, COALESCE(detail, ?) detail, COUNT(*) n FROM positions {w} "
                   "GROUP BY 1, 2, 3", [taxonomy.OTHER, taxonomy.OTHER] + p)
    out = {}
    for r in rows:
        c = out.setdefault(r["category"] or "other", {"n": 0, "subs": {}})
        c["n"] += r["n"]
        s_ = c["subs"].setdefault(r["sub"], {"n": 0, "details": {}})
        s_["n"] += r["n"]
        s_["details"][r["detail"]] = s_["details"].get(r["detail"], 0) + r["n"]
    return out


def _decorate(r):
    r["account_name"] = ACC_NAMES.get(r.get("account"), r.get("account"))
    r["pfad"] = taxonomy.path({"category": r.get("category"), "sub": r.get("sub"), "detail": r.get("detail")})
    if r.get("status") == "open" and r.get("mark") is not None and r.get("shares"):
        r["wert"] = round(r["shares"] * r["mark"], 4)
        r["gv_offen"] = round(r["wert"] - r["cost"], 4)
    if r.get("p_mid") is None and r.get("market_p") is not None:
        r["p_mid"] = r["market_p"]            # ältere Wetten: nur der Kaufkurs ist bekannt
        r["p_mid_alt"] = True
    return r


def bets(store, args):
    w, p = where(args)
    sort = SORT.get(args.get("sort"), "opened_at")
    direction = "ASC" if args.get("dir") == "asc" else "DESC"
    try:
        per = max(10, min(200, int(args.get("per") or 50)))
        page = max(1, int(args.get("page") or 1))
    except ValueError:
        per, page = 50, 1
    total = store.scalar(f"SELECT COUNT(*) FROM positions{w}", p) or 0
    rows = [_decorate(r) for r in store.q(f"SELECT {LIST_COLS} FROM positions{w} ORDER BY {sort} IS NULL, {sort} {direction}, id DESC "
                                          "LIMIT ? OFFSET ?", p + [per, (page - 1) * per])]
    s = store.q1(f"SELECT COUNT(*) n, SUM(cost) einsatz, SUM(CASE WHEN status!='open' THEN pnl END) gv, "
                 f"SUM(CASE WHEN status='open' THEN shares*COALESCE(mark, price)-cost END) gv_offen, "
                 f"SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) gewonnen, SUM(CASE WHEN status='lost' THEN 1 ELSE 0 END) verloren, "
                 f"SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) offen FROM positions{w}", p)
    return {"rows": rows, "total": total, "page": page, "per": per, "summe": s, "tree": tree(store, args),
            **choices()}


def bet_detail(store, pid, opportunities=None):
    r = store.q1("SELECT * FROM positions WHERE id=?", (pid,))
    if not r:
        return None
    r = _decorate(r)
    try:
        r["entry"] = json.loads(r["entry"]) if r.get("entry") else None
    except ValueError:
        r["entry"] = None
    mid = r["market_id"]
    market = store.q1("SELECT id, question, category, sub, detail, event_title, slug, end_date, price, bid, ask, liquidity, volume24h, "
                      "status, last_seen FROM markets WHERE id=?", (mid,))
    prices = store.q("SELECT ts, price FROM snapshots WHERE market_id=? ORDER BY ts", (mid,))
    fcs = store.q("SELECT created_at, model, version, horizon, p, market_p, uncertainty, champion, valid, reject_reason FROM forecasts "
                  "WHERE market_id=? ORDER BY id", (mid,))
    outcome = store.q1("SELECT outcome, resolved_at FROM outcomes WHERE market_id=?", (mid,))
    others = [_decorate(x) for x in store.q("SELECT id, account, side_name, price, own_p, cost, status, pnl, opened_at FROM positions "
                                            "WHERE market_id=? AND id!=? ORDER BY id", (mid, pid))]
    live = next((o for o in (opportunities or []) if o.get("id") == mid), None)
    # Sicht unserer Seite: Ja-Kurse in „Wahrscheinlichkeit für unseren Ausgang“ umrechnen
    flip = (lambda v: v) if r["side"] == 0 else (lambda v: None if v is None else 1 - v)
    return {"bet": r, "market": market, "outcome": outcome, "others": others, "live": live,
            "prices": [{"ts": x["ts"], "p": flip(x["price"])} for x in prices],
            "forecasts": [{**f, "p_side": flip(f["p"]), "market_side": flip(f["market_p"])} for f in fcs]}


def calendar(store, args):
    """G/V je Tag (nach Abschluss der Wette, deutsche Zeit) für einen Monat, dazu die Wetten eines gewählten Tages."""
    month = args.get("monat") or datetime.now(BERLIN).strftime("%Y-%m")
    try:
        first = datetime.strptime(month + "-01", "%Y-%m-%d")
    except ValueError:
        first = datetime.now(BERLIN).replace(day=1, tzinfo=None)
        month = first.strftime("%Y-%m")
    nxt = (first + timedelta(days=32)).replace(day=1)
    last = (nxt - timedelta(days=1)).strftime("%Y-%m-%d")
    a, b = _utc_bounds(first.strftime("%Y-%m-%d"), last)
    w, p = where({**args, "status": None, "von": None, "bis": None})
    joiner = " AND " if w else " WHERE "
    closed = store.q(f"SELECT id, pnl, cost, status, closed_at, own_p FROM positions{w}{joiner}status!='open' AND closed_at>=? AND closed_at<?",
                     p + [a, b])
    opened = store.q(f"SELECT opened_at, cost FROM positions{w}{joiner}opened_at>=? AND opened_at<?", p + [a, b])
    days = defaultdict(lambda: {"gv": 0.0, "n": 0, "gewonnen": 0, "verloren": 0, "verkauft": 0, "einsatz": 0.0, "eroeffnet": 0})
    for r in closed:
        d = days[day_of(r["closed_at"])]
        d["gv"] += r["pnl"] or 0
        d["n"] += 1
        d["einsatz"] += r["cost"] or 0
        if r["status"] == "won":
            d["gewonnen"] += 1
        elif r["status"] == "lost":
            d["verloren"] += 1
        elif r["status"] == "sold":
            d["verkauft"] += 1
    for r in opened:
        days[day_of(r["opened_at"])]["eroeffnet"] += 1
    out_days = [{"tag": k, **{kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()}} for k, v in sorted(days.items()) if k]
    total = {"gv": round(sum(x["gv"] for x in out_days), 4), "n": sum(x["n"] for x in out_days),
             "gewonnen": sum(x["gewonnen"] for x in out_days), "verloren": sum(x["verloren"] for x in out_days),
             "eroeffnet": sum(x["eroeffnet"] for x in out_days),
             "plus_tage": sum(1 for x in out_days if x["n"] and x["gv"] > 0.004), "minus_tage": sum(1 for x in out_days if x["n"] and x["gv"] < -0.004)}
    span = store.q1(f"SELECT MIN(opened_at) a, MAX(COALESCE(closed_at, opened_at)) b FROM positions{w}", p)
    res = {"monat": month, "tage": out_days, "summe": total, "erster": day_of(span["a"]) if span and span["a"] else None,
           "letzter": day_of(span["b"]) if span and span["b"] else None, "tree": tree(store, args),
           **choices()}
    day = _valid_day(args.get("tag"))
    if day:
        da, db = _utc_bounds(day, day)
        res["tag"] = day
        res["geschlossen"] = [_decorate(x) for x in store.q(
            f"SELECT {LIST_COLS} FROM positions{w}{joiner}status!='open' AND closed_at>=? AND closed_at<? ORDER BY closed_at", p + [da, db])]
        res["eroeffnet"] = [_decorate(x) for x in store.q(
            f"SELECT {LIST_COLS} FROM positions{w}{joiner}opened_at>=? AND opened_at<? ORDER BY opened_at", p + [da, db])]
    return res


def overview(store, engine, args):
    """Kennzahlen für die gewählten Wetten, Konten-Vergleich, offene Wetten und aktuelle Chancen."""
    w, p = where({**args, "status": None})
    acc = single_account(args)
    filtered = any(args.get(k) for k in ("sub", "detail", "spur")) or (bool(args.get("cat")) and not (args.get("strat") and acc))
    joiner = " AND " if w else " WHERE "
    k = store.q1(f"SELECT SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) offen, SUM(CASE WHEN status='open' THEN cost ELSE 0 END) gebunden, "
                 f"SUM(CASE WHEN status='open' THEN shares*COALESCE(mark, price)-cost ELSE 0 END) gv_offen, "
                 f"SUM(CASE WHEN status!='open' THEN 1 ELSE 0 END) geschlossen, SUM(CASE WHEN status!='open' THEN pnl ELSE 0 END) gv, "
                 f"SUM(CASE WHEN status!='open' THEN cost ELSE 0 END) einsatz, "
                 f"SUM(CASE WHEN status='won' AND COALESCE(lane,'model')!='arb' THEN 1 ELSE 0 END) gewonnen, "
                 f"SUM(CASE WHEN status IN ('won','lost') AND COALESCE(lane,'model')!='arb' THEN 1 ELSE 0 END) entschieden, "
                 f"AVG(CASE WHEN status IN ('won','lost') AND COALESCE(lane,'model')!='arb' THEN own_p END) erwartet "
                 f"FROM positions{w}", p) or {}
    today = datetime.now(BERLIN).strftime("%Y-%m-%d")
    ta, tb = _utc_bounds(today, today)
    k["gv_heute"] = store.scalar(f"SELECT SUM(pnl) FROM positions{w}{joiner}status!='open' AND closed_at>=? AND closed_at<?", p + [ta, tb])
    k["trefferquote"] = (k["gewonnen"] / k["entschieden"]) if k.get("entschieden") else None
    k["rendite"] = (k["gv"] / k["einsatz"]) if k.get("einsatz") else None
    # Kurve: Kontowert (ein Konto, kein Bereichsfilter) – sonst aufsummierter G/V der gefilterten Wetten
    if acc and not filtered:
        curve = {"art": "kontowert", "punkte": [[x["ts"], x["equity"]] for x in store.equity_history(acc)]}
        k["kontowert"] = engine.equity(acc) if engine else None
        k["bargeld"] = engine.cash(acc) if engine else None
        k["start"] = config.START_BALANCE
    else:
        run, pts = 0.0, []
        for x in store.q(f"SELECT closed_at, pnl FROM positions{w}{joiner}status!='open' AND closed_at IS NOT NULL ORDER BY closed_at", p):
            run += x["pnl"] or 0
            pts.append([x["closed_at"], round(run, 4)])
        curve = {"art": "gv", "punkte": pts}
    # Aufteilung eine Stufe unter dem aktuellen Filter
    level = "detail" if args.get("sub") else "sub" if args.get("cat") else "category"
    split = store.q(f"SELECT COALESCE({level}, ?) name, COUNT(*) n, SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) offen, "
                    f"SUM(CASE WHEN status!='open' THEN pnl ELSE 0 END) gv, SUM(CASE WHEN status!='open' THEN cost ELSE 0 END) einsatz, "
                    f"SUM(CASE WHEN status='won' THEN 1 ELSE 0 END) gewonnen, SUM(CASE WHEN status IN ('won','lost') THEN 1 ELSE 0 END) entschieden "
                    f"FROM positions{w} GROUP BY 1 ORDER BY n DESC", [taxonomy.OTHER if level != "category" else "other"] + p)
    open_rows = [_decorate(x) for x in store.q(f"SELECT {LIST_COLS} FROM positions{w}{joiner}status='open' ORDER BY end_date IS NULL, end_date LIMIT 100", p)]
    ow, op = where({**args, "status": None, "q": None, "von": None, "bis": None, "spur": None})
    orders = store.q(f"SELECT id, account, category, sub, detail, question, side_name, price, stake, q, created_at, end_date FROM orders"
                     f"{ow}{' AND ' if ow else ' WHERE '}status='open' ORDER BY created_at DESC LIMIT 50", op)
    for o in orders:
        o["account_name"] = ACC_NAMES.get(o["account"], o["account"])
    accounts = engine.accounts_overview() if engine else []
    strategies = engine.strategy_summary(accounts) if engine else []
    chances = []
    for o in (engine.state.get("opportunities") if engine else None) or []:
        if args.get("cat") and o.get("category") != args["cat"]:
            continue
        if args.get("sub") and o.get("sub") != args["sub"]:
            continue
        if args.get("detail") and o.get("detail") != args["detail"]:
            continue
        chances.append(o)
    return {"kennzahlen": k, "kurve": curve, "aufteilung": {"stufe": level, "rows": split}, "offen": open_rows, "gebote": orders,
            "konten": accounts, "strategien": strategies, "konto": next((a for a in accounts if a["id"] == acc), None) if acc else None,
            "chancen": chances[:25], "tree": tree(store, args), **choices(), "start": config.START_BALANCE,
            "letzter_lauf": engine.state.get("last_scan") if engine else None}
