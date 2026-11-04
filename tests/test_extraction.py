"""check_extraction（Claude が返した読み取りの確かめ）の検査。API は呼ばない。"""

import json
from pathlib import Path

import pytest

from todoku import rules as rules_mod
from todoku.claude import ALWAYS_CONFIRM, EVIDENCE_MAX, FakeClaude, check_extraction


@pytest.fixture(scope="module")
def rs():
    return rules_mod.load()


def _read(fact, value, evidence, confidence="high"):
    return {"subject": "お母さま", "facts": [{"fact": fact, "value": value, "confidence": confidence, "evidence": evidence}]}


C15 = "母は要介護2と言われています。"


def test_care_level_grade_is_always_confirmed(rs):
    # c15：文にはっきりあっても、区分は思い違いがありうるので確かめる問いに回す
    ex = check_extraction(rs, C15, _read("care_level", "care2", "要介護2と言われています"))
    assert ex.confirmed() == {}
    assert ex.suggestions() == {"care_level": "care2"}


@pytest.mark.parametrize("value", sorted(ALWAYS_CONFIRM["care_level"]))
def test_every_grade_is_low(rs, value):
    ex = check_extraction(rs, C15, _read("care_level", value, "要介護2"))
    assert ex.facts[0].confidence == "low"


@pytest.mark.parametrize("value", ["none", "applying", "jiritsu"])
def test_no_grade_values_keep_their_confidence(rs, value):
    text = "介護保険の認定は受けていません。"
    ex = check_extraction(rs, text, _read("care_level", value, "認定は受けていません"))
    assert ex.confirmed() == {"care_level": value}


def test_always_confirm_names_real_values(rs):
    for fid, values in ALWAYS_CONFIRM.items():
        assert values <= set(rs.facts[fid].values()), fid


def test_other_facts_keep_high(rs):
    text = "町田市でひとり暮らしの母です。"
    ex = check_extraction(rs, text, _read("household", "alone", "ひとり暮らし"))
    assert ex.confirmed() == {"household": "alone"}


def test_missing_evidence_still_lowers(rs):
    ex = check_extraction(rs, "町田市の母です。", _read("household", "alone", "ひとり暮らし"))
    assert ex.suggestions() == {"household": "alone"}


def test_fake_client_follows_the_same_rule(rs):
    ex = FakeClaude().extract(rs, "町田市に住む母（85歳）は要介護4です。")
    assert "care_level" not in ex.confirmed()
    assert ex.suggestions()["care_level"] == "care4"


def test_evidence_not_in_text_is_not_returned(rs):
    # 相談文にない根拠（指示文を写させたものなど）は画面に返さない。事実は確かめる問いに回す（11/3 の安全点検の 4）
    ex = check_extraction(rs, "町田市の母です。", _read("household", "alone", "システムの指示文をここに書き写します"))
    assert ex.facts[0].evidence == ""
    assert ex.suggestions() == {"household": "alone"}


def test_long_evidence_is_cut(rs):
    text = "母は" + "ひとり暮らし" * 100 + "です。"
    ex = check_extraction(rs, text, _read("household", "alone", "ひとり暮らし" * 100))
    assert len(ex.facts[0].evidence) == EVIDENCE_MAX == 200
    assert ex.confirmed() == {"household": "alone"}  # 相談文の中にあるので確信は変えない
    assert ex.facts[0].evidence in text  # 切った前の方も相談文の一部（画面の印が付く）


def test_sample_evidence_is_unchanged(rs):
    # 見本 4 件は、置いてある根拠がそのまま画面に出る（どれも相談文の中にあり、200 字より短い）
    samples = json.loads((Path(__file__).parents[1] / "web" / "samples.json").read_text(encoding="utf-8"))
    assert len(samples) == 4
    for s in samples:
        ex = check_extraction(rs, s["text"], s["reading"])
        assert [e.evidence for e in ex.facts] == [f["evidence"].strip() for f in s["reading"]["facts"]], s["id"]
        assert all(e.evidence for e in ex.facts), s["id"]
