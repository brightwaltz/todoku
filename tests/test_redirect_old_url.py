"""前の URL を新しい URL へ転送する（TODOKU_REDIRECT_TO、11/7）。"""
import importlib

from fastapi.testclient import TestClient


def _client(monkeypatch, value):
    monkeypatch.setenv("TODOKU_REDIRECT_TO", value)
    import todoku.app as app_mod
    importlib.reload(app_mod)
    return TestClient(app_mod.app), app_mod


def test_redirects_everything_but_health(monkeypatch):
    c, mod = _client(monkeypatch, "https://todoku.fly.dev/")
    try:
        r = c.get("/city/271004?x=1", follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"] == "https://todoku.fly.dev/city/271004?x=1"
        assert c.post("/api/sample", json={"id": "taiin-haha"}, follow_redirects=False).status_code == 308
        assert c.get("/api/health").status_code == 200
    finally:
        monkeypatch.delenv("TODOKU_REDIRECT_TO")
        importlib.reload(mod)


def test_no_redirect_without_setting(monkeypatch):
    c, mod = _client(monkeypatch, "")
    assert c.get("/", follow_redirects=False).status_code == 200
