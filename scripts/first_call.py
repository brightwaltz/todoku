"""本物の Claude で、デモの相談文を 1 回だけ読み取らせる（API の料金がかかる。呼び出しは 1 回）。

鍵は環境変数 ANTHROPIC_API_KEY から読む。この台本は鍵のファイルを開かない。
使い方：uv run python scripts/first_call.py [相談文]
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todoku import rules as rules_mod  # noqa: E402
from todoku.claude import EFFORT, MODEL, RealClaude  # noqa: E402
from todoku.questions import next_question  # noqa: E402

DEMO = "町田市に住む母（82歳）が先月退院してから、家の中で転びやすくなりました。介護保険は使ったことがありません。私は会社勤めで、日中は母ひとりです。"


def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else DEMO
    rs = rules_mod.load()
    claude = RealClaude()
    print(f"モデル {MODEL}・effort {EFFORT}")
    print(f"相談文：{text}\n")
    try:
        ex = claude.extract(rs, text)
    finally:
        for c in claude.calls:
            print(f"答えたモデル {c['model']}・止まった理由 {c['stop']}・入力 {c['input_tokens']}・出力 {c['output_tokens']} トークン")
    print(f"\n呼び方：{ex.subject}")
    for e in ex.facts:
        print(f"  [{e.confidence:4s}] {rs.facts[e.fact].name}＝{rs.facts[e.fact].label_of(e.value)}（根拠「{e.evidence}」）")
    print(f"捨てた読み取り：{len(ex.dropped)} 件")
    q = next_question(rs, ex.confirmed(), ex.suggestions(), asked=0)
    print(f"最初の聞き返し：{rs.facts[q.fact].name if q else 'なし'}")


if __name__ == "__main__":
    main()
