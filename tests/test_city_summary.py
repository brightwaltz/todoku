"""T-088：/city/ の頁の先頭に出す「とどくが書けている支え」の数と内訳。数が頁に並んだ制度と合い、全部ではないと断るか。"""

import os
import re

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku.app import PLACES, app  # noqa: E402
from todoku.city import FIRST_MARK, SUMMARY_FIRST, SUMMARY_NOTE, SUMMARY_TITLE, SUMMARY_TYPES  # noqa: E402

client = TestClient(app)
MACHIDA, YOKOHAMA, VILLAGE, OSAKA = "132098", "141003", "023043", "271004"  # 大阪市は国の制度と型だけの市


def summary(code: str) -> tuple[str, str]:
    html = client.get(f"/city/{code}").text
    block = re.search(r'<div class="city-summary" data-level="summary">(.*?)</div>', html, re.S)
    assert block, code
    return html, block.group(1)


def parts(block: str) -> dict[str, int]:
    return {label: int(n) for label, n in re.findall(r"<li>([^<]+) <b>(\d+)</b> 件</li>", block)}


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
    (YOKOHAMA, ["国", "神奈川県", "横浜市", SUMMARY_TYPES]),
    (VILLAGE, ["国", "青森県", "蓬田村", SUMMARY_TYPES]),
    (OSAKA, ["国", "大阪府", "大阪市", SUMMARY_TYPES]),
])
def test_breakdown_names_the_country_the_prefecture_and_the_city(code, labels):
    got = parts(summary(code)[1])
    assert list(got) == labels
    assert got["国"] > 0


@pytest.mark.parametrize("code", [VILLAGE, OSAKA])
def test_places_without_their_own_rules_show_zero_for_prefecture_and_city(code):
    pref, name = PLACES[code]
    got = parts(summary(code)[1])
    assert got[pref] == 0 and got[name] == 0 and got[SUMMARY_TYPES] == 5


def test_cities_with_their_own_rules_count_them():
    assert parts(summary(MACHIDA)[1])["町田市"] > 0 and parts(summary(MACHIDA)[1])["東京都"] > 0
    assert parts(summary(YOKOHAMA)[1])["横浜市"] > 0 and parts(summary(YOKOHAMA)[1])["神奈川県"] == 0


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


def test_band_says_the_kanagawa_zero_was_checked_and_none_found():
    """T-096：帯の内訳だけでも、神奈川県の 0 件が調べて見つからなかった数だと分かる。ほかの 0 件の県（青森県・大阪府）には出さない。"""
    from todoku.city import PREF_NONE_FOUND, SUMMARY_NONE_FOUND

    _, block = summary(YOKOHAMA)
    assert parts(block)["神奈川県"] == 0
    assert SUMMARY_NONE_FOUND.format(pref="神奈川県") in block and "見つからなかった" in SUMMARY_NONE_FOUND
    for code in (VILLAGE, OSAKA, MACHIDA):
        pref = PLACES[code][0]
        assert pref not in PREF_NONE_FOUND
        assert 'class="city-none-found"' not in summary(code)[1], code
