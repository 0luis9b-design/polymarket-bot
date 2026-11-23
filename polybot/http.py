"""HTTP-Helfer: jeder Abruf läuft über die Quellen-Verwaltung (Budget, Statistik) und wird bei Aussetzern wiederholt.

Fehlertexte werden geschwärzt (sources.redact), damit nie ein Schlüssel oder Token im Log landet.
"""
import time

import requests

from . import config, sources

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "polymarket-research-bot/2.0", "Accept": "application/json"})
MAX_TEXT_BYTES = 3_000_000


class HttpError(Exception):
    def __init__(self, msg, status=None, headers=None, blocked=False):
        super().__init__(sources.redact(msg))
        self.status = status
        self.headers = headers or {}
        self.blocked = blocked


def _request(url, params, retries, parse, headers=None, timeout=None):
    sid = sources.source_for(url)
    last = None
    for attempt in range(retries + 1):
        try:
            sources.before(sid)
        except sources.Blocked as b:
            raise HttpError(str(b), blocked=True) from None
        t0 = time.time()
        try:
            # Schlüssel nur im Kopf der Anfrage, nie in der Adresse (sonst stünde er in Logs)
            r = SESSION.get(url, params=params, timeout=timeout or config.HTTP_TIMEOUT, headers=headers)
            ms = (time.time() - t0) * 1000
            if r.status_code >= 400:
                sources.after(sid, False, r.status_code, ms, f"HTTP {r.status_code}")
                last = HttpError(f"HTTP {r.status_code} bei {url}: {r.text[:200]}", r.status_code, r.headers)
                if r.status_code == 429 or (400 <= r.status_code < 500):
                    break  # 429: Quelle ist jetzt pausiert; 4xx: Wiederholen hilft nicht
            else:
                data = parse(r)
                sources.after(sid, True, r.status_code, ms)
                return data, r.headers
        except HttpError as e:
            last = e
        except (requests.RequestException, ValueError) as e:
            sources.after(sid, False, None, (time.time() - t0) * 1000, type(e).__name__)
            last = HttpError(f"{type(e).__name__} bei {url.split('?')[0]}")
        if attempt < retries:
            time.sleep(1 + attempt * 2)
    raise last


def get_json(url, params=None, retries=2, want_headers=False, headers=None):
    data, hdrs = _request(url, params, retries, lambda r: r.json(), headers=headers)
    return (data, hdrs) if want_headers else data


def get_text(url, params=None, retries=1, timeout=None):
    """Für RSS und andere Textquellen; Größe begrenzt."""
    def parse(r):
        if len(r.content) > MAX_TEXT_BYTES:
            raise ValueError("Antwort zu groß")
        return r.content
    data, _ = _request(url, params, retries, parse, timeout=timeout)
    return data


def post_json(url, payload):
    """Einzige schreibende Stelle des Systems: Telegram-Nachrichten."""
    sid = sources.source_for(url)
    try:
        sources.before(sid)
    except sources.Blocked as b:
        raise HttpError(str(b), blocked=True) from None
    t0 = time.time()
    try:
        r = SESSION.post(url, json=payload, timeout=config.HTTP_TIMEOUT)
    except requests.RequestException as e:
        sources.after(sid, False, None, (time.time() - t0) * 1000, type(e).__name__)
        raise HttpError(f"{type(e).__name__} bei Telegram") from None
    ok = r.status_code == 200
    sources.after(sid, ok, r.status_code, (time.time() - t0) * 1000, "" if ok else f"HTTP {r.status_code}")
    if not ok:
        raise HttpError(f"HTTP {r.status_code}: {r.text[:150]}", r.status_code)
    return r.json()
