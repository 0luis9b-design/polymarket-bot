"""Krypto-Kurse (Stundenkerzen) mit Ausweich-Kette.

Polymarket entscheidet die Krypto-Märkte mit Binance BTC/USDT. Binance sperrt aber
US-Server, darum gibt es Ersatzquellen (Coinbase, Kraken). Der Unterschied ist winzig.
"""
import logging
import time

from .http import HttpError, get_json

log = logging.getLogger("polybot.prices")

SYMBOLS = {
    "BTC": {"binance": "BTCUSDT", "coinbase": "BTC-USD", "kraken": "XBTUSD"},
    "ETH": {"binance": "ETHUSDT", "coinbase": "ETH-USD", "kraken": "ETHUSD"},
    "SOL": {"binance": "SOLUSDT", "coinbase": "SOL-USD", "kraken": "SOLUSD"},
    "XRP": {"binance": "XRPUSDT", "coinbase": "XRP-USD", "kraken": "XRPUSD"},
    "DOGE": {"binance": "DOGEUSDT", "coinbase": "DOGE-USD", "kraken": "XDGUSD"},
}

_cache = {}
CACHE_SEC = 240
source_status = {}  # Quelle -> letzte Meldung (für das Dashboard)


def _binance(host, sym):
    rows = get_json(f"https://{host}/api/v3/klines", params={"symbol": sym, "interval": "1h", "limit": 720})
    return [float(r[4]) for r in rows]


def _coinbase(sym):
    rows = get_json(f"https://api.exchange.coinbase.com/products/{sym}/candles", params={"granularity": 3600})
    rows = sorted(rows, key=lambda r: r[0])  # Coinbase liefert neueste zuerst
    return [float(r[4]) for r in rows]


def _kraken(sym):
    data = get_json("https://api.kraken.com/0/public/OHLC", params={"pair": sym, "interval": 60})
    if data.get("error"):
        raise HttpError(f"Kraken: {data['error']}")
    res = data["result"]
    key = next(k for k in res if k != "last")
    return [float(r[4]) for r in res[key]]


def hourly_closes(asset):
    """Liste der Stunden-Schlusskurse (älteste zuerst); letzter Wert ≈ aktueller Kurs."""
    asset = asset.upper()
    if asset not in SYMBOLS:
        raise KeyError(asset)
    hit = _cache.get(asset)
    if hit and time.time() - hit[0] < CACHE_SEC:
        return hit[1], hit[2]
    s = SYMBOLS[asset]
    chain = [
        ("Binance", lambda: _binance("api.binance.com", s["binance"])),
        ("Binance-Vision", lambda: _binance("data-api.binance.vision", s["binance"])),
        ("Coinbase", lambda: _coinbase(s["coinbase"])),
        ("Kraken", lambda: _kraken(s["kraken"])),
    ]
    errors = []
    for name, fn in chain:
        try:
            closes = fn()
            if len(closes) < 100:
                raise HttpError(f"nur {len(closes)} Kerzen")
            _cache[asset] = (time.time(), closes, name)
            source_status[asset] = f"OK über {name}"
            return closes, name
        except Exception as e:  # noqa: BLE001 - jede Quelle darf ausfallen
            errors.append(f"{name}: {e}")
    source_status[asset] = "FEHLER: " + " | ".join(errors)[:300]
    raise HttpError(f"Keine Kursquelle für {asset} erreichbar")
