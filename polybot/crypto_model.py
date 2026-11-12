"""Eigene Wahrscheinlichkeit für Krypto-Preis-Märkte.

Idee: Der Kurs bewegt sich zufällig, aber mit messbarer Schwankung (Volatilität).
Aus aktuellem Kurs, Ziel-Preis, Restzeit und Volatilität lässt sich berechnen,
wie wahrscheinlich es ist, dass der Kurs am Stichtag über/unter dem Ziel liegt
oder das Ziel unterwegs berührt.

Die Kursänderungen werden als Mischung aus zwei Normalverteilungen modelliert
(80 % ruhige Phasen, 20 % wilde Phasen). Das bildet die "dicken Ränder" von Krypto
besser ab als eine einzelne Glockenkurve.
"""
import math
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import config
from .prices import hourly_closes

ASSET_PATTERNS = [
    ("BTC", r"\b(bitcoin|btc)\b"),
    ("ETH", r"\b(ethereum|eth|ether)\b"),
    ("SOL", r"\b(solana|sol)\b"),
    ("XRP", r"\b(xrp|ripple)\b"),
    ("DOGE", r"\b(dogecoin|doge)\b"),
]
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
ET = ZoneInfo("America/New_York")

# Mischung: Gewicht, Faktor auf die Streuung (Gesamtvarianz bleibt gleich)
_A = math.sqrt(1 / 1.6)
MIXTURE = [(0.8, _A), (0.2, 2 * _A)]

_AMOUNT = r"\$\s?([\d,]+(?:\.\d+)?)\s*([kKmM]\b)?"


def _amount(num, suffix):
    v = float(num.replace(",", ""))
    if suffix:
        v *= 1_000 if suffix.lower() == "k" else 1_000_000
    return v


def parse_question(q):
    """Erkennt Coin, Art der Wette und Ziel-Preis(e). None = kein unterstützter Markt."""
    t = q.lower()
    if any(x in t for x in ("up or down", "all time high", "all-time high", "hashprice", "hash rate", "hashrate",
                            "dominance", "market cap", "etf", "flows", "fees", "transactions", "reserve")):
        return None
    asset = next((a for a, pat in ASSET_PATTERNS if re.search(pat, t)), None)
    if not asset:
        return None
    amounts = [_amount(n, s) for n, s in re.findall(_AMOUNT, q)]
    if not amounts:
        return None
    if re.search(r"\bbetween\b", t) and len(amounts) >= 2:
        lo, hi = sorted(amounts[:2])
        return {"asset": asset, "kind": "between", "k1": lo, "k2": hi}
    k = amounts[0]
    if re.search(r"\b(dip|dips|drop|drops|fall|falls|crash|crashes)\b", t):
        return {"asset": asset, "kind": "touch_down", "k1": k}
    if re.search(r"\b(reach|reaches|hit|hits|touch|touches)\b", t):
        return {"asset": asset, "kind": "touch", "k1": k}  # Richtung entscheidet der aktuelle Kurs
    if re.search(r"\b(below|less than|lower than|under)\b", t):
        return {"asset": asset, "kind": "below", "k1": k}
    if re.search(r"\b(above|greater than|higher than|over)\b", t):
        return {"asset": asset, "kind": "above", "k1": k}
    return None


def target_time(question, end_date):
    """Bei "... on November 14?" gilt der Binance-Schlusskurs um 12:00 Uhr New Yorker Zeit."""
    m = re.search(r"\bon (january|february|march|april|may|june|july|august|september|october|november|december) (\d{1,2})\b",
                  question.lower())
    if m and end_date:
        month, day = MONTHS[m.group(1)], int(m.group(2))
        year = end_date.astimezone(ET).year
        try:
            t = datetime(year, month, day, 12, 0, tzinfo=ET).astimezone(timezone.utc)
        except ValueError:
            return end_date
        # Plausibel: höchstens 2 Tage Abstand zum offiziellen Enddatum
        if abs((t - end_date).total_seconds()) <= 2 * 86400:
            return t
    return end_date


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def prob_above(spot, strike, sd):
    """P(Kurs am Ende > strike), sd = Streuung der log-Rendite bis zum Ende."""
    if sd <= 0:
        return 1.0 if spot > strike else 0.0
    x = math.log(spot / strike)
    return sum(w * _ncdf((x - (f * sd) ** 2 / 2) / (f * sd)) for w, f in MIXTURE)


def prob_touch_up(spot, strike, sd):
    """P(Kurs berührt strike (oberhalb) bis zum Ende) – Spiegelungsprinzip."""
    if spot >= strike:
        return 1.0
    return min(1.0, 2 * prob_above(spot, strike, sd))


def prob_touch_down(spot, strike, sd):
    if spot <= strike:
        return 1.0
    return min(1.0, 2 * (1 - prob_above(spot, strike, sd)))


def hourly_vol(closes):
    """Volatilität pro Stunde: halb aktuell (EWMA, Halbwertszeit 3 Tage), halb 30-Tage-Schnitt."""
    rets = [math.log(b / a) for a, b in zip(closes[:-2], closes[1:-1]) if a > 0 and b > 0]
    if len(rets) < 48:
        raise ValueError("zu wenig Kursdaten")
    long_var = sum(r * r for r in rets) / len(rets)
    lam = 0.5 ** (1 / 72)
    ew = long_var
    for r in rets:
        ew = lam * ew + (1 - lam) * r * r
    return math.sqrt(0.5 * ew + 0.5 * long_var) * config.VOL_MULTIPLIER


def probability(parsed, spot, sigma_h, hours):
    sd = sigma_h * math.sqrt(max(hours, 1e-6))
    k = parsed["kind"]
    if k == "above":
        return prob_above(spot, parsed["k1"], sd)
    if k == "below":
        return 1 - prob_above(spot, parsed["k1"], sd)
    if k == "between":
        return max(0.0, prob_above(spot, parsed["k1"], sd) - prob_above(spot, parsed["k2"], sd))
    if k == "touch_down":
        return prob_touch_down(spot, parsed["k1"], sd)
    if k == "touch":
        if parsed["k1"] >= spot:
            return prob_touch_up(spot, parsed["k1"], sd)
        return prob_touch_down(spot, parsed["k1"], sd)
    raise ValueError(k)


KIND_DE = {"above": "über", "below": "unter", "between": "zwischen", "touch": "berührt", "touch_down": "fällt auf"}


def _fmt(v):
    return f"{v:,.0f}".replace(",", ".") if v >= 100 else f"{v:.4g}"


def probability_options(parsed, spot, ch, hours):
    """Wie probability(), aber mit der Deribit-Volatilitätsoberfläche."""
    above = lambda k: ch.prob_above(spot, k, hours)  # noqa: E731
    k = parsed["kind"]
    if k == "above":
        return above(parsed["k1"])
    if k == "below":
        return 1 - above(parsed["k1"])
    if k == "between":
        return max(0.0, above(parsed["k1"]) - above(parsed["k2"]))
    up = k == "touch" and parsed["k1"] >= spot
    if up:
        return 1.0 if spot >= parsed["k1"] else min(1.0, 2 * above(parsed["k1"]))
    return 1.0 if spot <= parsed["k1"] else min(1.0, 2 * (1 - above(parsed["k1"])))


def _vol_premium(now, chain_fn, closes_fn):
    """Für Coins ohne Optionsdaten: Verhältnis BTC-Optionsvolatilität zu BTC-Vergangenheitsvolatilität."""
    try:
        ch = chain_fn("BTC", now)
        if not ch:
            return 1.0
        closes, _ = closes_fn("BTC")
        rv = hourly_vol(closes) / config.VOL_MULTIPLIER * math.sqrt(24 * 365)
        iv = ch.atm_vol(24 / (24 * 365), closes[-1])
        return min(max(iv / rv, 0.8), 1.6)
    except Exception:  # noqa: BLE001
        return 1.0


def analyze(market, now=None, closes_fn=hourly_closes, chain_fn=None):
    """Gibt {'p_yes', 'info', ...} zurück oder {'skip': Grund}."""
    from . import options
    chain_fn = chain_fn or (lambda asset, n: options.chain(asset, n))
    now = now or datetime.now(timezone.utc)
    parsed = parse_question(market["question"])
    if not parsed:
        return {"skip": "Fragetyp nicht unterstützt"}
    if market["outcomes"][0].lower() not in ("yes", "ja"):
        return {"skip": "kein Ja/Nein-Markt"}
    end = market["end_date"]
    if not end:
        return {"skip": "kein Enddatum"}
    tt = end if parsed["kind"] in ("touch", "touch_down") else target_time(market["question"], end)
    hours = (tt - now).total_seconds() / 3600
    if hours < 0:
        return {"skip": "Stichtag vorbei, wartet auf Entscheidung"}
    if hours < config.CRYPTO_MIN_HOURS:
        return {"skip": "läuft bald ab"}
    if hours > config.CRYPTO_MAX_DAYS * 24:
        return {"skip": "zu lange Laufzeit"}
    closes, source = closes_fn(parsed["asset"])
    spot = closes[-1]
    # Schutz vor Fehlerkennung: Ziel-Preis muss in der Nähe des echten Kurses liegen
    for k in (parsed["k1"], parsed.get("k2") or parsed["k1"]):
        if not (spot / 4 <= k <= spot * 4):
            return {"skip": "Ziel-Preis unplausibel"}
    sigma = hourly_vol(closes)
    p_hist = probability(parsed, spot, sigma, hours)
    ch = None
    try:
        ch = chain_fn(parsed["asset"], now)
    except Exception:  # noqa: BLE001
        ch = None
    strikes = _fmt(parsed["k1"]) + (f"–{_fmt(parsed['k2'])}" if parsed["kind"] == "between" else "")
    head = f"{parsed['asset']} {_fmt(spot)} $ · Ziel {KIND_DE[parsed['kind']]} {strikes} $ · {hours:.0f} h"
    if ch:
        p = probability_options(parsed, spot, ch, hours)
        iv = ch.atm_vol(max(hours, 1) / (24 * 365), spot)
        model = "Deribit-Optionen"
        info = (f"{head} · Optionen-Vola {f'{iv * 100:.0f}'} % p. a. · ohne Optionen wären es "
                f"{f'{p_hist * 100:.1f}'.replace('.', ',')} % ({source})")
    else:
        prem = _vol_premium(now, chain_fn, closes_fn)
        sig2 = sigma * prem
        p = probability(parsed, spot, sig2, hours)
        model = "Kurshistorie"
        info = (f"{head} · Vol {f'{sig2 * 100:.2f}'.replace('.', ',')} %/h "
                f"(Historie × {f'{prem:.2f}'.replace('.', ',')} Optionsaufschlag, {source})")
    day = tt.astimezone(ET).strftime("%Y-%m-%d")
    return {"p_yes": min(max(p, 0.001), 0.999), "info": info, "asset": parsed["asset"], "model": model,
            "spot": spot, "sigma_h": sigma, "hours": hours, "kind": parsed["kind"], "parsed": parsed,
            "target": tt.isoformat(), "group": f"{parsed['asset']}:{day}"}
