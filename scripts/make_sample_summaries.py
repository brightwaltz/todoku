"""見本の道の「まず知っておきたいこと」（Claude が書くまとめ）を前もって作り、web/samples.json の summary に書き戻す（T-045、D-0013 の A）。

見本ごとに、保存した読み取りから、問い（最大 3 問）に台本の答え（samples.json の script）で
答えて結果まで進め（台本にない問いは、読み取りの候補があればそれ、なければ先頭の選択肢）、そのときまとめに渡る制度の組（key）と、まとめの文を残す。画面の見本の道では、組が同じときだけこの文を出す
（/api/sample/summary）。規則を直して組が変わったら tests/test_sample_summary.py が落ちるので、そのときは作り直す。

本物の Claude を呼ぶのは見本の数だけ（今は 4 回。まとめだけで、読み取りは呼ばない）。鍵は環境変数 ANTHROPIC_API_KEY から読む。
この台本は鍵のファイルを開かない。

使い方：
  見積もり（API を呼ばない）：uv run python scripts/make_sample_summaries.py --estimate
  試し（FakeClaude で流し、書き戻さない）：uv run python scripts/make_sample_summaries.py --fake
  本物：uv run --env-file "$TODOKU_ENV_FILE" python scripts/make_sample_summaries.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if "--estimate" in sys.argv or "--fake" in sys.argv:
    os.environ["TODOKU_FAKE_CLAUDE"] = "1"  # サーバの口（app.CLAUDE）も試験用にしておく
from todoku import app  # noqa: E402
from todoku.claude import MODEL, FakeClaude, RealClaude, check_extraction  # noqa: E402

SAMPLES = ROOT / "web" / "samples.json"
# 1 百万トークンあたりの米ドル（入力, 出力）。eval/compare_models.py の PRICES と同じ値
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
MAX_OUT = 4000  # RealClaude.summarize の max_tokens


def walk(s: dict) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    """見本を台本の答えで結果まで進める。返すのは (事実, 答えた問いと値, 呼び方, 市区町村コード)。

    画面の道（/api/sample → /api/step）と同じ関数を通す。
    """
    muni = s.get("municipality", "")
    view = app.consult_view(check_extraction(app.RULES, s["text"], s["reading"]), muni)
    facts, suggestions, subject = dict(view["facts"]), dict(view["suggestions"]), view["subject"]
    script = s.get("script", {})
    answers: dict[str, Any] = {}
    while view["question"]:
        q = view["question"]
        values = [o["value"] for o in q["options"]]
        fid = q["fact"]
        if fid in script and script[fid] in values:
            value = script[fid]
        elif fid in suggestions and suggestions[fid] in values:
            value = suggestions[fid]  # 読み取りの候補（相談文に書いてあること）を、そのとおりと答える
        else:
            value = values[0]
        facts[fid] = value
        suggestions.pop(fid, None)
        answers[fid] = value
        app.apply_place(facts, suggestions, muni)
        view = app.step_view(facts, suggestions, subject, len(answers), muni)
    return facts, answers, subject, muni


def make(samples: list[dict], claude: Any, write=None) -> None:
    """samples の各見本に summary を入れる。write を渡すと、1 件ごとに呼ぶ（途中で止まっても、作れた分は残る）。"""
    for s in samples:
        if s.get("reading") is None:
            continue
        facts, answers, subject, muni = walk(s)
        shown = app.summarized(facts, muni)
        before = len(getattr(claude, "calls", []))
        lines = claude.summarize(app.RULES, subject[:12] or "ご本人", shown)
        calls = getattr(claude, "calls", [])[before:]
        for c in calls:
            print(f"  答えたモデル {c['model']}・止まった理由 {c['stop']}・入力 {c['input_tokens']}・出力 {c['output_tokens']} トークン")
        print(f"[{s['id']}] 答え {answers}・まとめに渡す制度 {len(shown)} 件・文 {len(lines)} 行")
        for line in lines:
            print(f"  - {line.text}")
        s["summary"] = {
            "answers": answers,
            "key": app.summary_key(shown),
            "lines": [{"program_ids": line.program_ids, "text": line.text} for line in lines],
            "made": {"model": calls[-1]["model"] if calls else claude.name, "date": date.today().isoformat()},
        }
        if write:
            write(samples)


class Counting(RealClaude):
    """API を呼ばずに、まとめの頼みの字数だけを数える（見積もり用）。"""

    name = "counting"

    def __init__(self) -> None:  # noqa: D107（鍵も通信も要らない）
        self.chars: list[int] = []

    def _json(self, system: str, user: str, schema: dict, max_tokens: int) -> dict:
        self.chars.append(len(system) + len(user) + len(json.dumps(schema, ensure_ascii=False)))
        return {"lines": []}


def estimate(samples: list[dict]) -> str:
    """費用の上限の見込み。入力は 1 字 1 トークン（日本語は 1 字 1 トークンより少ないことが多い）、出力は max_tokens まで使うとして数える。"""
    counter = Counting()
    make(samples, counter)
    pin, pout = PRICES.get(MODEL, PRICES["claude-opus-5-5"])
    n = len(counter.chars)
    tin = sum(counter.chars)
    usd = tin / 1e6 * pin + n * MAX_OUT / 1e6 * pout
    return (f"{MODEL} を {n} 回、入力 約 {tin} 字（≒トークンの上限）・出力 各 {MAX_OUT} トークンまで → "
            f"多くて 約 {usd:.2f} 米ドル（{pin}/{pout} ドル毎百万トークン）")


def main() -> None:
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    if "--estimate" in sys.argv:
        print(estimate(samples))
        return
    if "--fake" in sys.argv:
        make(samples, FakeClaude())
        print("\n試しなので書き戻していません")
        return
    claude = RealClaude()
    print(f"モデル {MODEL}")

    def write(data: list[dict]) -> None:
        SAMPLES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    make(samples, claude, write)
    print(f"\n書き戻しました：{SAMPLES}")


if __name__ == "__main__":
    main()
