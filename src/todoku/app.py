"""相談窓口の Web API と画面。

サーバは相談文も答えも保存しない。画面が事実を持ち、毎回それを送ってくる。
合言葉と回数制限は手間を増やすだけの対策。使われすぎを止めるのは API の専用ワークスペースの上限。
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import rules as rules_mod
import anthropic

from .claude import ALWAYS_CONFIRM, ClaudeFailed, ClaudeRefused, Extraction, check_extraction, make_client
from .engine import DONT_KNOW, SHOWN, Result, evaluate, known, with_implied
from .questions import MAX_QUESTIONS, next_question

WEB_DIR = Path(os.environ.get("TODOKU_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
PASSPHRASE = os.environ.get("TODOKU_PASSPHRASE", "")
# Claude を呼ぶ API の回数制限（IP ごと、10 分あたり）
RATE_LIMIT = int(os.environ.get("TODOKU_RATE_LIMIT", "20"))
NOT_THIS = "__not__"
MACHIDA = "132098"  # 町田市の団体コード
# 地域包括支援センターを探すページ（厚生労働省 介護サービス情報公表システム、PDL1.0。リンクだけ）。都道府県が分からなければ入口
KAIGOKENSAKU = "https://www.kaigokensaku.mhlw.go.jp/"
CENTER = "地域包括支援センター"


def center_search(code: str) -> str:
    """団体コードの頭 2 桁が都道府県コード。2026-11-03 に 47 都道府県中 5 件（01・13・14・46・47）を開いて確かめた。"""
    if code not in PLACES:
        return KAIGOKENSAKU
    return f"{KAIGOKENSAKU}{code[:2]}/index.php?action_kouhyou_pref_seikatu_search_facility_search=true&SearchType=community"

STATUS_LABEL = {
    "eligible": "当てはまる見込み",
    "after_prereq": "先に手続きが要る",
    "unknown": "情報が足りない",
    "ineligible": "当てはまらない",
    "done": "済んでいる",
}

app = FastAPI(title="todoku", docs_url=None, redoc_url=None)
RULES = rules_mod.load()
CLAUDE = make_client()
_hits: dict[str, deque] = defaultdict(deque)


def load_places() -> dict[str, tuple[str, str]]:
    """団体コード → (都道府県名, 市区町村名)。画面の選択肢と同じ web/municipalities.json から作る。"""
    data = json.loads((WEB_DIR / "municipalities.json").read_text(encoding="utf-8"))
    return {code: (pref["name"], name) for pref in data["prefectures"] for code, name in pref["cities"]}


PLACES = load_places()
# 合言葉なしで見られる見本。reading は前もって本物の Claude が読み取った結果（scripts/make_samples.py で書く）
SAMPLES: list[dict] = json.loads((WEB_DIR / "samples.json").read_text(encoding="utf-8"))


class ConsultIn(BaseModel):
    text: str = Field(max_length=2000)
    municipality: str = Field("", max_length=6)


class StepIn(BaseModel):
    facts: dict[str, Any] = {}
    suggestions: dict[str, Any] = {}
    subject: str = "ご本人"
    asked: int = 0
    municipality: str = Field("", max_length=6)


class SummaryIn(BaseModel):
    facts: dict[str, Any] = {}
    subject: str = "ご本人"
    municipality: str = Field("", max_length=6)


class SampleIn(BaseModel):
    id: str = Field(max_length=40)


def guard(request: Request, costly: bool) -> None:
    # 合言葉と回数制限は Claude を呼ぶ口だけ。聞き返し（/api/step）は規則だけで費用がかからないので、合言葉なしで通す
    if costly:
        if PASSPHRASE and request.headers.get("x-aikotoba") != PASSPHRASE:
            raise HTTPException(401, "合言葉が違います")
        # Fly.io では接続元が Fly の中継になるので、中継が付ける Fly-Client-IP で数える（回数制限は手間を増やすだけ。費用を止めるのはワークスペースの上限）
        ip = request.headers.get("fly-client-ip") or (request.client.host if request.client else "?")
        now = time.time()
        hits = _hits[ip]
        while hits and now - hits[0] > 600:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            raise HTTPException(429, "しばらく時間をおいてから試してください")
        hits.append(now)


def clean_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """画面から来た事実のうち、定義にあり値が正しいものだけを残す。"""
    out = {}
    for fid, value in raw.items():
        fact = RULES.facts.get(fid)
        if fact is None:
            continue
        if fact.type == "bool" and not isinstance(value, bool) and value != DONT_KNOW:
            continue  # 1 や "true" は受け付けない（True と 1 は == で等しいため、ここで弾く）
        if value == DONT_KNOW or value in fact.values():
            out[fid] = value
    return out


def apply_place(facts: dict[str, Any], suggestions: dict[str, Any], code: str) -> None:
    """画面で選んだ市区町村を、読み取りや画面の事実より優先する。一覧にないコードは無視する。"""
    if code not in PLACES:
        return
    facts["lives_in_machida"] = code == MACHIDA
    suggestions.pop("lives_in_machida", None)


def place_view(code: str) -> dict | None:
    if code not in PLACES:
        return None
    pref, name = PLACES[code]
    return {"code": code, "pref": pref, "name": name}


def fact_view(fid: str, value: Any) -> dict:
    fact = RULES.facts[fid]
    return {"fact": fid, "name": fact.name, "value": value,
            "label": "わからない" if value == DONT_KNOW else fact.label_of(value), "question": fact.question}


def result_view(r: Result, facts: dict[str, Any], subject: str, municipality: str = "") -> dict:
    p = r.program
    missing = [
        {"fact": f, "name": RULES.facts[f].name, "question": personalize(RULES.facts[f].question, subject),
         "check": RULES.facts[f].check,
         "answered_dont_know": facts.get(f) == DONT_KNOW}
        for f in r.missing_facts
    ]
    # 町田市に住んでいると分かっているときだけ、町田市の窓口・電話・書類の名前を出す
    here = facts.get("lives_in_machida") is True
    other = {} if here else p.elsewhere
    sources = [s for s in p.sources if here or "city.machida.tokyo.jp" not in s.url] or p.sources
    apply = dict(other.get("apply", p.apply))
    if CENTER in apply.get("where", "") + apply.get("how", ""):
        apply["center_search"] = center_search(municipality)  # 申請先に挙げたセンターを、その場で探せるように
    return {
        "id": p.id,
        "number": p.number,
        "name": p.name,
        "level": rules_mod.LEVELS[p.level],
        "kind": rules_mod.KINDS[p.kind],
        "status": r.status,
        "status_label": STATUS_LABEL[r.status],
        "summary": p.summary,
        "reasons": [
            {"label": leaf.label, "state": leaf.state,
             # はい・いいえの事実は ✓・✕ だけで分かるので値を添えない（「町田市以外にお住まい（いいえ）」と読めてしまうため）
             "value": None if leaf.fact is None or not known(leaf.value) or RULES.facts[leaf.fact].type == "bool"
             else RULES.facts[leaf.fact].label_of(leaf.value),
             "prereq": RULES.programs[leaf.prereq].name if leaf.prereq else None}
            for leaf in r.leaves
        ],
        "missing": missing,
        "waiting_on": [{"id": w, "name": RULES.programs[w].name} for w in r.waiting_on],
        "at_application": p.at_application,
        "apply": apply,
        "bring": other.get("bring", p.bring),
        "amount": p.amount,
        "timing": other.get("timing", p.timing),
        "sources": [
            {"url": s.url, "publisher": s.publisher, "title": s.title, "license": s.license,
             "checked": s.checked.isoformat(), "quote": s.quote}
            for s in sources
        ],
        # 行政手続データの手続（根拠法令・条項）。画面では出典の名前を添える
        "procedures": [{"name": d.name, "law": d.law, "article": d.article, "match": d.match} for d in p.procedures],
        "procedures_source": rules_mod.PROCEDURES_DATASET if p.procedures else "",
    }


def personalize(text: str, subject: str) -> str:
    return text.replace("ご本人", subject) if subject and subject != "ご本人" else text


def question_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int) -> dict | None:
    q = next_question(RULES, facts, suggestions, asked=asked)
    if q is None:
        return None
    fact = RULES.facts[q.fact]
    affects = [RULES.programs[p].name for p in q.affects]
    if q.suggested in ALWAYS_CONFIRM.get(q.fact, ()):
        # 取り違えやすい値（認定の区分）は「合っていますか？」と聞かない。思い違いのままでも「はい」を押せてしまうため。
        # 読んだ値は文で知らせるだけにして、全部の選択肢から書類を見て選んでもらう。確かめられなければ読んだ値で進める
        read = fact.label_of(q.suggested)
        return {
            "fact": q.fact, "confirm": False,
            "text": personalize(f"ご本人の{fact.name}を、書類で確かめて選んでください", subject),
            "hint": f"相談の文からは「{read}」と読みました。取り違えやすいので、確かめています。{fact.check}",
            "options": [{"value": v, "label": label} for v, label in fact.options],
            "keep": {"value": q.suggested, "label": f"いま確かめられない（「{read}」として進める）"},
            "check": fact.check, "affects": affects, "affects_ids": list(q.affects),
        }
    if q.fact == "lives_in_machida":
        # 町田市かどうかだけを聞くと、町田市以外の人は「いいえ」しか押せない。画面は市区町村を選ぶ欄を出す（選んだ所は apply_place が扱う）。
        # はい・いいえは、市区町村の一覧を読み込めなかった画面のための控え
        return {
            "fact": q.fact, "confirm": False,
            "text": personalize(fact.question, subject),
            "place": {"text": personalize("ご本人はどちらにお住まいですか？", subject)},
            "options": [{"value": v, "label": label} for v, label in fact.options],
            "check": fact.check, "affects": affects, "affects_ids": list(q.affects),
        }
    if q.suggested is not None and fact.type == "enum":
        return {
            "fact": q.fact, "confirm": True,
            "text": f"{subject}は「{fact.label_of(q.suggested)}」で合っていますか？",
            "options": [{"value": q.suggested, "label": "はい"}, {"value": NOT_THIS, "label": "いいえ"}],
            "check": fact.check, "affects": affects, "affects_ids": list(q.affects),
        }
    return {
        "fact": q.fact, "confirm": False,
        "text": personalize(fact.question, subject),
        "options": [{"value": v, "label": label} for v, label in fact.options],
        "check": fact.check, "affects": affects, "affects_ids": list(q.affects),
    }


def step_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int, municipality: str = "") -> dict:
    results = evaluate(RULES, facts)
    order = {"eligible": 0, "after_prereq": 1, "unknown": 2, "ineligible": 3, "done": 4}
    ranked = sorted(results.values(), key=lambda r: (order[r.status], -RULES.weight(r.program.id), r.program.number))
    implied = with_implied(RULES, facts)
    return {
        "question": question_view(facts, suggestions, subject, asked),
        "max_questions": MAX_QUESTIONS,
        "known": [fact_view(f, v) for f, v in implied.items() if f in RULES.facts],
        "results": [result_view(r, facts, subject, municipality) for r in ranked],
        "counts": {s: sum(1 for r in results.values() if r.status == s) for s in order},
        "place": place_view(municipality),
        "center_search": center_search(municipality),
    }


def consult_view(extraction: Extraction, municipality: str) -> dict:
    """読み取りの結果を画面に返す形にする（/api/consult と見本で同じ形）。"""
    facts = extraction.confirmed()
    suggestions = extraction.suggestions()
    apply_place(facts, suggestions, municipality)
    return {
        "subject": extraction.subject,
        "facts": facts,
        "suggestions": suggestions,
        "extracted": [
            {**fact_view(e.fact, e.value), "confidence": e.confidence, "evidence": e.evidence}
            for e in extraction.facts
        ],
        "dropped": len(extraction.dropped),
        **step_view(facts, suggestions, extraction.subject, 0, municipality),
    }


@app.post("/api/consult")
def consult(body: ConsultIn, request: Request) -> dict:
    guard(request, costly=True)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "相談の内容を書いてください")
    try:
        extraction = CLAUDE.extract(RULES, text)
    except ClaudeRefused:
        raise HTTPException(422, "この相談文は読み取れませんでした。言い方を変えて試してください")
    except (ClaudeFailed, anthropic.APIError):
        raise HTTPException(503, "読み取りがうまくいきませんでした。少し待ってから、もう一度試してください")
    return {"engine": CLAUDE.name, **consult_view(extraction, body.municipality)}


@app.post("/api/step")
def step(body: StepIn, request: Request) -> dict:
    guard(request, costly=False)
    facts = clean_facts(body.facts)
    suggestions = {k: v for k, v in clean_facts(body.suggestions).items() if k not in facts}
    apply_place(facts, suggestions, body.municipality)
    return step_view(facts, suggestions, body.subject[:12] or "ご本人", body.asked, body.municipality)


@app.post("/api/summary")
def summary(body: SummaryIn, request: Request) -> dict:
    guard(request, costly=True)
    facts = clean_facts(body.facts)
    apply_place(facts, {}, body.municipality)
    results = evaluate(RULES, facts)
    shown = [r for r in results.values() if r.status in SHOWN and r.status != "unknown"]
    try:
        lines = CLAUDE.summarize(RULES, body.subject[:12] or "ご本人", shown)
    except (ClaudeFailed, anthropic.APIError):
        lines = []  # まとめが出なくても、結果は規則ファイルから読める
    return {"engine": CLAUDE.name, "lines": [{"program_ids": l.program_ids, "text": l.text} for l in lines]}


@app.get("/api/samples")
def samples() -> list[dict]:
    return [{"id": s["id"], "label": s["label"], "text": s["text"], "ready": s.get("reading") is not None}
            for s in SAMPLES]


@app.post("/api/sample")
def sample(body: SampleIn) -> dict:
    """見本を、前もって読み取った結果で返す。Claude を呼ばないので合言葉は求めない。"""
    s = next((s for s in SAMPLES if s["id"] == body.id), None)
    if s is None or s.get("reading") is None:
        raise HTTPException(404, "この見本はまだ用意できていません")
    extraction = check_extraction(RULES, s["text"], s["reading"])  # 置いてある結果も、毎回同じ検査を通す
    return {"engine": "sample", "sample": True, "made": s.get("made"),
            **consult_view(extraction, s.get("municipality", ""))}


@app.middleware("http")
async def revalidate_pages(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # 直した後に古い画面が残らないよう、毎回 ETag で確かめ直させる
    return response


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "programs": len(RULES.programs), "engine": CLAUDE.name, "passphrase": bool(PASSPHRASE)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
