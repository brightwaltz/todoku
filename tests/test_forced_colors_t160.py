"""T-160：Windows のハイコントラスト（forced-colors: active）で、背景色と影だけに頼る見た目が消えないこと。

forced-colors では background と box-shadow が消え、文字と枠は系の色になる。
- 「相談する」（.primary）は border: none なので、枠を足さないとボタンに見えない
- 選んだ場所の札は背景色で見せているので、系の「選んだ」色（Highlight）で塗る
- 押した数の図の帯（.seg）と、図の凡例の色の印は背景色だけで描くので、色を残す（forced-color-adjust: none）
ふつうの画面の規則は変えない（forced-colors の中だけに書く）。
"""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
CSS = (WEB / "style.css").read_text(encoding="utf-8")


def forced_block():
    m = re.search(r"@media \(forced-colors: active\) \{(.*?)\n\}", CSS, re.S)
    assert m, "forced-colors の規則が無い"
    return m.group(1)


def rule(block, selector):
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", block)
    assert m, f"{selector} の規則が無い"
    return m.group(1)


def test_primary_button_gets_a_border_in_forced_colors():
    base = rule(CSS, ".primary")
    assert "border: none" in base  # ふつうの画面は枠なし（だから forced では字だけになる）
    assert "ButtonText" in rule(forced_block(), ".primary")


def test_selected_place_tab_uses_system_highlight():
    base = rule(CSS, '.place-tab[aria-pressed="true"]')
    assert "background" in base and "✓" not in base  # 選んだ印は背景色だけ
    body = rule(forced_block(), '.place-tab[aria-pressed="true"]')
    assert "Highlight" in body and "HighlightText" in body
    assert "forced-color-adjust: none" in body


def test_colour_coded_marks_keep_their_colours():
    block = forced_block()
    m = re.search(r"([^{}]*)\{\s*forced-color-adjust: none;\s*\}", block)
    assert m
    sels = {s.strip() for s in m.group(1).split(",")}
    assert {".seg", ".effort-swatch", ".pmap-swatch", ".map-swatch"} <= sels
    # 色を残す印は、どれも背景色で描かれている
    for sel in (".effort-swatch.eligible", ".pmap-swatch.eligible", ".map-swatch.tier-0"):
        assert re.search(re.escape(sel) + r"[^{]*\{[^}]*background", CSS), sel


def test_forced_block_only_touches_listed_selectors():
    # forced-colors の中に、ふつうの画面の規則を動かすものを紛れ込ませない（ここに足すときは検査も足す）
    sels = re.findall(r"^\s*([^{}\n]+?)\s*\{", forced_block(), re.M)
    assert sels == ['.primary', '.primary:disabled', '.place-tab[aria-pressed="true"]',
                    '.seg, .effort-swatch, .pmap-swatch, .map-swatch']
