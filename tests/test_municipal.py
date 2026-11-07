"""市区町村の公式サイトから自動で集めた制度（第 3 の層、rules/municipal）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from todoku import engine, municipal, rules
from todoku.rules import RuleError

ROOT = Path(__file__).resolve().parents[1]
_M = json.loads((ROOT / "web" / "municipalities.json").read_text())["prefectures"]
# 市区町村（6 桁）と都道府県（2 桁）の名前。app.py の MUNI と同じ表
NAMES = {**{c: n for p in _M for c, n in p["cities"]}, **{p["code"]: p["name"] for p in _M}}
RULES = rules.load()


def _city(tmp_path: Path, programs: list[dict], code: str = "092011") -> municipal.CityPrograms:
    doc = {"city": code, "name": NAMES[code], "site": "www.city.utsunomiya.lg.jp", "site_level": "lg.jp",
           "review": {"read_by": "テスト", "checked_by": "機械", "date": "2026-11-07"}, "programs": programs}
    path = tmp_path / f"{code}.yaml"
    path.write_text(yaml.safe_dump(doc, allow_unicode=True))
    return municipal.load_city(path, NAMES)


def _p(**who) -> dict:
    return {"category": "kaigo-yohin", "name": "紙おむつの給付", "name_on_page": "紙おむつ給付事業", "summary": "紙おむつを届ける",
            "where": "高齢福祉課", "url": "https://www.city.utsunomiya.lg.jp/x.html", "who": who, "confidence": "high"}


def _status(city, facts):
    statuses = engine.statuses(RULES, facts)
    return [r.status for r in municipal.evaluate_city(RULES, city, facts, statuses)]


def test_compiled_conditions_are_judged_by_rules(tmp_path):
    city = _city(tmp_path, [_p(age_min=65, care_levels=["care3", "care4", "care5"], at_home=True, flags=["uses_diapers"])])
    base = {"age_band": "age_75_plus", "residence": "home", "uses_diapers": True}
    # 人が照らしていない層なので、条件がそろっても「当てはまる見込み」にせず「確かめる」にする
    assert _status(city, {**base, "care_level": "care4"}) == ["unknown"]
    # 値にした条件で外れたら「当てはまらない」
    assert _status(city, {**base, "care_level": "care1"}) == ["ineligible"]
    assert _status(city, {**base, "care_level": "care4", "residence": "facility"}) == ["ineligible"]
    assert _status(city, {**base, "care_level": "certified_unknown"}) == ["unknown"]
    assert _status(city, {}) == ["unknown"]
    # 認定がまだの人は、要介護認定（確かな層の kaigo-nintei）を前提の手続きとして理由に出す
    r = municipal.evaluate_city(RULES, city, {**base, "care_level": "none", "needs_daily_help": True},
                                engine.statuses(RULES, {**base, "care_level": "none", "needs_daily_help": True}))[0]
    assert r.status == "unknown" and any(leaf.prereq == "kaigo-nintei" for leaf in r.leaves)


def test_age_inside_a_band_is_unsure():
    node = municipal.age_node(60)
    assert node["in"] == ["age_65_69", "age_70_74", "age_75_plus"] and node["unsure"] == ["age_40_64"]
    node = municipal.age_node(80)
    assert node["in"] == [] and node["unsure"] == ["age_75_plus"]


def test_no_mappable_condition_is_asked_at_the_window(tmp_path):
    city = _city(tmp_path, [_p()])
    assert _status(city, {"age_band": "age_75_plus"}) == ["unknown"]


def test_bad_values_and_names_are_rejected(tmp_path):
    with pytest.raises(RuleError):
        _city(tmp_path, [_p(care_levels=["要介護3"])])
    doc = {"city": "092011", "name": "別の名前", "review": {"date": "2026-11-07"}, "programs": []}
    (tmp_path / "092011.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True))
    with pytest.raises(RuleError):
        municipal.load_city(tmp_path / "092011.yaml", NAMES)


def test_real_files_load_and_stay_out_of_the_scored_set():
    """入れたファイルは全部読め、制度の番号（採点する 1〜32）と問いの重みに入らない。"""
    loaded = municipal.load(ROOT / "rules" / "municipal", NAMES)
    for city in loaded.values():
        assert city.code not in ("132098", "141003")  # 町田市・横浜市は市の制度を一つずつ判定している
        assert len(city.code) in (2, 6)
        for p in city.programs:
            assert p.number == 0 and p.weight == 0 and p.level == "municipal"
            assert p.id not in RULES.programs
