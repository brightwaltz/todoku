"""T-135：一番上の見出し「とどく 尋ねなかった制度まで」を押すと、何も持たないトップ（/）に戻る。
入口（/）と全国の見取り図の頁（/static/japan.html）の両方。見た目は今の見出しのまま（下線や色を足さない）で、
キーボードのフォーカスは見える。文は増やさない。直しは web/ だけ。
見本 3 本の問いの順と数は test_entry_t134.py の表で見比べ続ける。
"""
import re
from pathlib import Path

from test_e2e_demo import client  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HEADING = re.compile(r'<h1><a class="home" href="/">とどく <span>尋ねなかった制度まで</span></a></h1>')


def test_heading_links_to_a_clean_top_on_both_pages():
    for path in ("/", "/static/japan.html"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert len(HEADING.findall(r.text)) == 1, path
        assert len(re.findall(r"<h1\b", r.text)) == 1, path  # 見出しを 2 つにしない
    # href は / だけ。?pref= や #k= を足さない（押すとそれらを持たないトップへ戻る）
    assert 'href="/?' not in client.get("/").text


def test_heading_link_keeps_the_look_and_shows_focus():
    css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.top h1 a\.home \{([^}]*)\}", css)
    assert rule, "見出しのリンクの見た目がない"
    assert "color: inherit" in rule.group(1) and "text-decoration: none" in rule.group(1)
    focus = re.search(r"\.top h1 a\.home:focus-visible \{([^}]*)\}", css)
    assert focus and "outline:" in focus.group(1)
