"""本物の規則ファイル（rules/）の検査と、場面ごとの判定。

場面は公式ページの条件から作った。条件を変えたら、ここも合わせて直す。
"""

import pytest

from todoku import rules as rules_mod
from todoku.engine import DONT_KNOW, evaluate, statuses
from todoku.questions import next_question, referenced_facts

MACHIDA_HOME_75 = {"lives_in_machida": True, "age_band": "age_75_plus", "residence": "home"}


@pytest.fixture(scope="module")
def rs():
    return rules_mod.load()


def test_programs_by_level(rs):
    # 国 17（全国で判定）・町田市 10・市区町村の型 5（町田市以外で、あるかを窓口で確かめる）
    levels = [p.level for p in rs.programs.values()]
    assert len(levels) == 32
    assert levels.count("national") == 17 and levels.count("machida") == 10 and levels.count("local") == 5
    assert sorted(p.number for p in rs.programs.values()) == list(range(1, 33))


def test_every_program_has_dated_official_sources(rs):
    for p in rs.programs.values():
        assert p.sources, p.id
        for s in p.sources:
            assert s.checked.isoformat() in ("2026-11-02", "2026-11-03"), p.id
            if "city.machida.tokyo.jp" in s.url:
                assert not s.quote, f"{p.id}: 町田市のページは引用しない"
        assert p.apply.get("where"), p.id


def test_every_fact_is_used(rs):
    assert set(rs.facts) == referenced_facts(rs)


def test_demo_not_certified_shows_the_chain(rs):
    # 「母が退院してから家で転びやすくなった」：町田市、75歳以上、自宅、認定なし、手すりを考えている
    facts = {**MACHIDA_HOME_75, "care_level": "none", "plans_home_modification": True}
    res = evaluate(rs, facts)
    assert res["kaigo-nintei"].status == "eligible"
    assert res["jutaku-kaishu"].status == "after_prereq"
    assert res["jutaku-kaishu"].waiting_on == ["kaigo-nintei"]
    # 障害者控除は、町田市の認定書を待ち、その認定書は要介護認定を待つ
    assert res["machida-shogaisha-nintei"].status == "after_prereq"
    assert res["shogaisha-kojo"].status == "after_prereq"
    assert res["shogaisha-kojo"].waiting_on == ["machida-shogaisha-nintei"]


def test_not_certified_never_asks_about_services(rs):
    facts = {**MACHIDA_HOME_75, "care_level": "none"}
    asked = []
    for i in range(10):
        q = next_question(rs, facts, asked=i, max_questions=10)
        if q is None:
            break
        asked.append(q.fact)
        facts[q.fact] = DONT_KNOW
    assert "uses_care_services" not in asked
    assert "shortstay_or_facility" not in asked
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "none"})
    assert s["kogaku-kaigo-service"] == "after_prereq"


def test_certified_care2_home_modification(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "care2", "plans_home_modification": True})
    assert s["kaigo-nintei"] == "done"
    assert s["jutaku-kaishu"] == "eligible"
    assert s["machida-shogaisha-nintei"] == "eligible"


def test_applying_can_pre_apply_home_modification(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "applying", "plans_home_modification": True})
    assert s["jutaku-kaishu"] == "eligible"
    assert s["fukushi-yogu-konyu"] in ("after_prereq", "unknown")


def test_support_level_is_not_enough_for_tax_certificate(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "support1"})
    assert s["machida-shogaisha-nintei"] == "ineligible"
    assert s["shogaisha-kojo"] == "ineligible"
    assert s["machida-haishoku"] == "ineligible"


def test_paper_diapers_needs_care4_and_tax_exempt(rs):
    base = {**MACHIDA_HOME_75, "care_level": "care4", "tax_status": "household_exempt", "on_welfare": False}
    assert statuses(rs, base)["machida-kami-omutsu"] == "eligible"
    assert statuses(rs, {**base, "care_level": "care3"})["machida-kami-omutsu"] == "ineligible"
    assert statuses(rs, {**base, "tax_status": "self_taxed"})["machida-kami-omutsu"] == "ineligible"
    assert statuses(rs, {**base, "on_welfare": True})["machida-kami-omutsu"] == "ineligible"
    assert statuses(rs, {**base, "residence": "facility"})["machida-kami-omutsu"] == "ineligible"


def test_facility_resident(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "residence": "facility", "care_level": "care3",
                      "tax_status": "household_exempt", "plans_home_modification": True})
    assert s["jutaku-kaishu"] == "ineligible"
    assert s["futan-gendogaku"] == "eligible"


def test_outside_machida_only_national(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "lives_in_machida": False, "care_level": "care2"})
    for pid, p in rs.programs.items():
        if p.level == "machida":
            assert s[pid] == "ineligible", pid
    assert s["kaigo-nintei"] == "done"


def test_under_65_city_programs_ineligible(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "age_band": "age_40_64", "care_level": "none"})
    assert s["kaigo-nintei"] == "eligible"  # 特定疾病のときに限る（申請で確かめる）
    for pid in ("machida-gps", "machida-kasai-yobo", "machida-hochoki", "machida-shogaisha-nintei"):
        assert s[pid] == "ineligible", pid


def test_diaper_deduction_has_two_routes(rs):
    facts = {**MACHIDA_HOME_75, "uses_diapers": True, "bedridden_6m": True, "care_level": "none"}
    res = evaluate(rs, facts)
    # 認定がなくても、医師の証明書の道がある
    assert res["iryohi-kojo-omutsu"].status == "after_prereq"
    facts["care_level"] = "care5"
    res = evaluate(rs, facts)
    assert res["machida-omutsu-kakunin"].status == "eligible"
    assert res["iryohi-kojo-omutsu"].status == "after_prereq"
    assert "machida-omutsu-kakunin" in res["iryohi-kojo-omutsu"].waiting_on


def test_gps(rs):
    base = {**MACHIDA_HOME_75, "dementia_diagnosis": True, "wandering_risk": True}
    assert statuses(rs, base)["machida-gps"] == "eligible"
    assert statuses(rs, {**base, "residence": "hospital"})["machida-gps"] == "ineligible"
    assert statuses(rs, {**base, "wandering_risk": False})["machida-gps"] == "ineligible"


def test_care_leave_depends_only_on_the_caregiver(rs):
    assert statuses(rs, {"caregiver_employee": True})["kaigo-kyugyo-kyufu"] == "eligible"
    assert statuses(rs, {"caregiver_employee": False})["kaigo-kyugyo-kyufu"] == "ineligible"


def test_hearing_aid_tax_rule(rs):
    base = {**MACHIDA_HOME_75, "hearing_difficulty": True}
    assert statuses(rs, {**base, "tax_status": "self_exempt"})["machida-hochoki"] == "eligible"
    assert statuses(rs, {**base, "tax_status": "self_taxed"})["machida-hochoki"] == "ineligible"
    assert statuses(rs, base)["machida-hochoki"] == "unknown"


def test_first_question_is_certification(rs):
    q = next_question(rs, dict(MACHIDA_HOME_75))
    assert q.fact == "care_level"


# ---- 全国対応（2026-11-03 に足した国の 7 制度と市区町村の型 5 件） ----

YOKOHAMA_DEMENTIA = {"lives_in_machida": False, "age_band": "age_75_plus", "residence": "home",
                     "dementia_diagnosis": True, "wandering_risk": True}


def test_outside_machida_shows_local_types_to_check(rs):
    s = statuses(rs, YOKOHAMA_DEMENTIA)
    assert s["local-haikai"] == "unknown" and s["local-koken"] == "unknown"  # あるかを窓口で確かめる
    assert s["machida-gps"] == "ineligible"
    assert s["nichijo-jiritsu-shien"] == "eligible" and s["jiritsu-shien-iryo"] == "eligible"  # 国の制度は全国で判定


def test_machida_shows_city_programs_not_types(rs):
    s = statuses(rs, {**YOKOHAMA_DEMENTIA, "lives_in_machida": True})
    assert all(s[pid] == "ineligible" for pid, p in rs.programs.items() if p.level == "local")


def test_hearing_aid_needs_the_certificate_first(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "hearing_difficulty": True, "hearing_certifiable": True})
    assert s["shintai-techo-chokaku"] == "eligible" and s["hosougu-hochoki"] == "after_prereq"
    s = statuses(rs, {**MACHIDA_HOME_75, "hearing_difficulty": True, "hearing_certifiable": False})
    assert s["shintai-techo-chokaku"] == "ineligible" and s["hosougu-hochoki"] == "ineligible"


def test_care_leave_comes_with_the_benefit(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "caregiver_employee": True})
    assert s["kaigo-kyuka"] == "eligible" and s["kaigo-kyugyo-kyufu"] == "eligible"


def test_special_allowance_is_for_people_at_home(rs):
    assert statuses(rs, {**MACHIDA_HOME_75, "severe_constant_care": True})["tokubetsu-shogaisha-teate"] == "eligible"
    assert statuses(rs, {**MACHIDA_HOME_75, "severe_constant_care": True, "residence": "facility"})["tokubetsu-shogaisha-teate"] == "ineligible"


def test_city_wording_has_a_version_for_other_cities(rs):
    # 国の制度の申請先・持ち物・時期に町田市の書き方があれば、町田市以外の人向けの書き方（elsewhere）もある
    for p in rs.programs.values():
        if p.level != "national":
            continue
        for key in ("apply", "bring", "timing"):
            if "町田" in str(getattr(p, key)):
                assert key in p.elsewhere, f"{p.id}: {key} に町田市以外の書き方がない"
        assert "町田" not in str(p.elsewhere), p.id
        assert "町田" not in p.summary, p.id


def test_tax_certificate_outside_machida_is_a_step(rs):
    outside = {"lives_in_machida": False, "age_band": "age_75_plus", "residence": "home"}
    assert statuses(rs, outside)["shogaisha-kojo"] == "after_prereq"
    assert statuses(rs, {**outside, "age_band": "age_40_64"})["shogaisha-kojo"] == "ineligible"


def test_loan_is_open_to_elderly_households_with_income(rs):
    # 生活福祉資金の高齢者世帯（65 歳以上の人がいる世帯）には、所得の限定が書かれていない
    s = statuses(rs, {**MACHIDA_HOME_75, "tax_status": "self_taxed", "plans_home_modification": True})
    assert s["seikatsu-fukushi-shikin"] == "eligible"


def test_daily_life_support_does_not_need_a_diagnosis(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "dementia_diagnosis": False, "money_management_difficulty": True})
    assert s["nichijo-jiritsu-shien"] == "eligible"
    s = statuses(rs, {**MACHIDA_HOME_75, "dementia_diagnosis": False, "money_management_difficulty": False})
    assert s["nichijo-jiritsu-shien"] == "ineligible"
