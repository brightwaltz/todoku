"""T-164：ブラウザの「文字の大きさ」の設定に文字が付いてくる。

style.css の文字の大きさを px で書くと、ブラウザの設定で既定の文字を大きくした人（Chrome・Edge の「フォントサイズ：大」など）
にも同じ大きさで出る。rem（既定の 16px が 1rem）で書けば、既定のままの人には前と同じ大きさで、大きくした人には大きく出る。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


# 絵（SVG）の中の字は絵と一緒に縮むので px のまま。rem にすると、文字を大きくした人で県名が絵の枠から切れる
SVG_TEXT = (".japan-map text",)


def _screen_css():
    # 印刷の @media の中（紙の大きさは pt で書く）と、絵の中の字は除く
    css = re.sub(r"@media print \{(?:[^{}]|\{[^{}]*\})*\}", "", CSS)
    for sel in SVG_TEXT:
        css = re.sub(r"^" + re.escape(sel) + r" \{[^}]*\}", "", css, flags=re.M)
    return css


def test_svg_labels_stay_in_px():
    for sel in SVG_TEXT:
        assert re.search(r"^" + re.escape(sel) + r" \{[^}]*font-size: 16px", CSS, re.M), sel


def test_no_font_size_in_px():
    assert not re.findall(r"font-size:\s*[\d.]+px", _screen_css())
    assert not re.findall(r"\bfont:[^;]*\d+px", _screen_css())


def test_root_font_size_is_left_to_the_browser():
    # html や :root に文字の大きさを決め打ちすると、rem でもブラウザの設定が効かなくなる
    for block in re.findall(r"^(?:html|:root)\s*\{([^}]*)\}", CSS, re.M):
        assert "font-size" not in block


def test_rem_values_are_whole_pixels_at_default():
    # 既定（16px）のとき、前の px と同じ整数の大きさになる値だけを使う（見た目を変えない）
    for v in re.findall(r"font-size:\s*([\d.]+)rem", CSS):
        assert (float(v) * 16).is_integer(), v
    body = re.search(r"^body \{([^}]*)\}", CSS, re.M).group(1)
    assert "font-size: 1.125rem" in body  # 前の 18px


def test_width_breakpoints_follow_the_font_setting():
    # 幅で段組みを替える @media は em（既定で 16px）。文字を大きくした人には、狭い画面の段組みが早めに効く
    widths = re.findall(r"@media \((?:max|min)-width: ([\d.]+)(px|em)\)", CSS)
    assert widths
    for v, unit in widths:
        assert unit == "em", v
        assert (float(v) * 16).is_integer(), v  # 既定のときは前の px と同じ幅で切り替わる


def test_todo_names_can_break_inside_a_phrase():
    # 文節で折る名前（auto-phrase）も、1 文節が 1 行に入らないときは折る（320 で文字を特大にすると横にはみ出した）
    rule = " ".join(re.findall(r"^\.todo-list a \{([^}]*)\}", CSS, re.M))
    assert "word-break: auto-phrase" in rule and "overflow-wrap: anywhere" in rule  # inline-flex の中なので anywhere
