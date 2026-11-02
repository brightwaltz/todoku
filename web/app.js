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

async function api(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Aikotoba": aikotoba },
    body: JSON.stringify(body),
  });
  if (res.status === 401) { show("gate"); throw new Error("合言葉を入れてください"); }
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
  try {
    const data = await api("/api/consult", { text });
    state.subject = data.subject;
    state.facts = data.facts;
    state.suggestions = data.suggestions;
    state.engine = data.engine;
    renderExtracted(data);
    render(data);
  } catch (e) {
    $("reading").replaceChildren(el("p", { class: "error" }, e.message));
    $("send").disabled = false;
    return;
  }
  $("send").disabled = false;
  show("reading", false);
}

function renderExtracted(data) {
  const list = $("extracted-list");
  list.replaceChildren();
  for (const f of data.extracted) {
    list.append(el("li", {},
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

// ---- 聞き返し ----
function render(data) {
  if (data.question) { renderQuestion(data.question, data.max_questions); show("ask"); show("result", false); }
  else { show("ask", false); renderResult(data); }
}

function renderQuestion(q, max) {
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
  $("ask").scrollIntoView({ behavior: "smooth", block: "start" });
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
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("text").focus();
});
$("gate-ok").addEventListener("click", () => {
  aikotoba = $("aikotoba").value;
  try { sessionStorage.setItem("aikotoba", aikotoba); } catch (e) { /* 保存できなくても続ける */ }
  show("gate", false);
});
