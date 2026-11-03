import os

# 下で todoku.app を読み込むと、その時の環境で Claude の呼び先が決まる。テストでは本物の API を呼ばない（test_app.py と同じ）
os.environ["TODOKU_FAKE_CLAUDE"] = "1"

import pytest  # noqa: E402

from todoku import app as app_mod  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_rate_limit(monkeypatch):
    # 回数制限は 10 分の窓で数える。ほかのテストの相談で上限（接続元ごと・アプリ全体）に届かないよう、毎回数え直す
    monkeypatch.setattr(app_mod, "_hits", app_mod.defaultdict(app_mod.deque))
    monkeypatch.setattr(app_mod, "_hits_all", app_mod.deque())
    monkeypatch.setattr(app_mod, "_swept", 0.0)  # 見回した時刻も戻す。残すと、次のテストで見回さないことがある
