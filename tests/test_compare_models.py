"""eval/compare_models.py（読み取りのモデル比較）の数え方を、API を呼ばずに確かめる。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "eval"))

import compare_models as cm  # noqa: E402
from todoku.claude import Extracted, Extraction  # noqa: E402
from todoku.engine import DONT_KNOW  # noqa: E402

ANSWERS = {"household": "alone", "age_band": "age_75_plus", "tax_status": DONT_KNOW}


def test_score_reads_splits_by_confidence_and_truth():
    ex = Extraction(facts=[
        Extracted("household", "alone", "high", "ひとり暮らし"),       # 高・正しい
        Extracted("age_band", "age_65_74", "high", "70代"),           # 高・誤り
        Extracted("tax_status", "household_exempt", "high", "非課税"),  # 本人は分からないのに決めた → 誤り
        Extracted("care_level", "care1", "low", "要介護"),             # answers にない事実 → 採点できない
    ])
    r = cm.score_reads(ex, ANSWERS)
    assert r["high_right"] == ["household"]
    assert sorted(r["high_wrong"]) == ["age_band", "tax_status"]
    assert r["low_wrong"] == [] and r["low_right"] == []
    assert r["unscored"] == ["care_level"]
    assert r["wrong_detail"]["age_band"] == ["age_65_74", "age_75_plus"]


def test_relative_recall_uses_union_of_models():
    runs = {
        "a": {"cases": {"c1": {"high_right": ["x", "y"], "high_wrong": [], "low_right": [], "low_wrong": []}}, "calls": []},
        "b": {"cases": {"c1": {"high_right": ["y", "z"], "high_wrong": ["w"], "low_right": [], "low_wrong": []}}, "calls": []},
    }
    s = cm.summarize(runs, [{"id": "c1"}])
    assert s["reference_size"] == 3
    assert abs(s["models"]["a"]["relative_recall"] - 2 / 3) < 1e-9
    assert s["models"]["b"]["high_precision"] == 2 / 3


def test_cost_uses_per_model_prices():
    assert abs(cm.cost_of("claude-opus-5-5", 1_000_000, 1_000_000) - 24.0) < 1e-9
    assert abs(cm.cost_of("claude-haiku-5-5", 1_000_000, 0) - 0.10) < 1e-9


def test_fake_run_does_not_touch_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(cm, "OUT", tmp_path)
    before = cm.REPORT.read_text(encoding="utf-8") if cm.REPORT.exists() else None
    assert cm.main(["--fake", "--limit", "2"]) == 0
    assert list(tmp_path.glob("compare-models-*-fake.json"))
    after = cm.REPORT.read_text(encoding="utf-8") if cm.REPORT.exists() else None
    assert before == after


def test_rescore_uses_saved_reads_without_api(tmp_path, monkeypatch):
    import json

    case = cm.load_cases()[0]
    fact, truth = next(iter(case["answers"].items()))
    saved = {"effort": "low", "fake": False, "runs": {"claude-opus-5-5": {
        "cases": {case["id"]: {"read": {fact: [truth, "high"], "new_fact_not_in_cases": [True, "low"]}}},
        "calls": [{"model": "claude-opus-5-5", "input_tokens": 1000, "output_tokens": 100, "seconds": 1.0}]}}}
    path = tmp_path / "compare-models-20261103-0154.json"
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(cm, "REPORT", tmp_path / "model_comparison.md")
    assert cm.main(["--rescore", str(path)]) == 0
    text = (tmp_path / "model_comparison.md").read_text(encoding="utf-8")
    assert "数え直した" in text and "2026-11-03 01:54" in text
    assert "| claude-opus-5-5 | 1 | 0 | 100.0% | 100.0% | 0 | 0 | 1 |" in text
