"""全国の見取り図（T-080c）：47 都道府県を正方形のタイルで日本の形に並べ、判定の深さで塗る SVG。

中身は規則ファイルの住所の事実（rules.places）と制度の段だけから作り、Claude も外のライブラリも使わない。
深さは「国の制度と市区町村の型（全国どこでも）」に、その都道府県で足して判定する段（都道府県の制度・市の制度）が
いくつあるかで決める：0 段・1 段・2 段以上の 3 色。段を足す（T-075 の政令指定都市など）と、ここも自然に塗り変わる。
タイルは /?pref=<都道府県コード> へのリンクで、入口ではその都道府県を選んで市区町村の欄へ移る（web/app.js）。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from . import rules as rules_mod
from .rules import RuleSet

# タイルの位置（列, 行）。地理の正確さより、隣り合う県が隣に来ることと、横幅を 13 列に収めることを優先した
TILES: dict[str, tuple[int, int]] = {
    "01": (12, 0),
    "02": (11, 1),
    "05": (10, 2), "03": (11, 2),
    "06": (10, 3), "04": (11, 3),
    "18": (7, 4), "17": (8, 4), "16": (9, 4), "15": (10, 4), "07": (11, 4),
    "35": (2, 5), "32": (3, 5), "31": (4, 5), "28": (5, 5), "26": (6, 5), "25": (7, 5), "21": (8, 5),
    "20": (9, 5), "10": (10, 5), "09": (11, 5), "08": (12, 5),
    "42": (0, 6), "41": (1, 6), "40": (2, 6), "34": (3, 6), "33": (4, 6), "27": (5, 6), "29": (6, 6),
    "24": (7, 6), "23": (8, 6), "19": (9, 6), "11": (10, 6), "13": (11, 6), "12": (12, 6),
    "43": (1, 7), "44": (2, 7), "38": (3, 7), "37": (4, 7), "30": (6, 7), "22": (9, 7), "14": (10, 7),
    "46": (1, 8), "45": (2, 8), "39": (3, 8), "36": (4, 8),
    "47": (0, 9),
}
TILE = 40  # タイルの一辺（viewBox の単位）
STEP = 42  # タイルの間隔（すき間 2）
TIERS = 3


@dataclass(frozen=True)
class PrefCoverage:
    code: str
    name: str
    levels: list[str]  # その都道府県で、国と型に足して判定する段（都道府県 → 市の順）

    @property
    def tier(self) -> int:
        return min(len(self.levels), TIERS - 1)


def short_name(name: str) -> str:
    """タイルに書く名前。北海道はそのまま、ほかは「都・府・県」を外す（神奈川・和歌山・鹿児島は 3 字）。"""
    # rstrip だと「京都府」が「京」になるので、最後の 1 字だけを外す
    return name[:-1] if name != "北海道" and name[-1] in "都府県" else name


def coverage(rules: RuleSet, prefs: dict[str, str]) -> list[PrefCoverage]:
    """都道府県コード → 名前 の表から、都道府県ごとに足して判定する段を返す（TILES の並び）。"""
    out = []
    for code in TILES:
        pref_levels, city_levels = [], []
        for level, fid in rules_mod.LEVEL_PLACE.items():
            place = rules.places.get(fid) or {}
            if not any(p.level == level for p in rules.programs.values()):
                continue
            if "city" in place and place["city"][:2] == code:
                city_levels.append(level)
            elif place.get("pref") == code:
                pref_levels.append(level)
        out.append(PrefCoverage(code, prefs[code], pref_levels + city_levels))
    return out


def _count(rules: RuleSet, level: str) -> int:
    return sum(1 for p in rules.programs.values() if p.level == level)


def tile_label(rules: RuleSet, c: PrefCoverage) -> str:
    extra = "・".join(f"{rules_mod.LEVELS[lv]}の制度 {_count(rules, lv)} 件" for lv in c.levels)
    return f"{c.name}：国の制度と市区町村の型" + (f"に加えて、{extra}を判定" if extra else "を判定") + "。押すと市区町村を選べます"


def japan_map(rules: RuleSet, prefs: dict[str, str], current: str = "") -> str:
    """見取り図の HTML の断片（figure）。current の都道府県のタイルに aria-current を付ける。"""
    e = escape
    cov = coverage(rules, prefs)
    cols = max(x for x, _ in TILES.values()) + 1
    rows = max(y for _, y in TILES.values()) + 1
    w, h = cols * STEP - (STEP - TILE), rows * STEP - (STEP - TILE)
    tiles = []
    for c in cov:
        x, y = TILES[c.code]
        label = tile_label(rules, c)
        short = short_name(c.name)
        fit = f' textLength="{TILE - 6}" lengthAdjust="spacingAndGlyphs"' if len(short) > 2 else ""
        cur = ' aria-current="true"' if c.code == current else ""
        tiles.append(
            f'<a href="/?pref={c.code}" class="tier-{c.tier}" data-pref="{c.code}" data-tier="{c.tier}" aria-label="{e(label)}"{cur}>'
            f'<title>{e(label)}</title>'
            f'<rect x="{x * STEP}" y="{y * STEP}" width="{TILE}" height="{TILE}" rx="6"/>'
            f'<text x="{x * STEP + TILE / 2:g}" y="{y * STEP + TILE / 2:g}"{fit}>{e(short)}</text></a>')
    national, local = _count(rules, "national"), _count(rules, "local")
    legend = [f'<li><span class="map-swatch tier-0" aria-hidden="true"></span>国の制度 {national} 件と、市区町村の事業の型 {local} 件'
              f'（{len(cov)} 都道府県のどこでも）</li>']
    for tier in range(1, TIERS):
        here = [c for c in cov if c.tier == tier]
        if not here:
            continue
        what = "、".join(f"{c.name}（" + "・".join(f"{rules_mod.LEVELS[lv]}の制度 {_count(rules, lv)} 件" for lv in c.levels) + "）"
                        for c in here)
        more = "以上" if any(len(c.levels) > tier for c in here) else ""
        legend.append(f'<li><span class="map-swatch tier-{tier}" aria-hidden="true"></span>さらに {tier} 段{more}：{e(what)}</li>')
    return (f'<figure class="japan-map">'
            f'<svg viewBox="0 0 {w} {h}" role="group" aria-label="全国の見取り図：都道府県ごとの判定の深さ">{"".join(tiles)}</svg>'
            f'<figcaption><ul class="map-legend">{"".join(legend)}</ul>'
            f'<p class="note">色が濃いほど、規則にして判定する段が多い都道府県です。都道府県を押すと、その市区町村を選べます。</p></figcaption>'
            f'</figure>')
