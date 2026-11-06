"""見本を開いたとき、見本の相談の文そのものを「読み取ったこと」の欄の見出しにする（T-180c）。

11/7 の試用で、自分の文を書いた後に町田市の見本を押した人が「設定が町田市にされた」としか見えなかった。
見本では、見出しを見本の文（根拠の印つき）にし、右の「あなた」の吹き出しは出さない。説明の文は足さない。
自分の相談・出来事から来たときの見出しと吹き出しは今のまま。画面での見え方（390・1280 の写真）は ops の open_t180c.py で見る。
"""

import json
import re
import shutil
import subprocess

import pytest

from todoku import app as app_mod

JS = (app_mod.WEB_DIR / "app.js").read_text(encoding="utf-8")
CSS = (app_mod.WEB_DIR / "style.css").read_text(encoding="utf-8")


def _run_render_extracted(cases: list[dict]) -> list[dict]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node が無い")
    func = re.search(r"^function renderExtracted\(data, text\) \{\n.*?^\}\n", JS, re.S | re.M).group(0)
    run = f"""
const nodes = {{}};
const mk = (id) => ({{ id, hidden: false, kids: [], textContent: "", cls: new Set(),
  classList: {{ toggle(c, on) {{ if (on) nodes._h.cls.add(c); else nodes._h.cls.delete(c); }} }},
  replaceChildren(...k) {{ this.kids = k; }}, append(...k) {{ this.kids.push(...k); }},
  get children() {{ return this.kids; }}, querySelector() {{ return nodes._h; }} }});
nodes._h = mk("h2");
const $ = (id) => (nodes[id] ||= mk(id));
const show = (id, on = true) => {{ $(id).hidden = !on; }};
const el = (tag, attrs, ...kids) => ({{ tag, cls: (attrs && attrs.class) || "", text: kids.join("") }});
const keepTail = (a, b) => [a, b];
const markEvidence = (text) => ["[" + text + "]"];
const flat = (k) => typeof k === "string" ? k : `<${{k.tag}}.${{k.cls}}>${{k.text}}`;
{func}
const out = [];
for (const data of {json.dumps(cases)}) {{
  renderExtracted({{ extracted: [], ...data }}, "町田市に住む母（82歳）が…");
  out.push({{ head: nodes._h.kids.map(flat), sampleHead: nodes._h.cls.has("sample-head"),
    said: $("said").kids.map(flat), saidHidden: $("said").hidden }});
}}
console.log(JSON.stringify(out));
"""
    return json.loads(subprocess.run([node, "-e", run], capture_output=True, text=True, check=True).stdout)


def test_sample_text_becomes_the_heading_and_the_you_bubble_is_hidden():
    sample, own, event, own_again = _run_render_extracted([
        {"sample": True, "engine": "sample"},
        {"sample": False, "engine": "fake"},
        {"sample": False, "event": True, "engine": "event"},
        {"sample": False, "engine": "fake"},
    ])
    # 見本：見出しは「見本」の一語と、根拠の印を付けた見本の文。吹き出しは空にして隠す
    assert sample == {"head": ["<span.kicker>見本", "[町田市に住む母（82歳）が…]"], "sampleHead": True,
                      "said": [], "saidHidden": True}
    # 自分の相談：見出しは今のまま、文は右の吹き出しに
    assert own == {"head": ["相談の文章から", "読み取ったこと"], "sampleHead": False,
                   "said": ["[町田市に住む母（82歳）が…]"], "saidHidden": False}
    # 出来事から来たときの見出しも今のまま（T-071）
    assert event["head"] == ["選んだ出来事から", "分かったこと"] and event["saidHidden"] is False
    # 見本の後に自分の相談を送ったら、見本の見出しの形と隠した吹き出しを戻す
    assert own_again == own


def test_sample_heading_adds_no_explanatory_sentence():
    body = re.search(r"^function renderExtracted\(data, text\) \{\n(.*?)^\}\n", JS, re.S | re.M).group(1)
    line = next(l for l in body.splitlines() if l.strip().startswith("if (data.sample) head.replaceChildren("))
    # 見出しに足す文字は「見本」だけ（説明の文を足さない）
    assert re.findall(r'"([^"]*[^\x00-\x7f][^"]*)"', line) == ["見本"]


def test_sample_heading_styles():
    assert ".said mark, .sample-head mark {" in CSS, "根拠の印は見出しの文にも同じ形で付ける"
    assert ".said mark.low, .sample-head mark.low {" in CSS
    assert re.search(r"prefers-reduced-motion: reduce\) \{\n  \.said mark, \.sample-head mark,", CSS), "動きを減らす設定では印を動かさない"
    m = re.search(r"\n\.sample-head \{([^}]*)\}", CSS)
    size = float(re.search(r"font-size: ([\d.]+)rem", m.group(1)).group(1))
    base = float(re.search(r"\nh2 \{ font-size: ([\d.]+)rem", CSS).group(1))
    assert size >= base, "見本の文は、ほかの欄の見出しより小さくしない"
    assert "@supports (word-break: auto-phrase) { .sample-head { word-break: auto-phrase; text-wrap: balance;" in CSS
