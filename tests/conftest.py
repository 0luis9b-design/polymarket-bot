"""Gemeinsame Vorbereitung für alle Tests."""
import os

os.environ.setdefault("AUTOSTART", "0")

import pytest  # noqa: E402

from polybot import config, metar, notify, settings, sources, weather_model  # noqa: E402


@pytest.fixture(autouse=True)
def _saubere_umgebung(monkeypatch):
    """Jeder Test startet mit leeren Zählern und den Standard-Einstellungen.

    Die Handels-Tests arbeiten absichtlich mit krass falsch bepreisten Märkten, damit sicher gekauft wird.
    Die Plausibilitäts-Grenze (MAX_DIVERGENCE) würde genau diese Märkte sperren – deshalb steht sie hier weit offen;
    ihre eigenen Tests (test_agents.py) setzen sie ausdrücklich. Ebenso die Mindest-Gewinnchance (MIN_WIN_P).
    """
    sources.reset()
    notify.reset()
    weather_model._blocked.clear()
    metar.reset()
    saved = {k: getattr(config, k) for k in settings.SPEC}
    monkeypatch.setattr(config, "MAX_DIVERGENCE", 0.99)
    # Ältere Handels-Tests kaufen auch Außenseiter-Seiten; die Mindest-Gewinnchance (Standard 50 %) prüfen
    # tests/test_games.py und tests/test_paper.py ausdrücklich.
    monkeypatch.setattr(config, "MIN_WIN_P", 0.0)
    # „Alles tippen“ (2.10) würde in den älteren Tests zusätzlich kaufen; eigene Tests in tests/test_all_tips.py
    monkeypatch.setattr(config, "ALL_TIPS", False)
    monkeypatch.setattr(config, "FORM_TIPS", False)          # eigene Tests in tests/test_form_model.py
    monkeypatch.setattr(config, "API_FOOTBALL_KEY", "")
    yield
    for k, v in saved.items():
        setattr(config, k, v)
