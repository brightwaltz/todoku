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
    assert {kind for kind, _ in population.SOURCES.values()} <= {"memory", "assumed"}


def test_theme_tables_cover_the_entry_events():
    ids = {e["id"] for e in events_mod.EVENTS[:events_mod.MAX_EVENTS]}
    assert set(run_sim.THEME_FITS) == ids == set(run_sim.THEME_FACTS)
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


def test_run_counts_outside_within_found(tmp_path):
    out = tmp_path / "sim.json"
    report = run_sim.main(["--n", "60", "--seed", "4", "--city", "132098", "--city", "023043", "--json", str(out)])
    assert out.exists()
    for s in report["cities"].values():
        assert 0 < s["counted"] <= s["residents"] == 60
        assert s["mean_outside_eligible"] <= s["mean_outside"] <= s["mean_found"]
        assert sum(s["outside_histogram"].values()) == s["counted"]
    none = run_sim.main(["--n", "60", "--seed", "4", "--city", "132098", "--rule", "none"])["cities"]["132098"]
    assert none["mean_outside"] == none["mean_found"]
