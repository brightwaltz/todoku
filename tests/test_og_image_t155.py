"""T-155：リンクを貼ったときの絵（og:image）。

og:image は絶対 URL が要る。本番は 2 つの名前（todoku.fly.dev・todoku-machida.fly.dev）で同じ版が動くので、
要求の host がそのどちらかならその host を指し、ほかの host（手元・試験・偽の Host ヘッダ）なら todoku.fly.dev を指す。
Host ヘッダの値をそのまま頁に書かない（よその URL を指す og:image を作らせない）。
"""

import os
import re
import struct

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402

client = TestClient(app_mod.app)
PAGES = ["/", "/city/132098", "/city/271004"]


def _meta(html: str, attr: str, key: str) -> str:
    m = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', html)
    assert m, f"{key} が無い"
    return m.group(1)


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("host", ["todoku.fly.dev", "todoku-machida.fly.dev"])
def test_og_image_points_to_the_same_production_host(path, host):
    html = client.get(path, headers={"host": host}).text
    assert _meta(html, "property", "og:image") == f"https://{host}/static/og.png"
    assert _meta(html, "property", "og:image:width") == "1200"
    assert _meta(html, "property", "og:image:height") == "630"
    assert _meta(html, "name", "twitter:card") == "summary_large_image"


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("host", ["testserver", "127.0.0.1:8000", "evil.example", "todoku.fly.dev.evil.example"])
def test_og_image_ignores_other_hosts(path, host):
    html = client.get(path, headers={"host": host}).text
    assert _meta(html, "property", "og:image") == "https://todoku.fly.dev/static/og.png"
    assert "evil" not in html


def test_og_image_file_is_1200x630_png_and_small():
    r = client.get("/static/og.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    w, h = struct.unpack(">II", r.content[16:24])
    assert (w, h) == (1200, 630)
    assert len(r.content) <= 300 * 1024


def test_index_head_still_matches_get_with_og_image():
    """「/」の HEAD と GET のヘッダは同じ（T-056）。og:image を書き込むようにしても長さがずれない。"""
    g, h = client.get("/"), client.head("/")
    assert h.status_code == 200 and h.content == b""
    for k in ["content-type", "content-length", "content-security-policy", "cache-control"]:
        assert h.headers.get(k) == g.headers.get(k), k
