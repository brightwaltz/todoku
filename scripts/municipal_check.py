"""市区町村の制度の調べ（サブエージェントが書いた raw/<団体コード>.json）を機械で確かめ、rules/municipal/<団体コード>.yaml に書く。

使い方：
  uv run python scripts/municipal_check.py check <作業の置き場> [団体コード ...]
  uv run python scripts/municipal_check.py compile <作業の置き場> [団体コード ...]

確かめること（どれか 1 つでも外れた制度は入れず、理由を log に書く）：
  1. 形：型・条件の値が決まった値の中にある。名前・申請先・出典がある。
  2. 公式のホスト：出典の URL のホストが、その市区町村の公式サイトのホスト（site_host）と同じか、その下。
     site_host が lg.jp で終わらないときは、トップの頁の <title> に市区町村の名前があることを確かめる（低い確かさの印を付ける）。
  3. 頁を取り直す：出典の URL が 200 で返り、頁（HTML か PDF）の文字に、制度の名前（name_on_page）がそのまま入っている。
     空白と全角・半角の違いは無視して比べる。

ここでは Claude を呼ばない（curl と pdftotext だけ）。頁の取り直しは 1 つの URL につき 1 回で、結果は cache/ に置く。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "rules" / "municipal"

CATEGORIES = {
    "kaigo-yohin": "紙おむつ・介護用品",
    "haishoku": "配食",
    "haikai": "徘徊の SOS・GPS・見守りシール",
    "iro": "家族介護慰労金・介護手当",
    "koken": "成年後見の費用の助成",
    "hochoki": "補聴器の購入費の助成",
    "kinkyu-tsuho": "緊急通報装置",
    "jutaku": "住宅改造費の助成",
    "shingu": "寝具の乾燥・消毒",
    "ribiyo": "訪問理美容",
    "gaishutsu": "外出支援（タクシー券・移送・バスのパス）",
    "mimamori": "見守り・安否確認",
    "nichijo-yogu": "日常生活用具（火災警報器など）",
    "chusha": "思いやり駐車場の利用証",
    "yutai": "シニアの優待・割引",
    "sonota": "そのほか",
}
CARE = ["support1", "support2", "care1", "care2", "care3", "care4", "care5"]
HOUSEHOLD = ["alone", "elderly_only", "alone_daytime", "with_family"]
TAX = ["household_exempt", "self_exempt", "self_taxed"]
FLAGS = ["uses_diapers", "hearing_difficulty", "dementia_diagnosis", "wandering_risk", "bedridden_6m",
         "fire_risk", "chronic_attack_risk", "on_welfare"]
LINE_MAX = 60
LINES_MAX = 5
UA = "Mozilla/5.0 (compatible; todoku-municipal-check/1.0; +https://github.com/brightwaltz/todoku)"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"\s+", "", s)


def fetch(url: str, cache: Path) -> tuple[int, str]:
    """（状態コード, 頁の文字）。PDF は pdftotext で文字にする。結果は cache に置き、2 回目は取りに行かない。"""
    key = hashlib.sha256(url.encode()).hexdigest()[:20]
    meta, body = cache / f"{key}.json", cache / f"{key}.txt"
    if meta.exists():
        return json.loads(meta.read_text())["status"], body.read_text() if body.exists() else ""
    raw = cache / f"{key}.bin"
    r = subprocess.run(["curl", "-sL", "--max-time", "30", "-A", UA, "-o", str(raw), "-w", "%{http_code} %{content_type}", url],
                       capture_output=True, text=True)
    parts = (r.stdout or "0 ").split(" ", 1)
    status = int(parts[0]) if parts[0].isdigit() else 0
    ctype = parts[1] if len(parts) > 1 else ""
    text = ""
    if status == 200 and raw.exists():
        data = raw.read_bytes()
        if "pdf" in ctype or data[:5] == b"%PDF-":
            p = subprocess.run(["pdftotext", "-q", str(raw), "-"], capture_output=True)
            text = p.stdout.decode("utf-8", "replace")
        else:
            for enc in ("utf-8", "cp932", "euc_jp"):
                try:
                    text = data.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                text = data.decode("utf-8", "replace")
            text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.S | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
    raw.unlink(missing_ok=True)
    body.write_text(text)
    meta.write_text(json.dumps({"url": url, "status": status, "ctype": ctype}))
    return status, text


def host_ok(url: str, site_host: str) -> bool:
    h = (urlparse(url).hostname or "").lower()
    s = site_host.lower().removeprefix("www.")
    return h == s or h.endswith("." + s) or h == "www." + s


def check_city(raw: dict, cache: Path, names: dict[str, str]) -> tuple[dict | None, list[dict]]:
    code, log = raw.get("code", ""), []
    if code not in names:
        return None, [{"code": code, "reason": "団体コードが一覧に無い"}]
    name = names[code]
    if raw.get("blocked"):
        # サイトが自動の取得を止めていて調べられなかった。「見つからなかった」とは言わないので、規則のファイルを作らない
        return None, [{"code": code, "reason": "サイトが自動の取得を止めていて調べられなかった"}]
    site = (raw.get("site_host") or "").lower().strip()
    if not site:
        return None, [{"code": code, "reason": "site_host が無い"}]
    wd = WIKIDATA.get(code, set())
    bare = site.removeprefix("www.")
    if any(bare == h.removeprefix("www.") for h in wd):
        site_level = "wikidata"  # Wikidata（P429 全国地方公共団体コード・P856 公式サイト）のホストと同じ。係とは別の出どころ
    elif site.endswith(".lg.jp"):
        site_level = "lg.jp"
    else:
        site_level = "title"
    if site_level == "title":
        st, txt = fetch(f"https://{site}/", cache)
        title = re.search(r"<title>(.*?)</title>", txt, re.S | re.I)
        # fetch は文字だけを返すので、<title> が消えている。頁の文字の頭 2,000 字に名前があるかで見る
        if st != 200 or norm(name) not in norm(txt[:2000]):
            return None, [{"code": code, "reason": f"公式サイトか確かめられない（{site}、状態 {st}）"}]
    programs = []
    for p in raw.get("programs") or []:
        why = problems(p)
        if not why and not host_ok(p["url"], site):
            why = f"出典が公式サイトの外（{urlparse(p['url']).hostname}）"
        if not why:
            st, txt = fetch(p["url"], cache)
            if st != 200:
                why = f"出典の頁が取れない（状態 {st}）"
            elif norm(p["name_on_page"]) not in norm(txt):
                why = "出典の頁に、制度の名前が見つからない"
        if why:
            log.append({"code": code, "name": p.get("name_on_page") or p.get("name"), "reason": why})
            continue
        programs.append(p)
    return {"code": code, "name": name, "site": site, "site_level": site_level, "programs": programs,
            "not_found": [c for c in raw.get("not_found") or [] if c in CATEGORIES],
            "read_via": raw.get("read_via", "subagent")}, log


def enrich(city: dict, cache: Path) -> dict:
    """裁定役に渡す頁の抜き出しを付ける（page_text・page_excerpt・more_urls・more_excerpts）。"""
    for p in city["programs"]:
        p["page_text"] = page_path(p["url"], cache)
        p["page_excerpt"] = excerpt(Path(p["page_text"]).read_text(), p["name_on_page"])
        # 条件が書かれた別の公式の頁（対象者の表・要綱の PDF など）。公式のホストで 200 のものだけ残す
        more, ex = [], []
        for u in (p.get("more_urls") or [])[:3]:
            if isinstance(u, str) and u.startswith("http") and host_ok(u, city["site"]):
                st, txt = fetch(u, cache)
                if st == 200 and txt.strip():
                    more.append(u)
                    ex.append(re.sub(r"\s+", " ", unicodedata.normalize("NFKC", txt))[:3000])
        p["more_urls"], p["more_excerpts"] = more, ex
    return city


READ_BY = {"subagent": "Claude Sonnet 5.5（サブエージェント、公式サイトを読んだ）",
           "api": "Claude Sonnet 5.5（Claude API、公式サイトの中だけを検索・取得して読んだ）"}


def problems(p: dict) -> str:
    for k in ("category", "name", "name_on_page", "url", "summary", "where"):
        if not isinstance(p.get(k), str) or not p[k].strip():
            return f"{k} が無い"
    if p["category"] not in CATEGORIES:
        return f"知らない型 {p['category']}"
    if not p["url"].startswith("https://") and not p["url"].startswith("http://"):
        return "出典が URL でない"
    if len(p["summary"]) > LINE_MAX * 2:
        return "summary が長すぎる"
    w = p.get("who") or {}
    for k in ("care_levels", "household", "tax", "flags"):
        if w.get(k) == []:
            w[k] = None  # 空の並びは「条件なし」と同じ（null にそろえる）
    for k, allowed in (("care_levels", CARE), ("household", HOUSEHOLD), ("tax", TAX), ("flags", FLAGS)):
        v = w.get(k)
        if v is not None and (not isinstance(v, list) or not v or any(x not in allowed for x in v)):
            return f"who.{k} の値が決まった値でない（{v}）"
    if w.get("age_min") is not None and not (isinstance(w["age_min"], int) and 40 <= w["age_min"] <= 100):
        return f"who.age_min が数でない（{w['age_min']}）"
    if w.get("at_home") not in (None, True):
        return "who.at_home は true か null"
    lines = w.get("at_application") or []
    if not isinstance(lines, list) or len(lines) > LINES_MAX or any(not isinstance(x, str) or len(x) > LINE_MAX for x in lines):
        return "at_application が長すぎるか形が違う"
    if p.get("confidence") not in ("high", "low"):
        return "confidence が high か low でない"
    return ""


def to_yaml(city: dict, date: str, read_by: str) -> dict:
    out = []
    for p in city["programs"]:
        w = p.get("who") or {}
        who = {k: w[k] for k in ("age_min", "care_levels", "household", "tax", "at_home", "flags") if w.get(k) is not None}
        item = {"category": p["category"], "name": p["name"].strip(), "name_on_page": p["name_on_page"].strip(),
                "summary": p["summary"].strip(), "who": who,
                "at_application": [x.strip() for x in w.get("at_application") or []],
                "where": p["where"].strip(), "url": p["url"].strip(), "confidence": p["confidence"]}
        if p.get("amount"):
            item["amount"] = str(p["amount"]).strip()
        if p.get("more_urls"):
            item["more_urls"] = list(p["more_urls"])
        out.append(item)
    key = "pref" if len(city["code"]) == 2 else "city"
    return {key: city["code"], "name": city["name"], "site": city["site"], "site_level": city["site_level"],
            "review": {"read_by": read_by, "checked_by": "機械（公式のホスト・頁の取り直し・名前の一致）", "date": date},
            "programs": out, "not_found": city["not_found"]}


def _wikidata() -> dict[str, set[str]]:
    """団体コード → 公式サイトのホスト（~/dev/todoku-municipal-work/sites/sites.tsv。Wikidata の CC0 のデータから作った）。"""
    path = Path.home() / "dev" / "todoku-municipal-work" / "sites" / "sites.tsv"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        parts = line.split("\t")
        if len(parts) >= 4:
            out[parts[0]] = set(parts[3].split())
    return out


WIKIDATA = _wikidata()


def municipality_names() -> dict[str, str]:
    m = json.loads((ROOT / "web" / "municipalities.json").read_text())
    out = {c: n for p in m["prefectures"] for c, n in p["cities"]}
    out.update({p["code"]: p["name"] for p in m["prefectures"]})  # 都道府県は 2 桁のコード
    return out


def page_path(url: str, cache: Path) -> str:
    return str(cache / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.txt")


def excerpt(text: str, name: str, before: int = 200, after: int = 2500, cap: int = 4000) -> str:
    """裁定役に渡す頁の抜き出し。制度の名前が出る所ごとに前 200 字・後ろ 2,500 字を取り、重なりをまとめて 4,000 字まで。

    頁の頭（題・道しるべ）にも名前が出るので、最後に出る所から 3 つまでを使う（本文の見出しはたいてい後ろにある）。
    """
    flat = unicodedata.normalize("NFKC", re.sub(r"\s+", " ", text))
    key = unicodedata.normalize("NFKC", name).strip()
    hits = [m.start() for m in re.finditer(re.escape(key), flat)] or [0]
    spans: list[list[int]] = []
    for i in sorted(hits[-3:]):
        a, b = max(0, i - before), i + after
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    return " … ".join(flat[a:b] for a, b in spans)[:cap]


def apply_verdicts(programs: list[dict], verdicts: list[dict], log: list[dict], code: str) -> list[dict]:
    """自動の裁定役（JUDGE.md）の keep・fix・drop を当てる。直したあとも形の検査を通す。"""
    by_i = {v.get("i"): v for v in verdicts if isinstance(v, dict)}
    out = []
    for i, p in enumerate(programs):
        v = by_i.get(i, {"verdict": "keep"})
        if v.get("verdict") == "drop":
            log.append({"code": code, "name": p.get("name_on_page"), "reason": "裁定役が落とした：" + str(v.get("reason", ""))})
            continue
        if v.get("verdict") == "fix":
            q = dict(p)
            for k, val in (v.get("fields") or {}).items():
                if k in ("who", "amount", "category", "summary", "where", "name"):
                    q[k] = val
            q["judge_fix"] = str(v.get("reason", ""))
            why = problems(q)
            if why:
                log.append({"code": code, "name": p.get("name_on_page"), "reason": "裁定役の直しが形の検査に通らない：" + why})
                continue
            p = q
        out.append(p)
    return out


def main(argv: list[str]) -> None:
    """check <work> [codes]：形・ホスト・頁の取り直しを確かめ、work/passed/<code>.json に書く。
    compile <work> [codes]：passed と（あれば）work/judged/<code>.json の裁定を合わせ、rules/municipal/<code>.yaml に書く。"""
    mode, work = argv[0], Path(argv[1])
    raw_dir, passed_dir, judged_dir = work / "raw", work / "passed", work / "judged"
    cache = work / "cache"
    for d in (cache, passed_dir, judged_dir, work / "logs"):
        d.mkdir(exist_ok=True)
    names, today = municipality_names(), dt.date.today().isoformat()
    src_dir = raw_dir if mode == "check" else passed_dir
    codes = argv[2:] or sorted(p.stem for p in src_dir.glob("*.json"))
    all_log, summary = [], []
    for code in codes:
        if mode == "check":
            raw = json.loads((raw_dir / f"{code}.json").read_text())
            city, log = check_city(raw, cache, names)
            all_log += log
            found = len(raw.get("programs") or [])
            if city is None:
                summary.append((code, found, 0))
                continue
            enrich(city, cache)
            (passed_dir / f"{code}.json").write_text(json.dumps(city, ensure_ascii=False, indent=1))
            summary.append((code, found, len(city["programs"])))
        else:
            city = json.loads((passed_dir / f"{code}.json").read_text())
            n = len(city["programs"])
            jf = judged_dir / f"{code}.json"
            judged = jf.exists()
            if judged:
                city["programs"] = apply_verdicts(city["programs"], json.loads(jf.read_text()), all_log, code)
            OUT.mkdir(parents=True, exist_ok=True)
            via = "api" if str(city.get("read_via", "")).startswith("api") else "subagent"
            doc = to_yaml(city, today, READ_BY[via])
            doc["review"]["judged_by"] = "自動の裁定役（Claude Sonnet 5.5、柴田先生のこれまでの基準）" if judged else "なし"
            (OUT / f"{code}.yaml").write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False, width=200))
            summary.append((code, n, len(city["programs"])))
    log_path = work / "logs" / f"{mode}-{dt.datetime.now():%Y%m%d-%H%M%S}.json"
    log_path.write_text(json.dumps({"summary": summary, "dropped": all_log}, ensure_ascii=False, indent=1))
    for code, f, k in summary:
        print(f"{code} {names.get(code, '?')}：{'見つけた' if mode == 'check' else '機械を通った'} {f}・残った {k}")
    reasons: dict[str, int] = {}
    for x in all_log:
        r = re.sub(r"（.*?）|：.*", "", x["reason"])
        reasons[r] = reasons.get(r, 0) + 1
    print("落とした理由", reasons, "→", log_path)


if __name__ == "__main__":
    main(sys.argv[1:])
