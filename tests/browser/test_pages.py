"""Alle Seiten der Zentrale in einem echten Browser (Desktop- und Handybreite).

Geprüft: Die Seite lädt ohne Skriptfehler, zeigt Inhalt (keine Fehlermeldung „konnte nicht geladen werden“)
und ist nicht breiter als der Bildschirm. Läuft nur mit BROWSER_TESTS=1 (in der GitHub-Prüfung), weil dafür
Playwright mit Chromium installiert sein muss."""
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

if os.environ.get("BROWSER_TESTS") != "1":
    pytest.skip("Browser-Tests nur mit BROWSER_TESTS=1", allow_module_level=True)
sync_api = pytest.importorskip("playwright.sync_api")

PAGES = ["", "maerkte", "markt/w1", "prognosen", "agenten", "lernen", "strategie", "performance", "daten", "system", "berichte",
         "einstellungen", "paper", "paper?cat=sports", "paper?account=haupt_sport", "paper?strat=haupt", "paper?strat=schwelle4&cat=politics", "paper?strat=kelly&cat=sports", "wetten", "wetten?cat=sports&sub=Fu%C3%9Fball",
         "wetten?status=verloren&sort=gv&dir=asc", "wetten?strat=schwelle4", "kalender?strat=haupt&cat=sports", "auswertung", "auswertung?strat=haupt&cat=weather", "wette/1", "wette/2", "kalender", "kalender?tag=" + time.strftime("%Y-%m-%d")]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    d = tmp_path_factory.mktemp("demo")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    # Ausgabe in eine Datei, nicht in eine Pipe: eine volle Pipe (64 KB Server-Log) würde den Server anhalten
    logf = open(d / "server.log", "wb")
    proc = subprocess.Popen([sys.executable, os.path.join(root, "tests", "browser", "serve_demo.py"), str(d), str(port)],
                            cwd=root, stdout=logf, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "/health", timeout=1)
            break
        except OSError:
            time.sleep(0.25)
    else:
        proc.kill()
        logf.close()
        raise RuntimeError("Demo-Server startet nicht: " + (d / "server.log").read_text(errors="replace")[-2000:])
    yield url
    proc.terminate()
    proc.wait(timeout=10)
    logf.close()


@pytest.mark.parametrize("size", [(1360, 900), (390, 844)], ids=["desktop", "handy"])
def test_every_page_renders_without_errors(server, size):
    with sync_api.sync_playwright() as p:
        kw = {"executable_path": os.environ["CHROME_PATH"]} if os.environ.get("CHROME_PATH") else {}
        browser = p.chromium.launch(args=["--no-sandbox"], **kw)
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("Skriptfehler: " + str(e)))
        problems = []
        for path in PAGES:
            errors.clear()
            page.goto("about:blank")
            page.goto(f"{server}/#/{path}")
            page.wait_for_selector("#main > *", timeout=10000)
            page.wait_for_timeout(300)
            text = page.inner_text("#main")
            overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            if errors:
                problems.append(f"{path}: {errors}")
            if "konnte nicht geladen werden" in text or not text.strip():
                problems.append(f"{path}: kein Inhalt / Fehlermeldung")
            if overflow > 2:
                problems.append(f"{path}: {overflow}px breiter als der Bildschirm")
        browser.close()
    assert not problems, "\n".join(problems)
