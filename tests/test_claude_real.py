"""本物の Claude の呼び出し口を、API を呼ばずに確かめる（答えの形ごとの扱い）。"""

import json
from types import SimpleNamespace

import pytest

from todoku import rules as rules_mod
from todoku.claude import ClaudeFailed, ClaudeRefused, RealClaude

RS = rules_mod.load()
TEXT = "母はひとり暮らしです。"
GOOD = json.dumps({"subject": "お母さま", "facts": [
    {"fact": "household", "value": "alone", "confidence": "high", "evidence": "ひとり暮らし"}]}, ensure_ascii=False)


def make(monkeypatch, stop="end_turn", text=GOOD, **kw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    claude = RealClaude(**kw)
    sent = {}
    content = [] if text is None else [SimpleNamespace(type="text", text=text)]
    response = SimpleNamespace(model="claude-opus-5-5", stop_reason=stop, stop_details=None, content=content,
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=80))

    def create(**kw):
        sent.update(kw)
        return response

    claude._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    return claude, sent


def test_reads_json_and_counts_tokens(monkeypatch):
    claude, sent = make(monkeypatch)
    ex = claude.extract(RS, TEXT)
    assert ex.subject == "お母さま"
    assert ex.confirmed() == {"household": "alone"}
    seconds = claude.calls[0].pop("seconds")  # 1 回の秒数（モデル比較で使う）。値は時計しだいなので形だけ見る
    assert isinstance(seconds, float) and seconds >= 0
    assert claude.calls == [{"model": "claude-opus-5-5", "stop": "end_turn", "input_tokens": 1200, "output_tokens": 80}]
    assert sent["model"] == "claude-opus-5-5" and sent["output_config"]["effort"] == "low"
    assert sent["fallbacks"] == "default" and sent["betas"] == ["server-side-fallback-2026-07-01"]
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert "<相談文>" in sent["messages"][0]["content"]


@pytest.mark.parametrize("stop,text,error", [
    ("max_tokens", GOOD, ClaudeFailed),
    ("refusal", None, ClaudeRefused),
    ("end_turn", None, ClaudeFailed),
    ("end_turn", '{"subject": "お母', ClaudeFailed),
])
def test_bad_answers_raise(monkeypatch, stop, text, error):
    claude, _ = make(monkeypatch, stop=stop, text=text)
    with pytest.raises(error):
        claude.extract(RS, TEXT)
    assert len(claude.calls) == 1  # 失敗した呼び出しも費用には数える


def test_model_and_effort_can_be_chosen(monkeypatch):
    claude, sent = make(monkeypatch, model="claude-sonnet-5-5", effort="medium")
    claude.extract(RS, TEXT)
    assert sent["model"] == "claude-sonnet-5-5" and sent["output_config"]["effort"] == "medium"
    assert sent["fallbacks"] == "default"


def test_haiku_is_sent_without_server_fallback(monkeypatch):
    # Haiku 5.5 にはサーバー側の代わりのモデルがないので、betas と fallbacks を付けない
    claude, sent = make(monkeypatch, model="claude-haiku-5-5")
    claude.extract(RS, TEXT)
    assert sent["model"] == "claude-haiku-5-5"
    assert "fallbacks" not in sent and "betas" not in sent
