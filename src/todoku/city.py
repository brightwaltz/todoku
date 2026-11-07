"""/city/<団体コード> の頁：その市区町村に住む人が使える制度を、規則から並べる（T-080b）。

中身は規則ファイルだけから作り、Claude は使わない。判定はせず、住所で決まる段（国・都道府県・市・市区町村の型）を分けて数える。
どの段を出すかは、結果の画面（app.step_view）と同じ考えにする：住んでいない市・都の制度は出さず、
市の制度を一つずつ判定している市（町田市・横浜市）では型を出さない（その市で書けていない型〔shown_in〕だけは出す）。
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape

from . import contacts as contacts_mod
from . import pref_programs as pref_mod
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
# T-118：帯の合計は制度の件数だけ。型は「種類」で数えるので足さず、内訳の「市区町村の事業 n 種類」にだけ出す（D-0037、既定は A）。
# 合計に型を並べる案（B）にするなら、ここに "＋型 {n} 種類" を入れる（型が 0 のときは出さない）
SUMMARY_TOTAL_TYPES = ""

# T-095：県の制度を調べて、個人で申し込めるものが見つからなかった都道府県（rules.py の LEVEL_PLACE の注・README の冒頭と同じ事実）。
# 0 件の段の 1 行を「まだ規則にしていません」から替える（そのままだと調べていないように読める）。文言はここだけで差し替える
PREF_NONE_FOUND = {
    "神奈川県": ("神奈川県の制度は、2026-11-03 に県の「介護・高齢者」の案内を全部見ましたが、個人で申し込めるものが見つかりませんでした"
               "（補聴器の補助などは市町が申し込み先で、県は費用を出すだけです）。"
               # 11/7 の第 3 の層の調べ（rules/municipal/14.yaml）で、高齢者の案内の外にある県の駐車場の利用証が見つかった
               "2026-11-07 に Claude が県の公式サイトから自動で集めた中に、要介護1以上の人も対象になる駐車場の利用証が 1 件あり、"
               "相談の結果の画面に出します。"),
}
# T-113：帯の内訳で、制度を書けていない段を「0 件」と出さない（町田市・横浜市以外の頁で「その市・県に支えが無い」と読めたため）。
# 都道府県は、調べた県（PREF_NONE_FOUND）と調べていない県を言い分ける。市の制度を書けていない市は市の段を出さず、型の数で示す。
# T-096 の帯の 1 行（「神奈川県の 0 件は…」）は、0 件を出さなくなったのでやめた。文言はここだけで差し替える
SUMMARY_PREF_UNSEEN = "{pref}の制度は、まだ調べていません"
SUMMARY_PREF_NONE = "{pref}の制度は、調べて、個人で申し込めるものが見つかりませんでした"
# T-129：47 都道府県を調べた記録（rules/pref_programs.yaml）。神奈川県は上の PREF_NONE_FOUND の文のまま。文言はここだけで差し替える
PREF_CHECKED_NONE = "{pref}の制度は、{date} に県の高齢者・介護の案内を見ましたが、個人で申し込めるものが見つかりませんでした。"
PREF_FOUND_NOTE = ("{pref}の制度で、個人で申し込めそうなものを {date} に県の公式頁で {n} 件見つけました。"
                   "とどくの規則にはまだしていないので、当てはまるかは申請先で確かめてください。")
SUMMARY_PREF_FOUND = "{pref}の制度は、確かめる候補が {n} 件あります"
PREF_LOOKED = "見た県の頁"
SUMMARY_TYPES_N = ("市区町村の事業", "種類（全国の実施率つき、窓口で確かめる候補）")
# T-143：下の段の見出しの数の単位。型の段は帯の内訳（SUMMARY_TYPES_N）と同じく「種類」、制度の段は「件」
SECTION_UNIT = {"local": "種類"}

# T-089：地域包括支援センターの職員が相談を受けながら使うとき（T-078 の候補 #2。判断は integration-1105.md の「T-078」、既定は A）。
# 相談先の枠に 1 文だけ出す。職員向けの別の画面は作らない。README の「包括の職員が相談を受けながら使うとき」と同じ趣旨。文言はここだけで差し替える
STAFF_NOTE = ("地域包括支援センターの職員の方は、相談を受けながらこの頁やとどくの結果の画面をご本人と一緒に見て、"
              "印刷した結果を控えにできます。控えはご本人の情報なので、保管や受け渡しはセンターの個人情報の取り扱いの決まりに従ってください。"
              "判定は規則から出した目安で、窓口の扱いに合うかは職員の方が確かめてください。")
# T-123：都道府県の段の下に出す相談先（rules/contacts/prefectures.yaml）の見出し。文言はここだけで差し替える
CONTACTS_TITLE = "{pref}の相談先"


@dataclass(frozen=True)
class Group:
    key: str  # national・都道府県や市の段の名前・local
    title: str
    programs: list[Program]
    empty: str = ""  # 制度が 0 件のときに出す 1 行
    contacts: tuple = ()  # 都道府県の段に出す相談先（T-123）
    checked: pref_mod.PrefCheck | None = None  # 都道府県の段：調べた記録（T-129）。None は調べていない


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
    contacts = contacts_mod.for_place(code)
    groups += [Group(level, f"{rules_mod.LEVELS[level]}の制度", of(level), contacts=contacts) for level in pref_levels]
    if not pref_levels:
        # T-118（画面のレーン）：調べていない県の 1 行は、帯（SUMMARY_PREF_UNSEEN）と同じ言い方にする。
        # T-129（データのレーン）：調べた県は、その記録から「見つかった」「調べて見つからなかった」を出す
        checked = pref_mod.for_place(code)
        empty = PREF_NONE_FOUND.get(pref, SUMMARY_PREF_UNSEEN.format(pref=pref) + "。")
        if checked and pref not in PREF_NONE_FOUND:
            date = checked.checked.isoformat()
            empty = (PREF_FOUND_NOTE.format(pref=pref, date=date, n=len(checked.found)) if checked.found
                     else PREF_CHECKED_NONE.format(pref=pref, date=date))
        groups.append(Group("pref", f"{pref}の制度", [], empty, contacts, checked))
    groups += [Group(level, f"{rules_mod.LEVELS[level]}の制度", of(level)) for level in city_levels]
    if not city_levels:
        groups.append(Group("city", f"{name}の制度", [], f"{name}の事業は、下の「市区町村の事業（型）」で示します。"))
    local = [p for p in of("local") if city is None or city in p.shown_in]
    groups.append(Group("local", "市区町村の事業（型）", local,
                        f"{name}の事業は、上の「{name}の制度」で一つずつ示しています。"))
    return groups


def summary_parts(groups: list[Group]) -> list[tuple[str, int | None]]:
    """頁の先頭の内訳：段の短い名前（国・都道府県の名前・市区町村の名前・型）と件数。

    制度を書けていない段は 0 件と出さない（T-113）。都道府県は件数を None にして、名前の代わりに
    「まだ調べていません」か「調べて見つかりませんでした」の文を返す。市の制度を書けていない市の段は外す
    （型の段が数を受け持つ）。型の 0 件は、市の制度を一つずつ書いた市（町田市）で型を出さないときだけなので、外す。
    """
    out: list[tuple[str, int | None]] = []
    for g in groups:
        if g.programs:
            out.append((SUMMARY_TYPES if g.key == "local" else g.title.removesuffix("の制度"), len(g.programs)))
        elif g.key == "pref":
            pref = g.title.removesuffix("の制度")
            if g.checked and g.checked.found and pref not in PREF_NONE_FOUND:
                out.append((SUMMARY_PREF_FOUND.format(pref=pref, n=len(g.checked.found)), None))
                continue
            none = pref in PREF_NONE_FOUND or g.checked is not None
            out.append(((SUMMARY_PREF_NONE if none else SUMMARY_PREF_UNSEEN).format(pref=pref), None))
    return out


def _summary_item(label: str, n: int | None) -> str:
    e = escape
    if n is None:
        return f'<li class="city-break-note">{e(label)}</li>'
    if label == SUMMARY_TYPES:
        head, tail = SUMMARY_TYPES_N
        return f'<li class="city-break-types">{e(head)} <b>{n}</b> {e(tail)}</li>'
    return f"<li>{e(label)} <b>{n}</b> 件</li>"


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


def _checked_html(g: Group) -> str:
    """都道府県の段：調べた記録（T-129）。見つかった制度は確かめる候補として並べ、見た頁を出典の欄に出す。"""
    if not g.checked:
        return ""
    e = escape
    items = "".join(
        f'<li class="city-item pref-candidate"><h3>{e(c.name)}</h3>'
        f'<p class="city-where"><b>主な条件</b> {e(c.who)}</p>'
        f'<p class="city-where"><b>申請先</b> {e(c.where)}</p>'
        f'<details><summary>出典</summary><ul class="city-src"><li><a href="{e(c.url, quote=True)}" target="_blank" '
        f'rel="noopener">{e(c.url)}</a></li></ul></details></li>'
        for c in g.checked.found)
    looked = "".join(f'<li><a href="{e(u, quote=True)}" target="_blank" rel="noopener">{e(u)}</a></li>' for u in g.checked.looked)
    return ((f'<ul class="city-list">{items}</ul>' if items else "")
            + f'<details class="pref-looked"><summary>{e(PREF_LOOKED)}（{g.checked.checked.isoformat()}）</summary>'
            f'<ul class="city-src">{looked}</ul></details>')


def _contacts_html(g: Group, pref: str) -> str:
    """都道府県の段の下に出す相談先（T-123）。名前は相談先の頁へのリンク、電話は tel: のリンクにする。"""
    if not g.contacts:
        return ""
    e = escape
    items = "".join(
        f'<li><a href="{e(c.url, quote=True)}" target="_blank" rel="noopener">{e(c.name(pref))}</a>'
        # 電話は結果の画面の番号と同じ .tel（太字・番号の途中で折らない・上下 14px まで押せる。T-029）にする（T-176）
        + (f' 電話 <a class="tel" href="tel:{e(c.tel, quote=True)}">{e(c.tel)}</a>' if c.tel else "")
        + f'<br><span class="note">{e(c.note)}（{c.checked.isoformat()} に確かめた）</span></li>'
        for c in g.contacts)
    return f'<div class="city-contacts"><h3>{e(CONTACTS_TITLE.format(pref=pref))}</h3><ul>{items}</ul></div>'


# 第 3 の層（T-199）。枠の名前と、確かさの低いものに添える 1 行（結果の画面の web/app.js の MUNI_LOW と同じ字）
MUNI_TITLE = "市区町村の公式サイトから自動で集めた制度"
MUNI_LOW = "一覧の説明だけから読んだもの"


def municipal_section(municipal: dict | None) -> str:
    """第 3 の層の段。この頁はご本人の状況を知らないので、判定（status）は出さない。無ければ空の文字列。

    T-201：municipal には都道府県の分も 1 つの並びで来る（各行の by が申し込む先の自治体）。この頁では、その市区町村の分
    （by がその市区町村の名前の行。by の無い行は市区町村の分と見る）だけを出す。この頁の都道府県の段は rules/pref_programs.yaml
    （T-129）のままで、両方にある制度（茨城県のシニアカード・群馬県のシニアパスポート）が 2 回出るため。
    """
    name = (municipal or {}).get("name")
    programs = [p for p in (municipal or {}).get("programs") or () if p.get("by", name) == name]
    if not programs:
        return ""
    e = escape
    items = "".join(
        f'<li class="city-item muni-item" data-id="{e(p["id"])}"><h3>{e(p["name"])}</h3>'
        + (f'<p class="muni-cat">{e(p["category_label"])}</p>' if p.get("category_label") else "")
        + f'<p>{e(p["summary"])}</p>'
        + (f'<p class="note">{e(MUNI_LOW)}</p>' if p.get("confidence") == "low" else "")
        + (f'<p class="city-where"><b>申請先</b> {e(p["where"])}</p>' if p.get("where") else "")
        + f'<details><summary>出典</summary><ul class="city-src"><li><a href="{e(p["url"], quote=True)}" target="_blank" rel="noopener">'
        f'{e(municipal["name"])}「{e(p["name_on_page"])}」</a></li></ul></details></li>'
        for p in programs)
    return (f'<section class="card soft city-group" data-level="municipal"><h2>{e(MUNI_TITLE)}'
            f'<span class="city-n">{len(programs)} 件</span></h2>'
            f'<p class="local-mark">{e(municipal["mark"])}</p><ul class="city-list">{items}</ul></section>')


def city_page(rules: RuleSet, code: str, pref: str, name: str, center_url: str, map_html: str = "",
              local: CityRules | None = None, municipal: dict | None = None) -> str:
    """local はその市の事業（rules/local。T-075b）。見つかった型は、名前・申請先・主な条件・出典をその市のものにして印を付ける。

    municipal は第 3 の層（T-199）：市区町村の公式サイトから自動で集めた制度。step_view の municipal と同じ形
    （code・name・site・mark・programs）。確かな層の段と合計には入れず、別の段にする。
    """
    groups = city_groups(rules, code, pref, name)
    found = local.found() if local else {}
    values = rules.place_values(code)
    here_machida = values.get(rules.place_question) is True
    e = escape
    shown = {p.id for g in groups for p in g.programs}
    firsts = {p.id: first_steps(p, rules, values, shown) for p in rules.programs.values() if p.id in shown}
    counted = {p.id for g in groups if g.key != "local" for p in g.programs}  # T-118：合計は制度だけ（型は種類で数える）
    n_types = len(shown) - len(counted)
    marked = sum(1 for pid, t in firsts.items() if t and pid in counted)
    parts = "".join(_summary_item(label, n) for label, n in summary_parts(groups))
    total_types = e(SUMMARY_TOTAL_TYPES.format(n=n_types)) if SUMMARY_TOTAL_TYPES and n_types else ""
    summary = (f'<div class="city-summary" data-level="summary">'
               f'<p class="city-total"><b>{len(counted)}</b> 件{total_types}<span>{e(SUMMARY_TITLE)}</span></p>'
               f'<ul class="city-break">{parts}</ul>'
               + (f'<p class="city-first-n">{e(SUMMARY_FIRST.format(mark=FIRST_MARK))} <b>{marked}</b> 件</p>' if marked else "")
               + f'<p class="city-caveat">{e(SUMMARY_NOTE.format(name=name))}</p></div>')
    body = []
    for g in groups:
        body.append(f'<section class="card city-group" data-level="{e(g.key)}">'
                    # T-113：書けていない段の見出しにも 0 件を出さない（すぐ下の 1 行が、なぜ無いかを言う）
                    f'<h2>{e(g.title)}' + (f'<span class="city-n">{len(g.programs)} {SECTION_UNIT.get(g.key, "件")}</span>' if g.programs else "") + '</h2>')
        if not g.programs:
            body.append(f'<p class="note">{e(g.empty)}</p>{_checked_html(g)}{_contacts_html(g, pref)}</section>')
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
        body.append(f"</ul>{_contacts_html(g, pref)}</section>")
    body.append(municipal_section(municipal))
    local_note = (f"印の付いた事業は、Claude が{e(name)}の公式頁を読んで名前・申請先・主な条件を書き、別の読み手が原文と照らし直したものです。"
                  "当てはまるかの目安は型のままです。") if found else ""
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(pref)}{e(name)}で使える制度 — とどく</title>
<meta name="description" content="{e(pref)}{e(name)}に住む人が使える介護・福祉の制度を、国・都道府県・市区町村の段に分けて、申請先と出典つきで並べた一覧です。">
<meta property="og:type" content="website">
<meta property="og:site_name" content="とどく">
<meta property="og:title" content="{e(pref)}{e(name)}で使える制度 — とどく">
<meta property="og:description" content="{e(pref)}{e(name)}に住む人が使える介護・福祉の制度を、国・都道府県・市区町村の段に分けて、申請先と出典つきで並べた一覧です。">
<meta property="og:locale" content="ja_JP">
<meta property="og:image" content="/static/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
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
