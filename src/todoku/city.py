"""/city/<団体コード> の頁：その市区町村に住む人が使える制度を、規則から並べる（T-080b）。

中身は規則ファイルだけから作り、Claude は使わない。判定はせず、住所で決まる段（国・都道府県・市・市区町村の型）を分けて数える。
どの段を出すかは、結果の画面（app.step_view）と同じ考えにする：住んでいない市・都の制度は出さず、
市の制度を一つずつ判定している市（町田市・横浜市）では型を出さない（その市で書けていない型〔shown_in〕だけは出す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from . import rules as rules_mod
from .local_rules import MARK, CityRules
from .rules import Program, RuleSet


# T-076：尋ねられにくい制度の候補の印。決め方は判断待ち（integration-1105.md の下書き、既定は A）。
# A＝その市に住む人で見て、規則の上でほかの手続き（prereq・via・step）をいつも先に済ませる制度。文言はここだけで差し替える
FIRST_MARK = "先に別の手続きが要る"
FIRST_NOTE = ("「{mark}」の印の {n} 件は、規則の上で、ほかの手続きを済ませてからでないと使えない制度です。"
              "最初の相談から一歩遠いので、住民から尋ねられにくい制度の候補として示しています"
              "（規則から機械的に選んだもので、実際に尋ねられにくいかは確かめていません）。")

# T-088：頁の先頭に出す数と内訳（T-078 の候補 #1。判断は integration-1105.md の「T-078」、既定は A）。文言はここだけで差し替える
SUMMARY_TITLE = "とどくが書けている支え"
SUMMARY_TYPES = "市区町村の事業（型）"
SUMMARY_FIRST = "うち「{mark}」"
SUMMARY_NOTE = "とどくが規則にできている数で、{name}の支えの全部ではありません。"

# T-095：県の制度を調べて、個人で申し込めるものが見つからなかった都道府県（rules.py の LEVEL_PLACE の注・README の冒頭と同じ事実）。
# 0 件の段の 1 行を「まだ規則にしていません」から替える（そのままだと調べていないように読める）。文言はここだけで差し替える
PREF_NONE_FOUND = {
    "神奈川県": ("神奈川県の制度は、2026-11-03 に県の「介護・高齢者」の案内を全部見ましたが、個人で申し込めるものが見つかりませんでした"
               "（補聴器の補助などは市町が申し込み先で、県は費用を出すだけです）。"),
}
# T-096：先頭の帯の内訳だけを見ても、上の都道府県の 0 件が「まだ書けていない」と区別がつくように、帯にも 1 行出す。文言はここだけで差し替える
SUMMARY_NONE_FOUND = "{pref}の 0 件は、県の制度を調べて、個人で申し込めるものが見つからなかった数です。"

# T-089：地域包括支援センターの職員が相談を受けながら使うとき（T-078 の候補 #2。判断は integration-1105.md の「T-078」、既定は A）。
# 相談先の枠に 1 文だけ出す。職員向けの別の画面は作らない。README の「包括の職員が相談を受けながら使うとき」と同じ趣旨。文言はここだけで差し替える
STAFF_NOTE = ("地域包括支援センターの職員の方は、相談を受けながらこの頁やとどくの結果の画面をご本人と一緒に見て、"
              "印刷した結果を控えにできます。判定は規則から出した目安で、窓口の扱いに合うかは職員の方が確かめてください。")


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
        groups.append(Group("pref", f"{pref}の制度", [], PREF_NONE_FOUND.get(pref, f"{pref}の制度は、まだ規則にしていません。")))
    groups += [Group(level, f"{rules_mod.LEVELS[level]}の制度", of(level)) for level in city_levels]
    if not city_levels:
        groups.append(Group("city", f"{name}の制度", [], f"{name}の事業は、下の「市区町村の事業（型）」で示します。"))
    local = [p for p in of("local") if city is None or city in p.shown_in]
    groups.append(Group("local", "市区町村の事業（型）", local,
                        f"{name}の事業は、上の「{name}の制度」で一つずつ示しています。"))
    return groups


def summary_parts(groups: list[Group]) -> list[tuple[str, int]]:
    """頁の先頭の内訳：段の短い名前（国・都道府県の名前・市区町村の名前・型）と件数。

    0 件の都道府県・市も出す（まだ書けていないか、調べて見つからなかったことが分かるように。段の 1 行で言い分ける）。型の 0 件は、市の制度を一つずつ書いた市（町田市）で
    型を出さないときだけなので、内訳から外す。
    """
    return [(SUMMARY_TYPES if g.key == "local" else g.title.removesuffix("の制度"), len(g.programs))
            for g in groups if g.programs or g.key != "local"]


def first_steps(p: Program, rules: RuleSet, values: dict[str, bool], shown: set[str]) -> str:
    """その市の人がこの制度を使う前に、いつも済ませる手続き（規則の prereq・via・step）を 1 行で返す。無ければ空。

    住所の事実が合わない枝（例：町田市以外にお住まい）と、この頁に無い制度を前提にした枝は、通れないので見ない。
    any は、通れる枝のどれもが手続きを要るときだけ「または」でつなぐ。all は、いつも要る子の手続きを「、」でつなぐ。
    """

    def walk(node: dict) -> tuple[str, bool] | None:  # (手続きの文, いつも要るか)。None は通れない枝
        if "all" in node or "any" in node:
            parts = [walk(c) for c in node.get("all") or node["any"]]
            if "all" in node:
                if any(x is None for x in parts):
                    return None
                need = list(dict.fromkeys(t for t, always in parts if always))
                return "、".join(need), bool(need)
            open_ = [x for x in parts if x is not None]
            if not open_:
                return None
            if all(always for _, always in open_):
                return " または ".join(dict.fromkeys(t for t, _ in open_)), True
            return "", False
        if "not" in node:
            return "", False
        if "step" in node:
            return node["label"], True
        if "prereq" in node:
            return (rules.programs[node["prereq"]].name, True) if node["prereq"] in shown else None
        fid = node.get("fact")
        if fid in values and "is" in node and values[fid] != node["is"]:
            return None
        if node.get("via") in shown:
            return rules.programs[node["via"]].name, True
        return "", False

    got = walk(p.eligibility)
    return got[0] if got and got[1] else ""


def _apply_where(p: Program, here_machida: bool) -> str:
    # 国の制度の申請先は町田市で書いてあるので、町田市の外の人には elsewhere の申請先を出す（result_view と同じ）
    apply = p.apply if here_machida else p.elsewhere.get("apply", p.apply)
    return apply.get("where", "") or apply.get("how", "")


def _sources(p: Program, values: dict[str, bool]) -> list:
    # 住んでいない市・都のページは出典から外す（ほかに出典がなければ、そのまま出す。result_view と同じ）
    away = [site for level, site in rules_mod.LEVEL_SITE.items() if values.get(rules_mod.LEVEL_PLACE[level]) is not True]
    return [s for s in p.sources if not any(site in s.url for site in away)] or p.sources


def city_page(rules: RuleSet, code: str, pref: str, name: str, center_url: str, map_html: str = "",
              local: CityRules | None = None) -> str:
    """local はその市の事業（rules/local。T-075b）。見つかった型は、名前・申請先・主な条件・出典をその市のものにして印を付ける。"""
    groups = city_groups(rules, code, pref, name)
    found = local.found() if local else {}
    values = rules.place_values(code)
    here_machida = values.get(rules.place_question) is True
    e = escape
    shown = {p.id for g in groups for p in g.programs}
    firsts = {p.id: first_steps(p, rules, values, shown) for p in rules.programs.values() if p.id in shown}
    marked = sum(1 for t in firsts.values() if t)
    parts = "".join(f"<li>{e(label)} <b>{n}</b> 件</li>" for label, n in summary_parts(groups))
    none_found = pref in PREF_NONE_FOUND and any(g.key == "pref" and not g.programs for g in groups)
    summary = (f'<div class="city-summary" data-level="summary">'
               f'<p class="city-total"><b>{len(shown)}</b> 件<span>{e(SUMMARY_TITLE)}</span></p>'
               f'<ul class="city-break">{parts}</ul>'
               + (f'<p class="city-none-found">{e(SUMMARY_NONE_FOUND.format(pref=pref))}</p>' if none_found else "")
               + (f'<p class="city-first-n">{e(SUMMARY_FIRST.format(mark=FIRST_MARK))} <b>{marked}</b> 件</p>' if marked else "")
               + f'<p class="city-caveat">{e(SUMMARY_NOTE.format(name=name))}</p></div>')
    body = []
    for g in groups:
        body.append(f'<section class="card city-group" data-level="{e(g.key)}">'
                    f'<h2>{e(g.title)}<span class="city-n">{len(g.programs)} 件</span></h2>')
        if not g.programs:
            body.append(f'<p class="note">{e(g.empty)}</p></section>')
            continue
        body.append('<ul class="city-list">')
        for p in g.programs:
            mine = found.get(p.id) if g.key == "local" else None
            where = mine.where if mine else _apply_where(p, here_machida)
            # 型の出典（全国調査）は説明の数字の出典なので、その市の頁の後ろに残す（結果の札と同じ）
            sources = (list(mine.sources) if mine else []) + list(_sources(p, values))
            links = "".join(f'<li><a href="{e(s.url, quote=True)}" target="_blank" rel="noopener">{e(s.publisher)}「{e(s.title)}」</a></li>'
                            for s in sources)
            extra = ""
            if mine:
                conds = "".join(f"<li>{e(c)}</li>" for c in mine.conditions)
                extra = (f'<p class="local-mark">{e(MARK.format(date=mine.review.date.isoformat()))}</p>'
                         f'<p class="city-where"><b>主な条件</b></p><ul class="city-cond">{conds}</ul>')
            first = firsts.get(p.id, "")
            mark = f'<p class="city-first"><b>{e(FIRST_MARK)}</b>{e(first)}</p>' if first else ""
            first_attr = ' data-first="1"' if first else ""
            body.append(
                f'<li class="city-item{" local" if mine else ""}" data-id="{e(p.id)}"{first_attr}>'
                f'<h3>{e(mine.name if mine else p.name)}</h3>{mark}'
                f'<p>{e(p.summary)}</p>{extra}'
                + (f'<p class="city-where"><b>申請先</b> {e(where)}</p>' if where else "")
                + f'<details><summary>出典</summary><ul class="city-src">{links}</ul></details></li>')
        body.append("</ul></section>")
    local_note = (f"印の付いた事業は、Claude が{e(name)}の公式頁を読んで名前・申請先・主な条件を書き、別の読み手が原文と照らし直したものです。"
                  "当てはまるかの目安は型のままです。") if found else ""
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
{summary}
</div></header>
<main class="wrap">
<p class="note">住所で決まる段（国・都道府県・市区町村）ごとに、規則にした制度を並べています。当てはまるかどうかは、ご本人の状況で変わります。
<a href="/">とどくで相談する</a>と、状況に合わせて判定します。</p>
{f'<p class="note city-first-note">{e(FIRST_NOTE.format(mark=FIRST_MARK, n=marked))}</p>' if marked else ""}
{"".join(body)}
{f'<section class="card soft city-group map-card" data-level="map"><h2>ほかの都道府県</h2>{map_html}</section>' if map_html else ""}
<section class="card soft city-group" data-level="center">
<h2>相談先</h2>
<p>介護や福祉のことは、お住まいの地域の地域包括支援センターでまとめて相談できます。</p>
<p><a href="{e(center_url, quote=True)}" target="_blank" rel="noopener">{e(pref)}の地域包括支援センターを探す（厚生労働省 介護サービス情報公表システム）</a></p>
<p class="note city-staff">{e(STAFF_NOTE)}</p>
</section>
<p class="note">この頁は、公式ページの条件をもとに書いた規則ファイルから作っています（AI は使っていません）。市区町村の事業（型）は全国調査の実施率つきの候補で、{e(name)}がしているかは窓口で確かめてください。{local_note}</p>
</main>
</body>
</html>
"""
