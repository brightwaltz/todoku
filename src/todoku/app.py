"""相談窓口の Web API と画面。

サーバは相談文も答えも保存しない。画面が事実を持ち、毎回それを送ってくる。
合言葉と回数制限は手間を増やすだけの対策。使われすぎを止めるのは API の専用ワークスペースの上限。
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, model_validator

from . import events as events_mod
from .city import city_groups, city_page
from .japan_map import japan_map
from . import local_rules as local_mod
from . import memo as memo_mod
from . import options as options_mod
from . import rules as rules_mod
import anthropic

from .claude import ALWAYS_CONFIRM, ClaudeFailed, ClaudeRefused, Extraction, check_extraction, check_summary, make_client
from .engine import DONT_KNOW, SHOWN, T, U, Result, evaluate, known, prereq_edges, statuses, with_implied
from .questions import MAX_QUESTIONS, Question, candidates, next_question, place_fact, score_fact

WEB_DIR = Path(os.environ.get("TODOKU_WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
PASSPHRASE = os.environ.get("TODOKU_PASSPHRASE", "")
# Claude を呼ぶ API の回数制限（IP ごと、10 分あたり）
RATE_LIMIT = int(os.environ.get("TODOKU_RATE_LIMIT", "20"))
# アプリ全体の回数制限（10 分あたり、合言葉が通った回だけ）。接続元を偽って IP ごとの制限を外しても、Claude を呼ぶ回数はこれを超えない。
# 数えるのはプロセスの中なので、マシン 1 台（uvicorn を 1 つで動かしている）ごとの上限。マシンが N 台なら N 倍になる
RATE_LIMIT_ALL = int(os.environ.get("TODOKU_RATE_LIMIT_ALL", "60"))
# 聞き返し（/api/step）の回数制限（IP ごと、STEP_WINDOW 秒あたり）。費用はかからないが合言葉なしで CPU を使うので、
# 1 人が送り続けて、ほかの人の画面を遅くしないための上限（手間を増やすだけの対策。接続元を変えながら送れば外せる）。
# 審査の人が同じ Wi-Fi（同じ IP）から何人も同時に問いに答えても届かないよう、大きめにする（1 回の判定は約 2〜4ms。T-047）
STEP_LIMIT = int(os.environ.get("TODOKU_STEP_LIMIT", "300"))
STEP_WINDOW = 60
# Fly.io の上で動いているか（Fly はマシンに FLY_APP_NAME を入れる。fly.toml には書いていない）。起動時に 1 回だけ決める。
# Fly の中継が付ける Fly-Client-IP は Fly の上でだけ信じる。ほかの置き場ではだれでも付けられるので、接続そのものの相手で数える
# （手間を増やすだけの対策。Fly の上でも、中継がヘッダを上書きしなければ偽れる）
ON_FLY = bool(os.environ.get("FLY_APP_NAME", "").strip())
WINDOW = 600
# 本文の大きさの上限。相談文は 2000 字まで、事実は 20 ほどなので、ふつうの要求は数 KB
MAX_BODY = 64 * 1024
# 事実と読み取りの鍵の数の上限（定義にある事実の数より十分大きく）
MAX_KEYS = 64
NOT_THIS = "__not__"
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
    "pending": "申請済み・結果待ち",
    "after_prereq": "先に手続きをすると使える",
    "unknown": "確かめると使えるかもしれない",
    "ineligible": "当てはまらない",
    "done": "済んでいる",
}

# まとめ（/api/summary）に渡す状態。画面の app.js の SUMMARIZED と同じにする
SUMMARIZED = ("eligible", "pending", "after_prereq")

app = FastAPI(title="todoku", docs_url=None, redoc_url=None)
RULES = rules_mod.load()
# 有料・民間の支え（行政の制度ではない候補。判定はせず、並べ替えるだけ。T-084）
OPTIONS = options_mod.load(rules_mod.rules_dir() / "options.yaml", RULES.facts)
CLAUDE = make_client()
_hits: dict[str, deque] = defaultdict(deque)
_hits_all: deque = deque()
_swept = 0.0  # 最後に forget_idle で _hits を見回した時刻
_step_hits: dict[str, deque] = defaultdict(deque)  # 聞き返しの回数（接続元ごと）。Claude を呼ぶ口の _hits・_hits_all とは別に数える
_step_swept = 0.0  # 最後に forget_idle で _step_hits を見回した時刻
# 回数制限の表（_hits・_hits_all・_swept・_step_hits・_step_swept）を読み書きするときに持つ鍵。口は同期の関数でスレッドで動くので、
# 鍵なしだと recent() の popleft が IndexError（500）になり、ほかのスレッドが積んだ記録を消して数え漏らしていた（T-039）
_lock = threading.Lock()


def load_places() -> dict[str, tuple[str, str]]:
    """団体コード → (都道府県名, 市区町村名)。画面の選択肢と同じ web/municipalities.json から作る。"""
    data = json.loads((WEB_DIR / "municipalities.json").read_text(encoding="utf-8"))
    return {code: (pref["name"], name) for pref in data["prefectures"] for code, name in pref["cities"]}


PLACES = load_places()
# その市の事業（rules/local/<団体コード>.yaml。T-075）。その市の人には、型の札の名前・申請先・主な条件・出典をこれに替える（判定は型のまま）
LOCAL = local_mod.load(rules_mod.rules_dir() / "local", RULES, PLACES)
# 都道府県コード → 名前（全国の見取り図のタイルに使う。T-080c）
PREFS = {code[:2]: pref for code, (pref, _) in PLACES.items()}
# 市の制度を一つずつ判定している市（段 → 住所の事実）。そこに住んでいる人には、市区町村の型を出さない
RULES_CITY = {level: fid for level, fid in rules_mod.LEVEL_PLACE.items() if "city" in (RULES.places.get(fid) or {})}
# 合言葉なしで見られる見本。reading は前もって本物の Claude が読み取った結果（scripts/make_samples.py で書く）
SAMPLES: list[dict] = json.loads((WEB_DIR / "samples.json").read_text(encoding="utf-8"))


def has_broken_text(data: Any) -> bool:
    """対になっていないサロゲート（JSON の \\ud800 や生の ED A0 80）を含む文字列が、鍵か値のどこかにあるか。

    入れ子が深くても止まらないよう、再帰せずに辿る。
    """
    stack = [data]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            try:
                x.encode("utf-8")
            except UnicodeEncodeError:
                return True
        elif isinstance(x, dict):
            stack.extend(x.keys())
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
    return False


class In(BaseModel):
    """画面から来る本文の型の土台。壊れた文字は、形を確かめる前に断る。

    通すと、応答を UTF-8 に直すところで 500 になり、uvicorn のトレースバックに相談文の全文が出ていた（T-002）。
    """

    @model_validator(mode="before")
    @classmethod
    def no_broken_text(cls, data: Any) -> Any:
        if has_broken_text(data):
            raise ValueError("broken text")
        return data


class ConsultIn(In):
    text: str = Field(max_length=2000)
    municipality: str = Field("", max_length=6)


class StepIn(In):
    facts: dict[str, Any] = Field(default_factory=dict, max_length=MAX_KEYS)
    suggestions: dict[str, Any] = Field(default_factory=dict, max_length=MAX_KEYS)
    subject: str = "ご本人"
    asked: int = 0
    municipality: str = Field("", max_length=6)


class SummaryIn(In):
    facts: dict[str, Any] = Field(default_factory=dict, max_length=MAX_KEYS)
    subject: str = "ご本人"
    municipality: str = Field("", max_length=6)


class SampleIn(In):
    id: str = Field(max_length=40)


class SampleSummaryIn(In):
    id: str = Field(max_length=40)
    facts: dict[str, Any] = Field(default_factory=dict, max_length=MAX_KEYS)
    municipality: str = Field("", max_length=6)


class EventIn(In):
    id: str = Field("", max_length=40)
    ids: list[Annotated[str, Field(max_length=40)]] = Field(default_factory=list, max_length=events_mod.MAX_EVENTS)  # 絵で選んだ出来事（T-124）
    municipality: str = Field("", max_length=6)


def guard(request: Request, costly: bool) -> None:
    # 合言葉は Claude を呼ぶ口だけ。聞き返し（/api/step）は規則だけで費用がかからないので、合言葉なしで通し、
    # 接続元ごとのゆるい回数制限（STEP_LIMIT）だけを掛ける。Claude を呼ぶ口の回数（_hits・_hits_all）は減らさない
    if not costly:
        with _lock:
            now = time.time()
            forget_idle(now)
            hits = recent(_step_hits[source(request)], now, STEP_WINDOW)
            if len(hits) >= STEP_LIMIT:
                raise HTTPException(429, "しばらく時間をおいてから試してください")
            hits.append(now)
        return
    if costly:
        # 本物の Claude を呼ぶのに合言葉がない（fly secrets の設定し忘れなど）ときは、Claude を呼ぶ口を閉じる。
        # 黙って合言葉なしで開くと、だれでも費用を使える。起動で止めると見本と判定し直しまで止まるので、ここで断る。
        # 数える前に断る（設定の誤りで、利用者の回数を減らさない）
        if CLAUDE.name == "claude" and not PASSPHRASE:
            raise HTTPException(503, "この窓口は合言葉が設定されていないため、相談の読み取りを止めています。見本は試せます")
        # 回数制限は手間を増やすだけ。費用を止めるのはワークスペースの上限
        # 見回し・数え・積むまでを鍵 1 本で囲む。数えてから積むまでの間に別のスレッドが入ると、上限を超えて通る
        with _lock:
            now = time.time()
            forget_idle(now)
            # 先にアプリ全体を見る。上限に届いていれば、接続元ごとの数も増やさない
            if len(recent(_hits_all, now)) >= RATE_LIMIT_ALL:
                raise HTTPException(429, "しばらく時間をおいてから試してください")
            # 接続元の決め方は source()。Fly-Client-IP の値を変えれば外せるので、アプリ全体の上限も置く
            hits = recent(_hits[source(request)], now)
            if len(hits) >= RATE_LIMIT:
                raise HTTPException(429, "しばらく時間をおいてから試してください")
            # 合言葉を確かめる前に数える。間違えた回も数えないと、合言葉を何度でも試せる
            hits.append(now)
            if PASSPHRASE and not same_word(request.headers.get("x-aikotoba", ""), PASSPHRASE):
                raise HTTPException(401, "合言葉が違います")
            # アプリ全体は、合言葉が通った回（Claude を呼ぶ回）だけ数える。間違えた回も数えると、
            # 合言葉を知らない人が接続元を変えながら送るだけで、ほかの全員を止められる
            _hits_all.append(now)


def source(request: Request) -> str:
    """回数制限で数える接続元の鍵。

    Fly.io では接続元が Fly の中継になるので、中継が付ける Fly-Client-IP で数える。
    Fly の外（ON_FLY が偽）ではヘッダを見ない。付けて送るだけで、接続元ごとの上限を外せるため。
    """
    forwarded = request.headers.get("fly-client-ip") if ON_FLY else None
    return client_key(forwarded or (request.client.host if request.client else "?"))


def client_key(ip: str) -> str:
    """回数制限で数える接続元の鍵。IPv6 は /64 ごとにまとめる（手間を増やすだけの対策）。

    IPv6 は 1 つの契約に /64（またはそれより広い範囲）が割り当てられることが多く、
    アドレスを変えながら送るだけで接続元ごとの上限を外せるため。IPv4 と、アドレスと読めない値は今のまま数える。
    IPv4 を IPv6 の形で書いたもの（::ffff:203.0.113.1）は、/64 にすると IPv4 の全員が 1 つにまとまるので IPv4 として数える。
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        return ip
    if addr.ipv4_mapped:
        return str(addr.ipv4_mapped)
    # 文字列でなく数で渡す（fe80::1%eth0 のような範囲の印つきは、文字列に /64 を付けると読めずに 500 になる）
    return str(ipaddress.IPv6Network((int(addr), 64), strict=False))


def recent(hits: deque, now: float, window: float = WINDOW) -> deque:
    """window 秒より前の記録を捨てて、同じ deque を返す。_lock を持って呼ぶ（見てから popleft するので）。"""
    while hits and now - hits[0] > window:
        hits.popleft()
    return hits


def forget_idle(now: float) -> None:
    """窓から外れて記録が空になった接続元を _hits と _step_hits から消す。

    消さないと、接続元を変えながら送るだけで、来なくなった接続元の行が表に残り続ける。
    全部の行を見るので、見回すのはそれぞれの窓（WINDOW・STEP_WINDOW）の秒数に 1 回だけ（来なくなった行は、遅くとも窓の 2 倍の秒数で消える）。
    同じ窓の中で接続元を変えて送られた分は残る（合言葉を間違えた回も数えるため）。
    _step_hits は合言葉なしで行が増えるので、窓を短くして早めに消す。
    """
    global _swept, _step_swept
    # 呼ぶのは guard の _lock の中だけ（recent() と同じく、鍵の外から呼ぶと競合する）
    if now - _swept >= WINDOW:
        _swept = now
        forget_empty(_hits, now, WINDOW)
    if now - _step_swept >= STEP_WINDOW:
        _step_swept = now
        forget_empty(_step_hits, now, STEP_WINDOW)


def forget_empty(table: dict[str, deque], now: float, window: float) -> None:
    for ip, hits in list(table.items()):
        if not recent(hits, now, window):
            table.pop(ip, None)


def same_word(sent: str, word: str) -> bool:
    """合言葉を、かかる時間が中身によらない比べ方で比べる（手間を増やすだけの対策の一つ）。

    Starlette はヘッダを latin-1 で文字にするので、latin-1 で送られたバイトに戻し、合言葉の UTF-8 と比べる。
    文字のまま compare_digest に渡すと、ASCII でない文字で TypeError（500）になる。
    環境変数の読めないバイトは os.environ が surrogateescape で文字にするので、同じ方法でバイトに戻す。
    """
    return hmac.compare_digest(sent.encode("latin-1"), word.encode("utf-8", "surrogateescape"))


def clean_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """画面から来た事実のうち、定義にあり値が正しいものだけを残す。"""
    out = {}
    for fid, value in raw.items():
        fact = RULES.facts.get(fid)
        if fact is None:
            continue
        # 「わからない」だけは通す：住所の問いを 2 度目に受け持つのは横浜市・東京都の事実で、その「わからない」を捨てると同じ問いが繰り返し出るため。
        # 「わからない」は住所を決めないので、両市に住んでいることにはできない（はい・いいえの値は今までどおり使わない）
        if fact.place and not fact.read and value != DONT_KNOW:
            continue  # 横浜市・東京都に住んでいるかは、選んだ市区町村（apply_place）か町田市の答えから決める。送られた値は使わない
        if fact.type == "bool" and not isinstance(value, bool) and value != DONT_KNOW:
            continue  # 1 や "true" は受け付けない（True と 1 は == で等しいため、ここで弾く）
        if value == DONT_KNOW or value in fact.values():
            out[fid] = value
    return out


def apply_place(facts: dict[str, Any], suggestions: dict[str, Any], code: str) -> None:
    """画面で選んだ市区町村を、読み取りや画面の事実より優先する。一覧にないコードは無視する。"""
    if code not in PLACES:
        return
    facts.update(RULES.place_values(code))  # 町田市・横浜市・東京都…に住んでいるかを、すべてここで決める
    for fid in RULES.places:
        suggestions.pop(fid, None)


def place_view(code: str) -> dict | None:
    if code not in PLACES:
        return None
    pref, name = PLACES[code]
    return {"code": code, "pref": pref, "name": name}


def region_view(code: str) -> dict | None:
    """結果の画面の「あなたの地域で確かめられる支え」（T-122）。住所が無ければ None。

    数は /city/ の頁の帯と同じ数え方（city_groups の、制度のある段だけ）で、規則だけから数える。Claude は使わない。
    住んでいない地域の制度の数は返さない（前の hidden_places は「町田市にお住まいなら…も判定します」と読め、
    ほかの地域の人が「自分は対象外だ」と受け取ったため。11/5 の柴田の感想）。
    detailed は、国と型のほかに一つずつ書けている段（例：["都の制度", "市の制度"]）。文言は web/app.js の REGION_* で組む
    """
    if code not in PLACES:
        return None
    pref, name = PLACES[code]
    groups = [g for g in city_groups(RULES, code, pref, name) if g.programs]
    parts = [{"label": "市区町村の事業" if g.key == "local" else g.title, "n": len(g.programs),
              "unit": "種類" if g.key == "local" else "件"} for g in groups]
    detailed = [f"{rules_mod.LEVELS[g.key][-1]}の制度" for g in groups if g.key in rules_mod.LEVEL_PLACE]
    local = next((g for g in groups if g.key == "local"), None)
    if local and any(local_mod.found_in(LOCAL, code, p.id) for p in local.programs):
        detailed.append(f"{name[-1]}の事業")  # その市の公式頁から名前・申請先まで書けた型（T-075b）
    return {"code": code, "total": sum(x["n"] for x in parts), "parts": parts, "detailed": detailed}


def fact_view(fid: str, value: Any) -> dict:
    # 問いの文は返さない。画面は名前と値だけを使い、問いの文は相談文の呼び方に置き換えずに「ご本人は、…」のまま出ていたため（T-027）。
    # 問いは ask_view・result_view の missing で、置き換えてから返す
    fact = RULES.facts[fid]
    return {"fact": fid, "name": fact.name, "value": value,
            "label": "わからない" if value == DONT_KNOW else fact.label_of(value)}


def result_view(r: Result, facts: dict[str, Any], subject: str, municipality: str = "",
                pending: frozenset[str] = frozenset()) -> dict:
    p = r.program
    missing = [
        {"fact": f, "name": RULES.facts[f].name, "question": personalize(RULES.facts[f].question, subject),
         "check": RULES.facts[f].check,
         "answered_dont_know": facts.get(f) == DONT_KNOW}
        for f in r.missing_facts
    ]
    # 町田市に住んでいると分かっているときだけ、町田市の窓口・電話・書類の名前を出す（国の制度の申請先は町田市で書いてある）
    here = facts.get("lives_in_machida") is True
    other = {} if here else p.elsewhere
    # 住んでいると分かっていない市・都・県のページは、出典から外す（ほかに出典がなければ、そのまま出す）
    away = [site for level, site in rules_mod.LEVEL_SITE.items() if facts.get(rules_mod.LEVEL_PLACE[level]) is not True]
    sources = [s for s in p.sources if not any(site in s.url for site in away)] or p.sources
    apply = dict(other.get("apply", p.apply))
    # その市の事業が見つかった型は、名前・申請先・出典をその市のものにする（T-075b）。判定の条件・状態は型のまま。
    # 型の出典（全国調査）は、札の説明の数字の出典なので後ろに残す
    local = local_mod.found_in(LOCAL, municipality, p.id) if p.level == "local" else None
    if local:
        apply = {"where": local.where}
        sources = list(local.sources) + list(sources)
    if CENTER in apply.get("where", "") + apply.get("how", ""):
        apply["center_search"] = center_search(municipality)  # 申請先に挙げたセンターを、その場で探せるように
    return {
        "id": p.id,
        "number": p.number,
        "name": local.name if local else p.name,
        "level": rules_mod.LEVELS[p.level],
        # その市の事業に替えたときの中身と印（無ければ None で、型のまま）
        "local": local_mod.view(local, PLACES[municipality][1]) if local else None,
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
        # 前提の制度を申請済みなら、画面で「結果待ち」と添える（もう一度申請するように読めないように）
        "waiting_on": [{"id": w, "name": RULES.programs[w].name, "pending": w in pending} for w in r.waiting_on],
        "at_application": p.at_application,
        "apply": apply,
        "bring": other.get("bring", p.bring),
        "amount": p.amount,
        # 費用の負担。札に 1 行で出す（出典で言えないものは「窓口で確かめる」）
        "cost": {"self_pay": p.cost.self_pay, "label": rules_mod.SELF_PAY[p.cost.self_pay],
                 "share": p.cost.share, "who_can_apply": list(p.cost.who_can_apply)},
        "timing": other.get("timing", p.timing),
        "sources": [
            {"url": s.url, "publisher": s.publisher, "title": s.title, "license": s.license,
             "checked": s.checked.isoformat(), "quote": s.quote}
            for s in sources
        ],
        # 行政手続データの手続（根拠法令・条項）。画面では出典の名前を添える
        "procedures": [{"name": d.name, "law": d.law, "article": d.article, "match": d.match} for d in p.procedures],
        "procedures_source": rules_mod.PROCEDURES_DATASET if p.procedures else "",
        # 申請メモ（T-074）：窓口へ持っていく記入の下書き。規則と相談の答えからだけ作り、Claude は使わない
        "memo": memo_mod.memo_view(r, facts, subject, RULES, place_view(municipality), apply,
                                   other.get("bring", p.bring), pending, local.name if local else ""),
    }


def personalize(text: str, subject: str) -> str:
    return text.replace("ご本人", subject) if subject and subject != "ご本人" else text


def question_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int,
                  visible: frozenset[str] | None = None) -> dict | None:
    q = next_question(RULES, facts, suggestions, asked=asked)
    return None if q is None else ask_view(q, subject, visible)


def unconfirmed_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str,
                     visible: frozenset[str] | None = None) -> list[dict]:
    """聞き返しが終わっても確かめていない読み取り（確信の低いもの）。結果の画面で、同じ形の問いで確かめられるようにする。

    住所の問いを受け持つ事実がまだあれば（3 問の枠で聞けなかったとき）、市区町村を選ぶ欄も先頭に出す。
    """
    base = statuses(RULES, facts)
    out = []
    for fid in sorted(suggestions, key=lambda f: RULES.facts[f].order):
        if fid in facts:
            continue
        _, affects = score_fact(RULES, facts, fid, base)
        out.append(ask_view(Question(fid, 0.0, suggestions[fid], affects), subject, visible))
    place = place_fact(RULES, facts)
    if place and place not in suggestions:  # 確信の低い読み取り（lives_in_machida）なら、上のループがもう出している
        score, affects = score_fact(RULES, facts, place, base)
        if score > 0:
            out.insert(0, ask_view(Question(place, score, None, affects), subject, visible))
    return out


def unread_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, visible: frozenset[str]) -> list[dict]:
    """聞き返しが終わっても分かっていない事実のうち、答えると結果の画面の制度の判定が変わるもの（T-014）。

    相談の文から読み取れなかった事実（生活保護など）にも、結果の画面で答えられるようにする。
    並びは次の問いの選び方と同じ値打ちの高い順。「わからない」と答えた事実と、確信の低い読み取り（unconfirmed で聞く）は入れない
    """
    base = statuses(RULES, facts)
    ranked = []
    for fid in candidates(RULES, facts):
        if fid in suggestions or fid in RULES.places:  # 住所の問いは unconfirmed_view が市区町村を選ぶ欄として出す（T-070 で合わせた）
            continue
        score, affects = score_fact(RULES, facts, fid, base)
        if score > 0 and any(p in visible for p in affects):
            ranked.append((-score, RULES.facts[fid].order, Question(fid, score, None, affects)))
    ranked.sort(key=lambda t: t[:2])
    return [ask_view(q, subject, visible) for _, _, q in ranked]


def ask_view(q: Question, subject: str, visible: frozenset[str] | None = None) -> dict:
    fact = RULES.facts[q.fact]
    # 「この答えで判定が決まる制度」には、結果の画面に出す制度（visible）だけを挙げる。step_view が外した型の名前が
    # 問いにだけ出ると食い違うため（T-007）。問いの値打ちには外した制度も入れたまま（判定は変えない）
    ids = [p for p in q.affects if visible is None or p in visible]
    affects = [RULES.programs[p].name for p in ids]
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
            "check": fact.check, "affects": affects, "affects_ids": ids,
        }
    if q.fact in RULES.places:
        # 町田市かどうかだけを聞くと、町田市以外の人は「いいえ」しか押せない。画面は市区町村を選ぶ欄を出す（選んだ所は apply_place が扱う）。
        # はい・いいえは、市区町村の一覧を読み込めなかった画面のための控え
        first = q.fact == RULES.place_question
        return {
            "fact": q.fact, "confirm": False,
            "text": personalize(fact.question if first else "ご本人はどちらにお住まいですか？", subject),
            "place": {"text": personalize("ご本人はどちらにお住まいですか？", subject)},
            # 町田市ではないと分かった後の聞き直し（横浜市・東京都の事実）では、はい・いいえを clean_facts が使わないので控えを出さない
            "options": [{"value": v, "label": label} for v, label in fact.options] if first else [],
            "check": fact.check, "affects": affects, "affects_ids": ids,
        }
    if q.suggested is not None and fact.type == "enum":
        return {
            "fact": q.fact, "confirm": True,
            "text": f"{subject}は「{fact.label_of(q.suggested)}」で合っていますか？",
            "options": [{"value": q.suggested, "label": "はい"}, {"value": NOT_THIS, "label": "いいえ"}],
            "check": fact.check, "affects": affects, "affects_ids": ids,
        }
    return {
        "fact": q.fact, "confirm": False,
        "text": personalize(fact.question, subject),
        "options": [{"value": v, "label": label} for v, label in fact.options],
        "check": fact.check, "affects": affects, "affects_ids": ids,
    }


def step_view(facts: dict[str, Any], suggestions: dict[str, Any], subject: str, asked: int, municipality: str = "") -> dict:
    results = evaluate(RULES, facts)
    implied = with_implied(RULES, facts)
    city = next((level for level, fid in RULES_CITY.items() if implied.get(fid) is True), None)
    if city:
        # 町田市・横浜市では市の制度を一つずつ判定するので、市区町村の型は出さない（判定は変えない）。
        # 「当てはまらない」に並ぶと、町田市の紙おむつ支給が「確かめる」にあるのに「紙おむつは駄目」と読めてしまうため。
        # その市で書けていない型（規則の shown_in）だけは出す
        results = {pid: r for pid, r in results.items() if r.program.level != "local" or city in r.program.shown_in}
    for level, fid in rules_mod.LEVEL_PLACE.items():
        # その市・都・県以外と分かったら、そこの制度は出さない（町田市で型を出さないのと同じ扱い。判定は変えない）。
        # 「当てはまらない」に住所だけで外れた制度が並ぶため（11/3 の判断 2）。住所が分からない間は出す。
        # 外した数も画面に返さない（T-122：「〜にお住まいなら…も判定します」の 1 行をやめ、region で自分の地域の数を出す）
        if implied.get(fid) is False:
            results = {pid: r for pid, r in results.items() if r.program.level != level}
    order = {"eligible": 0, "pending": 1, "after_prereq": 2, "unknown": 3, "ineligible": 4, "done": 5}
    pending = frozenset(pid for pid, r in results.items() if r.status == "pending")

    def rank(r: Result) -> tuple:
        # 確かめるは、あと少し確かめれば決まる制度から：分からない条件が少ない順、次に当てはまった条件が多い順（11/3 の判断 1）。
        # 重い順だけだと、祖母の相談で本人が書いた紙おむつの支給が、閉じた 18 件に隠れたため
        if r.status != "unknown":
            return (order[r.status], 0, 0, -RULES.weight(r.program.id), r.program.number)
        unknown = sum(1 for leaf in r.leaves if leaf.state == U)
        met = sum(1 for leaf in r.leaves if leaf.state == T)
        return (order[r.status], unknown, -met, -RULES.weight(r.program.id), r.program.number)

    ranked = sorted(results.values(), key=rank)
    visible = frozenset(results)  # 上の 2 つの絞り込みの後に残った、結果の画面に出す制度
    question = question_view(facts, suggestions, subject, asked, visible)
    return {
        "question": question,
        # 聞き返しが終わったら、確かめていない読み取りを結果の画面に出す（答えても Claude は呼ばない）
        "unconfirmed": unconfirmed_view(facts, suggestions, subject, visible) if question is None else [],
        # 読み取れなかった事実も、結果の画面で答えられるようにする（答えても Claude は呼ばない。画面は先頭の数件だけを先に出す）
        "unread": unread_view(facts, suggestions, subject, visible) if question is None else [],
        "max_questions": MAX_QUESTIONS,
        # 住所は、問いを受け持つ事実（町田市に住んでいる）だけを出す。東京都・横浜市などはそこから決まる
        "known": [fact_view(f, v) for f, v in implied.items()
                  if f in RULES.facts and (f not in RULES.places or f == RULES.place_question)],
        "results": [result_view(r, implied, subject, municipality, pending) for r in ranked],
        "counts": {s: sum(1 for r in results.values() if r.status == s) for s in order},
        # 結果の画面の「制度の地図」の矢印（規則の前提から。地図に置く 3 つの状態の制度どうしだけ。Claude は使わない）
        "edges": [list(e) for e in prereq_edges(RULES, (pid for pid, r in results.items() if r.status in SHOWN))],
        # 選んだ地域で確かめられる支えの数（T-122。住所が無ければ None）
        "region": region_view(municipality),
        "place": place_view(municipality),
        "center_search": center_search(municipality),
        # 結果の画面の下に出す、有料・民間の支え（判定はしない。関係しそうな事実が当てはまる数で並べ替える）
        "options": options_mod.ranked(OPTIONS, implied) if question is None else [],
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


def summarized(facts: dict[str, Any], municipality: str) -> list[Result]:
    """まとめ（Claude が書く）に渡す制度。facts は書き換える（住所を置く）。"""
    apply_place(facts, {}, municipality)
    # 申請済み・結果待ちの制度も渡す（前提にしている制度について「申請すると」と書かせないため）
    return [r for r in evaluate(RULES, facts).values() if r.status in SUMMARIZED]


def summary_key(shown: list[Result]) -> str:
    """まとめに渡す制度・状態・待っている前提の組。見本の保存したまとめは、この組が作ったときと同じときだけ出す（T-045）。"""
    return ",".join(sorted(f"{r.program.id}:{r.status}:{'+'.join(sorted(r.waiting_on))}" for r in shown))


@app.post("/api/summary")
def summary(body: SummaryIn, request: Request) -> dict:
    guard(request, costly=True)
    shown = summarized(clean_facts(body.facts), body.municipality)
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
    return {"engine": "sample", "sample": True, "sample_id": s["id"], "made": s.get("made"),
            **consult_view(extraction, s.get("municipality", ""))}


@app.post("/api/sample/summary")
def sample_summary(body: SampleSummaryIn, request: Request) -> dict:
    """見本の道のまとめ（T-045、D-0013 の A）。台本の答えで結果まで来たとき（まとめに渡す制度の組が、作ったときと同じとき）だけ、
    前もって Claude が書いて samples.json に置いたまとめを返す（scripts/make_sample_summaries.py で書く）。組が違えば空で返し、
    画面は今までどおり「まとめを省いています」と書く。Claude を呼ばないので合言葉は求めず、聞き返しと同じゆるい回数制限だけを掛ける。
    """
    guard(request, costly=False)
    s = next((s for s in SAMPLES if s["id"] == body.id), None)
    if s is None:
        raise HTTPException(404, "この見本はまだ用意できていません")
    saved = s.get("summary")
    shown = summarized(clean_facts(body.facts), body.municipality)
    if not saved or saved.get("key") != summary_key(shown):
        return {"engine": "sample", "lines": [], "made": None}
    lines = check_summary(RULES, shown, {"lines": saved["lines"]})  # 置いてあるまとめも、毎回同じ検査を通す
    return {"engine": "sample", "lines": [{"program_ids": l.program_ids, "text": l.text} for l in lines],
            "made": saved.get("made")}


@app.get("/api/events")
def events() -> list[dict]:
    return [{"id": e["id"], "label": e["label"], "text": e["text"]} for e in events_mod.EVENTS[:events_mod.MAX_EVENTS]]


@app.post("/api/event")
def event(body: EventIn, request: Request) -> dict:
    """入口の出来事のボタン（T-071）。出来事の文から言える事実だけを置いて、最初の問いを返す。

    Claude を呼ばないので合言葉は求めず、聞き返しと同じゆるい回数制限だけを掛ける。
    """
    guard(request, costly=False)
    ids = list(dict.fromkeys(body.ids or ([body.id] if body.id else [])))  # 同じ出来事を 2 度送っても 1 度に数える
    picked = [events_mod.find(i) for i in ids]
    if not picked or any(e is None for e in picked):
        raise HTTPException(404, "この出来事は用意していません")
    e = events_mod.combine(picked)
    extraction = check_extraction(RULES, e["text"], events_mod.reading(e))
    return {"engine": "event", "event": True, "text": e["text"], **consult_view(extraction, body.municipality)}


class BodyLimit:
    """本文が MAX_BODY を超える要求を 413 で断る。

    Content-Length を見るだけだと、長さを書かない送り方（chunked）で回り込めるので、読んだ量も数えて止める。
    合言葉の要らない /api/step にも大きな本文を送れたため（3rd の点検、13.9MB で RSS が 89→318MB）。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        length = dict(scope["headers"]).get(b"content-length", b"0")
        if not length.isdigit() or int(length) > MAX_BODY:
            return await self.too_large(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > MAX_BODY:
                return await self.too_large(scope, receive, send)
            chunks.append(message.get("body", b""))
            if not message.get("more_body"):
                break
        body, given = b"".join(chunks), False

        async def replay():
            nonlocal given
            if not given:
                given = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def too_large(scope, receive, send):
        await JSONResponse({"detail": "送られた内容が大きすぎます"}, status_code=413)(scope, receive, send)


app.add_middleware(BodyLimit)


class GZipButIndex:
    """応答を gzip で縮めて返す（受け手が Accept-Encoding: gzip のとき）。ただし「/」は縮めない。

    /api/sample と、答えるたびの /api/step は 1 回 約 84KB（40 制度の申請先・持ち物・出典を毎回返す）で、
    縮めないまま送っていた（T-061）。level 1 で 84KB → 23KB、手元で 0.42ms（/api/step の往復は 3.6ms）。
    level 6 でも 18KB にしかならず 1.2ms かかるので、CPU の少ない 1 にした。app.js・municipalities.json も 3 分の 1 になる。
    「/」を外すのは、HEAD / と GET / のヘッダを同じに保つため（T-056。HEAD は本文がなく縮まらない）。index.html は 7KB。
    応答に秘密（合言葉・鍵）は入らないので、縮めた長さから秘密を探る攻撃（BREACH）の的はない。
    """

    def __init__(self, app):
        self.app = app
        self.gzip = GZipMiddleware(app, minimum_size=500, compresslevel=1)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"] != "/":
            return await self.gzip(scope, receive, send)
        return await self.app(scope, receive, send)


app.add_middleware(GZipButIndex)


@app.exception_handler(RequestValidationError)
async def invalid_body(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI の既定の 422 は、送られた文（input）をそのまま返す。文が壊れていると、それを UTF-8 に直すところで 500 になり、
    # トレースバックに相談文の全文が出ていた（T-002）。送られた文は返さず、画面（app.js）がそのまま出せる 1 文だけにする
    return JSONResponse({"detail": "送られた内容を読めませんでした。文を書き直して、もう一度試してください"}, status_code=422)


# 守りを重ねるためのヘッダ（11/3 の安全点検の 5）。画面に文を差し込む道は見つかっていないが、今後できても
# よその出どころのスクリプト・インラインのスクリプトと style 属性を動かせなくし、ほかのサイトの枠に入れさせない。
# style 属性は web/app.js の el() が CSSOM（style.cssText）で付けるので 'unsafe-inline' は要らない。
# HSTS は deploy した後にブラウザに残って戻せないので付けない（http→https は fly.toml の force_https が回す）。
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


@app.middleware("http")
async def revalidate_pages(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers[k] = v
    if request.url.path == "/" or request.url.path.startswith(("/static/", "/city/", "/japan-map")):
        response.headers["Cache-Control"] = "no-cache"  # 直した後に古い画面が残らないよう、毎回 ETag で確かめ直させる
    return response


# 「/」と /api/health は HEAD にも答える（T-056）。@app.get だけだと HEAD は 405 になり、`curl -I` や
# 死活の見張り（HEAD で聞くものがある）が「落ちている」と読む。FileResponse は HEAD では本文を送らない
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health() -> dict:
    return {"ok": True, "programs": len(RULES.programs), "engine": CLAUDE.name, "passphrase": bool(PASSPHRASE)}


@app.api_route("/", methods=["GET", "HEAD"])
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/city/{code}", response_class=HTMLResponse)
def city(code: str) -> HTMLResponse:
    """その市区町村の人が使える制度の一覧（T-080b）。規則から作り、Claude は呼ばないので合言葉も回数制限もかけない。"""
    if code not in PLACES:
        raise HTTPException(404, "その団体コードの市区町村は一覧にありません")
    pref, name = PLACES[code]
    return HTMLResponse(city_page(RULES, code, pref, name, center_search(code), japan_map(RULES, PREFS, code[:2]),
                                  LOCAL.get(code)))


@app.get("/japan-map", response_class=HTMLResponse)
def japan_map_part() -> HTMLResponse:
    """全国の見取り図の HTML の断片（T-080c）。入口の app.js が読み込んで差し込む。規則だけから作り、Claude は呼ばない。"""
    return HTMLResponse(japan_map(RULES, PREFS))


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
