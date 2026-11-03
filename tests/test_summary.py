"""まとめの検査（check_summary）と、本物の Claude の呼び出し口の設定の検査。API は呼ばない。"""

from todoku import rules as rules_mod
from todoku.claude import EXTRACT_SYSTEM, SUMMARY_SYSTEM, RealClaude, check_summary
from todoku.engine import SHOWN, evaluate

RS = rules_mod.load()


def shown_for(facts):
    return [r for r in evaluate(RS, facts).values() if r.status in SHOWN]


def texts(shown, *lines):
    raw = {"lines": [{"program_ids": ["kaigo-kyugyo-kyufu"], "text": t} for t in lines]}
    return [l.text for l in check_summary(shown, raw)]


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
