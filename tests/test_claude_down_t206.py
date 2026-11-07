"""T-206：Claude の読み取りが使えないとき（11/7 16 時、組織の API が月の上限を超えた）、書いた人を「少し待ってから」で止めない。
サーバは今までどおり 503 で断り、Claude の失敗のときだけ印のヘッダ x-todoku-claude: failed を付ける。画面はその印を見て、
書いた文を残したまま、質問だけの道（/api/begin、T-204）で続ける。合言葉の設定し忘れの 503・受付終了の 429 は今までどおり。
"""
import os
from pathlib import Path

os.environ.setdefault("TODOKU_FAKE_CLAUDE", "1")

import anthropic  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from todoku import app as app_mod  # noqa: E402
from todoku.app import app  # noqa: E402
from todoku.claude import ClaudeFailed  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)


def test_claude_failure_is_marked(monkeypatch):
    for err in (ClaudeFailed("上限"), anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com"))):
        def broken(rules, text, err=err):
            raise err

        monkeypatch.setattr(app_mod.CLAUDE, "extract", broken)
        res = client.post("/api/consult", json={"text": "大阪に住む父が退院しました"})
        assert res.status_code == 503 and res.headers.get("x-todoku-claude") == "failed"


def test_missing_passphrase_503_is_not_marked(monkeypatch):
    class RealLike:
        name = "claude"

        def extract(self, rules, text):
            raise AssertionError("合言葉なしで Claude を呼んだ")

    monkeypatch.setattr(app_mod, "CLAUDE", RealLike())
    monkeypatch.setattr(app_mod, "PASSPHRASE", "")
    monkeypatch.setattr(app_mod, "OPEN", False, raising=False)
    res = client.post("/api/consult", json={"text": "母のことで相談です"})
    assert res.status_code == 503 and "x-todoku-claude" not in res.headers


def test_page_falls_back_to_questions_keeping_the_text():
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'claudeDown: res.headers.get("x-todoku-claude") === "failed"' in js
    send = js[js.index("async function send()"):js.index("function withClock")]
    assert "if (!e.claudeDown) throw e;" in send
    assert 'return { ...(await api("/api/begin", { municipality: pick.city })), fallback: true };' in send
    # 書いた文は吹き出しに残し（地名はお住まいの欄に先に入る、T-200）、まとめ（Claude）は送らない（blank と同じ扱い）
    assert "const own = !data.sample && (!data.blank || data.fallback);" in js
    assert "state.event = Boolean(data.event || data.blank);" in js
    assert "文章の読み取りがいま使えないので、いくつか質問します。" in js
