"""/city/<団体コード> の頁：その市区町村に住む人が使える制度を、規則から並べる（T-080b）。

中身は規則ファイルだけから作り、Claude は使わない。判定はせず、住所で決まる段（国・都道府県・市・市区町村の型）を分けて数える。
どの段を出すかは、結果の画面（app.step_view）と同じ考えにする：住んでいない市・都の制度は出さず、
市の制度を一つずつ判定している市（町田市・横浜市）では型を出さない（その市で書けていない型〔shown_in〕だけは出す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from . import rules as rules_mod
from .rules import Program, RuleSet


@dataclass(frozen=True)
class Group:
    key: str  # national・都道府県や市の段の名前・local
    title: str
    programs: list[Program]
    empty: str = ""  # 制度が 0 件のときに出す 1 行


def city_groups(rules: RuleSet, code: str, pref: str, name: str) -> list[Group]:
    """国 → 都道府県 → 市 → 市区町村の型 の順に、その団体コードの人に出す制度を返す。"""
    values = rules.place_values(code)
    programs = sorted(rules.programs.values(), key=lambda p: p.number)
    levels = [(level, fid) for level, fid in rules_mod.LEVEL_PLACE.items() if values.get(fid) is True]
    pref_levels = [level for level, fid in levels if "city" not in rules.places[fid]]
    city_levels = [level for level, fid in levels if "city" in rules.places[fid]]
    city = city_levels[0] if city_levels else None

    def of(level: str) -> list[Program]:
        return [p for p in programs if p.level == level]

    groups = [Group("national", "国の制度", of("national"))]
    groups += [Group(level, f"{rules_mod.LEVELS[level]}の制度", of(level)) for level in pref_levels]
    if not pref_levels:
        groups.append(Group("pref", f"{pref}の制度", [], f"{pref}の制度は、まだ規則にしていません。"))
    groups += [Group(level, f"{rules_mod.LEVELS[level]}の制度", of(level)) for level in city_levels]
    if not city_levels:
        groups.append(Group("city", f"{name}の制度", [], f"{name}の事業は、下の「市区町村の事業（型）」で示します。"))
    local = [p for p in of("local") if city is None or city in p.shown_in]
    groups.append(Group("local", "市区町村の事業（型）", local,
                        f"{name}の事業は、上の「{name}の制度」で一つずつ示しています。"))
    return groups


def _apply_where(p: Program, here_machida: bool) -> str:
    # 国の制度の申請先は町田市で書いてあるので、町田市の外の人には elsewhere の申請先を出す（result_view と同じ）
    apply = p.apply if here_machida else p.elsewhere.get("apply", p.apply)
    return apply.get("where", "") or apply.get("how", "")


def _sources(p: Program, values: dict[str, bool]) -> list:
    # 住んでいない市・都のページは出典から外す（ほかに出典がなければ、そのまま出す。result_view と同じ）
    away = [site for level, site in rules_mod.LEVEL_SITE.items() if values.get(rules_mod.LEVEL_PLACE[level]) is not True]
    return [s for s in p.sources if not any(site in s.url for site in away)] or p.sources


def city_page(rules: RuleSet, code: str, pref: str, name: str, center_url: str, map_html: str = "") -> str:
    groups = city_groups(rules, code, pref, name)
    values = rules.place_values(code)
    here_machida = values.get(rules.place_question) is True
    e = escape
    counts = "・".join(f"{g.title} {len(g.programs)} 件" for g in groups if g.programs)
    body = []
    for g in groups:
        body.append(f'<section class="card city-group" data-level="{e(g.key)}">'
                    f'<h2>{e(g.title)}<span class="city-n">{len(g.programs)} 件</span></h2>')
        if not g.programs:
            body.append(f'<p class="note">{e(g.empty)}</p></section>')
            continue
        body.append('<ul class="city-list">')
        for p in g.programs:
            where = _apply_where(p, here_machida)
            links = "".join(f'<li><a href="{e(s.url, quote=True)}" target="_blank" rel="noopener">{e(s.publisher)}「{e(s.title)}」</a></li>'
                            for s in _sources(p, values))
            body.append(
                f'<li class="city-item" data-id="{e(p.id)}"><h3>{e(p.name)}</h3>'
                f'<p>{e(p.summary)}</p>'
                + (f'<p class="city-where"><b>申請先</b> {e(where)}</p>' if where else "")
                + f'<details><summary>出典</summary><ul class="city-src">{links}</ul></details></li>')
        body.append("</ul></section>")
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(pref)}{e(name)}で使える制度 — とどく</title>
<meta name="description" content="{e(pref)}{e(name)}に住む人が使える介護・福祉の制度を、国・都道府県・市区町村の段に分けて、申請先と出典つきで並べた一覧です。">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@400;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
</head>
<body>
<header class="top"><div class="wrap">
<h1>とどく<span>{e(pref)}{e(name)}で使える制度</span></h1>
<p class="lead">{e(counts)}</p>
</div></header>
<main class="wrap">
<p class="note">住所で決まる段（国・都道府県・市区町村）ごとに、規則にした制度を並べています。当てはまるかどうかは、ご本人の状況で変わります。
<a href="/">とどくで相談する</a>と、状況に合わせて判定します。</p>
{"".join(body)}
{f'<section class="card soft city-group map-card" data-level="map"><h2>ほかの都道府県</h2>{map_html}</section>' if map_html else ""}
<section class="card soft city-group" data-level="center">
<h2>相談先</h2>
<p>介護や福祉のことは、お住まいの地域の地域包括支援センターでまとめて相談できます。</p>
<p><a href="{e(center_url, quote=True)}" target="_blank" rel="noopener">{e(pref)}の地域包括支援センターを探す（厚生労働省 介護サービス情報公表システム）</a></p>
</section>
<p class="note">この頁は、公式ページの条件をもとに書いた規則ファイルから作っています（AI は使っていません）。市区町村の事業（型）は全国調査の実施率つきの候補で、{e(name)}がしているかは窓口で確かめてください。</p>
</main>
</body>
</html>
"""
