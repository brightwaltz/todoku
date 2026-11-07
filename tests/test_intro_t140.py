"""入口の紹介の動画（T-140）。置き場所・属性・素材の形を、bwc-intro の仕様（research-intro-video-1106.md の
「テストで確かめること」）のとおりに見る。素材は同じ名前で差し替わることがあるので、ファイルの名前は index.html の
src・poster から読み、大きさは上限だけを見る。"""
import os
import re
import struct
from pathlib import Path

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import app  # noqa: E402

WEB = Path(__file__).resolve().parents[1] / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
client = TestClient(app)


def _video_tag() -> str:
    m = re.search(r'<video id="intro-video"[^>]*>', HTML)
    assert m, "紹介の動画の video が無い"
    return m.group(0)


def _src() -> str:
    m = re.search(r'<section id="intro" class="card intro"[^>]*>.*?<source src="/static/([^"]+)" type="video/mp4">', HTML, re.S)
    assert m
    return m.group(1)


def _boxes(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        head = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            head = 16
        elif size == 0:
            size = end - i
        yield kind.decode("latin-1"), i, i + head, i + size
        i += size


def test_intro_is_its_own_card_between_the_input_card_and_about():
    # T-140 では絵と見本の間に置き、T-182（RSI-002）で絵の下・合言葉の欄の上に移った。T-197 で入力の札から外し、
    # 入力の札のすぐ下・「このサイトについて」の上の独立した札にした
    i = HTML.index('id="intro"')
    input_end = HTML.index("</section>", HTML.index('id="input"'))
    assert HTML.index('id="gate"') < input_end < i < HTML.index('id="about"')
    assert '<section id="intro" class="card intro"' in HTML
    # 相談を書く欄・相談する・見本・絵から選ぶの順（test_app の順の検査と同じ並び）
    order = [HTML.index(k) for k in ('id="input"', 'id="text"', 'id="send"', 'id="samples"', 'id="sample-list"', 'id="events"', 'id="gate"')]
    assert order == sorted(order)


def test_intro_video_is_click_to_play_with_a_poster_and_no_autoplay():
    tag = _video_tag()
    # T-197：ブラウザの再生の帯（controls）は焼き込みの字幕に重なるので外し、自前の操作を画の外に置く
    for want in (" playsinline", 'preload="none"', 'poster="/static/', 'width="720"', 'height="720"'):
        assert want in tag, want
    for ng in ("autoplay", "loop", "style=", " controls"):
        assert ng not in tag, ng
    assert "音声なし" in re.search(r'aria-label="([^"]+)"', tag).group(1)
    desc_id = re.search(r'aria-describedby="([^"]+)"', tag).group(1)
    m = re.search(rf'<ol class="intro-steps" id="{desc_id}"[^>]*>(.*?)</ol>', HTML, re.S)
    assert m and re.sub(r"<[^>]+>", "", m.group(1)).strip()


def test_intro_adds_only_the_approved_words():
    """T-140 では見える文を足さなかった（柴田の条件「文と言葉を増やさない」）。T-197 で柴田が承認した文だけを見える字にする：
    題・「音声なし」・4 つの手順（前の読み上げだけの #intro-desc）・操作の名前（もう一度・時間）。ほかの文は足さない。"""
    card = re.search(r'<section id="intro" class="card intro"[^>]*>(.*?)</section>', HTML, re.S).group(1)
    words = [w.strip() for w in re.sub(r"<[^>]+>", "\n", card).split("\n") if w.strip()]
    assert words == ["21秒でわかる、とどくの使い方", "音声なし", "0:00 / 0:21", "もう一度",
                     "困りごとを一度書く", "尋ねていなかった制度が見えてくる", "判定の理由と出典", "申請先と持ち物を1枚に"], words


def test_intro_files_are_small_faststart_and_silent():
    data = (WEB / _src()).read_bytes()
    assert len(data) <= 1_200_000, len(data)
    top = [(k, s, b, e) for k, s, b, e in _boxes(data)]
    kinds = [k for k, *_ in top]
    assert "moov" in kinds and "mdat" in kinds
    assert kinds.index("moov") < kinds.index("mdat"), kinds  # faststart（押してすぐ流れ始める）
    _, _, body, end = top[kinds.index("moov")]
    assert b"soun" not in data[body:end]  # 音の道が無い（音のボタンを置かない前提）
    poster = re.search(r'poster="/static/([^"]+)"', _video_tag()).group(1)
    assert (WEB / poster).stat().st_size <= 300_000


def test_intro_video_is_served_with_range_and_revalidation():
    path = "/static/" + _src()
    r = client.get(path, headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert r.headers["content-range"].startswith("bytes 0-99/")
    assert r.headers["content-type"] == "video/mp4"
    full = client.get(path)
    assert full.status_code == 200 and full.headers["cache-control"] == "no-cache"
    again = client.get(path, headers={"If-None-Match": full.headers["etag"]})
    assert again.status_code == 304
    poster = client.get(re.search(r'poster="([^"]+)"', _video_tag()).group(1))
    assert poster.status_code == 200 and poster.headers["content-type"] == "image/jpeg"


def test_intro_is_not_printed_and_has_room_before_the_poster_arrives():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.intro-player video \{([^}]+)\}", css).group(1)  # T-197：480px → 札の幅の中央で 560px まで
    assert "aspect-ratio: 1 / 1" in rule and "width: 100%" in rule and "max-width: 560px" in rule and "margin: 12px auto 0" in rule
    prints = re.findall(r"@media print \{(.*?)\n\}", css, re.S)
    assert any(".intro { display: none; }" in p for p in prints)
