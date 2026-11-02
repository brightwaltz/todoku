"""合言葉なしで見られる見本 3 件を、本物の Claude で 1 回ずつ読み取らせ、web/samples.json に書き戻す（API の料金がかかる。呼び出しは計 3 回）。

画面の見本は、ここで書いた読み取りの結果をそのまま使う（見本のときはサーバが Claude を呼ばない）。
鍵は環境変数 ANTHROPIC_API_KEY から読む。この台本は鍵のファイルを開かない。
使い方：uv run --env-file ~/.config/bwcj26/.env python scripts/make_samples.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from todoku import rules as rules_mod  # noqa: E402
from todoku.claude import EFFORT, MODEL, Extraction, RealClaude  # noqa: E402

SAMPLES = ROOT / "web" / "samples.json"


def to_raw(ex: Extraction) -> dict:
    """読み取りの結果を、Claude が返す形（bool の値は "true" / "false"）に戻す。サーバは毎回これを検査し直す。"""
    return {
        "subject": ex.subject,
        "facts": [
            {"fact": e.fact, "value": json.dumps(e.value) if isinstance(e.value, bool) else e.value,
             "confidence": e.confidence, "evidence": e.evidence}
            for e in ex.facts
        ],
    }


def main() -> None:
    samples = json.loads(SAMPLES.read_text(encoding="utf-8"))
    rs = rules_mod.load()
    claude = RealClaude()
    print(f"モデル {MODEL}・effort {EFFORT}")
    for s in samples:
        print(f"\n[{s['id']}] {s['text']}")
        before = len(claude.calls)
        try:
            ex = claude.extract(rs, s["text"])
        finally:
            for c in claude.calls[before:]:
                print(f"答えたモデル {c['model']}・止まった理由 {c['stop']}・入力 {c['input_tokens']}・出力 {c['output_tokens']} トークン")
        for e in ex.facts:
            print(f"  [{e.confidence:4s}] {rs.facts[e.fact].name}＝{rs.facts[e.fact].label_of(e.value)}（根拠「{e.evidence}」）")
        print(f"  捨てた読み取り：{len(ex.dropped)} 件")
        s["reading"] = to_raw(ex)
        s["made"] = {"model": c["model"], "date": date.today().isoformat()}
        # 1 件ずつ書き戻す（途中で止まっても、読み取れた分は残る）
        SAMPLES.write_text(json.dumps(samples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n書き戻しました：{SAMPLES}")


if __name__ == "__main__":
    main()
