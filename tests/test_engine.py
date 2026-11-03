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
    # 認定がなくても、認定を受ければ使える給付は「先に手続きをすると使える」
    res = evaluate(rs, {"age65": True, "level": "none"})
    assert res["base"].status == "eligible"
    assert res["needs_level"].status == "after_prereq"
    assert res["needs_level"].waiting_on == ["base"]
    # 前提の制度を「先に手続きをすると使える」で待つ制度も、同じく待つ
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


def test_any_shows_only_the_branch_that_passed(rs):
    # 当てはまるときは通った枝の理由だけ。通らないときは、どの道もだめだったと分かるよう全部
    res = evaluate(rs, {"age65": True, "alone": False, "level": "high"})
    assert {leaf.label: leaf.state for leaf in res["city_alone"].leaves} == {"65歳以上": "T", "重い認定": "T"}
    res = evaluate(rs, {"age65": True, "alone": False, "level": "low"})
    assert {leaf.label for leaf in res["city_alone"].leaves} == {"65歳以上", "ひとり暮らし", "重い認定"}


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


LOCAL_TYPE = """id: local_type
number: 9
name: 市区町村の型
level: local
kind: review
summary: 試験用の、市区町村にあるかもしれない事業
eligibility:
  all:
    - {fact: age65, is: true, label: 65歳以上}
ASK_AT
apply: {where: 市区町村の窓口}
sources:
  - {url: "https://example.lg.jp/a", publisher: 県, license: 利用条件を確かめていない, checked: 2026-11-03}
"""


def _with_local(tmp_path, ask_at_line):
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "rules")
    (tmp_path / "rules" / "programs" / "local_type.yaml").write_text(LOCAL_TYPE.replace("ASK_AT", ask_at_line), encoding="utf-8")
    return rules_mod.load(tmp_path / "rules")


def test_ask_at_stays_unknown_and_is_never_asked(tmp_path):
    rs = _with_local(tmp_path, "    - {ask_at: 市区町村の窓口, label: 市区町村がこの事業をしている}")
    r = evaluate(rs, {"age65": True})["local_type"]
    assert r.status == "unknown"
    assert [(leaf.label, leaf.state) for leaf in r.leaves] == [("65歳以上", "T"), ("市区町村がこの事業をしている", "U")]
    assert r.missing_facts == []  # 聞ける事実がないので、問いにならない
    assert evaluate(rs, {"age65": False})["local_type"].status == "ineligible"


def test_local_type_without_ask_at_is_rejected(tmp_path):
    with pytest.raises(rules_mod.RuleError, match="ask_at"):
        _with_local(tmp_path, "    - {fact: alone, is: true, label: ひとり暮らし}")


def test_statuses_matches_evaluate_on_real_rules():
    """statuses は evaluate と同じ状態を返す（T-047。問いの値打ちを数える側は、葉を作らない別の道で速くした）。

    本物の規則で、事例 30 件の答えと見本 4 件の読み取りから、事実を抜いたり「わからない」や別の値に替えたりした組を試す。
    """
    import json
    import random

    import yaml

    root = Path(__file__).resolve().parents[1]
    rs = rules_mod.load()
    cases = yaml.safe_load((root / "eval" / "cases" / "cases.yaml").read_text(encoding="utf-8"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    starts = [{k: v for k, v in (c.get("answers") or {}).items() if k in rs.facts} for c in cases]
    for s in json.loads((root / "web" / "samples.json").read_text(encoding="utf-8")):
        # 保存した読み取りは Claude の返した形のまま（はい・いいえは "true"・"false" の文字）
        starts.append({e["fact"]: {"true": True, "false": False}.get(e["value"], e["value"])
                       for e in s["reading"]["facts"] if e["fact"] in rs.facts})
    starts.append({})
    assert len(starts) >= 35 and sum(1 for s in starts if s) >= 34
    rng = random.Random(47)
    tried = seen = 0
    states = set()
    for start in starts:
        for _ in range(40):
            facts = {}
            for fid, v in start.items():
                roll = rng.random()
                if roll < 0.2:
                    continue
                facts[fid] = DONT_KNOW if roll < 0.3 else rng.choice(rs.facts[fid].values()) if roll < 0.45 else v
            for fid in rng.sample(sorted(rs.facts), 3):  # 事例にない事実も、たまに入れる
                facts.setdefault(fid, rng.choice(rs.facts[fid].values()))
            full = evaluate(rs, facts)
            got = statuses(rs, facts)
            assert list(got.items()) == [(pid, r.status) for pid, r in full.items()], facts
            tried += 1
            states.update(got.values())
    assert tried == len(starts) * 40
    # 6 つの状態がどれも出る組を試している（どれかの道を通らないまま一致しても意味がないため）
    assert states == {"done", "pending", "eligible", "after_prereq", "unknown", "ineligible"}


NOT_PROGRAM = """id: not_program
number: 8
name: not と any と step の試験
level: national
kind: review
summary: 試験用
eligibility:
  any:
    - all:
        - not: {any: [{fact: alone, is: true, label: ひとり暮らし}, {fact: level, eq: high, label: 認定が重い}]}
        - {fact: age65, is: true, label: 65歳以上}
    - all:
        - {prereq: needs_level, label: 先に認定が要る給付}
        - {step: 医師に書いてもらう, label: 証明書}
apply: {where: 市役所}
sources:
  - {url: "https://example.go.jp/n", publisher: 国, license: PDL1.0, checked: 2026-11-03}
"""


def test_statuses_matches_evaluate_with_not(tmp_path):
    """本物の規則には not がないので、試験用の規則に not・any・step を使う制度を足して、事実の組を全部試す（T-047）。"""
    import itertools
    import shutil

    shutil.copytree(FIXTURE, tmp_path / "rules")
    (tmp_path / "rules" / "programs" / "not_program.yaml").write_text(NOT_PROGRAM, encoding="utf-8")
    rs = rules_mod.load(tmp_path / "rules")
    choices = {fid: [None, DONT_KNOW, *rs.facts[fid].values()] for fid in rs.facts}
    seen = set()
    for combo in itertools.product(*choices.values()):
        facts = {fid: v for fid, v in zip(choices, combo) if v is not None}
        got = statuses(rs, facts)
        assert list(got.items()) == [(pid, r.status) for pid, r in evaluate(rs, facts).items()], facts
        seen.add(got["not_program"])
    assert seen == {"eligible", "after_prereq", "unknown", "ineligible"}
