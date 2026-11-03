"""読み B：規則を持たない Claude に、公式資料と人物の説明だけで ○×？ を付けさせる。

手順は eval/PROTOCOL.md。要点：
- 道具を渡さない Messages API の呼び出し。規則ファイル・判定結果・規則を書いた人の要約は、どれも入力に入れない
  （指示で禁じるのではなく、読む手段がない）。渡すのは、制度の名前、eval/sources.py で保存した公式資料の文、
  事例の相談文と人物の説明だけ。
- 付け方の定義は、先生の正解付けページの「はじめに」と同じ文。
- 判定ごとに、公式資料からの引用を付けさせる。引用は adjudicate.py が保存した原文と機械で照らし合わせる。
- 費用は呼び出しごとに usage から数え、--max-usd を超えそうなら止める。

使い方（鍵は先生が `!` で渡す。費用がかかる）：
  uv run --env-file ~/.config/bwcj26/.env python eval/blind_read.py facts --max-usd 1
  uv run --env-file ~/.config/bwcj26/.env python eval/blind_read.py read --model claude-fable-5-1 --programs 1,11 --label pilot --max-usd 3
  uv run --env-file ~/.config/bwcj26/.env python eval/blind_read.py read --model claude-sonnet-5-5 --programs all --label main --max-usd 8
結果は eval/out/blind/（.gitignore 済み。町田市の引用を含むのでリポジトリに入れない）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "eval"))
from sources import load_index, program_sources, program_text  # noqa: E402

OUT = ROOT / "eval" / "out" / "blind"
CASES = ROOT / "eval" / "cases" / "cases.yaml"

# 1M トークンあたりのドル（claude-api スキルの models.md と prompt-caching.md、2026-11-03 に読んだ）。
# 書き込みは 5 分の TTL で入力の 1.25 倍。
PRICES = {
    "claude-fable-5-1": {"in": 10.0, "out": 50.0, "cache_write": 12.5, "cache_read": 0.25},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_write": 2.5, "cache_read": 0.20},
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_write": 5.0, "cache_read": 0.20},
}

# 28〜32（町田市以外の人に出す市区町村の型）は、町田市以外に住む事例だけで採点する（先生の判断、2026-11-03）
LOCAL_TYPES = range(28, 33)

STATES = ["当たりうる", "当たらない", "済んでいる", "申請済み・結果待ち", "決められない"]

READ_SYSTEM = """あなたは、高齢者と介護する家族のための公的制度について、公式資料だけを根拠に判断する独立の読み手です。
渡されるのは、1 つの制度の名前と、その制度の公式資料（国・自治体などのページや通知の本文）と、架空の相談事例の一覧です。
それぞれの事例の人に、その制度が当たりうるかを決めてください。

付け方（人が正解を付けるときと同じ定義です）
- 当たりうる：公式の条件に、今すぐ、または前の手続き（要介護認定など）を済ませれば当たる。本人が望むか、役に立ちそうかは問わない。
- 当たらない：公式の条件に当たらない。
- 済んでいる：すでに受けている・済んでいる（認定を受けている人の「認定の申請」など）。
- 申請済み・結果待ち：その制度をすでに申請して、結果を待っている。
- 決められない：説明だけでは決められない。決め手（住民税の区分、勤め方など）が説明に書かれていないとき。決め手がないときは、調べ込まずに「決められない」にする。ふつうの読み手がそう読むことは、そう読んでかまわない。

読み方
- 各事例の「人物の説明」に本当のことが書いてある。「相談文」は相談した人が窓口に書いた文で、思い違いを含むことがある。食い違うときは人物の説明に従う。
- 条件は、渡された公式資料に書いてあることだけで判断する。資料にない条件を自分の知識で足さない。資料から決められないときは「決められない」にする。
- 自治体の資料に書かれた制度は、その自治体に住む人のための制度として読む。
- 「決め手」には、判断を決めた条件を自分の言葉で 1 文で書く。
- 「引用」には、決め手の根拠になる公式資料の文を、一字一句そのまま抜き出す（言い換え・要約・つなぎ合わせをしない。40 字から 120 字くらい）。「決められない」で引ける文がなければ空にしてよい。
- 「資料」には、引用した資料の番号（【資料N】の N）を書く。引用が空なら 0。
- すべての事例について 1 つずつ答える。"""

FACTS = ["severe_constant_care", "money_management_difficulty", "hearing_certifiable"]
FACT_ANSWERS = {"はい": True, "いいえ": False, "わからない": "__dont_know__"}

FACTS_SYSTEM = """あなたは、架空の相談事例の「聞かれたときの答え」を書き足す係です。
各事例には、相談した人が窓口に書いた「相談文」と、本当のことを書いた「人物の説明」があります。
窓口から次の問いを聞かれたら、相談した人が何と答えるかを、人物の説明から決めてください。
- 答えは「はい」「いいえ」「わからない」のどれか。
- 説明にはっきり書いてあればそれに従う。ふつうの読み手がそう読むことは、そう読んでかまわない。
- 相談した人が知らないはずのこと（検査を受けていない結果など）や、説明から決められないことは「わからない」。
- 「根拠」には、決め手にした人物の説明の文を短く抜き出す（なければ「書かれていない」）。"""


def load_cases() -> list[dict]:
    data = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data["cases"]


def cases_in_scope(num: int, cases: list[dict]) -> list[dict]:
    if num in LOCAL_TYPES:
        return [c for c in cases if c["answers"].get("lives_in_machida") is False]
    return cases


def cases_block(cases: list[dict]) -> str:
    parts = []
    for c in cases:
        parts.append(f"■ 事例 {c['id']}\n相談文：{c['text'].strip()}\n人物の説明：{c['profile'].strip()}")
    return "事例の一覧\n\n" + "\n\n".join(parts)


def read_schema(case_ids: list[str]) -> dict:
    item = {
        "type": "object",
        "properties": {
            "case": {"type": "string", "enum": case_ids},
            "state": {"type": "string", "enum": STATES},
            "deciding": {"type": "string"},
            "quote": {"type": "string"},
            "source": {"type": "integer"},
            "reason": {"type": "string"},
        },
        "required": ["case", "state", "deciding", "quote", "source", "reason"],
        "additionalProperties": False,
    }
    return {"type": "object", "properties": {"judgments": {"type": "array", "items": item}},
            "required": ["judgments"], "additionalProperties": False}


def facts_schema(case_ids: list[str]) -> dict:
    ans = {"type": "string", "enum": list(FACT_ANSWERS)}
    props = {"case": {"type": "string", "enum": case_ids}, **{f: ans for f in FACTS},
             **{f"{f}_evidence": {"type": "string"} for f in FACTS}}
    item = {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}
    return {"type": "object", "properties": {"answers": {"type": "array", "items": item}},
            "required": ["answers"], "additionalProperties": False}


def cost_of(model: str, usage) -> float:
    p = PRICES[model]
    return (usage.input_tokens * p["in"] + usage.output_tokens * p["out"]
            + (usage.cache_creation_input_tokens or 0) * p["cache_write"]
            + (usage.cache_read_input_tokens or 0) * p["cache_read"]) / 1e6


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def call(client, model: str, system: str, content: list[dict], schema: dict, max_tokens: int):
    with client.messages.stream(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason not in ("end_turn", "stop_sequence"):
        raise RuntimeError(f"止まった理由：{msg.stop_reason}")
    text = next(b.text for b in msg.content if b.type == "text")
    return json.loads(text), msg


def make_client():
    import anthropic
    return anthropic.Anthropic(timeout=900.0, max_retries=2)


def cmd_facts(args) -> None:
    model = "claude-opus-5-5"
    cases = load_cases()
    defs = yaml.safe_load((ROOT / "rules" / "facts.yaml").read_text(encoding="utf-8"))
    defs = defs.get("facts", defs)
    qs = "\n".join(f"- {f}：{defs[f]['question']}（参考：{defs[f].get('check', '')}）" for f in FACTS)
    content = [{"type": "text", "text": f"問い\n{qs}\n\n{cases_block(cases)}"}]
    ids = [c["id"] for c in cases]
    client = make_client()
    data, msg = call(client, model, FACTS_SYSTEM, content, facts_schema(ids), 32000)
    usd = cost_of(model, msg.usage)
    if usd > args.max_usd:
        print(f"  上限 {args.max_usd} ドルを超えた（{usd:.3f} ドル）", file=sys.stderr)
    got = {a["case"] for a in data["answers"]}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"facts-{datetime.now():%Y%m%d-%H%M}.json"
    path.write_text(json.dumps({"model": model, "system_sha256": sha(FACTS_SYSTEM), "usd": usd,
                                "usage": msg.usage.model_dump(), "answers": data["answers"]},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"事実の書き足し：{len(got)}/{len(ids)} 件、{usd:.3f} ドル → {path.relative_to(ROOT)}")
    if got != set(ids):
        print(f"  答えのない事例：{sorted(set(ids) - got)}", file=sys.stderr)
        sys.exit(1)


def dry_run(model: str, nums: list[int], progs: dict, cases: list[dict], index: dict) -> None:
    """API を呼ばずに、入力の大きさと費用の目安を出す（日本語 1 字≒1 トークン、出力は 1 事例 250＋考える分 8,000 の仮定）。"""
    p = PRICES[model]
    block = cases_block(cases)
    total, first = 0.0, True
    for num in nums:
        scope = cases_in_scope(num, cases)
        if not scope:
            continue
        text, _ = program_text(num, progs, index)
        b = len(block) if len(scope) == len(cases) else len(cases_block(scope))
        cached_in = b * (p["cache_write"] if first or len(scope) != len(cases) else p["cache_read"])
        first = first and len(scope) != len(cases)
        out = 250 * len(scope) + 8000
        usd = (cached_in + (len(READ_SYSTEM) + len(text)) * p["in"] + out * p["out"]) / 1e6
        total += usd
        print(f"  {num:>2} 事例 {len(scope):>2} 件、資料 {len(text):>6} 字、目安 {usd:.3f} ドル")
    print(f"目安の合計 {total:.2f} ドル（{model}）")


def cmd_read(args) -> None:
    model = args.model
    if model not in PRICES:
        sys.exit(f"知らないモデル：{model}")
    progs = program_sources()
    nums = list(progs) if args.programs == "all" else [int(x) for x in args.programs.split(",")]
    cases = load_cases()
    index = load_index()
    if args.dry_run:
        dry_run(model, nums, progs, cases, index)
        return
    client = make_client()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = OUT / f"read-{model}-{args.label}-{stamp}.json"
    run = {"model": model, "label": args.label, "effort": "high", "system_sha256": sha(READ_SYSTEM),
           "started": stamp, "max_usd": args.max_usd, "programs": {}, "usd": 0.0}
    full_block = cases_block(cases)
    for num in nums:
        if run["usd"] >= args.max_usd * 0.92:
            print(f"上限に近いので止めた（{run['usd']:.2f}／{args.max_usd} ドル）。残り：{nums[nums.index(num):]}")
            break
        scope = cases_in_scope(num, cases)
        if not scope:
            continue
        text, used = program_text(num, progs, index)
        block = full_block if len(scope) == len(cases) else cases_block(scope)
        content = [
            # 事例の一覧は制度をまたいで同じなので、先に置いてキャッシュする
            {"type": "text", "text": block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": f"制度：{progs[num]['name']}\n\n公式資料\n\n{text}\n\n"
                                     f"上の事例の一覧の全員（{len(scope)} 件）について、この制度が当たりうるかを答えてください。"},
        ]
        ids = [c["id"] for c in scope]
        t0 = time.time()
        try:
            data, msg = call(client, model, READ_SYSTEM, content, read_schema(ids), 48000)
        except Exception as e:  # 1 制度の失敗で全体を止めない。記録して先へ
            run["programs"][str(num)] = {"error": repr(e)[:500]}
            print(f"  {num:>2} 失敗：{e!r}"[:300], file=sys.stderr)
            continue
        usd = cost_of(model, msg.usage)
        run["usd"] += usd
        got = [j["case"] for j in data["judgments"]]
        missing = sorted(set(ids) - set(got))
        run["programs"][str(num)] = {"name": progs[num]["name"], "sources": used, "cases": ids,
                                     "judgments": data["judgments"], "missing": missing,
                                     "duplicates": sorted({c for c in got if got.count(c) > 1}),
                                     "usage": msg.usage.model_dump(), "usd": usd,
                                     "seconds": round(time.time() - t0, 1)}
        path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {num:>2} {progs[num]['name']}：{len(got)}/{len(ids)} 件、{usd:.3f} ドル、"
              f"{time.time() - t0:.0f} 秒（累計 {run['usd']:.2f} ドル）{'  欠け ' + ','.join(missing) if missing else ''}")
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"合計 {run['usd']:.2f} ドル → {path.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("facts")
    f.add_argument("--max-usd", type=float, required=True)
    r = sub.add_parser("read")
    r.add_argument("--model", required=True)
    r.add_argument("--programs", required=True, help="all か 1,11 のような番号")
    r.add_argument("--label", required=True)
    r.add_argument("--max-usd", type=float, required=True)
    r.add_argument("--dry-run", action="store_true", help="API を呼ばずに大きさと費用の目安だけ出す")
    args = ap.parse_args()
    {"facts": cmd_facts, "read": cmd_read}[args.cmd](args)


if __name__ == "__main__":
    main()
