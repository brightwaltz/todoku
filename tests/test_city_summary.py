"""T-088：/city/ の頁の先頭に出す「とどくが書けている支え」の数と内訳。数が頁に並んだ制度と合い、全部ではないと断るか。"""

import os
import re

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import PLACES, app  # noqa: E402
from todoku.city import (FIRST_MARK, PREF_NONE_FOUND, SUMMARY_FIRST, SUMMARY_NOTE, SUMMARY_PREF_NONE,  # noqa: E402
                         SUMMARY_PREF_UNSEEN, SUMMARY_TITLE, SUMMARY_TYPES, SUMMARY_TYPES_N)

client = TestClient(app)
MACHIDA, YOKOHAMA, VILLAGE, OSAKA = "132098", "141003", "023043", "271004"  # 大阪市は国の制度と型だけの市


def summary(code: str) -> tuple[str, str]:
    html = client.get(f"/city/{code}").text
    block = re.search(r'<div class="city-summary" data-level="summary">(.*?)</div>', html, re.S)
    assert block, code
    return html, block.group(1)


def parts(block: str) -> dict[str, int]:
    """内訳の数のある項目。型は「市区町村の事業 <b>n</b> 種類（…）」なので SUMMARY_TYPES の名前で返す。"""
    got = {label: int(n) for label, n in re.findall(r"<li>([^<]+) <b>(\d+)</b> 件</li>", block)}
    head, tail = SUMMARY_TYPES_N
    types = re.search(rf'<li class="city-break-types">{head} <b>(\d+)</b> {re.escape(tail)}</li>', block)
    if types:
        got[SUMMARY_TYPES] = int(types.group(1))
    return got


def notes(block: str) -> list[str]:
    """内訳の数の無い項目（制度を書けていない都道府県の文）。"""
    return re.findall(r'<li class="city-break-note">([^<]+)</li>', block)


def section_counts(html: str) -> dict[str, int]:
    """段（data-level）ごとの見出しの件数を、見出しの順に。"""
    return {title: int(n) for title, n in
            re.findall(r'<section class="card city-group" data-level="[^"]+"><h2>([^<]+)<span class="city-n">(\d+) 件', html)}


@pytest.mark.parametrize("code", [MACHIDA, YOKOHAMA, VILLAGE, OSAKA])
def test_total_and_breakdown_match_the_programs_listed_below(code):
    html, block = summary(code)
    total = int(re.search(r'<p class="city-total"><b>(\d+)</b> 件', block).group(1))
    assert SUMMARY_TITLE in block
    assert total == html.count('<li class="city-item')
    got = parts(block)
    assert sum(got.values()) == total
    # 内訳は下の段の見出しと同じ数・同じ順（段の名前から「の制度」を落としたもの）
    # 型が 0 件の段（町田市）だけは内訳に出さない
    want = {(SUMMARY_TYPES if t == "市区町村の事業（型）" else t.removesuffix("の制度")): n for t, n in section_counts(html).items()
            if n or t != "市区町村の事業（型）"}
    assert list(got.items()) == list(want.items())


@pytest.mark.parametrize("code, labels", [
    (MACHIDA, ["国", "東京都", "町田市"]),  # 町田市では型を出さないので、型の 0 件は内訳に無い
    (YOKOHAMA, ["国", "横浜市", SUMMARY_TYPES]),  # 神奈川県は数の無い文で出す（T-113）
    (VILLAGE, ["国", SUMMARY_TYPES]),  # 青森県・蓬田村は 0 件と出さない（T-113）
    (OSAKA, ["国", SUMMARY_TYPES]),
])
def test_breakdown_names_the_country_the_prefecture_and_the_city(code, labels):
    got = parts(summary(code)[1])
    assert list(got) == labels
    assert got["国"] > 0


@pytest.mark.parametrize("code", [VILLAGE, OSAKA])
def test_places_without_their_own_rules_do_not_show_zero(code):
    """T-113：市・県の制度を書けていない頁で、帯に「<県> 0 件」「<市> 0 件」を出さない。県はまだ調べていないと書き、市は型の数で示す。"""
    pref, name = PLACES[code]
    block = summary(code)[1]
    got = parts(block)
    assert pref not in got and name not in got and got[SUMMARY_TYPES] == 5
    assert notes(block) == [SUMMARY_PREF_UNSEEN.format(pref=pref)]
    assert "0</b> 件" not in block and " 0 件" not in block
    head, tail = SUMMARY_TYPES_N
    assert f"{head} <b>5</b> {tail}" in block and "窓口で確かめる候補" in tail


@pytest.mark.parametrize("code", [VILLAGE, OSAKA, YOKOHAMA])
def test_empty_sections_do_not_show_zero_in_the_heading(code):
    """T-113：下の段の見出しにも 0 件を出さない。書けていない段は、見出しのすぐ下の 1 行が理由を言う。"""
    html = summary(code)[0]
    assert '<span class="city-n">0 件</span>' not in html
    pref = PLACES[code][0]
    assert re.search(rf'<h2>{pref}の制度</h2>\s*<p class="note">', html)


def test_cities_with_their_own_rules_count_them():
    assert parts(summary(MACHIDA)[1])["町田市"] > 0 and parts(summary(MACHIDA)[1])["東京都"] > 0
    assert parts(summary(YOKOHAMA)[1])["横浜市"] > 0 and "神奈川県" not in parts(summary(YOKOHAMA)[1])


@pytest.mark.parametrize("code", [MACHIDA, YOKOHAMA, VILLAGE, OSAKA])
def test_says_it_is_not_all_of_the_city_support_and_counts_the_first_marks(code):
    html, block = summary(code)
    name = PLACES[code][1]
    assert SUMMARY_NOTE.format(name=name) in block
    assert "全部ではありません" in SUMMARY_NOTE
    n = html.count('data-first="1"')
    assert n > 0
    assert f"{SUMMARY_FIRST.format(mark=FIRST_MARK)} <b>{n}</b> 件" in block


def test_summary_sits_in_the_header_before_the_lists():
    html, _ = summary(MACHIDA)
    assert html.index('class="city-summary"') < html.index("</header>") < html.index('class="city-item')


def test_band_says_kanagawa_was_checked_and_other_prefectures_were_not():
    """T-096 → T-113：神奈川県は調べて見つからなかったと書き、ほかの書けていない県（青森県・大阪府）はまだ調べていないと書く。0 件の注の行はもう無い。"""
    block = summary(YOKOHAMA)[1]
    assert notes(block) == [SUMMARY_PREF_NONE.format(pref="神奈川県")] and "見つかりませんでした" in SUMMARY_PREF_NONE
    assert "city-none-found" not in block
    for code in (VILLAGE, OSAKA):
        pref = PLACES[code][0]
        assert pref not in PREF_NONE_FOUND
        assert notes(summary(code)[1]) == [SUMMARY_PREF_UNSEEN.format(pref=pref)], code
    assert notes(summary(MACHIDA)[1]) == []  # 東京都は 1 件書けている
