"""T-171：JavaScript を切ったブラウザで、入口が黙って動かないのを避ける。

JS が無いと、相談の欄も「相談する」も絵も見本も動かない（絵・見本・選ぶ欄は hidden のまま出ない）。
- 入口の見出しのすぐ下に、noscript で理由を 1 文だけ出す（JS が動くときは出ないので、ふつうの画面の文は増えない）
- 押しても何も起きない欄と「相談する」は、@media (scripting: none) で隠す
- CSP は style-src 'self' なので、noscript の中に style 要素は置かない（効かない）
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")


def input_section():
    m = re.search(r'<section id="input" class="card">(.*?)</section>', HTML, re.S)
    assert m, "入口の欄が無い"
    return m.group(1)


def test_noscript_note_sits_right_under_the_heading_before_the_textarea():
    sec = input_section()
    h2, nos, ta = sec.find("<h2>"), sec.find("<noscript>"), sec.find('<textarea id="text"')
    assert -1 < h2 < nos < ta, "noscript は見出しの下・相談の欄の上に置く"
    body = re.search(r"<noscript>(.*?)</noscript>", sec, re.S).group(1)
    assert 'class="noscript"' in body and "JavaScript" in body
    assert "<style" not in body  # CSP の style-src 'self' で効かない
    phrases = re.findall(r"<span>([^<]*)</span>", body)
    assert len(phrases) >= 2, "文は文節のかたまりに分けて、語の途中で折らない"
    assert "".join(phrases) == "この窓口は JavaScript で動きます。ブラウザの設定で JavaScript を有効にしてから、もう一度開いてください。"


def test_noscript_text_appears_only_inside_noscript():
    assert HTML.count("<noscript>") == 1
    outside = re.sub(r"<noscript>.*?</noscript>", "", HTML, flags=re.S)
    assert "JavaScript で動きます" not in outside


def test_dead_controls_are_hidden_when_scripting_is_none():
    m = re.search(r"@media \(scripting: none\) \{(.*?)\n\}", CSS, re.S)
    assert m, "scripting: none の規則が無い"
    rule = re.search(r"([^{}]+)\{([^}]*)\}", m.group(1))
    sels = {s.strip() for s in rule.group(1).split(",")}
    assert {"#text", "#send"} <= sels and "display: none" in rule.group(2)
    assert re.search(r"\.noscript \{[^}]*border", CSS)
    assert re.search(r"\.noscript span \{[^}]*display: inline-block", CSS)
