from pathlib import Path

import pytest

from todoku import rules as rules_mod
from todoku.engine import DONT_KNOW, evaluate, statuses
from todoku.questions import next_question

FIXTURE = Path(__file__).parent / "fixtures" / "rules"


@pytest.fixture(scope="module")
def rs():
    return rules_mod.load(FIXTURE)


def test_order_puts_prerequisites_first(rs):
    order = rs.order
    assert order.index("base") < order.index("needs_level") < order.index("tax_deduction")
    assert rs.dependents["base"] == 1
    assert rs.weight("needs_level") == 2 + 1


def test_nothing_known_is_unknown(rs):
    assert set(statuses(rs, {}).values()) == {"unknown"}


def test_false_dominates_unknown_in_all(rs):
    # 65 歳未満なら、ほかが分からなくても当てはまらない
    s = statuses(rs, {"age65": False})
    assert s["base"] == "ineligible"
    assert s["city_alone"] == "ineligible"


def test_done_when(rs):
    s = statuses(rs, {"age65": True, "level": "low"})
    assert s["base"] == "done"
    assert s["needs_level"] == "eligible"


def test_via_makes_after_prereq(rs):
    # 認定がなくても、認定を受ければ使える給付は「先に手続きが要る」
    res = evaluate(rs, {"age65": True, "level": "none"})
    assert res["base"].status == "eligible"
    assert res["needs_level"].status == "after_prereq"
    assert res["needs_level"].waiting_on == ["base"]
    # 前提の制度を「先に手続きが要る」で待つ制度も、同じく待つ
    assert res["tax_deduction"].status == "after_prereq"
    assert res["tax_deduction"].waiting_on == ["needs_level"]


def test_via_is_false_when_prereq_ineligible(rs):
    s = statuses(rs, {"age65": False, "level": "none"})
    assert s["needs_level"] == "ineligible"
    assert s["tax_deduction"] == "ineligible"


def test_any_true_wins(rs):
    s = statuses(rs, {"age65": True, "level": "high"})
    assert s["city_alone"] == "eligible"
    s = statuses(rs, {"age65": True, "level": "low", "alone": False})
    assert s["city_alone"] == "ineligible"
    s = statuses(rs, {"age65": True, "level": "low"})
    assert s["city_alone"] == "unknown"


def test_dont_know_stays_unknown_and_is_reported(rs):
    res = evaluate(rs, {"age65": True, "level": "low", "alone": DONT_KNOW})
    assert res["city_alone"].status == "unknown"
    assert res["city_alone"].missing_facts == ["alone"]


def test_leaves_record_reasons(rs):
    res = evaluate(rs, {"age65": True, "level": "high"})
    leaves = {leaf.label: leaf.state for leaf in res["needs_level"].leaves}
    assert leaves == {"65歳以上": "T", "認定あり": "T"}


def test_first_question_is_the_one_that_moves_most(rs):
    q = next_question(rs, {})
    assert q.fact == "age65"


def test_question_skips_facts_that_change_nothing(rs):
    # noise はどの制度にも出てこないので候補にならない。分かったあとは聞かない
    q = next_question(rs, {"age65": True})
    assert q.fact == "level"
    q = next_question(rs, {"age65": True, "level": "high"})
    assert q is None


def test_question_never_repeats_dont_know(rs):
    q = next_question(rs, {"age65": True, "level": "low"})
    assert q.fact == "alone"
    assert next_question(rs, {"age65": True, "level": "low", "alone": DONT_KNOW}) is None


def test_question_cap(rs):
    assert next_question(rs, {}, asked=3) is None


def test_suggestion_turns_into_confirmation(rs):
    q = next_question(rs, {}, suggestions={"age65": True})
    assert q.fact == "age65" and q.suggested is True


def test_quote_only_from_pdl(tmp_path):
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "rules")
    path = tmp_path / "rules" / "programs" / "city_alone.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "checked: 2026-11-02}", 'checked: 2026-11-02, quote: "町田市の文"}'
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(rules_mod.RuleError, match="引用しない"):
        rules_mod.load(tmp_path / "rules")


def test_cycle_is_rejected(tmp_path):
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "rules")
    path = tmp_path / "rules" / "programs" / "base.yaml"
    text = path.read_text(encoding="utf-8").replace(
        "label: 認定なし}", "label: 認定なし}\n    - {prereq: tax_deduction, label: 循環}"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(rules_mod.RuleError, match="循環"):
        rules_mod.load(tmp_path / "rules")
