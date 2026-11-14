"""Gemeinsame Vorbereitung für alle Tests."""
import os

os.environ.setdefault("AUTOSTART", "0")

import pytest  # noqa: E402

from polybot import config, notify, settings, sources  # noqa: E402


@pytest.fixture(autouse=True)
def _saubere_umgebung(monkeypatch):
    """Jeder Test startet mit leeren Zählern und den Standard-Einstellungen.

    Die Handels-Tests arbeiten absichtlich mit krass falsch bepreisten Märkten, damit sicher gekauft wird.
    Die Plausibilitäts-Grenze (MAX_DIVERGENCE) würde genau diese Märkte sperren – deshalb steht sie hier weit offen;
    ihre eigenen Tests (test_agents.py) setzen sie ausdrücklich.
    """
    sources.reset()
    notify.reset()
    saved = {k: getattr(config, k) for k in settings.SPEC}
    monkeypatch.setattr(config, "MAX_DIVERGENCE", 0.99)
    yield
    for k, v in saved.items():
        setattr(config, k, v)
