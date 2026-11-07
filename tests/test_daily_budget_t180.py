"""T-180：合言葉なしで相談を送れる形（TODOKU_OPEN）と、1 日の費用の上限（TODOKU_DAILY_USD ÷ TODOKU_MACHINES）。"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from todoku import app as app_mod
from todoku.claude import FakeClaude, RealClaude, cost_usd

client = TestClient(app_mod.app)
DEMO = "町田市に住む母（82歳）が先月退院してから、家の中で転びやすくなりました。介護保険は使ったことがありません。"
USAGE = SimpleNamespace(input_tokens=10_000, output_tokens=1_000)  # Opus 5.5 で 0.04 + 0.02 = 0.06 ドル


class PaidFake(FakeClaude):
    """本物の Claude の形（name が claude）で、呼ぶたびに usage を渡す偽物。"""
    name = "claude"
    on_usage = None

    def extract(self, rules, text):
        self.on_usage("claude-opus-5-5", USAGE)
        return super().extract(rules, text)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    monkeypatch.setattr(app_mod, "_hits_all", app_mod.deque())
    monkeypatch.setattr(app_mod, "_spent", {"day": "", "usd": 0.0})
    monkeypatch.setattr(app_mod, "DAILY_USD", 30.0)
    monkeypatch.setattr(app_mod, "MACHINES", 1)
    monkeypatch.setattr(app_mod, "OPEN", False)
    monkeypatch.setattr(app_mod, "PASSPHRASE", "")


def at(monkeypatch, text):
    monkeypatch.setattr(app_mod, "clock", lambda: datetime.fromisoformat(text))


def spend(usd, day="2026-11-07"):
    app_mod._spent.update(day=day, usd=usd)


def send(word=None):
    headers = {} if word is None else {"x-aikotoba": word}
    return client.post("/api/consult", json={"text": DEMO}, headers=headers)


def test_closed_by_default_asks_for_passphrase(monkeypatch):
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    assert send().status_code == 401
    assert send("himitsu").status_code == 200


def test_open_lets_anyone_consult_and_summarize(monkeypatch):
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    monkeypatch.setattr(app_mod, "OPEN", True)
    assert send().status_code == 200
    assert client.post("/api/summary", json={"facts": {}, "subject": "母"}).status_code == 200


def test_open_without_passphrase_does_not_close_the_real_claude(monkeypatch):
    monkeypatch.setattr(app_mod, "CLAUDE", PaidFake())
    app_mod.CLAUDE.on_usage = app_mod.note_cost
    assert send().status_code == 503  # 今のまま：鍵があって合言葉が無ければ閉じる（設定し忘れ）
    monkeypatch.setattr(app_mod, "OPEN", True)
    assert send().status_code == 200  # 開くと決めたときは開く


def test_reaching_the_day_cap_closes_but_passphrase_still_passes(monkeypatch):
    at(monkeypatch, "2026-11-07T15:00:00+09:00")
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    monkeypatch.setattr(app_mod, "OPEN", True)
    spend(30.0)
    r = send()
    assert r.status_code == 429 and r.headers.get("x-todoku-closed") == "1"
    assert r.json()["detail"] == app_mod.CLOSED_FOR_TODAY
    assert send("wrong").status_code == 429  # 間違えた合言葉は上限を外さない
    assert send("himitsu").status_code == 200


def test_cap_resets_at_midnight_japan_time(monkeypatch):
    monkeypatch.setattr(app_mod, "OPEN", True)
    at(monkeypatch, "2026-11-07T23:59:00+09:00")
    spend(30.0)
    assert send().status_code == 429
    # 日本時間の 0 時（UTC ではまだ 11/7 の 15 時）で戻る
    at(monkeypatch, "2026-11-08T00:00:00+09:00")
    assert send().status_code == 200
    assert app_mod._spent == {"day": "2026-11-08", "usd": 0.0}


@pytest.mark.parametrize("spent, code", [(14.99, 200), (15.0, 429)])
def test_cap_is_per_machine(monkeypatch, spent, code):
    monkeypatch.setattr(app_mod, "OPEN", True)
    monkeypatch.setattr(app_mod, "MACHINES", 2)
    at(monkeypatch, "2026-11-07T12:00:00+09:00")
    spend(spent)
    assert send().status_code == code


def test_spending_is_counted_except_for_the_admin(monkeypatch):
    at(monkeypatch, "2026-11-07T12:00:00+09:00")
    monkeypatch.setattr(app_mod, "CLAUDE", PaidFake())
    app_mod.CLAUDE.on_usage = app_mod.note_cost
    monkeypatch.setattr(app_mod, "PASSPHRASE", "himitsu")
    monkeypatch.setattr(app_mod, "OPEN", True)
    assert send().status_code == 200
    assert app_mod._spent["usd"] == pytest.approx(0.06)
    assert send("himitsu").status_code == 200
    assert app_mod._spent["usd"] == pytest.approx(0.06)  # 合言葉が合った回は数えない
    assert send().status_code == 200
    assert app_mod._spent["usd"] == pytest.approx(0.12)


def test_health_tells_open_and_rounded_left(monkeypatch):
    at(monkeypatch, "2026-11-07T12:00:00+09:00")
    monkeypatch.setattr(app_mod, "OPEN", True)
    monkeypatch.setattr(app_mod, "MACHINES", 2)
    spend(3.7)
    h = client.get("/api/health").json()
    assert h["open"] is True and h["passphrase"] is False
    assert h["today"] == {"daily_usd": 30.0, "machines": 2, "left_usd": 11, "closed": False}
    spend(15.2)
    assert client.get("/api/health").json()["today"]["closed"] is True


def test_real_client_reports_usage_even_when_refused(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    claude = RealClaude(keep_calls=False)
    seen = []
    claude.on_usage = lambda model, usage: seen.append((model, usage.input_tokens, usage.output_tokens))
    response = SimpleNamespace(model="claude-opus-5-5", stop_reason="refusal", stop_details=None, content=[],
                               usage=SimpleNamespace(input_tokens=1200, output_tokens=80))
    claude._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response)))
    with pytest.raises(Exception):
        claude._json("s", "u", {}, 100)
    assert seen == [("claude-opus-5-5", 1200, 80)]


def test_unknown_model_is_priced_at_the_highest():
    assert cost_usd("claude-opus-5-5", USAGE) == pytest.approx(0.06)
    assert cost_usd("claude-someday-9", USAGE) == pytest.approx(cost_usd("claude-fable-5-1", USAGE))
