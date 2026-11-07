"""T-186：見本の見出し（.sample-head）は「Safari は text-wrap: pretty」のつもりだったが、WebKit は行をまたぐ印（mark）に
box-decoration-break: clone があると pretty も balance も効かせず、390 で「…住民税非課税で / す。」と最後の行が 1〜2 字になっていた
（WebKit の 320・375・390 で見本 4 件中 4 か所）。auto-phrase の無いブラウザでだけ見出しの印を slice にする。
Chrome（auto-phrase あり）の印と折れ目は変えない。相談の吹き出し（.said mark）は text-wrap を使わないので clone のまま。
WebKit と Chromium の折れ目の前と後は $OPS/tools/open_t180c.py（T180_ENGINE=webkit）と差し込みの測りで確かめた。ここではファイルの文を読むだけ。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def test_sample_head_mark_is_sliced_where_there_is_no_auto_phrase():
    rule = re.search(r"^@supports not \(word-break: auto-phrase\) \{ \.sample-head mark \{([^}]*)\} \}", CSS, re.M)
    assert rule, "見本の見出しの印は @supports not (word-break: auto-phrase) の中で slice にする"
    for decl in ("-webkit-box-decoration-break: slice", "box-decoration-break: slice"):
        assert decl in rule.group(1), decl


def test_shared_mark_rule_keeps_clone_for_chrome_and_said():
    # 吹き出しと見本の見出しの共通の印は clone のまま（Chrome の見た目と吹き出しは変えない）
    shared = re.search(r"^\.said mark, \.sample-head mark \{([^}]*)\}", CSS, re.M)
    assert shared and "box-decoration-break: clone" in shared.group(1)
    assert "text-wrap: pretty" in re.search(r"^\.sample-head \{([^}]*)\}", CSS, re.M).group(1)
