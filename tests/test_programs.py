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
    # 11/3 に東京都 1・横浜市 7 を足した（33〜40）
    levels = [p.level for p in rs.programs.values()]
    assert len(levels) == 40
    assert levels.count("national") == 17 and levels.count("machida") == 10 and levels.count("local") == 5
    assert levels.count("tokyo") == 1 and levels.count("yokohama") == 7
    assert sorted(p.number for p in rs.programs.values()) == list(range(1, 41))


def test_every_program_has_dated_official_sources(rs):
    for p in rs.programs.values():
        assert p.sources, p.id
        for s in p.sources:
            assert s.checked.isoformat() in ("2026-11-02", "2026-11-03", "2026-11-04"), p.id  # 11/4 は裁定を受けた直し
            if "city.machida.tokyo.jp" in s.url:
                assert not s.quote, f"{p.id}: 町田市のページは引用しない"
        assert p.apply.get("where"), p.id


def test_every_fact_is_used(rs):
    assert set(rs.facts) == referenced_facts(rs)


def test_demo_not_certified_shows_the_chain(rs):
    # 「母が退院してから家で転びやすくなった」：町田市、75歳以上、自宅、認定なし、暮らしに手助けが要る
    facts = {**MACHIDA_HOME_75, "care_level": "none", "needs_daily_help": True}
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
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "none", "needs_daily_help": True})
    assert s["kogaku-kaigo-service"] == "after_prereq"


def test_certified_care2_home_modification(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "care2"})
    assert s["kaigo-nintei"] == "done"
    assert s["jutaku-kaishu"] == "eligible"
    assert s["machida-shogaisha-nintei"] == "eligible"


def test_applying_can_pre_apply_home_modification(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "applying"})
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
                      "tax_status": "household_exempt"})
    assert s["jutaku-kaishu"] == "ineligible"
    assert s["futan-gendogaku"] == "eligible"


def test_outside_machida_only_national(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "lives_in_machida": False, "care_level": "care2"})
    for pid, p in rs.programs.items():
        if p.level == "machida":
            assert s[pid] == "ineligible", pid
    assert s["kaigo-nintei"] == "done"


def test_under_65_city_programs_ineligible(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "age_band": "age_40_64", "care_level": "none", "needs_daily_help": True})
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
    # 介護する家族の中に雇われて働く人がいて、本人が常時介護の要る状態（要介護2以上か、2 週間以上いつも介護が要る）
    worker = {"caregiver_employee": True, "caregiver_insured": True}
    assert statuses(rs, {**worker, "care_level": "care2"})["kaigo-kyugyo-kyufu"] == "eligible"
    assert statuses(rs, {**worker, "care_level": "care1", "constant_care_2w": True})["kaigo-kyugyo-kyufu"] == "eligible"
    assert statuses(rs, {**worker, "care_level": "care1"})["kaigo-kyugyo-kyufu"] == "unknown"
    assert statuses(rs, {**worker, "care_level": "care1", "constant_care_2w": False})["kaigo-kyugyo-kyufu"] == "ineligible"
    assert statuses(rs, {"caregiver_employee": False})["kaigo-kyugyo-kyufu"] == "ineligible"
    # 雇用保険に入っていなければ給付金は出ないが、介護休暇は取れる（c04 の週 12 時間のパート）
    s = statuses(rs, {"caregiver_employee": True, "caregiver_insured": False, "care_level": "care3"})
    assert s["kaigo-kyugyo-kyufu"] == "ineligible" and s["kaigo-kyuka"] == "eligible"
    assert statuses(rs, {"caregiver_employee": False})["kaigo-kyuka"] == "ineligible"


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
    assert s["jiritsu-shien-iryo"] == "eligible"  # 国の制度は全国で判定
    # 日常生活自立支援事業は、認知症の診断だけでは決めない（D-0009 の B）。お金の管理が難しいと分かれば全国で判定する
    assert s["nichijo-jiritsu-shien"] == "unknown"
    assert statuses(rs, {**YOKOHAMA_DEMENTIA, "money_management_difficulty": True})["nichijo-jiritsu-shien"] == "eligible"


def test_machida_shows_city_programs_not_types(rs):
    s = statuses(rs, {**YOKOHAMA_DEMENTIA, "lives_in_machida": True})
    assert all(s[pid] == "ineligible" for pid, p in rs.programs.items() if p.level == "local")


def test_hearing_aid_needs_the_certificate_first(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "hearing_difficulty": True, "hearing_certifiable": True})
    assert s["shintai-techo-chokaku"] == "eligible" and s["hosougu-hochoki"] == "after_prereq"
    s = statuses(rs, {**MACHIDA_HOME_75, "hearing_difficulty": True, "hearing_certifiable": False})
    assert s["shintai-techo-chokaku"] == "ineligible" and s["hosougu-hochoki"] == "ineligible"


def test_care_leave_comes_with_the_benefit(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "caregiver_employee": True, "caregiver_insured": True, "care_level": "care3"})
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
    # ただし国の要綱で、日常生活で療養や介護が要る 65 歳以上の人がいる世帯に限られる（11/4 に条件へ入れた）
    s = statuses(rs, {**MACHIDA_HOME_75, "tax_status": "self_taxed", "needs_daily_help": True})
    assert s["seikatsu-fukushi-shikin"] == "eligible"
    s = statuses(rs, {**MACHIDA_HOME_75, "tax_status": "self_taxed", "needs_daily_help": False})
    assert s["seikatsu-fukushi-shikin"] == "ineligible"


def test_daily_life_support_does_not_need_a_diagnosis(rs):
    s = statuses(rs, {**MACHIDA_HOME_75, "dementia_diagnosis": False, "money_management_difficulty": True})
    assert s["nichijo-jiritsu-shien"] == "eligible"
    s = statuses(rs, {**MACHIDA_HOME_75, "dementia_diagnosis": False, "money_management_difficulty": False})
    assert s["nichijo-jiritsu-shien"] == "ineligible"


def test_applying_waits_for_the_result(rs):
    # 申請中なら、認定の申請は「申請済み・結果待ち」。認定が前提の制度は「先に手続きをすると使える」のまま
    base = {**MACHIDA_HOME_75, "care_level": "applying", "tax_status": "household_exempt"}
    res = evaluate(rs, base)
    assert res["kaigo-nintei"].status == "pending"
    assert res["kaigo-nintei"].leaves[0].label == "申請して、結果を待っている"
    assert res["futan-gendogaku"].status == "after_prereq" and "kaigo-nintei" in res["futan-gendogaku"].waiting_on
    assert res["jutaku-kaishu"].status == "eligible"  # 申請中でも住宅改修は申請できる（今までどおり）
    assert statuses(rs, {**base, "care_level": "none", "needs_daily_help": True})["kaigo-nintei"] == "eligible"


def test_checks_read_anywhere(rs):
    # 確かめ方は住所を問わずそのまま出る。町田市の呼び名だけを書くと、ほかの市区町村の人にはどこのことか分からない
    for fact in rs.facts.values():
        if fact.id == "lives_in_machida":
            continue
        assert "町田市の住民税" not in fact.check, fact.id
        if "高齢者支援センター" in fact.check:
            assert "地域包括支援センター（町田市では「高齢者支援センター」）" in fact.check, fact.id


YOKOHAMA_HOME_75 = {"lives_in_yokohama": True, "age_band": "age_75_plus", "residence": "home"}


def test_place_facts_follow_the_city(rs):
    # 町田市に住んでいると分かれば東京都に住んでいて、横浜市ではない（住所を選ばず、読み取りだけで分かったときも）
    res = evaluate(rs, {"lives_in_machida": True, "age_band": "age_75_plus"})
    assert res["tokyo-silver-pass"].status == "eligible"
    assert res["yokohama-keiro-pass"].status == "ineligible"
    res = evaluate(rs, {"lives_in_yokohama": True, "age_band": "age_70_74"})
    assert res["yokohama-keiro-pass"].status == "eligible"
    assert res["tokyo-silver-pass"].status == "ineligible" and res["machida-kami-omutsu"].status == "ineligible"
    # 町田市以外とだけ分かっているときは、東京都かどうかは決まらない
    assert evaluate(rs, {"lives_in_machida": False, "age_band": "age_75_plus"})["tokyo-silver-pass"].status == "unknown"
    assert evaluate(rs, {"lives_in_tokyo": True, "age_band": "age_65_69"})["tokyo-silver-pass"].status == "ineligible"


def test_yokohama_programs(rs):
    # 横浜市の公式ページ（2026-11-03 に確認）の条件から作った場面。当てはまる見込みと、当てはまらないを 1 つずつ
    def status(pid, **facts):
        return evaluate(rs, {**YOKOHAMA_HOME_75, **facts})[pid].status
    assert status("yokohama-keiro-pass") == "eligible"
    assert status("yokohama-keiro-pass", age_band="age_65_69") == "ineligible"
    assert status("yokohama-kami-omutsu", care_level="care4", tax_status="household_exempt") == "eligible"
    assert status("yokohama-kami-omutsu", care_level="care4", on_welfare=True) == "eligible"
    assert status("yokohama-kami-omutsu", care_level="care4", tax_status="self_taxed", on_welfare=False) == "ineligible"
    assert status("yokohama-kami-omutsu", care_level="none", needs_daily_help=True, tax_status="household_exempt") == "after_prereq"
    assert status("yokohama-sos", dementia_diagnosis=True, wandering_risk=True) == "eligible"
    assert status("yokohama-sos", dementia_diagnosis=True, wandering_risk=False) == "ineligible"
    assert status("yokohama-sos", dementia_diagnosis=False, wandering_risk=True) == "eligible"  # 診断は求めていない
    hearing = {"hearing_difficulty": True, "hearing_certifiable": False, "tax_status": "household_exempt"}
    assert status("yokohama-hochoki", **hearing) == "eligible"
    assert status("yokohama-hochoki", **{**hearing, "tax_status": "self_taxed", "on_welfare": False}) == "ineligible"
    # 手帳の対象と言われていても、交付されていなければ対象。50 歳以上かは 40〜64 歳の人だけ窓口で確かめる（3rd の照合）
    assert status("yokohama-hochoki", **{**hearing, "hearing_certifiable": True}) == "eligible"
    assert status("yokohama-hochoki", **{**hearing, "age_band": "age_40_64"}) == "unknown"
    assert status("yokohama-hochoki", **{**hearing, "age_band": "under_40"}) == "ineligible"
    assert status("yokohama-mimamori", household="alone") == "eligible"
    assert status("yokohama-mimamori", household="with_family") == "ineligible"
    assert status("yokohama-ribiyo", care_level="care5") == "eligible"
    assert status("yokohama-ribiyo", care_level="care5", residence="facility") == "ineligible"
    assert status("yokohama-shika-kenshin", care_level="care3", on_welfare=False) == "eligible"
    assert status("yokohama-shika-kenshin", care_level="care3", on_welfare=True) == "ineligible"
    # 横浜市以外の人には、どれも当てはまらない
    away = evaluate(rs, {**YOKOHAMA_HOME_75, "lives_in_yokohama": False, "care_level": "care5", "household": "alone"})
    assert all(away[p.id].status == "ineligible" for p in rs.programs.values() if p.level == "yokohama")


def test_certified_but_level_unknown(rs):
    # 認定は受けているが区分が分からない人（裁定 p-care-unknown、11/4）：どの区分でも当たる制度は当たり、区分で分かれる制度は確かめる
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "certified_unknown", "uses_care_services": True})
    assert s["kaigo-nintei"] == "done"
    for pid in ("kogaku-kaigo-service", "jutaku-kaishu", "fukushi-yogu-konyu", "machida-jutaku-setsubi"):
        assert s[pid] == "eligible", pid
    assert s["machida-shogaisha-nintei"] == "unknown"  # 要介護1〜5 か要支援かで分かれる
    # 介護休暇の「要介護2〜5」も、区分が分からないので確かめる
    assert statuses(rs, {**MACHIDA_HOME_75, "care_level": "certified_unknown", "caregiver_employee": True})["kaigo-kyuka"] == "unknown"


def test_not_needing_help_is_not_sent_to_certification(rs):
    # 介助の要る場面がない人は、認定の申請も、認定を前提にした制度も案内しない（裁定 p-prereq、11/4）
    s = statuses(rs, {**MACHIDA_HOME_75, "care_level": "none", "needs_daily_help": False})
    assert s["kaigo-nintei"] == "ineligible"
    assert s["kogaku-kaigo-service"] == "ineligible" and s["machida-shogaisha-nintei"] == "ineligible"
    # 前に非該当だった人も、介助の要る場面があれば申請し直せる（裁定 p-jiritsu）
    assert statuses(rs, {**MACHIDA_HOME_75, "care_level": "jiritsu", "needs_daily_help": True})["kaigo-nintei"] == "eligible"


def test_hospital_stay_is_checked_at_the_window(rs):
    # 入院中の人：戻る家があれば住宅改修などは当たりうるので、窓口で確かめる。施設に入所していれば当たらない（11/4）
    s = statuses(rs, {**MACHIDA_HOME_75, "residence": "hospital", "care_level": "care2", "household": "alone"})
    for pid in ("jutaku-kaishu", "fukushi-yogu-konyu", "machida-jutaku-setsubi", "machida-haishoku"):
        assert s[pid] == "unknown", pid
    s = statuses(rs, {**MACHIDA_HOME_75, "residence": "facility", "care_level": "care2", "household": "alone"})
    assert s["machida-haishoku"] == "ineligible"


def test_facility_and_exempt_household_can_reach_the_combined_limit(rs):
    # 医療費が小さくても、特養の介護の自己負担が毎月あり、世帯が非課税なら合算療養費に当たりうる（裁定 i03、11/4）
    base = {**MACHIDA_HOME_75, "residence": "facility", "care_level": "care5", "uses_care_services": True,
            "high_medical_costs": False}
    assert statuses(rs, {**base, "tax_status": "household_exempt"})["kogaku-gassan"] == "eligible"
    assert statuses(rs, {**base, "tax_status": "self_taxed"})["kogaku-gassan"] == "ineligible"


def test_epilepsy_outpatient_care(rs):
    # 精神通院医療は、精神疾患（てんかんを含む）で通院を続ける人が対象（c20、11/4）
    s = statuses(rs, {**MACHIDA_HOME_75, "dementia_diagnosis": False, "mental_outpatient": True})
    assert s["jiritsu-shien-iryo"] == "eligible"
