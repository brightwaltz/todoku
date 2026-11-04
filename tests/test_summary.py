"""まとめの検査（check_summary）と、本物の Claude の呼び出し口の設定の検査。API は呼ばない。"""

from todoku import rules as rules_mod
from todoku.claude import EXTRACT_SYSTEM, SUMMARY_SYSTEM, RealClaude, check_summary
from todoku.engine import SHOWN, evaluate

RS = rules_mod.load()


def shown_for(facts):
    return [r for r in evaluate(RS, facts).values() if r.status in SHOWN]


def texts(shown, *lines):
    raw = {"lines": [{"program_ids": ["kaigo-kyugyo-kyufu"], "text": t} for t in lines]}
    return [l.text for l in check_summary(RS, shown, raw)]


ELSEWHERE = {"lives_in_machida": False, "caregiver_employee": True}
MACHIDA = {"lives_in_machida": True, "caregiver_employee": True}


def test_kanji_amounts_are_dropped_but_words_are_kept():
    shown = shown_for(ELSEWHERE)
    got = texts(shown, "自己負担は一割です。", "三十日まで休めます。", "ご家族と一緒に、まず相談してみましょう。", "時間は十分にあります。")
    assert got == ["ご家族と一緒に、まず相談してみましょう。", "時間は十分にあります。"]


def test_machida_lines_are_dropped_for_people_elsewhere():
    shown = shown_for(ELSEWHERE)
    assert not any(r.program.level == "machida" for r in shown)
    assert texts(shown, "町田市の窓口で申し込めます。", "勤め先に申し出ると使えます。") == ["勤め先に申し出ると使えます。"]


def test_machida_lines_pass_when_machida_programs_are_shown():
    shown = shown_for(MACHIDA)
    assert any(r.program.level == "machida" for r in shown)
    assert texts(shown, "町田市の窓口でも相談できます。") == ["町田市の窓口でも相談できます。"]


def test_prompts_are_not_only_for_machida():
    assert "東京都町田市の" not in EXTRACT_SYSTEM
    assert "東京都町田市の" not in SUMMARY_SYSTEM


def test_client_waits_at_most_a_minute(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    claude = RealClaude()
    assert claude._client.timeout == 60.0
    assert claude._client.max_retries == 1


def test_yokohama_and_tokyo_lines_need_their_programs():
    # 町田市・横浜市・東京都の名前を含む行は、その段の制度が画面に出ているときだけ通す（ほかの所に住む人に窓口を取り違えさせない）
    machida = shown_for({**MACHIDA, "age_band": "age_75_plus"})
    assert any(r.program.level == "tokyo" for r in machida) and not any(r.program.level == "yokohama" for r in machida)
    assert texts(machida, "東京都のシルバーパスも買えます。", "横浜市の窓口でも相談できます。") == ["東京都のシルバーパスも買えます。"]


def test_review_lines_are_dropped():
    # 11/3 の安全点検（4）で check_summary を通った 2 行。program_ids が画面の制度でも、言い切りと URL の行は捨てる（T-035）
    shown = shown_for(MACHIDA)
    assert texts(shown, "生活保護も必ず受けられます。窓口で強く主張してください。", "詳しくは https://evil.example/ へ") == []
    # 全角の URL・メールアドレスも（NFKC で半角にしてから見る）
    assert texts(shown, "詳しくは ｈｔｔｐｓ：／／ｅｘａｍｐｌｅ．ｊｐ へ", "相談は info＠example.jp へ", "www.example.jp で調べられます。") == []
    assert texts(shown, "勤め先に申し出ると使えます。") == ["勤め先に申し出ると使えます。"]


def test_names_of_programs_not_given_are_dropped():
    # まとめに渡していない規則の制度の正式名を含む行は捨てる。渡した制度の名前は通す。
    # /api/summary が渡すのは当てはまる見込み・申請済み・先に手続きの制度だけ（確かめるの制度は渡さない）
    # 介護休業給付は、雇用保険と常時介護まで分かって「当てはまる見込み」になる（fix/gold-rules の規則。T-070 で事実を足した）
    facts = {**MACHIDA, "caregiver_insured": True, "constant_care_2w": True}
    shown = [r for r in evaluate(RS, facts).values() if r.status in ("eligible", "pending", "after_prereq")]
    given = {r.program.id for r in shown}
    assert "kaigo-kyugyo-kyufu" in given and "tokubetsu-shogaisha-teate" not in given and "kogaku-ryoyohi" not in given
    assert texts(shown, "介護休業給付金に加えて、特別障害者手当も使えます。", "介護休業給付金と一緒に、高額療養費も申請しましょう。",
                 "介護休業給付金は、勤め先に申し出て使えます。") == ["介護休業給付金は、勤め先に申し出て使えます。"]


def test_prerequisite_names_are_kept():
    # 前提の制度（waiting_on）は、画面の制度でなくても名前を Claude に渡している（「まず〜を申請すると」と書かせる）ので通す
    results = evaluate(RS, {**MACHIDA, "care_level": "none", "uses_care_services": False, "residence": "home",
                            "age_band": "age_75_plus", "plans_home_modification": True,
                            "needs_daily_help": True})  # 認定の申請が当たりうるのは介助の要る場面があるとき（裁定 p-prereq。T-070 で足した）
    after = next(r for r in results.values() if r.status == "after_prereq" and r.waiting_on)
    first = RS.programs[after.waiting_on[0]]
    line = f"まず{first.name}をすると、{after.program.name}も使えるようになります。"
    raw = {"lines": [{"program_ids": [after.program.id], "text": line}]}
    assert [l.text for l in check_summary(RS, [after], raw)] == [line]


def test_fake_summaries_for_the_cases_are_unchanged(monkeypatch):
    # 評価の事例 30 件で、答えを全部入れた後の FakeClaude のまとめ（規則の正式名を並べた行）が、検査で 1 行も消えない
    from pathlib import Path

    import yaml

    import todoku.claude as claude_mod
    from todoku.app import SUMMARIZED, apply_place, clean_facts

    cases = yaml.safe_load((Path(__file__).parent.parent / "eval/cases/cases.yaml").read_text(encoding="utf-8"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    given = []
    monkeypatch.setattr(claude_mod, "check_summary", lambda rules, shown, raw: given.append((shown, raw)) or [])
    for c in cases:
        facts = clean_facts(dict(c["answers"]))
        apply_place(facts, {}, c.get("municipality", ""))
        claude_mod.FakeClaude().summarize(RS, "ご本人", [r for r in evaluate(RS, facts).values() if r.status in SUMMARIZED])
    monkeypatch.undo()
    n = 0
    for shown, raw in given:
        assert [l.text for l in check_summary(RS, shown, raw)] == [l["text"] for l in raw["lines"]][:4]
        n += len(raw["lines"])
    assert n >= 15  # 11/4 の規則で 22 行、fix/gold-rules の規則（雇用保険・常時介護・介助の事実を足した後）で 19 行（T-070）
