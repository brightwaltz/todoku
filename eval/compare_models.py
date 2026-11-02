"""読み取りのモデル比較：同じ架空事例の相談文を、Opus 5.5・Sonnet 5.5・Haiku 5.5 に読ませて比べる。

窓口で Claude が担う「入口」（話し言葉 → 根拠つきの事実）に、どのモデルを使うかを数字で決めるための道具。
正解は事例の answers（相談した人が、聞かれたら答える内容）。answers は人物の答えの全部で、
相談文に書いてあるのはその一部なので、次のように分けて数える。

- 確信「高」で返した事実（そのまま判定に使われる）
    answers と同じ値 → 正しい。違う値 → 誤り。answers が「わからない」なのに値を決めたのも誤り
- 確信「低」で返した事実（窓口では確かめる問いに回る）
    正しい値の候補か、違う値の候補かを別に数える（違っていても、利用者が押し直せる）
- 取れた率は、3 モデルのどれかが確信「高」で正しく取れた（事例, 事実）を分母にした相対の値。
  相談文に書いてある事実の印を人が付けていないので、絶対の取れた率ではない

使い方（API の料金がかかる。鍵はリポジトリの外のファイルから渡す）：
  uv run python eval/compare_models.py --dry-run                         見込みの費用だけを出す
  uv run --env-file ~/.config/bwcj26/.env python eval/compare_models.py   3 モデルで 30 件
  uv run python eval/compare_models.py --fake                            鍵なし（試験モードの読み取り。道具の確かめ用）
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todoku import rules as rules_mod  # noqa: E402
from todoku.claude import ClaudeFailed, Extraction, FakeClaude  # noqa: E402
from todoku.engine import DONT_KNOW  # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
REPORT = HERE / "model_comparison.md"
MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]
# 1M トークンあたりの米ドル（入力, 出力）。2026-11-03 に claude-api の資料で確かめた。Haiku は 10 万トークンまでの値
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-sonnet-5-5": (2.00, 10.00), "claude-haiku-5-5": (0.10, 0.50)}
# 見込みの計算に使う 1 回あたりのトークン数（11/2 の Opus の 1 回目：入力 2,384・出力 272）。出力は多めに見る
ESTIMATE_TOKENS = (2400, 800)


def load_cases() -> list[dict]:
    return yaml.safe_load((HERE / "cases" / "cases.yaml").read_text(encoding="utf-8"))


def score_reads(extraction: Extraction, answers: dict[str, Any]) -> dict:
    """1 件の読み取りを事例の answers と比べる。"""
    out = {"high_right": [], "high_wrong": [], "low_right": [], "low_wrong": []}
    for e in extraction.facts:
        truth = answers.get(e.fact, DONT_KNOW)
        right = truth != DONT_KNOW and e.value == truth
        key = ("high" if e.confidence == "high" else "low") + ("_right" if right else "_wrong")
        out[key].append(e.fact)
    return out


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (0.0, 0.0))
    return input_tokens / 1e6 * pin + output_tokens / 1e6 * pout


def summarize(per_model: dict[str, dict], cases: list[dict]) -> dict[str, dict]:
    reference = set()
    for runs in per_model.values():
        for cid, r in runs["cases"].items():
            reference |= {(cid, f) for f in r.get("high_right", [])}
    table = {}
    for model, runs in per_model.items():
        rows = runs["cases"].values()
        ok = [r for r in rows if "error" not in r]
        hr = sum(len(r["high_right"]) for r in ok)
        hw = sum(len(r["high_wrong"]) for r in ok)
        calls = runs["calls"]
        tin = sum(c["input_tokens"] for c in calls)
        tout = sum(c["output_tokens"] for c in calls)
        secs = sorted(c.get("seconds", 0.0) for c in calls)
        table[model] = {
            "cases": len(cases), "failed": len(rows) - len(ok),
            "high_right": hr, "high_wrong": hw,
            "high_precision": hr / (hr + hw) if hr + hw else None,
            "relative_recall": hr / len(reference) if reference else None,
            "low_right": sum(len(r["low_right"]) for r in ok), "low_wrong": sum(len(r["low_wrong"]) for r in ok),
            "answered_by": sorted({c["model"] for c in calls}),
            "input_tokens": tin, "output_tokens": tout,
            "usd": round(sum(cost_of(c["model"], c["input_tokens"], c["output_tokens"]) for c in calls), 4),
            "median_seconds": secs[len(secs) // 2] if secs else None,
        }
    return {"reference_size": len(reference), "models": table}


def write_report(summary: dict, stamp: str, effort: str, fake: bool) -> str:
    lines = [
        "# 読み取りのモデル比較",
        "",
        f"実行：{stamp}（effort {effort}{'、試験モード' if fake else ''}）。`eval/compare_models.py` が書き出した。"
        "正解は事例の answers。取れた率は、3 モデルのどれかが確信「高」で正しく取れた"
        f"（事例, 事実）{summary['reference_size']} 個を分母にした相対の値。",
        "",
        "| モデル | 確信「高」で正しい | 確信「高」で誤り | 「高」の正しさ | 取れた率（相対） | 「低」で正しい候補 | 「低」で違う候補 | 失敗 | 費用（米ドル） | 1 回の秒数（中央値） |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for model, t in summary["models"].items():
        prec = "—" if t["high_precision"] is None else f"{t['high_precision']:.1%}"
        rec = "—" if t["relative_recall"] is None else f"{t['relative_recall']:.1%}"
        lines.append(
            f"| {model} | {t['high_right']} | {t['high_wrong']} | {prec} | {rec} | {t['low_right']} | {t['low_wrong']} "
            f"| {t['failed']}/{t['cases']} | {t['usd']:.3f} | {t['median_seconds']} |"
        )
    lines += ["", "誤りの内訳（事例と事実）は `eval/out/` の JSON にある。"]
    return "\n".join(lines) + "\n"


def make_client(model: str, effort: str, fake: bool):
    if fake:
        client = FakeClaude()
        client.calls = []
        return client
    from todoku.claude import RealClaude

    return RealClaude(model=model, effort=effort)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--models", nargs="+", default=MODELS)
    ap.add_argument("--effort", default="low")
    ap.add_argument("--limit", type=int, default=0, help="先頭から何件だけ読むか（0 は全部）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args(argv)

    rs = rules_mod.load()
    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]
    est = sum(cost_of(m, ESTIMATE_TOKENS[0] * len(cases), ESTIMATE_TOKENS[1] * len(cases)) for m in args.models)
    print(f"事例 {len(cases)} 件 × モデル {len(args.models)} 個 = 呼び出し {len(cases) * len(args.models)} 回、"
          f"見込み {est:.2f} 米ドル（1 回あたり入力 {ESTIMATE_TOKENS[0]}・出力 {ESTIMATE_TOKENS[1]} トークンで計算）")
    if args.dry_run:
        return 0

    per_model: dict[str, dict] = {}
    for model in args.models:
        client = make_client(model, args.effort, args.fake)
        runs = {"cases": {}, "calls": []}
        for case in cases:
            try:
                extraction = client.extract(rs, case["text"])
            except ClaudeFailed as e:
                runs["cases"][case["id"]] = {"error": str(e)}
                continue
            except Exception as e:  # 通信の失敗などは記録して次へ（SDK が 2 回まで再試行したあと）
                runs["cases"][case["id"]] = {"error": f"{type(e).__name__}: {e}"}
                continue
            r = score_reads(extraction, case.get("answers") or {})
            r["read"] = {e.fact: [e.value, e.confidence] for e in extraction.facts}
            r["wrong_detail"] = {f: [r["read"][f][0], (case.get("answers") or {}).get(f, DONT_KNOW)] for f in r["high_wrong"]}
            runs["cases"][case["id"]] = r
        runs["calls"] = list(getattr(client, "calls", []))
        per_model[model] = runs
        t = summarize({model: runs}, cases)["models"][model]
        print(f"  {model}：高で正しい {t['high_right']}・高で誤り {t['high_wrong']}・失敗 {t['failed']}・{t['usd']:.3f} 米ドル")

    summary = summarize(per_model, cases)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    OUT.mkdir(exist_ok=True)
    name = "compare-models-" + dt.datetime.now().strftime("%Y%m%d-%H%M") + ("-fake" if args.fake else "") + ".json"
    (OUT / name).write_text(json.dumps({"effort": args.effort, "fake": args.fake, "summary": summary,
                                        "runs": per_model}, ensure_ascii=False, indent=1), encoding="utf-8")
    report = write_report(summary, stamp, args.effort, args.fake)
    if not args.fake:
        REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"詳しい結果：eval/out/{name}" + ("" if args.fake else "、表：eval/model_comparison.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
