"""相談窓口の Web API と画面。

サーバは相談文も答えも保存しない。画面が事実を持ち、毎回それを送ってくる。
合言葉と回数制限は手間を増やすだけの対策。使われすぎを止めるのは API の専用ワークスペースの上限。
"""

from __future__ import annotations

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
from .claude import ClaudeRefused, make_client
from .engine import DONT_KNOW, SHOWN, Result, evaluate, known, with_implied
from .questions import MAX_QUESTIONS, next_question

WEB_DIR = Path(os.environ.get("TODOKU_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
PASSPHRASE = os.environ.get("TODOKU_PASSPHRASE", "")
# Claude を呼ぶ API の回数制限（IP ごと、10 分あたり）
RATE_LIMIT = int(os.environ.get("TODOKU_RATE_LIMIT", "20"))
NOT_THIS = "__not__"

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


class ConsultIn(BaseModel):
    text: str = Field(max_length=2000)


class StepIn(BaseModel):
    facts: dict[str, Any] = {}
    suggestions: dict[str, Any] = {}
    subject: str = "ご本人"
    asked: int = 0


class SummaryIn(BaseModel):
    facts: dict[str, Any] = {}
    subject: str = "ご本人"


def guard(request: Request, costly: bool) -> None:
    if PASSPHRASE and request.headers.get("x-aikotoba") != PASSPHRASE:
        raise HTTPException(401, "合言葉が違います")
    if costly:
        ip = request.client.host if request.client else "?"
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
        if value == DONT_KNOW or value in fact.values():
            out[fid] = value
    return out


def fact_view(fid: str, value: Any) -> dict:
    fact = RULES.facts[fid]
    return {"fact": fid, "name": fact.name, "value": value,
            "label": "わからない" if value == DONT_KNOW else fact.label_of(value), "question": fact.question}


def result_view(r: Result, facts: dict[str, Any], subject: str) -> dict:
    p = r.program
    missing = [
        {"fact": f, "name": RULES.facts[f].name, "question": personalize(RULES.facts[f].question, subject),
         "check": RULES.facts[f].check,
         "answered_dont_know": facts.get(f) == DONT_KNOW}
        for f in r.missing_facts
    ]
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
             "value": None if leaf.fact is None or not known(leaf.value) else RULES.facts[leaf.fact].label_of(leaf.value),
             "prereq": RULES.programs[leaf.prereq].name if leaf.prereq else None}
            for leaf in r.leaves
        ],
        "missing": missing,
        "waiting_on": [{"id": w, "name": RULES.programs[w].name} for w in r.waiting_on],
        "at_application": p.at_application,
        "apply": p.apply,
        "bring": p.bring,
        "amount": p.amount,
        "timing": p.timing,
        "sources": [
            {"url": s.url, "publisher": s.publisher, "title": s.title, "license": s.license,
             "checked": s.checked.isoformat(), "quote": s.quote}
            for s in p.sources
        ],
    }


def personalize(text: str, subject: str) -> str:
    return text.replace("ご本人", subject) if subject and subject != "ご本人" else text


def question_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int) -> dict | None:
    q = next_question(RULES, facts, suggestions, asked=asked)
    if q is None:
        return None
    fact = RULES.facts[q.fact]
    affects = [RULES.programs[p].name for p in q.affects]
    if q.suggested is not None and fact.type == "enum":
        return {
            "fact": q.fact, "confirm": True,
            "text": f"{subject}は「{fact.label_of(q.suggested)}」で合っていますか？",
            "options": [{"value": q.suggested, "label": "はい"}, {"value": NOT_THIS, "label": "いいえ"}],
            "check": fact.check, "affects": affects,
        }
    return {
        "fact": q.fact, "confirm": False,
        "text": personalize(fact.question, subject),
        "options": [{"value": v, "label": label} for v, label in fact.options],
        "check": fact.check, "affects": affects,
    }


def step_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int) -> dict:
    results = evaluate(RULES, facts)
    order = {"eligible": 0, "after_prereq": 1, "unknown": 2, "ineligible": 3, "done": 4}
    ranked = sorted(results.values(), key=lambda r: (order[r.status], -RULES.weight(r.program.id), r.program.number))
    implied = with_implied(RULES, facts)
    return {
        "question": question_view(facts, suggestions, subject, asked),
        "max_questions": MAX_QUESTIONS,
        "known": [fact_view(f, v) for f, v in implied.items() if f in RULES.facts],
        "results": [result_view(r, facts, subject) for r in ranked],
        "counts": {s: sum(1 for r in results.values() if r.status == s) for s in order},
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
    facts = extraction.confirmed()
    suggestions = extraction.suggestions()
    return {
        "engine": CLAUDE.name,
        "subject": extraction.subject,
        "facts": facts,
        "suggestions": suggestions,
        "extracted": [
            {**fact_view(e.fact, e.value), "confidence": e.confidence, "evidence": e.evidence}
            for e in extraction.facts
        ],
        "dropped": len(extraction.dropped),
        **step_view(facts, suggestions, extraction.subject, 0),
    }


@app.post("/api/step")
def step(body: StepIn, request: Request) -> dict:
    guard(request, costly=False)
    facts = clean_facts(body.facts)
    suggestions = {k: v for k, v in clean_facts(body.suggestions).items() if k not in facts}
    return step_view(facts, suggestions, body.subject[:12] or "ご本人", body.asked)


@app.post("/api/summary")
def summary(body: SummaryIn, request: Request) -> dict:
    guard(request, costly=True)
    facts = clean_facts(body.facts)
    results = evaluate(RULES, facts)
    shown = [r for r in results.values() if r.status in SHOWN and r.status != "unknown"]
    try:
        lines = CLAUDE.summarize(RULES, body.subject[:12] or "ご本人", shown)
    except ClaudeRefused:
        lines = []
    return {"engine": CLAUDE.name, "lines": [{"program_ids": l.program_ids, "text": l.text} for l in lines]}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "programs": len(RULES.programs), "engine": CLAUDE.name, "passphrase": bool(PASSPHRASE)}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
