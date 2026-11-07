"""T-202：第 3 の層の札の頁へのリンク（.plain の li の中の a）は、名前が折れても「•」を 1 行目に付ける。

inline-block の a は、ベースラインが最後の行になるので、li の「•」が折れた最後の行の前に付いていた
（合わせた木の本物のデータ、横浜市の見本の「かながわ障害者等用駐車区画利用証制度」を 390px で）。
block にすると、「•」は a の 1 行目に揃う。押す所の 44px（T-146）はそのまま。
"""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, selector
    return m.group(1)


def test_muni_link_is_block_so_the_bullet_sits_on_the_first_line():
    body = _rule("details.muni-item a")
    assert re.search(r"display:\s*block", body)
    assert "inline-block" not in body


def test_muni_link_keeps_the_44px_target():
    body = _rule("details.muni-item a")
    assert re.search(r"min-height:\s*44px", body)
