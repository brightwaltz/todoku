"""T-075：市ごとの事業（rules/local/<団体コード>.yaml）の読み込みと形の検査。画面に出すところは tests/test_local_view.py（T-075b）。"""
import json
from pathlib import Path

import pytest
import yaml

from todoku import local_rules
from todoku import rules as rules_mod

ROOT = Path(__file__).resolve().parents[1]
RULES = rules_mod.load()
DATA = json.loads((ROOT / "web" / "municipalities.json").read_text(encoding="utf-8"))
PLACES = {code: (p["name"], name) for p in DATA["prefectures"] for code, name in p["cities"]}
# 政令指定都市 20 市（総務省の団体コード。web/municipalities.json の名前で引いて確かめる）
DESIGNATED = {
    "011002": "札幌市", "041009": "仙台市", "111007": "さいたま市", "121002": "千葉市", "141003": "横浜市",
    "141305": "川崎市", "141500": "相模原市", "151009": "新潟市", "221007": "静岡市", "221309": "浜松市",
    "231002": "名古屋市", "261009": "京都市", "271004": "大阪市", "271403": "堺市", "281000": "神戸市",
    "331007": "岡山市", "341002": "広島市", "401005": "北九州市", "401307": "福岡市", "431001": "熊本市",
}
REVIEW = {"read_by": "Managed Agent（試験）", "recheck": "match", "date": "2026-11-05"}
SRC = [{"url": "https://www.city.sapporo.jp/example.html", "publisher": "札幌市", "license": "未確認",
        "checked": "2026-11-05"}]


def found(**kw):
    base = {"template": "local-haishoku", "status": "found", "name": "配食サービス", "where": "区役所の保健福祉課",
            "conditions": ["65歳以上でひとり暮らしなど", "食事の用意が難しい"], "sources": SRC, "review": REVIEW}
    return {**base, **kw}


def write(tmp_path: Path, programs: list, code: str = "011002", **top) -> Path:
    d = tmp_path / "local"
    d.mkdir(exist_ok=True)
    body = {"city": code, "name": PLACES[code][1] if code in PLACES else "?", "site": "city.sapporo.jp",
            "programs": programs, **top}
    (d / f"{code}.yaml").write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return d


def load(d: Path):
    return local_rules.load(d, RULES, PLACES)


def test_designated_city_codes_are_in_the_municipality_list():
    assert len(DESIGNATED) == 20
    assert {code: PLACES[code][1] for code in DESIGNATED} == DESIGNATED


def test_no_directory_means_no_city_programs(tmp_path):
    assert load(tmp_path / "local") == {}


def test_loads_found_and_keeps_not_found_as_template(tmp_path):
    nf = {"template": "local-iro", "status": "not_found", "review": {**REVIEW, "read_by": "Managed Agent（試験）"}}
    city = load(write(tmp_path, [found(), nf]))["011002"]
    assert city.name == "札幌市" and set(city.programs) == {"local-haishoku", "local-iro"}
    assert list(city.found()) == ["local-haishoku"]
    p = city.found()["local-haishoku"]
    assert p.name == "配食サービス" and p.conditions == ("65歳以上でひとり暮らしなど", "食事の用意が難しい")
    assert p.review.recheck == "match" and p.review.date.isoformat() == "2026-11-05"


@pytest.mark.parametrize("bad", [
    found(template="kaigo-nintei"),  # 型ではない（国の制度）
    found(template="no-such"),
    found(status="maybe"),
    found(where=""),
    found(conditions=[]),
    found(conditions=["あ" * 61]),
    found(conditions=["条件"] * 6),
    found(sources=[{**SRC[0], "url": "https://www.example.com/a.html"}]),  # その市の公式ページの出典がない
    found(sources=[{**SRC[0], "quote": "原文"}]),  # 利用条件を確かめていない頁の原文は引用しない
    found(review={**REVIEW, "recheck": "mismatch"}),  # 照らし直しで合わなかったものは置かない
    found(review={**REVIEW, "recheck": "fixed"}),  # 直したときは何を直したかを書く
    found(review={"read_by": "Managed Agent"}),
    {"template": "local-iro", "status": "not_found", "name": "家族介護慰労金", "review": REVIEW},
])
def test_bad_city_programs_are_rejected(tmp_path, bad):
    with pytest.raises(rules_mod.RuleError):
        load(write(tmp_path, [bad]))


def test_duplicate_template_is_rejected(tmp_path):
    with pytest.raises(rules_mod.RuleError, match="重複"):
        load(write(tmp_path, [found(), found()]))


@pytest.mark.parametrize("code,top", [
    ("011002", {"city": "11002"}),  # 先頭の 0 を落とした（ファイル名と違う）
    ("011002", {"name": "さっぽろ市"}),
    ("011002", {"site": ""}),
    ("011002", {"site": "https://www.city.sapporo.jp/"}),
    ("999999", {}),  # 一覧にない団体コード
    ("141003", {}),  # 横浜市は市の制度を一つずつ判定している
])
def test_bad_city_files_are_rejected(tmp_path, code, top):
    with pytest.raises(rules_mod.RuleError):
        load(write(tmp_path, [found()], code=code, **top))


def test_fixed_with_note_is_accepted(tmp_path):
    review = {**REVIEW, "recheck": "fixed", "note": "対象の年齢を 70 歳から 65 歳に直した"}
    city = load(write(tmp_path, [found(review=review)]))["011002"]
    assert city.found()["local-haishoku"].review.note.startswith("対象の年齢")


def test_readme_example_loads(tmp_path):
    """rules/local/README.md の書き方の例が、読み込みの検査を通る（例と検査がずれないように）。"""
    import re
    text = (ROOT / "rules" / "local" / "README.md").read_text(encoding="utf-8")
    d = tmp_path / "local"
    d.mkdir()
    (d / "011002.yaml").write_text(re.search(r"```yaml\n(.*?)```", text, re.S).group(1), encoding="utf-8")
    assert list(load(d)["011002"].found()) == ["local-haishoku"]


def test_real_local_files_load():
    """rules/local に置いたファイル（まだ無くてもよい）が、規則と市区町村の一覧に合っている。"""
    cities = load(ROOT / "rules" / "local")
    for code, city in cities.items():
        assert code in DESIGNATED and city.name == DESIGNATED[code]
