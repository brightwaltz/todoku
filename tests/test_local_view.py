"""T-075b：その市の事業（rules/local）を、結果の札と /city/ の頁で型の札の代わりに出す。データは tmp に書く。"""
import os

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku import local_rules  # noqa: E402
from todoku import rules as rules_mod  # noqa: E402

client = TestClient(app_mod.app)
SAPPORO, SENDAI = "011002", "041009"
FACTS = {"lives_in_machida": False, "household": "alone", "residence": "home"}
REVIEW = {"read_by": "Managed Agent（試験）", "recheck": "match", "date": "2026-11-05"}
CITY_SRC = {"url": "https://www.city.sapporo.jp/example.html", "publisher": "札幌市", "title": "配食サービス（試験）",
            "license": "利用条件を確かめていない（引用しない）", "checked": "2026-11-05"}
HAISHOKU = {"template": "local-haishoku", "status": "found", "name": "札幌市の配食サービス（試験）",
            "where": "お住まいの区の保健福祉課（試験）", "conditions": ["65歳以上でひとり暮らしなど（試験）", "食事の用意が難しい（試験）"],
            "sources": [CITY_SRC], "review": REVIEW}
IRO = {"template": "local-iro", "status": "not_found", "review": REVIEW}


@pytest.fixture
def sapporo(tmp_path, monkeypatch):
    d = tmp_path / "local"
    d.mkdir()
    body = {"city": SAPPORO, "name": "札幌市", "site": "city.sapporo.jp", "programs": [HAISHOKU, IRO]}
    (d / f"{SAPPORO}.yaml").write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(app_mod, "LOCAL", local_rules.load(d, app_mod.RULES, app_mod.PLACES))


def cards(code: str) -> dict[str, dict]:
    view = app_mod.step_view(dict(FACTS), {}, "ご本人", 3, code)
    return {r["id"]: r for r in view["results"]}


def test_app_reads_the_rules_local_directory():
    # 起動のときに rules/local（TODOKU_RULES_DIR の下）を読む。本物のデータが入るまでは空で、画面は今までどおり
    assert app_mod.LOCAL == local_rules.load(rules_mod.rules_dir() / "local", app_mod.RULES, app_mod.PLACES)


def test_found_template_becomes_the_city_program(sapporo, monkeypatch):
    r = cards(SAPPORO)["local-haishoku"]
    assert r["name"] == HAISHOKU["name"]
    assert r["apply"] == {"where": HAISHOKU["where"]}
    assert r["local"]["mark"] == "Claude が公式頁から書き、照らし直したもの（2026-11-05）"
    assert r["local"]["conditions"] == HAISHOKU["conditions"] and r["local"]["city"] == "札幌市"
    assert r["local"]["recheck"] == "原文の行と合っていた"
    # その市の頁が先、型の出典（説明の数字の全国調査）は後ろに残す
    assert r["sources"][0]["url"] == CITY_SRC["url"]
    assert r["sources"][1:] and all("city.sapporo.jp" not in s["url"] for s in r["sources"][1:])
    # 申請メモの題と窓口の一言も、札と同じ名前にする
    assert r["memo"]["title"] == f"申請メモ：{HAISHOKU['name']}"
    assert HAISHOKU["name"] in r["memo"]["say"] and r["memo"]["where"] == HAISHOKU["where"]


def test_judgement_stays_the_template(sapporo, monkeypatch):
    # 判定の条件・状態・説明・並び順は型のまま（D-0008 の採点と run_eval の数を変えない）
    after = app_mod.step_view(dict(FACTS), {}, "ご本人", 3, SAPPORO)
    monkeypatch.setattr(app_mod, "LOCAL", {})
    before = app_mod.step_view(dict(FACTS), {}, "ご本人", 3, SAPPORO)
    assert [r["id"] for r in after["results"]] == [r["id"] for r in before["results"]]
    assert after["counts"] == before["counts"]
    a = next(r for r in after["results"] if r["id"] == "local-haishoku")
    b = next(r for r in before["results"] if r["id"] == "local-haishoku")
    for key in ("status", "reasons", "summary", "level", "missing", "at_application", "cost"):
        assert a[key] == b[key], key
    assert b["local"] is None and b["name"] == app_mod.RULES.programs["local-haishoku"].name


def test_not_found_and_other_cities_stay_the_template(sapporo):
    here = cards(SAPPORO)
    assert here["local-iro"]["local"] is None
    assert here["local-iro"]["name"] == app_mod.RULES.programs["local-iro"].name
    there = cards(SENDAI)["local-haishoku"]
    assert there["local"] is None and there["name"] == app_mod.RULES.programs["local-haishoku"].name
    # 市区町村が分からない人にも出さない
    assert cards("")["local-haishoku"]["local"] is None


def test_city_page_shows_the_city_program_with_the_mark(sapporo):
    html = client.get(f"/city/{SAPPORO}").text
    assert HAISHOKU["name"] in html and HAISHOKU["where"] in html
    assert all(c in html for c in HAISHOKU["conditions"])
    assert "Claude が公式頁から書き、照らし直したもの（2026-11-05）" in html
    assert CITY_SRC["url"] in html
    assert html.count('class="local-mark"') == 1  # 見つからなかった型（local-iro）には付けない
    assert app_mod.RULES.programs["local-iro"].name in html
    assert "別の読み手が原文と照らし直したもの" in html


def test_city_page_without_data_is_unchanged(sapporo):
    html = client.get(f"/city/{SENDAI}").text
    assert "local-mark" not in html and "別の読み手が原文と照らし直したもの" not in html
    assert app_mod.RULES.programs["local-haishoku"].name in html
