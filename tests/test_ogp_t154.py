"""T-154：リンクを LINE・Slack・Discord などに貼ったときの見出し（OGP）。

文と言葉は増やさない（柴田の条件）。og:title と og:description は、頁の <title> と meta の description を
そのまま写す。片方だけ直して食い違わないよう、ここで同じであることを確かめる。
"""

import os
import re

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import app  # noqa: E402

client = TestClient(app)


def _meta(html: str, attr: str, key: str) -> str:
    m = re.search(rf'<meta {attr}="{re.escape(key)}" content="([^"]*)">', html)
    assert m, f"{key} が無い"
    return m.group(1)


@pytest.mark.parametrize("path", ["/", "/city/132098", "/city/271004"])
def test_ogp_copies_title_and_description(path):
    html = client.get(path).text
    title = re.search(r"<title>([^<]*)</title>", html).group(1)
    assert _meta(html, "property", "og:title") == title
    assert _meta(html, "property", "og:description") == _meta(html, "name", "description")
    assert _meta(html, "property", "og:site_name") == "とどく"
    assert _meta(html, "property", "og:type") == "website"
    assert _meta(html, "property", "og:locale") == "ja_JP"
    assert _meta(html, "name", "twitter:card") == "summary_large_image"  # T-155 で絵を付けて大きい札に
    assert html.index("og:title") < html.index("</head>")
