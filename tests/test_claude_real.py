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


def make(monkeypatch, stop="end_turn", text=GOOD):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    claude = RealClaude()
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
    assert claude.calls == [{"model": "claude-opus-5-5", "stop": "end_turn", "input_tokens": 1200, "output_tokens": 80}]
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
