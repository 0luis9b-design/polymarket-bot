"""Tests für 2.5: Befunde aus dem Claude-Review vom 18.11.2026 (Betrieb, Lernsystem, Wächter, Bericht)."""
import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from polybot import base_model, config, prices, reports
from polybot.agents import learning as L
from polybot.agents import strategy as S
from polybot.agents.probability import ProbabilityAgent
from polybot.http import HttpError

from tests.test_agents import NOW, _fill, _store


# ================================================================ Binance: zuerst der Datenspiegel
def test_binance_vision_is_asked_first(monkeypatch):
    """Live 18.11.: api.binance.com lehnt den US-Server immer ab – jeder zweite Abruf war ein Fehler."""
    asked = []

    def fake(url, params=None, **kw):
        asked.append(url.split("/")[2])
        return [[0, 0, 0, 0, str(100 + i)] for i in range(720)]
    monkeypatch.setattr(prices, "get_json", fake)
    prices._cache.clear()
    closes, name = prices.hourly_closes("BTC")
    assert name == "Binance-Vision" and asked == ["data-api.binance.vision"] and len(closes) == 720


def test_binance_main_host_still_used_when_mirror_fails(monkeypatch):
    def fake(url, params=None, **kw):
        if "binance.vision" in url:
            raise HttpError("HTTP 503", 503)
        return [[0, 0, 0, 0, "1"] for _ in range(200)]
    monkeypatch.setattr(prices, "get_json", fake)
    prices._cache.clear()
    assert prices.hourly_closes("ETH")[1] == "Binance"


# ================================================================ Datensatz: Polymarket-Fehler 500 bricht den Agenten nicht ab
def _raw(i, end):
    return {"id": f"r{i}", "question": f"Will thing {i} happen?", "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]',
            "closed": True, "umaResolutionStatus": "resolved", "volumeNum": 9000, "endDate": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "clobTokenIds": '["tokY","tokN"]'}


def test_strategy_collect_survives_gamma_500_and_keeps_first_page(tmp_path):
    st = _store(tmp_path)
    end = NOW - timedelta(days=1)

    def fetch_markets(params):
        if params["offset"] == 0:
            return [_raw(i, end) for i in range(100)]
        raise HttpError("HTTP 500 bei https://gamma-api.polymarket.com/markets: internal server error", 500)
    sa = S.StrategyAgent(st, fetch_markets=fetch_markets,
                         fetch_history=lambda t, a, b: [(int(end.timestamp()) - 3600 * h, 0.4) for h in range(100, 0, -1)])
    out = sa.collect(NOW, time.time() + 60, limit=150)
    assert out["neu"] == 100 and "Polymarket-Fehler 500 bei Seite 2" in out["abgebrochen"]
    assert st.scalar("SELECT COUNT(*) FROM resolved") == 100                         # Seite 1 bleibt gespeichert
    assert st.kv_json("hist:gamma_fail", {}) == {"crypto|neu|1": 1}                  # Abfrage wird zur Ursachensuche gezählt
    res = sa.run(NOW, time.time() + 60)                                             # ganzer Lauf: kein Abbruch, Backtest läuft
    assert "abgebrochen" in res and "backtest" in res


def test_strategy_collect_still_raises_on_client_errors(tmp_path):
    st = _store(tmp_path)

    def fetch_markets(params):
        raise HttpError("HTTP 422 bei https://gamma-api.polymarket.com/markets", 422)
    sa = S.StrategyAgent(st, fetch_markets=fetch_markets, fetch_history=lambda *a: [])
    with pytest.raises(HttpError):                                                  # eigener Fehler in der Abfrage: sichtbar bleiben
        sa.collect(NOW, time.time() + 60)


# ================================================================ Wächter: schon bei „wahrscheinlich schlechter“
class _KV:
    def __init__(self, summary):
        self.summary = summary

    def kv_json(self, key, default=None):
        return self.summary if key == "learn:summary" else default


def _base_row(cat, z, brier, market, n=243, clusters=85):
    return {"model": base_model.MODEL_ID, "category": cat, "z": z, "brier": brier, "brier_market": market, "n": n, "clusters": clusters}


def test_guard_blocks_sport_at_the_18_11_values():
    """Bericht 18.11.: Sport-Basisraten Brier 0.139 gegen 0.126, z = +1.64 (knapp darunter) – bis 2.4 nicht gesperrt."""
    br = base_model.BaseRates(_KV({"modelle": [_base_row("sports", 1.639, 0.139, 0.126),
                                               _base_row("weather", -2.12, 0.077, 0.079, n=132, clusters=31),
                                               _base_row("culture", 0.8, 0.061, 0.060, n=68, clusters=14),
                                               _base_row("esports", 3.23, 0.290, 0.257, n=50, clusters=23)]}))
    blocked = br.guard()
    assert set(blocked) == {"sports", "esports"}
    assert "wahrscheinlich schlechter" in blocked["sports"] and "z = +1.64" in blocked["sports"]
    assert "gesichert schlechter" in blocked["esports"]


def test_guard_threshold_is_a_setting(monkeypatch):
    monkeypatch.setattr(config, "BASE_GUARD_Z", 1.64)
    br = base_model.BaseRates(_KV({"modelle": [_base_row("sports", 1.639, 0.139, 0.126)]}))
    assert br.guard() == {}
    monkeypatch.setattr(config, "BASE_GUARD_Z", 1.0)
    assert set(br.guard()) == {"sports"}
    # wenig Daten oder besser als der Markt: nie sperren
    br = base_model.BaseRates(_KV({"modelle": [_base_row("sports", 1.5, 0.139, 0.126, n=10), _base_row("tech", 1.2, 0.002, 0.004, n=40)]}))
    assert br.guard() == {}


# ================================================================ Lernsystem: keine Dauerschleife gleicher Vorschläge
def _market_beats_model(st, pa):
    _fill(st, pa, 400, lambda i: 0.80, lambda i: 0.26, lambda i: 1.0 if i % 4 == 0 else 0.0)


def test_approved_mix_is_not_proposed_again(tmp_path):
    """Live 16.–18.11.: dieselbe Wetter-Mischung bestand nach jeder Freigabe sofort wieder und kam erneut."""
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    _market_beats_model(st, pa)
    la.run(NOW + timedelta(days=30), time.time() + 60)
    prop = st.q1("SELECT * FROM proposals")
    assert prop["kind"] == "mischung" and json.loads(prop["detail"])["w"] == 0.0
    assert la.decide(prop["id"], True, "ok")[0] and pa.reg["weather"]["mix_w"] == 0.0
    for h in (1, 2, 3):
        la.run(NOW + timedelta(days=30, hours=h), time.time() + 60)
    assert st.scalar("SELECT COUNT(*) FROM proposals") == 1                         # bis 2.4: jede Stunde ein neuer Vorschlag
    exp = {e["challenger"]: e for e in st.q("SELECT * FROM experiments")}
    # Maßstab ist jetzt die freigegebene Mischung; der alte Test „Marktpreis gegen reines Modell“ ist weg (sonst stünde er
    # weiter als „bestanden“ im Bericht), und „zurück zum reinen Modell“ wird mitgeprüft
    assert "Marktpreis" not in exp and "Reines Modell (ohne Mischung)" in exp
    assert {e["champion"] for e in exp.values()} == {"weather.ens@2 (0 % Modell)"}


def test_rejected_proposal_waits_a_week(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    _market_beats_model(st, pa)
    t0 = NOW + timedelta(days=30)
    la.run(t0, time.time() + 60)
    prop = st.q1("SELECT * FROM proposals")
    la.decide(prop["id"], False, "erst abwarten")
    st.x("UPDATE proposals SET decided_at=? WHERE id=?", (t0.isoformat(timespec="seconds"), prop["id"]))
    la.run(t0 + timedelta(hours=1), time.time() + 60)
    la.run(t0 + timedelta(days=6), time.time() + 60)
    assert st.scalar("SELECT COUNT(*) FROM proposals") == 1
    la.run(t0 + timedelta(days=8), time.time() + 60)
    assert st.scalar("SELECT COUNT(*) FROM proposals WHERE status='offen'") == 1    # nach einer Woche darf er wieder fragen


def test_only_the_traded_variant_carries_the_star(tmp_path):
    st = _store(tmp_path)
    pa = ProbabilityAgent(st)
    la = L.LearningAgent(st, pa)
    pa.activate("weather", "weather.ens", "2", 0.5, "Test")
    pa.reload()
    out = lambda i: 1.0 if i % 4 == 0 else 0.0                                       # noqa: E731
    _fill(st, pa, 60, lambda i: 0.30, lambda i: 0.26, out, extra={"weather.ens.mix@2w50": lambda i: 0.28, "weather.ens.mix@2w25": lambda i: 0.27})
    la.run(NOW + timedelta(days=10), time.time() + 60)
    ms = {m["model"]: m for m in la.summary()["modelle"]}
    assert ms["weather.ens.mix@2w50"]["active"] and not ms["weather.ens.mix@2w25"]["active"]
    assert not ms["weather.ens@2"]["active"] and ms["weather.ens@2"]["grundlage"]
    assert L.traded_key({"model": "weather.live", "version": "1", "mix_w": 0.25}) == ("weather.live.mix@1w25", "weather.live@1")
    assert L.traded_key({"model": "crypto.options", "version": "1", "mix_w": None}) == ("crypto.options@1", "crypto.options@1")


# ================================================================ Bericht: Wetten im Rückblick
def _pos(st, cat, lane, status, own, price, pnl, edge=None, closed=NOW):
    st.insert_position({"account": "haupt", "kind": "model", "market_id": f"m{time.time_ns()}", "category": cat, "lane": lane,
                        "question": "Q", "side": 1, "side_name": "No", "shares": 10, "price": price, "fee": 0, "cost": price * 10,
                        "own_p": own, "market_p": price, "edge": own - price if edge is None else edge, "status": status, "pnl": pnl,
                        "opened_at": (closed - timedelta(days=1)).isoformat(), "closed_at": closed.isoformat()})


def test_bets_review_shows_overconfidence_on_traded_markets(tmp_path):
    st = _store(tmp_path)
    for i in range(40):                     # erwartet 70 %, Preis 50 %, gewonnen nur 40 % -> der Bot überschätzt seine Wetten
        won = i % 10 < 4
        _pos(st, "weather", "model", "won" if won else "lost", 0.70, 0.50, 5.0 if won else -5.0, edge=0.22)
    _pos(st, "weather", "model", "sold", 0.70, 0.50, -2.0)
    _pos(st, "sports", "base", "won", 0.60, 0.55, 4.0)
    _pos(st, "crypto", "arb", "won", 0.5, 0.5, 1.0)                                  # Arbitrage zählt nicht
    lines, recs = reports.bets_review(st, (NOW - timedelta(days=3)).isoformat())
    md = "\n".join(lines)
    assert "| Wetter | eigenes Modell | 40 | 70 % | 50 % | 40 % | -40 $ | 1 / -2 $ | -42 $ |" in md
    assert "Sport" not in md.split("Dieselben Wetten")[0].split("|---|")[1]          # 1 Wette ist zu wenig für eine Zeile
    assert "| Wetter | 20 Pkt und mehr | 40 | 70 % | 50 % | 40 % | -40 $ |" in md
    assert len(recs) == 1 and "seltener als beim Kauf erwartet" in recs[0] and "n = 40" in recs[0]


def test_bets_review_without_bets(tmp_path):
    lines, recs = reports.bets_review(_store(tmp_path), NOW.isoformat())
    assert lines[0].endswith("Keine Daten.") and recs == []
