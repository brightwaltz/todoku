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
    "text": "町田市に住む母（82歳）が退院してから家で転びやすくなりました。要介護の認定は受けていません。",
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
    # 値打ち順は、決まった順よりも少ない問いで多くを拾う（この例では 3 問目で介護休業給付金に届き、決まった順は届かない）
    s_value = run_eval.score({"t1": res["variants"]["value-3"]}, GOLD)
    s_fixed = run_eval.score({"t1": res["variants"]["fixed-3"]}, GOLD)
    assert s_value["recall_recommended"] > s_fixed["recall_recommended"]


# ---- 正解の読み方・問いの扱い・失敗の受け止め（3rd の点検、11/3） ----

import pytest  # noqa: E402


@pytest.mark.parametrize("cell,mark", [
    ("○", "yes"), ("〇", "yes"), ("◯", "yes"), (" o ", "yes"), ("yes", "yes"),
    ("×", "no"), ("✕", "no"), ("✗", "no"), ("Ｘ", "no"), ("no", "no"),
    ("？", "unsure"), ("?", "unsure"), ("unsure", "unsure"), ("", None), (None, None),
])
def test_parse_mark_reads_variants(cell, mark):
    assert run_eval.parse_mark(cell) == mark


def test_parse_mark_rejects_unknown():
    with pytest.raises(ValueError):
        run_eval.parse_mark("△")


def test_load_gold_counts_blanks_and_stops_on_unknown(tmp_path, capsys):
    path = tmp_path / "gold.csv"
    path.write_text("事例,1 要介護・要支援認定の申請,2 高額療養費,8 介護休業給付金\nc01,◯,Ｘ,\nc02,,,\n", encoding="utf-8")
    gold = run_eval.load_gold(RS, path)
    no2 = next(p.id for p in RS.programs.values() if p.number == 2)
    assert gold == {"c01": {"kaigo-nintei": "yes", no2: "no"}}
    assert "空欄 4 マス" in capsys.readouterr().err
    path.write_text("事例,1 要介護・要支援認定の申請\nc01,△\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        run_eval.load_gold(RS, path)


def test_confirm_question_answered_dont_know_is_not_asked_again():
    # 画面の確かめる問い（はい・いいえ）には「わからない」もあり、押すと DONT_KNOW が入って二度と聞かれない
    facts = {k: v for k, v in CASE["answers"].items() if k != "household"}
    case = {**CASE, "answers": {**CASE["answers"], "household": DONT_KNOW}}
    res = run_eval.simulate(RS, case, facts, {"household": "alone"}, 3, "fixed")
    assert res["asked"] == ["household"]


def test_place_run_skips_the_place_question():
    case = {**CASE, "text": "母（82歳）が退院してから家で転びやすくなりました。"}
    res = run_eval.run_pipeline(RS, case, FakeClaude())
    assert "lives_in_machida" not in res["place"]["asked"]


def test_claude_only_sees_only_gold_programs():
    sent = {}

    class Stub:
        def _json(self, system, user, schema, max_tokens):
            sent.update(system=system, schema=schema)
            return {"recommend": ["kaigo-nintei"]}

    res = run_eval.run_claude_only(RS, CASE, Stub(), ["kaigo-nintei", "machida-gps"])
    assert sent["schema"]["properties"]["recommend"]["items"]["enum"] == ["kaigo-nintei", "machida-gps"]
    assert "東京都町田市の" not in sent["system"] and RS.programs["machida-gps"].summary in sent["system"]
    assert res["statuses"]["kaigo-nintei"] == "eligible" and res["statuses"]["machida-gps"] == "ineligible"


def test_failed_cases_are_reported_not_fatal(tmp_path, monkeypatch, capsys):
    import todoku.claude as claude_mod

    class Flaky(FakeClaude):
        calls: list = []

        def extract(self, rules, text):
            if "退院" in text:
                raise RuntimeError("切れた")
            return super().extract(rules, text)

    cases = run_eval.load_cases()[:3]
    gold = {c["id"]: {"kaigo-nintei": "yes"} for c in cases}
    cases[0] = {**cases[0], "text": "退院した母のことです。"}
    monkeypatch.setattr(run_eval, "load_cases", lambda: cases)
    monkeypatch.setattr(run_eval, "load_gold", lambda rs: gold)
    monkeypatch.setattr(run_eval, "OUT", tmp_path)
    monkeypatch.setattr(claude_mod, "RealClaude", Flaky)
    run_eval.main(["pipeline", "claude_only"])  # claude_only は FakeClaude に _json がなく、全件失敗する
    out = capsys.readouterr().out
    assert f"失敗した事例：{cases[0]['id']}" in out
    assert "[claude_only] 事例 0 件" in out and "—" in out


def test_pct_handles_none():
    assert run_eval.pct(None) == "—" and run_eval.pct(0.5) == "50.0%"


def test_place_question_fills_all_place_facts_like_the_screen():
    """住所の問いには、事例の市区町村コードから、画面と同じ place_values で住所の事実を全部入れる（3rd の点検、11/3 夜）。"""
    hachioji = {**CASE, "municipality": "132012", "answers": {**CASE["answers"], "lives_in_machida": False}}
    facts, sugg = {}, {"lives_in_machida": True}
    run_eval.apply_place(RS, hachioji, facts, sugg, RS.place_question)
    assert facts == RS.place_values("132012")
    assert facts["lives_in_machida"] is False and facts.get("lives_in_tokyo") is True
    assert sugg == {}
    # コードのない事例は、前と同じく lives_in_machida の答えだけを入れる
    facts2 = {}
    run_eval.apply_place(RS, CASE, facts2, {}, "lives_in_machida")
    assert facts2 == {"lives_in_machida": True}


def test_cases_have_municipality_matching_lives_in_machida():
    for c in run_eval.load_cases():
        assert (c["municipality"] == "132098") == (c["answers"]["lives_in_machida"] is True), c["id"]
        assert RS.place_values(c["municipality"])["lives_in_machida"] is c["answers"]["lives_in_machida"]
