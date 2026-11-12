import importlib
import os


def test_dashboard_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSTART", "0")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from polybot import config
    importlib.reload(config)
    import app as app_module
    importlib.reload(app_module)
    c = app_module.app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Polymarket-Bot" in html and "Treffsicherheit" in html
    assert c.get("/health").status_code == 200
    assert c.get("/api/state").status_code == 200
    assert c.post("/api/scan").status_code == 403
    assert os.path.exists(tmp_path / "polybot.sqlite")
