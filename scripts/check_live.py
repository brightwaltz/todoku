"""deploy した窓口を、外から確かめる（Claude は呼ばない。費用はかからない）。

使い方:
    python3 -I scripts/check_live.py https://todoku.fly.dev
    python3 -I scripts/check_live.py https://todoku.fly.dev --expect-open   # TODOKU_OPEN=1 を設定した後（T-194）
    python3 -I scripts/check_live.py http://127.0.0.1:8000 --allow-fake   # 手元の試験モード（TODOKU_FAKE_CLAUDE=1）

標準ライブラリだけで動く（uv sync は要らない）。1 つでも NG があれば終了コード 1。

見ること:
- /api/health：本物の Claude で動いていて（engine が claude）、合言葉が設定されている（fly secrets の設定し忘れがない）
- 合言葉なしで開いた置き場（/api/health の open が true、TODOKU_OPEN=1）：1 日の費用の上限が health に出ている。
  合言葉は管理者の外し方なので、無くても NG にしない。--expect-open を付けると、open でなければ NG（設定し忘れに気づける）
- 画面：GET / と HEAD /・HEAD /api/health が 200、安全のヘッダ（CSP など 4 つ）と Cache-Control: no-cache、タブのアイコン
- 見本：全部を、どの問いも 1 つ目の選択肢で答えて結果まで通す（/api/sample と /api/step。審査の人が合言葉なしで通る道）
- 縮めて返す：Accept-Encoding: gzip を付けた /api/sample が gzip で返り、解くと JSON になる（T-061）
- 合言葉の口：合言葉なしの /api/consult が 401 で断られる（503 なら合言葉の設定し忘れ、200 なら合言葉なしで開いている）。
  open の置き場では送らない（合言葉なしで Claude が読み取り、費用がかかって 1 日の上限にも数えられるため）

合言葉は受け取らないし送らない。/api/summary（合言葉があると Claude を呼ぶ）には送らない。
/api/consult には、open でない置き場にだけ合言葉なしで 1 回送る。間違えた合言葉と同じく、その IP の回数制限（既定は 10 分に 20 回）の 1 回に数えられる。
HEAD・安全のヘッダ・タブのアイコンは、2026-11-04 より前の版にはない。これが NG なら、新しい版が deploy されていない見込み。
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.error
import urllib.request

NOT_THIS = "__not__"  # web/app.js の NOT_THIS と同じ（「合っていますか？」の「いいえ」）
SECURITY_HEADERS = ("Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options")
NEW_IN_DEV = "（2026-11-04 より前の版にはない。NG なら新しい版が deploy されていない見込み）"


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, dict, object]:
    """(状態コード, ヘッダ, 本文) を返す。4xx・5xx も例外にせず返す。"""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        res = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        res = e
    with res:
        raw = res.read().decode("utf-8", "replace")
        headers = {k.lower(): v for k, v in res.headers.items()}
        try:
            payload: object = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            payload = raw
        return res.status, headers, payload


def gzipped_sample(base: str, sample_id: str) -> tuple[bool, str]:
    """Accept-Encoding: gzip で /api/sample を引き、gzip で返って解くと JSON になるか。call() は縮めずに受け取る。"""
    req = urllib.request.Request(base + "/api/sample", data=json.dumps({"id": sample_id}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            raw, encoding = res.read(), res.headers.get("Content-Encoding", "")
    except urllib.error.HTTPError as e:
        return False, f"{e.code}"
    if encoding != "gzip":
        return False, f"Content-Encoding が「{encoding}」・{len(raw)} バイト"
    try:
        size = len(gzip.decompress(raw))
        json.loads(gzip.decompress(raw))
    except (OSError, ValueError) as e:
        return False, f"解けない: {e}"
    return True, f"{size} → {len(raw)} バイト"


def walk(base: str, start: dict) -> tuple[int, list[dict]]:
    """web/app.js と同じ形で、どの問いも 1 つ目の選択肢で答え、問いがなくなるまで /api/step に送る。"""
    state = {"facts": dict(start["facts"]), "suggestions": dict(start["suggestions"]),
             "subject": start["subject"], "asked": 0,
             "municipality": start["place"]["code"] if start.get("place") else ""}
    data = start
    while data["question"] is not None:
        if state["asked"] > start["max_questions"]:
            raise AssertionError(f"{start['max_questions']} 問で終わらない")
        q = data["question"]
        answer = q["options"][0]
        facts, suggestions = dict(state["facts"]), dict(state["suggestions"])
        if "place" in answer:
            suggestions.pop("lives_in_machida", None)
            change = {"suggestions": suggestions, "municipality": answer["place"]}
        else:
            if not (q["confirm"] and answer["value"] == NOT_THIS):
                facts[q["fact"]] = answer["value"]
            suggestions.pop(q["fact"], None)
            change = {"facts": facts, "suggestions": suggestions}
        state = {**state, **change, "asked": state["asked"] + 1}
        status, _, data = call(base, "POST", "/api/step",
                               {k: state[k] for k in ("facts", "suggestions", "subject", "asked", "municipality")})
        if status != 200:
            raise AssertionError(f"/api/step が {status}: {data}")
    return state["asked"], data["results"]


def daily_limit(today: object) -> tuple[bool, str]:
    """/api/health の today（T-180）を読む。受付が終わった日も NG にはしない（見本は動く）。"""
    if not isinstance(today, dict):
        return False, f"today がない: {today}"
    usd, machines, left, closed = (today.get(k) for k in ("daily_usd", "machines", "left_usd", "closed"))
    nums = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (usd, machines, left))
    if not (nums and isinstance(closed, bool) and usd >= 0 and machines >= 1):  # 上限 0 は毎日受付を終えた形
        return False, f"today の形が違う: {today}"
    detail = f"1 日 {usd:g} ドル ÷ {machines} 台 = 1 台 {usd / machines:g} ドル、応えたマシンの残り {left} ドル"
    if closed:
        detail += "。このマシンは今日の受付を終えている（見本だけ）"
    return True, detail


def run(base: str, allow_fake: bool = False, expect_open: bool = False) -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    out: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, bool(ok), detail))

    status, _, health = call(base, "GET", "/api/health")
    health = health if isinstance(health, dict) else {}
    check("GET /api/health が 200 で ok", status == 200 and health.get("ok") is True, f"{status} {health}")
    engine, passphrase = health.get("engine"), health.get("passphrase")
    # open は T-180 より前の版の health には無い（無ければ合言葉が要る置き場として確かめる）
    is_open = health.get("open") is True
    if allow_fake:
        check("読み取りの道（--allow-fake）", engine in ("claude", "fake"), f"engine={engine}")
    else:
        check("本物の Claude で動いている（ANTHROPIC_API_KEY がある）", engine == "claude", f"engine={engine}")
    if expect_open:
        check("合言葉なしで相談を送れる（TODOKU_OPEN=1）", is_open, f"open={health.get('open')}")
    if is_open:
        ok, detail = daily_limit(health.get("today"))
        check("1 日の費用の上限が出ている（TODOKU_DAILY_USD・TODOKU_MACHINES）", ok, detail)
        check("合言葉（open の置き場では管理者の外し方。無くてもよい）", True, f"passphrase={passphrase}")
    elif engine == "claude" or not allow_fake:
        check("合言葉が設定されている（TODOKU_PASSPHRASE）", passphrase is True, f"passphrase={passphrase}")

    status, headers, page = call(base, "GET", "/")
    check("GET / が 200", status == 200 and isinstance(page, str) and "<html" in page.lower(), str(status))
    missing = [h for h in SECURITY_HEADERS if h.lower() not in headers]
    check("安全のヘッダ 4 つ" + NEW_IN_DEV, not missing, "ない: " + "、".join(missing) if missing else "")
    check("GET / の Cache-Control が no-cache", headers.get("cache-control") == "no-cache", headers.get("cache-control", ""))
    for path in ("/", "/api/health"):
        status, _, _ = call(base, "HEAD", path)
        check(f"HEAD {path} が 200" + NEW_IN_DEV, status == 200, str(status))
    status, headers, _ = call(base, "GET", "/static/favicon.svg")
    check("タブのアイコン /static/favicon.svg" + NEW_IN_DEV,
          status == 200 and headers.get("content-type", "").startswith("image/svg"), f"{status} {headers.get('content-type', '')}")

    status, _, samples = call(base, "GET", "/api/samples")
    samples = samples if isinstance(samples, list) else []
    check("見本の一覧", status == 200 and bool(samples), f"{status} {len(samples)} 件")
    ready = [s for s in samples if s.get("ready")]
    if ready:
        ok, detail = gzipped_sample(base, ready[0]["id"])
        check("/api/sample を gzip で縮めて返す" + NEW_IN_DEV, ok, detail)
    for s in samples:
        name = f"見本「{s.get('label', s.get('id'))}」を結果まで通す"
        if not s.get("ready"):
            check(name, False, "読み取りの結果が入っていない（ready が false）")
            continue
        status, _, start = call(base, "POST", "/api/sample", {"id": s["id"]})
        if status != 200 or not isinstance(start, dict):
            check(name, False, f"/api/sample が {status}")
            continue
        try:
            asked, results = walk(base, start)
        except AssertionError as e:
            check(name, False, str(e))
            continue
        shown = sum(1 for r in results if r["status"] in ("eligible", "unknown"))
        check(name, bool(results), f"問い {asked} 問・結果 {len(results)} 件・画面に出す {shown} 件")

    if is_open:
        # 合言葉なしで送ると、本物の Claude が読み取る（費用がかかり、1 日の上限にも数えられる）。
        # 受付が終わった日でも送らない：数えるのはマシンごとなので、ほかのマシンに届けば読み取ってしまう
        check("合言葉なしの /api/consult（open の置き場では Claude が読み取るので送らない）", True, "skip")
    elif engine == "claude" or passphrase:
        status, _, body = call(base, "POST", "/api/consult", {"text": "確かめのための送信です（合言葉なし）。"})
        expected = {401: "", 503: "合言葉が設定されていない", 200: "合言葉なしで読み取りが動いた"}
        check("合言葉なしの /api/consult が 401（この IP の回数制限の 1 回に数えられる）", status == 401,
              f"{status} {expected.get(status, body)}".strip())
    else:
        check("合言葉の口（試験モードで合言葉なしなので送らない）", True, "skip")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("base", help="窓口の URL（例: https://todoku.fly.dev）")
    p.add_argument("--allow-fake", action="store_true", help="試験モード（engine が fake）でも NG にしない")
    p.add_argument("--expect-open", action="store_true", help="合言葉なしで開いている（TODOKU_OPEN=1）ことを求める")
    args = p.parse_args(argv)
    try:
        results = run(args.base, args.allow_fake, args.expect_open)
    except (urllib.error.URLError, OSError) as e:
        print(f"NG  {args.base} に届かない: {e}")
        return 1
    for name, ok, detail in results:
        print(f"{'OK' if ok else 'NG'}  {name}" + (f"  — {detail}" if detail else ""))
    bad = sum(1 for _, ok, _ in results if not ok)
    print(f"{len(results)} 項目中 NG {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
