"""合成住民での試算（eval/sim/。T-077a）。API は呼ばない。"""

import importlib.util
import sys
from pathlib import Path

from todoku import app as app_mod
from todoku import events as events_mod
from todoku import rules as rules_mod

SIM = Path(__file__).parents[1] / "eval" / "sim"
sys.path.insert(0, str(SIM))
spec = importlib.util.spec_from_file_location("run_sim", SIM / "run_sim.py")
run_sim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_sim)
population = run_sim.population

RS = rules_mod.load()


def test_residents_have_every_non_place_fact_with_valid_values():
    # facts.yaml に事実が増えたら、作り方（population.draw）も足す。規則に無い事実（dev と dev-merge の差）は値を見ない
    people = population.generate(300, seed=3)
    want = {fid for fid in RS.facts if fid not in RS.places}
    for f in people:
        assert want <= set(f)
        for fid in want:
            assert f[fid] in RS.facts[fid].values(), (fid, f[fid])


def test_same_seed_same_residents_and_false_when_holds():
    assert population.generate(50, seed=7) == population.generate(50, seed=7)
    for f in population.generate(500, seed=2):
        if f["care_level"] in ("none", "applying", "jiritsu"):
            assert f["uses_care_services"] is False and f["shortstay_or_facility"] is False
        if not f["caregiver_employee"]:
            assert f["caregiver_insured"] is False
        if f["on_welfare"]:
            assert f["tax_status"] == "household_exempt"
        assert f["age_band"] in population.AGE_BANDS  # 65 歳以上だけ


def test_every_source_is_labelled():
    assert {kind for kind, _ in population.SOURCES.values()} <= {"stat", "assumed"}


def test_population_matches_the_checked_statistics():
    # T-077b で照らした数（population.py の SOURCES の stat と、認知症の全体の 12.3%）から大きく外れない
    people = population.generate(20000, seed=5)
    share = lambda pred: sum(1 for f in people if pred(f)) / len(people)  # noqa: E731
    assert abs(share(lambda f: f["age_band"] == "age_75_plus") - 0.573) < 0.015  # 人口推計 2024 年 10 月
    assert abs(share(lambda f: f["care_level"] in population.CERTIFIED) - 0.19) < 0.015  # 白書 第 1 号被保険者の 19.0%
    assert abs(share(lambda f: f["on_welfare"]) - 0.029) < 0.006
    assert abs(share(lambda f: f["household"] == "alone") - 0.236) < 0.015
    assert abs(share(lambda f: f["dementia_diagnosis"]) - 0.123) < 0.015
    certified = [f for f in people if f["care_level"] in population.CERTIFIED]
    heavy = sum(1 for f in certified if f["care_level"] in population.HEAVY) / len(certified)
    assert abs(heavy - (902 + 870 + 571) / 6814) < 0.03  # 白書 図 1-2-2-6（2022 年度）の要介護 3〜5


def test_theme_tables_cover_the_entry_events():
    ids = {e["id"] for e in events_mod.EVENTS[:events_mod.MAX_EVENTS]}
    # T-139 で小項目は 16 個になった。試しは元の 6 つのまま（表のある出来事は、どれも入口にある）
    assert set(run_sim.THEME_FITS) <= ids and set(run_sim.THEME_FITS) == set(run_sim.THEME_FACTS) == set(run_sim.THEME_EXTRA)
    assert set(run_sim.THEME_FITS) == {"discharged", "forgetful", "alone", "falls", "work", "diapers"}
    for extra in run_sim.THEME_EXTRA.values():
        assert set(extra) <= set(RS.programs)  # 書き間違いを止める（無い ID は direct_programs で黙って落ちる）
    latent = set(population.generate(1)[0])
    for facts in run_sim.THEME_FACTS.values():
        assert set(facts) <= latent  # 書き間違いを止める（規則に無い事実は direct_programs で何にも当たらない）


def test_visible_matches_the_result_screen():
    # 画面（app.step_view）の 2 つの絞り込みと同じ制度になる
    for code in run_sim.CITIES:
        facts, suggestions = {}, {}
        app_mod.apply_place(facts, suggestions, code)
        shown = {r["id"] for r in app_mod.step_view(facts, suggestions, "ご本人", 3, code)["results"]}
        assert run_sim.visible(RS, code) == shown, code


def test_direct_programs_follow_the_rule():
    assert run_sim.direct_programs(RS, "diapers", "none") == set()
    direct = run_sim.direct_programs(RS, "diapers", "facts")
    assert direct
    for pid in direct:
        assert run_sim.eligibility_facts(RS.programs[pid].eligibility) & {"uses_diapers", "incontinence_bedding"}


def test_extra_rule_adds_the_listed_programs_to_facts():
    # --rule extra（D-0027 の B）は facts（A）に THEME_EXTRA を足すだけ。転倒は住宅改修などが直接つながる
    for event_id, extra in run_sim.THEME_EXTRA.items():
        facts = run_sim.direct_programs(RS, event_id, "facts")
        assert run_sim.direct_programs(RS, event_id, "extra") == facts | set(extra), event_id
    assert {"jutaku-kaishu", "fukushi-yogu-konyu"} <= run_sim.direct_programs(RS, "falls", "extra")
    assert "machida-kami-omutsu" in run_sim.direct_programs(RS, "diapers", "extra")


def test_run_counts_outside_within_found(tmp_path):
    out = tmp_path / "sim.json"
    report = run_sim.main(["--n", "60", "--seed", "4", "--city", "132098", "--city", "023043", "--json", str(out)])
    assert out.exists()
    for s in report["cities"].values():
        assert 0 < s["counted"] <= s["residents"] == 60
        assert s["mean_outside_eligible"] <= s["mean_outside"] <= s["mean_found"]
        assert sum(s["outside_histogram"].values()) == s["counted"]
    assert report["rule"] == "extra"  # D-0027 の B が既定
    none = run_sim.main(["--n", "60", "--seed", "4", "--city", "132098", "--rule", "none"])["cities"]["132098"]
    assert none["mean_outside"] == none["mean_found"]
    facts = run_sim.main(["--n", "60", "--seed", "4", "--city", "132098", "--rule", "facts"])["cities"]["132098"]
    extra = report["cities"]["132098"]
    assert extra["mean_found"] == facts["mean_found"] and extra["mean_outside"] <= facts["mean_outside"]
