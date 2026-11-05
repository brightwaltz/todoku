"""お住まいは 3 問の枠の外で最初に聞く（11/6、柴田「町田市前提はやめてほしい。ユーザは全国どの人の可能性もある」）。"""

import importlib.util
from pathlib import Path

from todoku import rules as rules_mod
from todoku.engine import DONT_KNOW
from todoku.questions import MAX_QUESTIONS, next_question

spec = importlib.util.spec_from_file_location("run_eval", Path(__file__).parents[1] / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_eval)

RS = rules_mod.load()
FIRST_PLACE = next(iter(RS.places))
# お住まいを書いていない相談（八王子市に住む人。認定は受けていない）
CASE = {
    "id": "t2",
    "municipality": "132012",
    "answers": {**{f: DONT_KNOW for f in RS.facts},
                "age_band": "age_75_plus", "residence": "home", "care_level": "none",
                "household": "alone", "needs_daily_help": True, "high_medical_costs": True},
}
READ = {"age_band": ["age_75_plus", "high"], "care_level": ["none", "high"], "residence": ["home", "high"]}


def test_place_is_asked_first_and_not_counted():
    q = next_question(RS, {"age_band": "age_75_plus"}, asked=0)
    assert q is not None and q.fact == FIRST_PLACE and q.counted is False


def test_place_is_not_asked_once_known_or_dont_know():
    q = next_question(RS, {"age_band": "age_75_plus", FIRST_PLACE: DONT_KNOW}, asked=0)
    assert q is None or (q.fact not in RS.places and q.counted is True)
    known = {**RS.place_values("132098"), "age_band": "age_75_plus"}
    q = next_question(RS, known, asked=0)
    assert q is None or q.fact not in RS.places


def test_stopped_flow_does_not_ask_place():
    # 聞き返しをやめた（asked が枠に届いた）ときは、お住まいも聞かない。結果の画面の欄で選べる
    assert next_question(RS, {}, asked=MAX_QUESTIONS) is None


def test_legacy_flow_counts_place():
    q = next_question(RS, {"age_band": "age_75_plus"}, asked=0, place_first=False)
    assert q is None or q.counted is True


def test_simulate_asks_place_outside_the_three():
    res = run_eval.from_read(RS, CASE, READ)
    assert res["place_asked"] and all(f in RS.places for f in res["place_asked"])
    assert len(res["asked"]) <= MAX_QUESTIONS and not set(res["asked"]) & set(RS.places)
    # 前の流れ（枠の中）は、お住まいを数える
    legacy = res["variants"]["legacy-value-3"]
    assert legacy["place_asked"] == [] and len(legacy["asked"]) <= MAX_QUESTIONS
    # 聞き返さない（value-0）は、お住まいも聞かない（相談文だけで比べる行は前と同じ意味）
    assert res["variants"]["value-0"]["asked"] == [] and res["variants"]["value-0"]["place_asked"] == []


def test_replay_reproduces_saved_flow(tmp_path):
    # 保存した読み取りで流し直すと、前の流れ（legacy-value-3）は保存した main の判定と同じになる
    saved = run_eval.from_read(RS, CASE, READ)
    again = run_eval.from_read(RS, CASE, saved["read"])
    assert again["statuses"] == saved["statuses"]
    assert again["variants"]["legacy-value-3"]["statuses"] == saved["variants"]["legacy-value-3"]["statuses"]
