"""規則ファイルの原文の引用が、公式ページに一字も違わず載っているかを確かめる。

公式ページを取りに行くので、ふだんのテスト（pytest）とは分けてある。
PDF は pdftotext（poppler）で文字を取り出す。文字を持たない PDF（画像だけ）は「確かめられない」と出す。
空白・改行と、全角・半角の違い（NFKC）は無視して比べる。

使い方：uv run python scripts/check_quotes.py
"""

from __future__ import annotations

import html
import re
import subprocess
import sys
import tempfile
import unicodedata
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todoku import rules as rules_mod  # noqa: E402


def norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def page_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "todoku-quote-check/0.1"})
    with urllib.request.urlopen(req, timeout=30) as res:
        body = res.read()
        ctype = res.headers.get("Content-Type", "")
    if url.lower().endswith(".pdf") or "pdf" in ctype:
        with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
            f.write(body)
            f.flush()
            out = subprocess.run(["pdftotext", "-layout", f.name, "-"], capture_output=True, text=True, check=True)
        return out.stdout
    charset = "utf-8"
    m = re.search(r"charset=([\w-]+)", ctype) or re.search(rb'charset=["\']?([\w-]+)', body[:2000])
    if m:
        charset = m.group(1) if isinstance(m.group(1), str) else m.group(1).decode()
    text = body.decode(charset, errors="replace")
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def main() -> int:
    rs = rules_mod.load()
    failed = 0
    checked = 0
    for p in sorted(rs.programs.values(), key=lambda p: p.number):
        for s in p.sources:
            if not s.quote:
                continue
            checked += 1
            try:
                text = norm(page_text(s.url))
            except Exception as e:  # noqa: BLE001  取れなかった理由をそのまま出す
                print(f"取れない  {p.id}  {s.url}  {e}")
                failed += 1
                continue
            if not text:
                print(f"文字なし  {p.id}  {s.url}（画像だけの PDF の見込み）")
                failed += 1
            elif norm(s.quote) in text:
                print(f"一致      {p.id}  {s.url}")
            else:
                print(f"不一致    {p.id}  {s.url}\n          引用：{s.quote}")
                failed += 1
    print(f"引用 {checked} 件中、一致 {checked - failed} 件")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
