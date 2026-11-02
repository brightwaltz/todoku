import importlib.util
from pathlib import Path

from todoku import rules as rules_mod
from todoku.claude import FakeClaude
from todoku.engine import DONT_KNOW

spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).parents[1] / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

RS = rules_mod.load()
CASE = {
    "id": "t1",
    "text": "町田市に住む母（82歳）が退院してから家で転びやすくなりました。介護保険は使ったことがありません。",
    "answers": {**{f: DONT_KNOW for f in RS.facts},
                "lives_in_machida": True, "age_band": "age_75_plus", "residence": "home", "care_level": "none",
                "household": "alone_daytime", "plans_home_modification": True, "caregiver_employee": True},
}
GOLD = {"t1": {"kaigo-nintei": "yes", "jutaku-kaishu": "yes", "kaigo-kyugyo-kyufu": "yes", "machida-gps": "no"}}


def test_rules_mode_scores():
    res = {"t1": run_eval.run_rules(RS, CASE)}
    s = run_eval.score(res, GOLD)
    assert s["cells"]["yes"] == 3 and s["cells"]["no"] == 1
    assert s["recall_recommended"] == 1.0
    assert s["cells"]["no_recommended"] == 0


def test_pipeline_variants_without_api():
    res = run_eval.run_pipeline(RS, CASE, FakeClaude())
    assert set(res["variants"]) == {f"{o}-{k}" for o in ("value", "fixed") for k in range(4)}
    assert res["variants"]["value-0"]["asked"] == []
    assert len(res["variants"]["value-3"]["asked"]) <= 3
    # 値打ち順は、決まった順よりも少ない問いで多くを拾う（この例では 1 問で介護休業給付金に届く）
    s_value = run_eval.score({"t1": res["variants"]["value-3"]}, GOLD)
    s_fixed = run_eval.score({"t1": res["variants"]["fixed-3"]}, GOLD)
    assert s_value["recall_recommended"] >= s_fixed["recall_recommended"]
