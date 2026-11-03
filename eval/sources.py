"""目隠しの読み手（読み B）に渡す公式資料の文を、手元に保存する。

規則ファイルの sources にある URL を 1 回ずつ取りに行き、本文の文字だけを eval/out/sources/ に置く
（eval/out/ は .gitignore 済み）。町田市のページは転載不可なので、リポジトリには入れない。
e-Gov の法令は、ページが画面の組み立てで本文を持たないので、e-Gov 法令 API から、規則の title に書いた条だけを切り出す。

使い方：
  uv run python eval/sources.py fetch      取りに行く（取ってあるものは飛ばす。--force で取り直す）
  uv run python eval/sources.py stats      制度ごとの文字数を出す
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "eval" / "out" / "sources"
INDEX = CACHE / "index.json"
UA = "todoku-eval/0.1 (+https://github.com/brightwaltz/todoku)"

SKIP_TAGS = {"script", "style", "noscript", "nav", "header", "footer", "form", "select", "svg", "button", "template"}
BLOCK_TAGS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "table", "section",
              "article", "ul", "ol", "dl", "td", "th", "caption", "blockquote", "pre"}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag in BLOCK_TAGS:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag in BLOCK_TAGS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def tidy(text: str) -> str:
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln in ("", "|"):
            if out and out[-1] != "":
                out.append("")
            continue
        out.append(ln)
    return "\n".join(out).strip() + "\n"


def html_to_text(raw: bytes) -> str:
    head = raw[:4096].decode("ascii", "ignore")
    m = re.search(r"charset=[\"']?([A-Za-z0-9_\-]+)", head)
    enc = (m.group(1) if m else "utf-8").lower()
    if enc in ("shift_jis", "sjis", "x-sjis"):
        enc = "cp932"
    try:
        html = raw.decode(enc, "replace")
    except LookupError:
        html = raw.decode("utf-8", "replace")
    p = _Text()
    p.feed(html)
    return tidy("".join(p.out))


def pdf_to_text(raw: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        r = subprocess.run(["pdftotext", "-enc", "UTF-8", f.name, "-"], capture_output=True, check=True)
    return tidy(r.stdout.decode("utf-8", "replace"))


EGOV_LAW = re.compile(r"https://laws\.e-gov\.go\.jp/law/([0-9A-Z]+)")
ARTICLE = re.compile(r"第([0-9０-９]+)条")


def egov_articles(law_id: str, articles: list[str], client: httpx.Client) -> str:
    """e-Gov 法令 API（v1 条文内容取得）で、指定の条だけを文字にする。"""
    parts = []
    for a in articles:
        url = f"https://laws.e-gov.go.jp/api/1/articles;lawId={law_id};article={a}"
        r = client.get(url)
        r.raise_for_status()
        xml = r.content.decode("utf-8", "replace")
        if "<Code>0</Code>" not in xml and "<Code>2</Code>" not in xml:
            raise RuntimeError(f"e-Gov API の応答が読めない：{url}")
        body = re.sub(r"<[^>]+>", "\n", xml.split("<ApplData>", 1)[-1])
        parts.append(f"（第{a}条）\n" + tidy(body))
        time.sleep(0.5)
    return "\n".join(parts)


def key_of(url: str, extra: str = "") -> str:
    return hashlib.sha1((url + extra).encode()).hexdigest()[:12]


def program_sources(rules_dir: Path = ROOT / "rules" / "programs") -> dict[int, dict]:
    progs = {}
    for f in sorted(rules_dir.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8"))
        progs[d["number"]] = {"id": d["id"], "name": d["name"], "level": d["level"], "sources": d.get("sources", [])}
    return dict(sorted(progs.items()))


def source_key(s: dict) -> tuple[str, str, list[str]]:
    """（キャッシュの鍵、種類、e-Gov の条）を返す。e-Gov は title の「第N条」ごとに鍵を分ける。"""
    url = s["url"]
    m = EGOV_LAW.match(url)
    if m:
        arts = [unicodedata.normalize("NFKC", a) for a in ARTICLE.findall(s.get("title", ""))]
        return key_of(url, ",".join(arts)), "egov", arts
    if url.lower().endswith(".pdf"):
        return key_of(url), "pdf", []
    return key_of(url), "html", []


def load_index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {}


def fetch(force: bool = False) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    index = load_index()
    seen = set()
    failed = []
    with httpx.Client(headers={"User-Agent": UA}, timeout=60.0, follow_redirects=True) as client:
        for num, p in program_sources().items():
            for s in p["sources"]:
                key, kind, arts = source_key(s)
                if key in seen:
                    continue
                seen.add(key)
                if key in index and not force and (CACHE / f"{key}.txt").exists():
                    continue
                try:
                    if kind == "egov":
                        if not arts:
                            raise RuntimeError("title に「第N条」がない")
                        text = egov_articles(EGOV_LAW.match(s["url"]).group(1), arts, client)
                        raw_len = len(text)
                    else:
                        r = client.get(s["url"])
                        r.raise_for_status()
                        raw_len = len(r.content)
                        text = pdf_to_text(r.content) if kind == "pdf" else html_to_text(r.content)
                except Exception as e:  # 取れなかったものは記録して先へ
                    failed.append((num, s["url"], repr(e)[:200]))
                    print(f"  取れない：{num} {s['url']} {e!r}"[:300], file=sys.stderr)
                    continue
                (CACHE / f"{key}.txt").write_text(text, encoding="utf-8")
                index[key] = {"url": s["url"], "kind": kind, "articles": arts, "title": s.get("title", ""),
                              "publisher": s.get("publisher", ""), "bytes": raw_len, "chars": len(text),
                              "sha256": hashlib.sha256(text.encode()).hexdigest(),
                              "fetched": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                warn = "  ← 文字が取れない（画像の資料）" if len(text.strip()) < 50 else ""
                print(f"  {num:>2} {kind:4} {len(text):>7} 字  {s['url']}{warn}")
                time.sleep(1.0)
    INDEX.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"保存 {len(index)} 件、取れなかった {len(failed)} 件")
    if failed:
        sys.exit(1)


def program_text(num: int, progs: dict | None = None, index: dict | None = None) -> tuple[str, list[dict]]:
    """制度 num の公式資料の文をつないで返す（資料の見出しつき）。"""
    progs = progs or program_sources()
    index = index if index is not None else load_index()
    parts, used = [], []
    for i, s in enumerate(progs[num]["sources"], 1):
        key, _, _ = source_key(s)
        meta = index.get(key)
        if not meta:
            raise RuntimeError(f"資料が保存されていない：制度 {num} {s['url']}（先に fetch）")
        text = (CACHE / f"{key}.txt").read_text(encoding="utf-8")
        if len(text.strip()) < 50:
            text = "（この資料は画像で、文字を取り出せなかった）\n"
        # 規則の title は渡さない。規則を書いた人の読み（「対象の病気に認知症を含む」など）が入っているため
        parts.append(f"【資料{i}】{s.get('publisher', '')}\n{s['url']}\n\n{text}")
        used.append({"key": key, "url": s["url"], "sha256": meta["sha256"]})
    return "\n\n".join(parts), used


def stats() -> None:
    progs = program_sources()
    index = load_index()
    total = 0
    for num, p in progs.items():
        try:
            text, _ = program_text(num, progs, index)
        except RuntimeError as e:
            print(f"{num:>2} {p['name']}：{e}")
            continue
        total += len(text)
        print(f"{num:>2} {len(text):>7} 字  {p['name']}")
    print(f"合計 {total} 字")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch":
        fetch(force="--force" in sys.argv)
    elif cmd == "stats":
        stats()
    else:
        sys.exit(__doc__)
