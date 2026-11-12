"""Kleiner HTTP-Helfer mit Wiederholung, damit ein kurzer Aussetzer nicht den ganzen Lauf stoppt."""
import time

import requests

from . import config

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "polymarket-paper-bot/1.0", "Accept": "application/json"})


class HttpError(Exception):
    def __init__(self, msg, status=None, headers=None):
        super().__init__(msg)
        self.status = status
        self.headers = headers or {}


def get_json(url, params=None, retries=2, want_headers=False):
    last = None
    for attempt in range(retries + 1):
        try:
            r = SESSION.get(url, params=params, timeout=config.HTTP_TIMEOUT)
            if r.status_code == 429 and attempt < retries:
                time.sleep(2 + attempt * 3)
                continue
            if r.status_code >= 400:
                raise HttpError(f"HTTP {r.status_code} bei {url}: {r.text[:200]}", r.status_code, r.headers)
            data = r.json()
            return (data, r.headers) if want_headers else data
        except HttpError as e:
            last = e
            if e.status and 400 <= e.status < 500 and e.status != 429:
                break
        except (requests.RequestException, ValueError) as e:
            last = HttpError(f"{type(e).__name__} bei {url}: {e}")
        if attempt < retries:
            time.sleep(1 + attempt * 2)
    raise last
