"""Erwartete Schwankung aus dem Deribit-Optionsmarkt (implizite Volatilität inkl. "Smile").

Warum: Die Kurse der letzten 30 Tage sagen wenig darüber, was Profis für die nächsten
Tage erwarten. Optionspreise schon – und sie enthalten auch, wie wahrscheinlich große
Sprünge eingeschätzt werden (weit entfernte Strikes haben höhere Volatilität = Smile).

Rechnung: Für Ziel-Preis K und Stichtag T wird die Volatilität am Strike K aus den
beiden umliegenden Deribit-Verfallstagen interpoliert (Gesamtvarianz linear in der Zeit).
P(Kurs > K) = Preisdifferenz zweier eng benachbarter Calls (Call-Spread) – das berücksichtigt
die Schiefe des Smiles automatisch.

Öffentliche API, kein Schlüssel nötig. Deribit verfällt täglich um 08:00 UTC.
"""
import logging
import math
import statistics
import time
from datetime import datetime, timezone

from .http import HttpError, get_json

log = logging.getLogger("polybot.options")
API = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
CACHE_SEC = 600
MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}
# Coin -> (Deribit-Währung, Präfix der Instrumente)
SOURCES = {"BTC": ("BTC", "BTC-"), "ETH": ("ETH", "ETH-"), "SOL": ("USDC", "SOL_USDC-"), "XRP": ("USDC", "XRP_USDC-")}
YEAR_H = 365.0 * 24

_cache = {}         # Währung -> (zeit, rohdaten)
status = {}         # Coin -> Text fürs Dashboard


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def parse_instrument(name):
    """'BTC-14NOV26-72000-C' / 'XRP_USDC-27NOV26-1d08-P' -> (expiry_utc, strike, 'C'/'P')."""
    parts = name.split("-")
    if len(parts) != 4:
        return None
    _, exp, strike, kind = parts
    try:
        day = int(exp[:-5])
        mon = MONTHS[exp[-5:-2]]
        year = 2000 + int(exp[-2:])
        k = float(strike.replace("d", "."))
    except (KeyError, ValueError):
        return None
    return datetime(year, mon, day, 8, 0, tzinfo=timezone.utc), k, kind


class Chain:
    """Volatilitäts-Oberfläche eines Coins."""

    def __init__(self, rows, prefix, now):
        by_exp = {}
        for r in rows:
            name = r.get("instrument_name") or ""
            if not name.startswith(prefix):
                continue
            p = parse_instrument(name)
            iv = r.get("mark_iv")
            fwd = r.get("underlying_price")
            if not p or not iv or iv <= 0 or not fwd:
                continue
            exp, k, kind = p
            by_exp.setdefault(exp, []).append((k, kind, iv / 100.0, float(fwd)))
        self.expiries = []  # (t_jahre, forward, [(log_moneyness, iv)])
        for exp, rows_ in sorted(by_exp.items()):
            t = (exp - now).total_seconds() / 3600 / YEAR_H
            if t < 1 / YEAR_H:  # weniger als 1 h – nicht mehr brauchbar
                continue
            fwd = statistics.median(r[3] for r in rows_)
            pts = {}
            for k, kind, iv, _ in rows_:
                # nur aus dem Geld liegende Optionen: Calls über, Puts unter dem Forward
                if (kind == "C" and k >= fwd) or (kind == "P" and k < fwd):
                    pts[round(math.log(k / fwd), 6)] = iv
            if len(pts) >= 5:
                self.expiries.append((t, fwd, sorted(pts.items())))

    @property
    def ok(self):
        return len(self.expiries) >= 2

    @staticmethod
    def _iv_at(points, x):
        if x <= points[0][0]:
            return points[0][1]
        if x >= points[-1][0]:
            return points[-1][1]
        for (x0, v0), (x1, v1) in zip(points, points[1:]):
            if x0 <= x <= x1:
                return v0 + (v1 - v0) * (x - x0) / (x1 - x0)
        return points[-1][1]

    def total_var(self, t, strike):
        """Gesamtvarianz der log-Rendite bis t (Jahre) am Strike."""
        ex = self.expiries
        vals = [(te, self._iv_at(pts, math.log(strike / f)) ** 2 * te) for te, f, pts in ex]
        if t <= vals[0][0]:
            return vals[0][1] / vals[0][0] * t
        for (t0, w0), (t1, w1) in zip(vals, vals[1:]):
            if t0 <= t <= t1:
                return w0 + (w1 - w0) * (t - t0) / (t1 - t0)
        return vals[-1][1] / vals[-1][0] * t

    def atm_vol(self, t, spot):
        return math.sqrt(self.total_var(t, spot) / t)

    def _call(self, spot, strike, t):
        sd = math.sqrt(max(self.total_var(t, strike), 1e-12))
        d1 = (math.log(spot / strike) + sd * sd / 2) / sd
        return spot * _ncdf(d1) - strike * _ncdf(d1 - sd)

    def prob_above(self, spot, strike, hours):
        t = max(hours, 0.01) / YEAR_H
        h = strike * 0.003
        p = (self._call(spot, strike - h, t) - self._call(spot, strike + h, t)) / (2 * h)
        return min(max(p, 0.0), 1.0)


def chain(asset, now=None, fetch=get_json):
    """Liefert die Chain eines Coins oder None (z. B. DOGE, oder Deribit nicht erreichbar)."""
    asset = asset.upper()
    if asset not in SOURCES:
        return None
    cur, prefix = SOURCES[asset]
    now = now or datetime.now(timezone.utc)
    hit = _cache.get(cur)
    if not hit or time.time() - hit[0] > CACHE_SEC:
        try:
            data = fetch(API, params={"currency": cur, "kind": "option"})
            rows = data.get("result") or []
            _cache[cur] = (time.time(), rows)
        except (HttpError, AttributeError) as e:
            status[asset] = f"Deribit nicht erreichbar: {str(e)[:120]}"
            if not hit:
                return None
        hit = _cache.get(cur)
    c = Chain(hit[1], prefix, now)
    if not c.ok:
        status[asset] = "zu wenige Optionsdaten"
        return None
    status[asset] = f"OK – {len(c.expiries)} Verfallstage"
    return c
