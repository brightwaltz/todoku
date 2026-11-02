// とどく：画面の動き。事実は画面が持ち、毎回サーバに送って判定と次の問いを受け取る。
// 相談の文章と答えはサーバに残らない。数えるのは聞き返した数・押した数・秒数だけ。
"use strict";

const DONT_KNOW = "__dont_know__";
const NOT_THIS = "__not__";
const EXAMPLES = [
  "町田市に住む母（82歳）が先月退院してから、家の中で転びやすくなりました。介護保険は使ったことがありません。私は会社勤めで、日中は母ひとりです。",
  "父が認知症と診断されました。最近、散歩に出て帰り道が分からなくなることがあります。町田市で父と母の二人暮らしです。",
  "町田市でひとり暮らしの祖母が要介護4になり、紙おむつを使っています。世帯は住民税非課税です。",
];

const $ = (id) => document.getElementById(id);
let state = fresh();
let aikotoba = "";
try { aikotoba = sessionStorage.getItem("aikotoba") || ""; } catch (e) { /* 保存できない環境でも動く */ }
let resendAfterGate = false;  // 合言葉で止まった相談を、合言葉のあとで送り直す
class GateError extends Error {}

function fresh() {
  return { subject: "ご本人", facts: {}, suggestions: {}, asked: 0, presses: 0, startedAt: 0, engine: "" };
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function show(id, on = true) { $(id).hidden = !on; }

function askGate(wrong) {
  $("gate-msg").textContent = wrong
    ? "合言葉が違います。入れ直してください。"
    : "試しに使っていただくための合言葉を入れてください。";
  show("gate");
  $("gate").scrollIntoView({ block: "start" });
  $("aikotoba").focus();
  if (wrong) $("aikotoba").select();  // 違ったときは、そのまま打ち直せるように
}

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Aikotoba": aikotoba },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { askGate(aikotoba !== ""); throw new GateError("合言葉を入れると、続けて読み取ります"); }
  if (!res.ok) {
    let msg = "うまくいきませんでした";
    try { msg = (await res.json()).detail || msg; } catch (e) { /* 本文なし */ }
    throw new Error(msg);
  }
  return res.json();
}

// ---- 相談を送る ----
async function send() {
  const text = $("text").value.trim();
  if (!text) { $("text").focus(); return; }
  state = fresh();
  state.startedAt = Date.now();
  $("send").disabled = true;
  show("reading"); show("extracted", false); show("ask", false); show("result", false);
  clearBoard();  // 前の相談の見立てから動かさない
  try {
    const data = await api("/api/consult", { text });
    state.subject = data.subject;
    state.facts = data.facts;
    state.suggestions = data.suggestions;
    state.engine = data.engine;
    renderExtracted(data, text);
    render(data, true);
  } catch (e) {
    resendAfterGate = e instanceof GateError;
    $("reading").replaceChildren(el("p", { class: "error" }, e.message));
    $("send").disabled = false;
    return;
  }
  $("send").disabled = false;
  show("reading", false);
}

function renderExtracted(data, text) {
  $("said").replaceChildren(...markEvidence(text, data.extracted));
  const list = $("extracted-list");
  list.replaceChildren();
  for (const [i, f] of data.extracted.entries()) {
    list.append(el("li", { style: `--i: ${i}` },
      `${f.name}：${f.label}`,
      f.confidence === "low" ? el("span", { class: "tentative" }, "（あとで確かめます）") : null,
      f.evidence ? el("div", { class: "ev" }, `「${f.evidence}」から`) : null));
  }
  if (!data.extracted.length) list.append(el("li", {}, "読み取れた事実はありませんでした。いくつか質問します。"));
  $("engine-note").textContent = data.engine === "fake"
    ? "試験モード：Claude を使わず、キーワードで読み取っています。"
    : "Claude が読み取り、根拠の箇所が相談の文章にあるかをプログラムで確かめました。";
  show("extracted");
}

// 相談の文章のうち、事実の根拠になった言葉に印を付ける。印 i と事実の行 i は同じ遅れで現れる
function markEvidence(text, extracted) {
  const spans = [];
  for (const [i, f] of extracted.entries()) {
    const at = f.evidence ? text.indexOf(f.evidence) : -1;
    if (at < 0) continue;
    const end = at + f.evidence.length;
    if (spans.some((s) => at < s.end && s.start < end)) continue;  // 重なる印は後のものを付けない
    spans.push({ start: at, end, i, low: f.confidence === "low" });
  }
  spans.sort((a, b) => a.start - b.start);
  const parts = [];
  let last = 0;
  for (const s of spans) {
    parts.push(text.slice(last, s.start),
      el("mark", { class: s.low ? "low" : "", style: `--i: ${s.i}` }, text.slice(s.start, s.end)));
    last = s.end;
  }
  parts.push(text.slice(last));
  return parts;  // 文字列は replaceChildren がテキストノードにする
}

// ---- いまの見立て ----
// 制度ごとの札を 4 つの列に置き、答えのたびに列が変わった札だけを動かす（FLIP）
const BOARD = [
  ["eligible", "当てはまる", "当てはまる見込み"],
  ["after_prereq", "先に手続き", "先に手続きが要る制度"],
  ["unknown", "確かめる", "確かめると分かる制度"],
  ["no", "当てはまらない", "当てはまらない制度"],
];
const colOf = (status) => (status === "ineligible" || status === "done" ? "no" : status);
const calm = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let tiles = new Map();  // 制度の id → 札。同じ札を動かすので作り直さない
let boardPrev = null;   // 前回の { 制度の id: 列 }

function clearBoard() {
  tiles = new Map();
  boardPrev = null;
  $("board-cols").replaceChildren();
  $("board-change").textContent = "";
  show("board", false);
}

function restart(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;  // 外したことを描画に知らせてから付け直す（続けて同じ動きを出すため）
  node.classList.add(cls);
}

function renderBoard(data) {
  const box = $("board-cols");
  if (!box.children.length) {
    box.append(...BOARD.map(([key, label]) => el("div", { class: `col ${key}`, "data-col": key },
      el("div", { class: "col-head" }, el("span", { class: "label" }, label), el("span", { class: "count" }, "0")),
      el("ul", { class: "tiles", "aria-label": label }))));
  }
  show("board");  // 隠れていると位置が測れない
  // 1. 動かす前の位置を測る
  const first = new Map([...tiles].map(([id, t]) => [id, t.getBoundingClientRect()]));
  // 2. 札を今の列に並べ直す（順は判定の並び）
  const now = {};
  for (const r of data.results) {
    let t = tiles.get(r.id);
    if (!t) {
      t = el("li", { class: "tile", title: r.name, "aria-label": r.name });
      tiles.set(r.id, t);
    }
    t.className = `tile ${r.status}`;
    now[r.id] = colOf(r.status);
    box.querySelector(`[data-col="${now[r.id]}"] .tiles`).append(t);
  }
  const moved = boardPrev ? data.results.filter((r) => boardPrev[r.id] && boardPrev[r.id] !== now[r.id]) : [];
  // 3. 動いた札を元の位置に戻して見せ、transform を外して新しい位置まで滑らせる
  if (!calm()) {
    for (const r of moved) {
      const t = tiles.get(r.id);
      const a = first.get(r.id);
      const b = t.getBoundingClientRect();
      t.style.transition = "none";
      t.style.transform = `translate(${a.left - b.left}px, ${a.top - b.top}px)`;
      void t.offsetWidth;
      t.style.transition = "transform 420ms cubic-bezier(.2, .7, .3, 1)";
      t.style.transform = "";
      t.addEventListener("transitionend", () => { t.style.transition = ""; }, { once: true });
    }
  }
  for (const [key] of BOARD) {
    const col = box.querySelector(`[data-col="${key}"]`);
    const count = col.querySelector(".count");
    const n = String(col.querySelector(".tiles").children.length);
    if (count.textContent !== n) {
      count.textContent = n;
      if (boardPrev) restart(count, "bump");
    }
  }
  // 4. 質問の答えで判定が決まりうる制度の札に、輪を 2 回
  for (const t of tiles.values()) t.classList.remove("pulse");
  if (data.question) for (const id of data.question.affects_ids) { const t = tiles.get(id); if (t) restart(t, "pulse"); }
  $("board-change").textContent = changeText(moved, now);
  boardPrev = now;
  // 質問へ移るとき、上に留まる見立ての帯の下に問いが来るように
  $("board").classList.toggle("live", Boolean(data.question));
  document.documentElement.style.setProperty("--board-h", `${$("board").offsetHeight + 8}px`);
}

function changeText(moved, now) {
  const parts = [];
  for (const [key, , say] of BOARD) {
    const names = moved.filter((r) => now[r.id] === key).map((r) => r.name);
    if (!names.length) continue;
    // 上に留まる帯を低く保つため、名前を添えるのは「当てはまる見込み」に入った制度だけ（2 件まで）
    const named = key === "eligible"
      ? `（${names.slice(0, 2).join("、")}${names.length > 2 ? ` ほか ${names.length - 2} 件` : ""}）` : "";
    parts.push(`${say} +${names.length}${named}`);
  }
  return parts.join("・");
}

// ---- 聞き返し ----
function render(data, first = false) {
  renderBoard(data);
  if (data.question) { show("ask"); show("result", false); renderQuestion(data.question, data.max_questions, first); }
  else { show("ask", false); renderResult(data); }
}

// 読み取った直後は、根拠の言葉に印が付き終わるのを見せてから質問へ移る（自分で動かしたら追わない）
function revealThenAsk() {
  $("extracted").scrollIntoView({ behavior: "smooth", block: "start" });
  const wait = calm() ? 0 : $("extracted-list").children.length * 150 + 1200;
  const timer = setTimeout(() => $("ask").scrollIntoView({ behavior: "smooth", block: "start" }), wait);
  const stop = () => clearTimeout(timer);
  for (const ev of ["wheel", "touchmove", "keydown"]) window.addEventListener(ev, stop, { once: true, passive: true });
}

function renderQuestion(q, max, first = false) {
  $("ask-count").textContent = `質問 ${state.asked + 1}（多くて ${max} つ）`;
  $("ask-text").textContent = q.text;
  const box = $("ask-options");
  box.replaceChildren();
  for (const o of q.options) {
    box.append(el("button", { class: "option", onclick: () => answer(q, o.value) }, o.label));
  }
  box.append(el("button", { class: "option dk", onclick: () => answer(q, DONT_KNOW) }, "わからない"));
  const names = q.affects.slice(0, 3).join("、") + (q.affects.length > 3 ? ` ほか ${q.affects.length - 3} 件` : "");
  $("ask-why").textContent = `この答えで判定が決まる制度：${names}`;
  $("ask-check").textContent = q.check;
  // 選択肢ではなく問いの文に移る（1 つ目の選択肢に色が付くと、先に選んであるように見えるため）
  $("ask-text").focus({ preventScroll: true });
  if (first) revealThenAsk();
  else $("ask").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function answer(q, value) {
  state.presses += 1;
  state.asked += 1;
  if (q.confirm && value === NOT_THIS) {
    delete state.suggestions[q.fact];
  } else {
    state.facts[q.fact] = value;
    delete state.suggestions[q.fact];
  }
  await step();
}

async function step(stop = false) {
  try {
    const data = await api("/api/step", {
      facts: state.facts, suggestions: state.suggestions, subject: state.subject,
      asked: stop ? 99 : state.asked,
    });
    render(data);
  } catch (e) {
    $("ask-why").textContent = e.message;
  }
}

// ---- 結果 ----
const GROUPS = [
  ["eligible", "当てはまる見込み", "申請できそうな制度です。最終的な判断は申請先で行われます。"],
  ["after_prereq", "先に手続きをすると使える", "前の手続きを済ませると、対象になりそうな制度です。"],
  ["unknown", "確かめると使えるかもしれない", "分からない点があり、まだ決められない制度です。確かめ方を添えています。"],
];

function renderResult(data) {
  const seconds = Math.round((Date.now() - state.startedAt) / 1000);
  $("stats").textContent = `聞き返し ${state.asked} 問・押した数 ${state.presses} 回・${seconds} 秒`;
  const groups = $("groups");
  groups.replaceChildren();
  for (const [status, title, hint] of GROUPS) {
    const items = data.results.filter((r) => r.status === status);
    if (!items.length) continue;
    groups.append(el("h2", { class: "group-title" }, title, el("span", { class: "n" }, `${items.length} 件`)));
    groups.append(el("p", { class: "note" }, hint));
    // 確かめると使えるかもしれない制度は多くなりやすいので、重みの大きい順に 5 件だけ先に出す
    const first = status === "unknown" ? items.slice(0, 5) : items;
    for (const r of first) groups.append(programCard(r));
    if (first.length < items.length) {
      const rest = el("div", { hidden: "" }, items.slice(first.length).map(programCard));
      const more = el("button", { class: "link", onclick: () => { rest.hidden = false; more.remove(); } },
        `ほか ${items.length - first.length} 件を見る`);
      groups.append(more, rest);
    }
  }
  const no = data.results.filter((r) => r.status === "ineligible" || r.status === "done");
  $("ineligible-title").textContent = `当てはまらない・済んでいる制度（${no.length} 件）`;
  $("ineligible-list").replaceChildren(...no.map((r) => el("li", {},
    `${r.name}：`, r.status === "done" ? "済んでいる" : reasonText(r))));
  show("ineligible-box", no.length > 0);
  show("result");
  $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  summarize();
}

function reasonText(r) {
  const f = r.reasons.find((x) => x.state === "F");
  return f ? `${f.label}${f.value ? `（${f.value}）` : ""} に当たらない` : "条件に当たらない";
}

function programCard(r) {
  const head = el("div", { class: "head" },
    el("span", { class: "tag" }, r.level), el("span", { class: "name" }, r.name),
    el("span", { class: `pill ${r.status}` }, r.status_label));
  const sumParts = [head, el("p", { class: "sum" }, r.summary)];
  if (r.waiting_on.length) {
    sumParts.push(el("p", { class: "chain" }, "先に：",
      ...r.waiting_on.map((w, i) => [i ? "、" : "", el("a", { href: `#p-${w.id}`, onclick: (ev) => openCard(ev, w.id) }, w.name)])));
  }
  if (r.status === "unknown" && r.missing.length) {
    sumParts.push(el("p", { class: "chain" }, "分からない点：", r.missing.map((m) => m.name).join("、")));
  }
  const body = el("div", { class: "body" });
  body.append(el("h3", {}, "判定の理由"), el("ul", { class: "reasons" },
    r.reasons.map((x) => el("li", { class: x.state }, x.label, x.value ? el("span", { class: "val" }, `（${x.value}）`) : null))));
  if (r.missing.length) {
    body.append(el("h3", {}, "確かめ方"), el("ul", { class: "plain" }, r.missing.map((m) => el("li", {}, m.check))));
  }
  if (r.at_application.length) body.append(el("h3", {}, "申請のときに確かめられること"), el("ul", { class: "plain" }, r.at_application.map((t) => el("li", {}, t))));
  body.append(el("h3", {}, "申請先"), el("p", {}, r.apply.where));
  if (r.apply.how) body.append(el("p", {}, r.apply.how));
  if (r.apply.phone) body.append(el("p", {}, "電話：", telLinks(r.apply.phone)));
  if (r.bring.length) body.append(el("h3", {}, "持ち物・書類"), el("ul", { class: "plain" }, r.bring.map((t) => el("li", {}, t))));
  if (r.amount.length) body.append(el("h3", {}, "受けられる内容"), el("ul", { class: "plain" }, r.amount.map((t) => el("li", {}, t))));
  if (r.timing.length) body.append(el("h3", {}, "時期・期限"), el("ul", { class: "plain" }, r.timing.map((t) => el("li", {}, t))));
  body.append(el("h3", {}, "出典"), ...r.sources.map(sourceView));
  return el("details", { class: "card prog", id: `p-${r.id}` }, el("summary", {}, sumParts), body);
}

function telLinks(text) {
  const parts = [];
  let last = 0;
  for (const m of text.matchAll(/0\d{1,4}-\d{1,4}-\d{3,4}/g)) {
    parts.push(text.slice(last, m.index), el("a", { class: "tel", href: `tel:${m[0].replaceAll("-", "")}` }, m[0]));
    last = m.index + m[0].length;
  }
  parts.push(text.slice(last));
  return parts;
}

function sourceView(s) {
  return el("div", { class: "src" },
    s.quote ? el("blockquote", {}, `「${s.quote}」`) : null,
    `${s.publisher}${s.title ? `「${s.title}」` : ""}（${s.checked} 確認・${s.license}）`, el("br"),
    el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.url));
}

function openCard(ev, id) {
  ev.preventDefault();
  const card = $(`p-${id}`);
  if (!card) return;
  if (card.parentElement.hidden) card.parentElement.hidden = false;
  card.open = true;
  card.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function summarize() {
  show("summary", false);
  try {
    const data = await api("/api/summary", { facts: state.facts, subject: state.subject });
    if (!data.lines.length) return;
    $("summary-lines").replaceChildren(...data.lines.map((l) => el("li", {}, l.text)));
    $("summary-note").textContent = data.engine === "fake"
      ? "試験モード：このまとめは決まった文の組み立てです。判定そのものは規則のプログラムが決めています。"
      : "このまとめは Claude が判定の結果から書きました。判定そのものは規則のプログラムが決めています。";
    show("summary");
  } catch (e) { /* まとめが出なくても結果は読める */ }
}

// ---- 始める ----
document.querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
  $("text").value = EXAMPLES[Number(b.dataset.ex)];
}));
$("send").addEventListener("click", send);
$("skip").addEventListener("click", () => step(true));
$("again").addEventListener("click", () => {
  state = fresh();
  $("text").value = "";
  show("extracted", false); show("ask", false); show("result", false);
  clearBoard();
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("text").focus();
});
$("gate-ok").addEventListener("click", () => {
  if (!$("aikotoba").value.trim()) { $("aikotoba").focus(); return; }
  aikotoba = $("aikotoba").value;
  try { sessionStorage.setItem("aikotoba", aikotoba); } catch (e) { /* 保存できなくても続ける */ }
  show("gate", false);
  if (resendAfterGate) { resendAfterGate = false; send(); }
});
$("aikotoba").addEventListener("keydown", (ev) => { if (ev.key === "Enter") $("gate-ok").click(); });
// 合言葉が要る置き場では、書き始める前に聞く（書いて送ってから止められないように）
fetch("/api/health").then((r) => r.json()).then((h) => {
  if (h.passphrase && !aikotoba) show("gate");
}).catch(() => { /* 分からなければ、送ったときに聞く */ });
