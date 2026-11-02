"""check_extraction（Claude が返した読み取りの確かめ）の検査。API は呼ばない。"""

import pytest

from todoku import rules as rules_mod
from todoku.claude import ALWAYS_CONFIRM, FakeClaude, check_extraction


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
