"""お住まいの問いで、相談の文にある地名を欄に先に入れておく（T-200）。

画面だけの直し。相談の文に都道府県・市区町村の名前がちょうど 1 つの所として出てくるときだけ、お住まいの問いの欄に
その都道府県（と、1 つに決まればその市区町村）を先に選んでおく。違う所が 2 つ以上なら何も入れない。
事実の読み取り・判定・問いの選び方は変えない（サーバも Claude への指示も触らない）。
"""

import json
import os
import re
import shutil
import subprocess

import pytest

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

from todoku import app as app_mod  # noqa: E402

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
MUNI = json.loads((app_mod.WEB_DIR / "municipalities.json").read_text(encoding="utf-8"))
CODE = {name: code for p in MUNI["prefectures"] for code, name in p["cities"]}


def guess_all(texts: list[str]) -> list:
    if not shutil.which("node"):
        pytest.skip("node が無い")
    src = re.search(r"\nfunction guessPlace\(text, list\) \{.*?\n\}\n", JS, re.S).group(0)
    code = (src + f"\nconst list = {json.dumps(MUNI['prefectures'], ensure_ascii=False)};"
            f"\nconsole.log(JSON.stringify({json.dumps(texts, ensure_ascii=False)}.map((t) => guessPlace(t, list))));")
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    return json.loads(out)


def guess(text: str):
    return guess_all([text])[0]


def test_four_cases_from_the_task():
    # 完了条件 (4) の 4 通り：大阪府だけ／大阪市だけ／大阪と東京の両方／地名なし
    assert guess_all([
        "大阪府に住む父（78歳）が、先月退院してから一人で風呂に入れなくなりました。",
        "大阪市に住む父（78歳）が、先月退院してから一人で風呂に入れなくなりました。",
        "大阪に住む父（78歳）のことで、東京に住む私が相談しています。",
        "父（78歳）が、先月退院してから一人で風呂に入れなくなりました。",
    ]) == [
        {"pref": "27", "city": ""},
        {"pref": "27", "city": CODE["大阪市"]},
        None,
        None,
    ]


def test_short_form_of_the_prefecture_is_read_when_it_reads_as_a_place():
    # 本番で見つけた文（「大阪に住む父」）。短い形は後ろが助詞・句読点などのときだけ
    assert guess("大阪に住む父（78歳）が、先月退院してから一人で風呂に入れなくなりました。") == {"pref": "27", "city": ""}
    assert guess("大分弱ってきた母のことです。") is None  # 「だいぶ」
    assert guess("山口さんに勧められて相談します。") is None  # 人の名前


def test_longest_name_wins():
    assert guess("東京都に住む母のことです。") == {"pref": "13", "city": ""}  # 「京都」と読まない
    assert guess("大津市の実家に父が一人で住んでいます。") == {"pref": "25", "city": CODE["大津市"]}  # 「津市」ではない
    assert guess("大阪府大阪市に住む父") == {"pref": "27", "city": CODE["大阪市"]}  # 都道府県と市区町村で 1 つの所


def test_ward_of_a_big_city_is_not_a_tokyo_ward():
    # 東京 23 区の名前は政令市の区とも重なる。市の名前に続く区・語の途中の区は東京の区と読まない
    assert guess("大阪市中央区に住む父") == {"pref": "27", "city": CODE["大阪市"]}
    assert guess("横浜市港北区に住む母") == {"pref": "14", "city": CODE["横浜市"]}
    assert guess("東京都中央区に住む母") == {"pref": "13", "city": CODE["中央区"]}
    assert guess("母は北区で一人暮らしです。") == {"pref": "13", "city": CODE["北区"]}


def test_same_city_name_in_two_prefectures_needs_the_prefecture():
    assert guess("伊達市に住む父") is None  # 北海道と福島県
    fukushima = next(c for c, n in next(p for p in MUNI["prefectures"] if p["code"] == "07")["cities"] if n == "伊達市")
    assert guess("福島県伊達市に住む父") == {"pref": "07", "city": fukushima}
    assert guess("父は府中市、私は大阪に住んでいます。") is None  # 府中市は東京都と広島県で、どちらも大阪府ではない


def test_two_cities_in_one_prefecture_are_two_places():
    assert guess("父は堺市、母は大阪市に住んでいます。") is None


def test_event_and_sample_texts():
    # 入口の出来事の文からは何も入れない（地名を書いていない）。見本の文は、書いてある所だけを拾う
    from todoku import events as events_mod
    assert all(g is None for g in guess_all([e["text"] for e in events_mod.EVENTS]))
    samples = json.loads((app_mod.WEB_DIR / "samples.json").read_text(encoding="utf-8"))
    items = samples if isinstance(samples, list) else samples.get("samples", list(samples.values()))
    texts = [s["text"] for s in items if isinstance(s, dict) and s.get("text")]
    for t, g in zip(texts, guess_all(texts)):
        if g is not None:
            name = next(p["name"] for p in MUNI["prefectures"] if p["code"] == g["pref"])
            city = next((n for n, c in CODE.items() if c == g["city"]), None)
            assert name.rstrip("都府県") in t or (city and city in t), (t, g)


def test_question_prefills_without_counting_a_press():
    # お住まいの問いの欄に入れるのは送った文から。change を起こさないので押した数（state.presses）に入れない
    body = re.search(r"function fillAnswers\(box, q, pick, pickPlace\) \{.*?\n\}\n", JS, re.S).group(0)
    tail = body.split("const guess = guessPlace(state.text, prefs);")[1].split("\n")[1]
    assert "pref.value = guess.pref" in tail and "city.value = guess.city" in tail
    assert "go.disabled = !city.value" in tail
    assert "presses" not in tail and "dispatchEvent" not in body
    assert "state.text = text;" in JS
