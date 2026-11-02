"""国の制度の手続（行政手続データの手続ID・根拠法令・条項）の検査。

規則ファイルの procedures は、デジタル庁の行政手続データを MCP サーバーで引いて写したもの。
tests/fixtures/procedures_r7.json は、同じデータを CLI で手続ID ごとに引き直した控え（2026-11-03）。
規則ファイルの値が控えと一字でも違えば落ちる。
"""

import json
import shutil
from pathlib import Path

import pytest

from todoku import rules as rules_mod

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures" / "rules"
SNAPSHOT = json.loads((HERE / "fixtures" / "procedures_r7.json").read_text(encoding="utf-8"))
RECORDS = {r["手続ID"]: r for r in SNAPSHOT["records"]}


@pytest.fixture(scope="module")
def rs():
    return rules_mod.load()


def test_every_national_program_has_procedures(rs):
    for p in rs.programs.values():
        if p.level == "national":
            assert p.procedures, f"{p.id}: 国の制度に手続がない"
        else:
            assert not p.procedures, f"{p.id}: 町田市の制度に手続がある"


def test_procedures_match_the_snapshot(rs):
    for p in rs.programs.values():
        for x in p.procedures:
            record = RECORDS.get(x.id)
            assert record is not None, f"{p.id}: 手続ID {x.id} が控えにない"
            assert x.name == record["手続名"], (p.id, x.id)
            assert x.law == record["法令名"], (p.id, x.id)
            assert x.article == record["根拠条項号"], (p.id, x.id)
            assert x.attachments == record["申請時に添付させる書類"], (p.id, x.id)


def test_only_tax_deductions_use_general_procedures(rs):
    general = {p.id for p in rs.programs.values() for x in p.procedures if x.match == "general"}
    assert general == {"shogaisha-kojo", "iryohi-kojo-omutsu"}
    for p in rs.programs.values():
        for x in p.procedures:
            if x.match == "general":
                assert x.note, (p.id, x.id)


def test_snapshot_states_source_and_editing():
    assert "デジタル庁" in SNAPSHOT["source"] and "公共データ利用規約" in SNAPSHOT["source"]
    assert "編集・加工" in SNAPSHOT["edited"]


def _broken(tmp_path, name, old, new):
    shutil.copytree(FIXTURE, tmp_path / "rules")
    path = tmp_path / "rules" / "programs" / name
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new), encoding="utf-8")
    return tmp_path / "rules"


PROC = '\nprocedures:\n  - {id: "44977", name: 手続, law: 法, article: 第1条}\n'


def test_fixture_with_procedure_loads(tmp_path):
    rs = rules_mod.load(_broken(tmp_path, "base.yaml", "apply: {where: 市役所}\n", "apply: {where: 市役所}\n" + PROC))
    assert rs.programs["base"].procedures[0].id == "44977"


def test_numeric_id_is_rejected(tmp_path):
    bad = PROC.replace('"44977"', "44977")
    with pytest.raises(rules_mod.RuleError, match="文字列"):
        rules_mod.load(_broken(tmp_path, "base.yaml", "apply: {where: 市役所}\n", "apply: {where: 市役所}\n" + bad))


def test_general_without_note_is_rejected(tmp_path):
    bad = PROC.replace("article: 第1条}", "article: 第1条, match: general}")
    with pytest.raises(rules_mod.RuleError, match="note"):
        rules_mod.load(_broken(tmp_path, "base.yaml", "apply: {where: 市役所}\n", "apply: {where: 市役所}\n" + bad))


def test_city_program_cannot_have_procedures(tmp_path):
    path = FIXTURE / "programs" / "city_alone.yaml"
    last = path.read_text(encoding="utf-8").splitlines()[-1] + "\n"
    with pytest.raises(rules_mod.RuleError, match="国の制度だけ"):
        rules_mod.load(_broken(tmp_path, "city_alone.yaml", last, last + PROC))
