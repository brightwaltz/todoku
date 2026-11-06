"""T-182（RSI-002）：見本の並びを「相談する」のすぐ下に置く。

見本は合言葉なしで最後まで動く。家の絵（T-124）と紹介の動画（T-140）の下にあったので、390・1280 とも最初の 1 画面に出なかった。
文や言葉は足さず、置き場所だけを変える。1 画面に入ることは $OPS/tools/samples_t182.py で測る（Chromium・WebKit の 390・1280）。
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def _visible(chunk: str) -> str:
    chunk = re.sub(r"<!--.*?-->", "", chunk, flags=re.S)
    return re.sub(r"<[^>]+>", "", chunk).strip()


def test_samples_follow_the_send_button_and_the_closed_note():
    send, closed, samples, events = (HTML.index(f'id="{k}"') for k in ("send", "closed", "samples", "events"))
    assert send < closed < samples < events
    # 受付終了の札と見本の間に、見える要素を挟まない（コメントだけ）
    between = HTML[HTML.index("</p>", closed) + 4 : HTML.rindex("<div", 0, samples)]
    assert _visible(between) == "", between


def test_samples_block_keeps_its_words():
    block = HTML[HTML.rindex("<div", 0, HTML.index('id="samples"')) :].split("</div>\n    </div>")[0]
    assert _visible(block) == "見本で試す（合言葉なしで動きます）"
    assert 'class="examples" id="sample-list"' in block
    # 見本より上の塗りのボタンは「相談する」の 1 つだけ（T-044）
    assert HTML[: HTML.index('id="samples"')].count('class="primary"') == 1
