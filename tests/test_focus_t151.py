"""T-151：全国の見取り図で、今いる県（aria-current）のタイルにフォーカスしても印が変わるか。

今いる県の枠とフォーカスの枠が同じ色・同じ太さだと、キーボードで今いる県に来ても見た目が変わらない
（/city/132098 の東京都・/city/271004 の大阪府で、$OPS/tools/kbd_t151.py が見つけた）。
"""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "web" / "style.css").read_text(encoding="utf-8")


def decls(selector_pattern: str) -> dict[str, str]:
    """選択子の並びが selector_pattern に当たる規則の宣言を、後のものを優先して 1 つにまとめる。"""
    out: dict[str, str] = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", CSS):
        if any(re.fullmatch(selector_pattern, s.strip()) for s in sel.split(",")):
            for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+)", body):
                out[k] = v.strip()
    return out


def test_focused_tile_differs_from_current_tile():
    base = decls(r"\.japan-map rect")
    current = {**base, **decls(r"\.japan-map a\[aria-current\] rect")}
    focused = {**base, **decls(r"\.japan-map a:focus-visible rect")}
    assert current.get("stroke") and focused.get("stroke")
    assert (focused["stroke"], focused.get("stroke-width")) != (current["stroke"], current.get("stroke-width"))


def test_tile_outline_is_still_removed():
    # 枠は rect の stroke で描くので、a の outline（タイルの外の四角）は出さないまま
    assert decls(r"\.japan-map a:focus").get("outline") == "none"
