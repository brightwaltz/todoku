"""/city/ の頁も HEAD に答える（T-159）。

リンクを LINE などに貼る頁（T-154・T-155）なのに、/city/ だけ @app.get で HEAD が 405 だった。
「/」と /api/health と同じく（T-056）、HEAD には本文を送らず、縮めない GET と同じヘッダで答える。
"""
from fastapi.testclient import TestClient

from todoku.app import app

client = TestClient(app)
PLAIN = {"accept-encoding": "identity"}
BROWSER = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}


def test_city_answers_head_like_get():
    for code in ["132098", "141003", "271004", "011002"]:  # 町田市・横浜市・大阪市・札幌市
        path = f"/city/{code}"
        g, h = client.get(path, headers=PLAIN), client.head(path, headers=PLAIN)
        assert g.status_code == 200 and h.status_code == 200, path
        assert h.content == b"", path
        for k in ["content-type", "content-length", "content-security-policy", "x-frame-options", "cache-control"]:
            assert h.headers.get(k) == g.headers.get(k), (path, k)
        assert int(h.headers["content-length"]) == len(g.content), path


def test_city_head_follows_the_host_for_og_image():
    # og:image の host を書き換えた後の長さを返す（本番の 2 つの名前で長さが変わる）
    for host in ["todoku.fly.dev", "todoku-machida.fly.dev", "example.com"]:
        hd = PLAIN | {"host": host}
        g, h = client.get("/city/132098", headers=hd), client.head("/city/132098", headers=hd)
        assert h.headers["content-length"] == str(len(g.content)), host


def test_unknown_city_head_is_404_not_405():
    assert client.head("/city/99999").status_code == 404
    assert client.head("/city/99999", headers=BROWSER).status_code == 404


def test_city_get_is_unchanged():
    r = client.get("/city/132098", headers=BROWSER | {"accept-encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers.get("content-encoding") == "gzip"  # GET は今までどおり縮める（T-061）
    assert "町田市" in r.text
    assert client.post("/city/132098").status_code == 405  # GET・HEAD のほかは今までどおり断る
