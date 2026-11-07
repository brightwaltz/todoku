"""API で回す台本（scripts/municipal_api.py）。本物の API は呼ばず、作り物の client で確かめる。"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
api = pytest.importorskip("municipal_api")

needs_work = pytest.mark.skipif(not (api.WORK / "INSTRUCTIONS.md").exists(), reason="作業の置き場が無い")


def _usage(i=1000, o=100, s=0, f=0):
    return NS(input_tokens=i, output_tokens=o, cache_creation_input_tokens=0, cache_read_input_tokens=0,
              server_tool_use=NS(web_search_requests=s, web_fetch_requests=f))


def _msg(stop, text="", usage=None):
    return NS(stop_reason=stop, content=[NS(type="text", text=text)], usage=usage or _usage())


class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(json.loads(json.dumps(kw, default=lambda o: "<obj>")))
        msg = self.replies.pop(0)

        class S:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return msg
        return S()


def test_cost_counts_tokens_and_searches():
    usd, s, f = api.cost_of(_usage(1_000_000, 100_000, s=3, f=5))
    assert (s, f) == (3, 5)
    assert usd == pytest.approx(2.0 + 1.0 + 0.03)


def test_domains_strip_www_and_keep_ascii_only():
    assert api.domains(["www.city.nagoya.jp", "city.nagoya.jp", "www.pref.ibaraki.jp"]) == ["city.nagoya.jp", "pref.ibaraki.jp"]
    assert api.domains(["www.аmazon.com"]) == []  # キリル文字の а は入れない


def test_json_block_takes_the_last_block():
    text = "見つけた 2\n```json\n{\"a\": 1}\n```\n直し\n```json\n{\"a\": 2}\n```"
    assert api.json_block(text) == {"a": 2}
    assert api.json_block("前置き [ {\"i\": 0, \"verdict\": \"keep\"} ]") == [{"i": 0, "verdict": "keep"}]
    assert api.json_block('```json\n[{"i": 0, "verdict": "keep",},\n]\n```') == [{"i": 0, "verdict": "keep"}]


@needs_work
def test_api_prompts_have_no_files_or_read():
    for text in (api.reader_system(False), api.reader_system(True), api.judge_system()):
        assert "ファイルを 1 つだけ書きます" not in text and "Read で読みます" not in text
        assert "/Users/" not in text
    assert "```json のブロックで 1 つだけ" in api.reader_system(False)


@needs_work
def test_read_resumes_pause_turn_and_sums_cost(tmp_path):
    record = {"code": "999999", "name": "違う名前", "site_host": "www.city.nagoya.jp", "programs": []}
    client = FakeClient([_msg("pause_turn", usage=_usage(s=2, f=4)),
                         _msg("end_turn", "```json\n" + json.dumps(record) + "\n```", _usage(s=1, f=3))])
    budget = api.Budget(10, 2)
    names = {"231002": "名古屋市"}
    raw, tally = api.read_city(client, "231002", names, budget, "high", tmp_path)
    assert len(client.calls) == 2
    # 送り返すときは、前の返事を assistant として足し、残りの回数を max_uses にする
    assert client.calls[1]["messages"][-1]["role"] == "assistant"
    assert client.calls[1]["tools"][0]["max_uses"] == api.SEARCH_MAX - 2
    assert client.calls[1]["tools"][1]["max_uses"] == api.FETCH_MAX - 4
    assert all(t["allowed_domains"] == ["city.nagoya.jp"] for t in client.calls[0]["tools"])
    assert (tally["searches"], tally["fetches"]) == (3, 7)
    assert budget.spent == pytest.approx(tally["usd"])
    assert raw["code"] == "231002" and raw["name"] == "名古屋市" and raw["read_via"].startswith("api:")


@needs_work
def test_one_city_over_its_cap_stops(tmp_path):
    client = FakeClient([_msg("pause_turn", usage=_usage(i=2_000_000))])
    with pytest.raises(api.Stop):
        api.read_city(client, "231002", {"231002": "名古屋市"}, api.Budget(10, 1.0), "high", tmp_path)


@needs_work
def test_unexpected_stop_gets_one_wrap_up(tmp_path):
    """京都市（11/7）：stop_reason が tool_use のまま JSON を書かずに止まったら、1 回だけ記録を書くよう頼む。"""
    record = {"site_host": "www.city.kyoto.lg.jp", "programs": []}
    client = FakeClient([_msg("tool_use", ""), _msg("end_turn", "```json\n" + json.dumps(record) + "\n```")])
    tally: dict = {}
    raw, _ = api.read_city(client, "261009", {"261009": "京都市"}, api.Budget(10, 2), "high", tmp_path, tally)
    assert client.calls[1]["messages"][-1] == {"role": "user", "content": [{"type": "text", "text": api.WRAP_UP}]}
    assert tally["wrapped"] and raw["code"] == "261009"
    # 2 回目も JSON が無ければ、それ以上は頼まずに失敗にし、かかった費用は tally に残る
    # （そのあと道具なしで JSON を取り出す呼び出しが 1 回ある）
    client = FakeClient([_msg("tool_use", ""), _msg("end_turn", "書けませんでした"), _msg("end_turn", "無い")])
    tally = {}
    with pytest.raises(ValueError):
        api.read_city(client, "261009", {"261009": "京都市"}, api.Budget(10, 2), "high", tmp_path, tally)
    assert len(client.calls) == 3 and tally["calls"] == 2 and tally["usd"] > 0


@needs_work
def test_pending_tool_use_gets_a_result_before_the_wrap_up(tmp_path):
    """釧路市（11/7）：返事に tool_use が残ったまま止まったら、その答え（tool_result）を先に置いてから記録を頼む。"""
    record = {"site_host": "www.city.kushiro.lg.jp", "programs": []}
    stuck = NS(stop_reason="tool_use", usage=_usage(),
               content=[NS(type="text", text=""), NS(type="tool_use", id="toolu_x", name="web_fetch", input={})])
    client = FakeClient([stuck, _msg("end_turn", "```json\n" + json.dumps(record) + "\n```")])
    api.read_city(client, "012068", {"012068": "釧路市"}, api.Budget(10, 2), "high", tmp_path)
    first = client.calls[1]["messages"][-1]["content"][0]
    assert first["type"] == "tool_result" and first["tool_use_id"] == "toolu_x"
    assert all(t["allowed_callers"] == ["direct", "code_execution_20260120"] for t in client.calls[0]["tools"])
