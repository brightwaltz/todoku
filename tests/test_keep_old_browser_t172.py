"""残した結果のリンクを、縮めた符号を戻せないブラウザで開いたときの知らせ（T-172）。

縮めた符号（版 1）は DecompressionStream で戻す。無いブラウザ（iOS 16.3 以前の Safari など）では、
前は「リンクが途中で切れていないか確かめてください」と出て、正しいリンクを疑わせていた。
Chrome と Safari のエンジン（WebKit）の間でリンクを行き来させる確かめは ops の keep_t172.py が playwright で行う。
ここでは、知らせの分け方が消えていないことだけを見る。
"""

import re

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
OLD = "このブラウザでは、残した結果のリンクを開けません。ブラウザを新しくするか、別のブラウザで開いてください。"
CUT = "残した結果のリンクを読めませんでした。リンクが途中で切れていないか確かめてください。"


def _restore_catch() -> str:
    m = re.search(r"\nasync function restoreFrom\(code\) \{\n(.*?)\n\}\n", JS, re.S)
    assert m
    c = re.search(r"saved = await decodeKeep\(code\);\n\s*\} catch \(e\) \{\n(.*?)\n\s*return;\n\s*\}", m.group(1), re.S)
    assert c, "decodeKeep の失敗を受ける catch がある"
    return c.group(1)


def test_old_browser_gets_its_own_message():
    body = _restore_catch()
    assert OLD in body and CUT in body
    cond = re.search(r'code\.startsWith\("1\."\) && typeof DecompressionStream === "undefined"', body)
    assert cond, "版 1 の符号で、DecompressionStream が無いときだけ、ブラウザのことだと知らせる"


def test_message_is_shown_and_read_aloud():
    body = _restore_catch()
    assert re.search(r'\$\("reading"\)\.replaceChildren\(el\("p", \{ class: "error" \}, msg\)\)', body)
    assert "say(msg" in body
    assert 'show("reading")' in body


def test_uncompressed_links_still_decode_without_streams():
    keep = (app_mod.WEB_DIR / "keep.js").read_text(encoding="utf-8")
    # 版 0 は DecompressionStream を使わずに読む（古いブラウザで作ったリンクは古いブラウザでも開ける）
    assert re.search(r'if \(version === "1"\) bytes = await through\(bytes, new DecompressionStream', keep)
    assert 'if (typeof CompressionStream === "undefined") return "0." + toB64url(raw);' in keep


def test_restore_failures_bring_the_message_into_view():
    m = re.search(r"\nasync function restoreFrom\(code\) \{\n(.*?)\n\}\n", JS, re.S)
    # 知らせは入口の絵より下にあるので、読めないとき・結果を作れないときの両方で画面を送る
    assert m.group(1).count('show("reading");\n    $("reading").scrollIntoView({ block: "center" });') == 2
