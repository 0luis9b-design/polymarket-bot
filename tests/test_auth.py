import base64
import importlib


def _load(monkeypatch, tmp_path, user, pw):
    monkeypatch.setenv("AUTOSTART", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DASHBOARD_USER", user)
    monkeypatch.setenv("DASHBOARD_PASSWORD", pw)
    from polybot import config
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)
    return app_module.app.test_client()


def _hdr(u, p):
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()}


def test_auth_required(monkeypatch, tmp_path):
    c = _load(monkeypatch, tmp_path, "luis", "geheim")
    r = c.get("/")
    assert r.status_code == 401 and "Basic" in r.headers["WWW-Authenticate"]
    assert c.get("/api/state").status_code == 401
    assert c.get("/", headers=_hdr("luis", "falsch")).status_code == 401
    assert c.get("/", headers=_hdr("Luis", "geheim")).status_code == 401
    assert c.get("/", headers=_hdr("luis", "geheim")).status_code == 200
    assert c.get("/api/state", headers=_hdr("luis", "geheim")).status_code == 200
    assert c.get("/health").status_code == 200  # Railway-Prüfung bleibt frei


def test_no_credentials_means_open(monkeypatch, tmp_path):
    c = _load(monkeypatch, tmp_path, "", "")
    assert c.get("/").status_code == 200
