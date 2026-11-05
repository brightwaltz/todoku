"""見本の道の、前もって作ったまとめ（T-045、D-0013 の A）。API は呼ばない（作るのは FakeClaude）。"""

import copy
import importlib.util
import os
from pathlib import Path

os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku.claude import FakeClaude  # noqa: E402

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("make_sample_summaries", ROOT / "scripts" / "make_sample_summaries.py")
mss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mss)
client = TestClient(app_mod.app)


@pytest.fixture
def made(monkeypatch):
    """今の見本 4 件に、FakeClaude でまとめを入れたもの。"""
    samples = copy.deepcopy(app_mod.SAMPLES)
    for s in samples:
        s.pop("summary", None)
    mss.make(samples, FakeClaude())
    monkeypatch.setattr(app_mod, "SAMPLES", samples)
    return {s["id"]: s for s in samples}


def ask(s, facts):
    return client.post("/api/sample/summary", json={"id": s["id"], "facts": facts, "municipality": s["municipality"]})


def test_script_answers_show_saved_summary(made, monkeypatch):
    s = made["taiin-haha"]
    assert s["summary"]["lines"], "FakeClaude でも、認定の申請の先の制度で 1 行は出る"
    assert s["summary"]["answers"]["care_level"] == "none"  # 台本の答え
    # 合言葉を設定した置き場でも、合言葉なしで出る（Claude を呼ばない）
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")

    def no_claude(*a, **k):
        raise AssertionError("見本のまとめで Claude を呼んだ")

    monkeypatch.setattr(app_mod.CLAUDE, "summarize", no_claude)
    res = ask(s, mss.walk(s)[0])
    assert res.status_code == 200
    body = res.json()
    assert body["lines"] == s["summary"]["lines"]
    assert body["made"]["model"] == "fake"


def test_other_answers_hide_summary(made):
    s = made["taiin-haha"]
    facts = mss.walk(s)[0]
    facts["care_level"] = "care1"  # 台本と違う答え：まとめに渡す制度の組が変わる
    assert ask(s, facts).json() == {"engine": "sample", "lines": [], "made": None}


def test_all_samples_reach_a_summary(made):
    for s in made.values():
        assert ask(s, mss.walk(s)[0]).json()["lines"] == s["summary"]["lines"], s["id"]


def test_without_saved_summary_nothing_is_shown(monkeypatch):
    samples = copy.deepcopy(app_mod.SAMPLES)
    for s in samples:
        s.pop("summary", None)
    monkeypatch.setattr(app_mod, "SAMPLES", samples)
    s = samples[0]
    assert ask(s, mss.walk(s)[0]).json()["lines"] == []
    assert client.post("/api/sample/summary", json={"id": "nai", "facts": {}}).status_code == 404


def test_saved_summaries_match_current_rules():
    """samples.json に置いたまとめは、今の規則で台本どおりに進めたときの組と同じでなければならない。
    落ちたら規則か見本が変わったので、scripts/make_sample_summaries.py で作り直す（作り直すまで画面には出ない）。"""
    for s in app_mod.SAMPLES:
        if s.get("summary"):
            facts, answers, _, muni = mss.walk(s)
            assert answers == s["summary"]["answers"], s["id"]
            assert app_mod.summary_key(app_mod.summarized(facts, muni)) == s["summary"]["key"], s["id"]


def test_estimate_does_not_call_api():
    line = mss.estimate(copy.deepcopy(app_mod.SAMPLES))
    assert "4 回" in line and "米ドル" in line


def test_page_asks_for_saved_summary():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert '"/api/sample/summary"' in js
    assert "見本では、Claude が書くまとめを省いています" in js  # 台本と違う答えのときは、今までどおり
    assert "前もって Claude が判定の結果から書いたまとめ" in js
