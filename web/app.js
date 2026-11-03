// とどく：画面の動き。事実は画面が持ち、毎回サーバに送って判定と次の問いを受け取る。
// 相談の文章と答えはサーバに残らない。数えるのは聞き返した数・押した数・秒数だけ。
"use strict";

const DONT_KNOW = "__dont_know__";
const NOT_THIS = "__not__";
// 選ぶだけで下書きを作る入口。書くのが難しい人のために、選んだものを文にして欄に入れる（本人が直して送る）
const WHO = ["母", "父", "妻", "夫", "祖母", "祖父", "ご自分", "ほかの方"];
const AGES = ["65歳未満", "65〜74歳", "75歳以上"];
const WORRIES = [
  ["転びやすい", "家の中で転びやすくなりました。"],
  ["退院した", "最近、病院から退院しました。"],
  ["物忘れ", "物忘れが増えてきました。"],
  ["道に迷う", "外出すると道に迷うことがあります。"],
  ["火の消し忘れ", "火の消し忘れが心配です。"],
  ["おむつ", "紙おむつを使っています。"],
  ["聞こえにくい", "耳が聞こえにくくなりました。"],
  ["日中ひとり", "日中はひとりで過ごしています。"],
  ["医療費", "入院や通院の医療費の負担が重くなっています。"],
  ["仕事と介護", "私は働きながら介護をしています。"],
  ["家を直したい", "手すりを付けたり、段差をなくしたりしたいです。"],
  ["介護サービス", "介護保険のサービスを使っています。"],
];

// 合言葉はヘッダ（X-Aikotoba）で送る。ブラウザはヘッダに日本語を入れられず、fetch が送る前に失敗する（見本も動かなくなる）。
// 全角の英数字は半角に直し、それでも半角の英数字・記号でない文字が残れば null を返す。
// サーバは合言葉を UTF-8 のバイトで比べるので、ASCII でない文字の合言葉は、ここで断らなくても画面からは通らない
function aikotobaWord(raw) {
  const word = raw.normalize("NFKC").trim();
  return /^[\x20-\x7e]*$/.test(word) ? word : null;
}

const $ = (id) => document.getElementById(id);
let state = fresh();
let aikotoba = "";
try {
  aikotoba = aikotobaWord(sessionStorage.getItem("aikotoba") || "") ?? "";
  if (!aikotoba) sessionStorage.removeItem("aikotoba");  // 前の版で保存した、送れない合言葉を残さない
} catch (e) { /* 保存できない環境でも動く */ }
let pick = freshPick();
let lastDraft = "";     // 最後に作った下書き。欄がこれと違えば、本人が書き換えたとみなして上書きしない
let prefs = [];         // municipalities.json の都道府県（市区町村つき）
let resendAfterGate = false;  // 合言葉で止まった相談を、合言葉のあとで送り直す
class GateError extends Error {}

function fresh() {
  return { subject: "ご本人", facts: {}, suggestions: {}, asked: 0, presses: 0, startedAt: 0, engine: "",
    municipality: "", sample: false, resultShown: false, summaryKey: "", laterOpen: false };
}

function freshPick() {
  return { who: "", age: "", worries: new Set(), pref: "", city: "" };
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
    : "ご自分の相談を送るには合言葉が要ります。下の見本は合言葉なしで見られます。";
  show("gate");
  $("gate").scrollIntoView({ block: "start" });
  $("aikotoba").focus();
  if (wrong) $("aikotoba").select();  // 違ったときは、そのまま打ち直せるように
}

async function api(path, body) {
  let res;
  const headers = { "Content-Type": "application/json" };
  if (aikotoba) headers["X-Aikotoba"] = aikotoba;  // aikotobaWord を通した値だけが入る
  try {
    res = await fetch(path, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch (e) {
    // 通信が切れたとき、ブラウザの英語の文（Failed to fetch・Load failed）を出さない
    throw new Error("通信できませんでした。電波の届く所で、もう一度試してください");
  }
  if (res.status === 401) { askGate(aikotoba !== ""); throw new GateError("合言葉を入れると、続けて読み取ります"); }
  if (!res.ok) {
    let msg = "うまくいきませんでした";
    try { msg = (await res.json()).detail || msg; } catch (e) { /* 本文なし */ }
    throw new Error(msg);
  }
  return res.json();
}

// ---- 選ぶだけの下書き ----
function chipGroup(boxId, labels, multi) {
  $(boxId).replaceChildren(...labels.map((label) => el("button", {
    type: "button", class: "chip pick-chip", "aria-pressed": "false",
    onclick: (ev) => choose(boxId, label, multi, ev.currentTarget),
  }, label)));
}

function choose(boxId, label, multi, button) {
  const on = button.getAttribute("aria-pressed") !== "true";
  if (multi) {
    if (on) pick.worries.add(label); else pick.worries.delete(label);
  } else {
    // 1 つだけ選ぶ組。もう一度押すと外れる
    for (const b of $(boxId).children) b.setAttribute("aria-pressed", "false");
    pick[boxId === "pick-who" ? "who" : "age"] = on ? label : "";
  }
  button.setAttribute("aria-pressed", String(on));
  redraft();
}

function buildDraft() {
  if (!pick.who && !pick.worries.size) return "";
  const parts = [];
  const age = pick.age ? `（${pick.age}）` : "";
  if (pick.who === "ご自分") parts.push(`私自身${age}のことです。`);
  else if (pick.who) parts.push(`${pick.who}${age}のことです。`);
  else if (pick.age) parts.push(`${pick.age}の方のことです。`);
  const pref = prefs.find((p) => p.code === pick.pref);
  if (pref) {
    const city = pref.cities.find(([code]) => code === pick.city);
    parts.push(`${pref.name}${city ? city[1] : ""}に住んでいます。`);
  }
  for (const [label, sentence] of WORRIES) if (pick.worries.has(label)) parts.push(sentence);
  return parts.join("");
}

// 選ぶたびに作り直す。ただし本人が欄を書き換えていたら上書きせず、作り直すボタンを出す
function redraft(force = false) {
  const box = $("text");
  const draft = buildDraft();
  const edited = box.value !== lastDraft && box.value.trim() !== "";
  if (edited && !force) { show("redraft", draft !== "" && draft !== box.value); return; }
  box.value = draft;
  lastDraft = draft;
  show("redraft", false);
}

function fillCities(prefCode = pick.pref, select = $("city")) {
  const pref = prefs.find((p) => p.code === prefCode);
  select.replaceChildren(el("option", { value: "" }, "市区町村"),
    ...(pref ? pref.cities.map(([code, name]) => el("option", { value: code }, name)) : []));
  select.disabled = !pref;
}

function resetPick() {
  pick = freshPick();
  lastDraft = "";
  document.querySelectorAll(".pick-chip").forEach((b) => b.setAttribute("aria-pressed", "false"));
  $("pref").value = "";
  fillCities();
  show("redraft", false);
}

// ---- 相談を送る ----
async function send() {
  const text = $("text").value.trim();
  if (!text) { $("text").focus(); return; }
  await consultWith(text, () => api("/api/consult", { text, municipality: pick.city }));
}

// 見本は、前もって Claude が読み取った結果をサーバから受け取る（Claude を呼ばない）
async function openSample(s) {
  if (!s.ready) { $("text").value = s.text; redraft(); return; }  // 用意がなければ、欄に例文を入れるだけ
  await consultWith(s.text, () => api("/api/sample", { id: s.id }));
}

async function consultWith(text, call) {
  state = fresh();
  state.startedAt = Date.now();
  $("send").disabled = true;
  $("reading").replaceChildren(el("p", {}, "読み取っています…"));
  show("reading"); show("extracted", false); show("ask", false); show("result", false);
  clearBoard();  // 前の相談の見立てから動かさない
  try {
    const data = await call();
    // 読み取ったことの欄へ送る前に消す。送った後に消すと、その高さぶん中身が上へずれ、見出しが画面の上に切れる（T-023）
    show("reading", false);
    state.subject = data.subject;
    state.facts = data.facts;
    state.suggestions = data.suggestions;
    state.engine = data.engine;
    state.sample = Boolean(data.sample);
    state.municipality = data.place ? data.place.code : "";
    renderExtracted(data, text);
    render(data, true);
  } catch (e) {
    resendAfterGate = e instanceof GateError;
    $("reading").replaceChildren(el("p", { class: "error" }, e.message));
    show("reading");  // 受け取った後に落ちたときも、知らせが見えるように
    $("send").disabled = false;
    return;
  }
  $("send").disabled = false;
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
  const made = data.made && data.made.model && data.made.date ? `（${data.made.model}、${data.made.date}）` : "";
  $("engine-note").textContent = data.sample
    ? `見本：前もって Claude${made}が読み取った結果です。この見本では Claude を呼びません。`
    : data.engine === "fake"
      ? "試験モード：Claude を使わず、キーワードで読み取っています。"
      : "Claude が読み取り、根拠の箇所が相談の文章にあるかをプログラムで確かめました。";
  show("extracted");
}

// サーバの照合（claude.normalize）と同じく、NFKC にそろえて空白を除く。全角・半角が違っても根拠の言葉が見つかるように
const squash = (s) => s.normalize("NFKC").replace(/\s+/g, "");

// 文をそろえた形にし、そろえた後の 1 文字ごとに元の文の何文字目から何文字目までかを持つ（印は元の文に付けるため）
function squashed(text) {
  const units = [];
  let i = 0;
  for (const ch of text) {
    const n = squash(ch);
    const last = units[units.length - 1];
    if (last && /^\p{M}/u.test(n)) {
      // 半角の濁点（ｶﾞ）のように、前の字と合わせて 1 字になるものは、前の字とまとめてそろえ直す
      last.end = i + ch.length;
      last.n = squash(text.slice(last.start, last.end));
    } else units.push({ start: i, end: i + ch.length, n });
    i += ch.length;
  }
  const starts = [], ends = [];
  // indexOf と length は UTF-16 の単位で数えるので、こちらも同じ単位で並べる
  for (const u of units) for (let k = 0; k < u.n.length; k++) { starts.push(u.start); ends.push(u.end); }
  return { s: units.map((u) => u.n).join(""), starts, ends };
}

// 相談の文章のうち、事実の根拠になった言葉に印を付ける。印 i と事実の行 i は同じ遅れで現れる
function markEvidence(text, extracted) {
  const flat = squashed(text);
  const spans = [];
  for (const [i, f] of extracted.entries()) {
    const ev = f.evidence ? squash(f.evidence) : "";
    const k = ev ? flat.s.indexOf(ev) : -1;
    if (k < 0) continue;
    const at = flat.starts[k];
    const end = flat.ends[k + ev.length - 1];
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
  ["after_prereq", "先に手続き", "先に手続きをすると使える制度"],
  ["unknown", "確かめる", "確かめると使えるかもしれない制度"],
  ["no", "当てはまらない", "当てはまらない制度"],
];
// 済んでいる・申請済み（結果待ち）の制度は、いま手を打たなくてよく、当てはまらないのでもないので、帯に札を置かない
// （列の数にも入れない）。動いたことは変化の文で言い、結果の画面では別に出す（T-022）
const OFF_BOARD = [["done", "済んでいる制度"], ["pending", "申請済み・結果待ちの制度"]];
const colOf = (status) => (status === "ineligible" ? "no" : status);
const calm = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;
let tiles = new Map();  // 制度の id → 札。同じ札を動かすので作り直さない
let boardPrev = null;   // 前回の { 制度の id: 列（帯に置かない制度は状態） }

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
    now[r.id] = colOf(r.status);
    const list = box.querySelector(`[data-col="${now[r.id]}"] .tiles`);
    let t = tiles.get(r.id);
    if (!list) {  // 済んでいる・結果待ち：札を置かない（前に置いていたら外す）
      if (t) { t.remove(); tiles.delete(r.id); }
      continue;
    }
    if (!t) {
      t = el("li", { class: "tile", title: r.name, "aria-label": r.name });
      tiles.set(r.id, t);
    }
    t.className = `tile ${r.status}`;
    list.append(t);
  }
  // 途中で町田市と分かると、型の制度が結果から外れる。外れた札は残さない（数にも入れない）
  let removed = 0;
  for (const [id, t] of tiles) if (!(id in now)) { t.remove(); tiles.delete(id); if (boardPrev) removed += 1; }
  const moved = boardPrev ? data.results.filter((r) => boardPrev[r.id] && boardPrev[r.id] !== now[r.id]) : [];
  // 3. 動いた札を元の位置に戻して見せ、transform を外して新しい位置まで滑らせる
  if (!calm()) {
    for (const r of moved) {
      const t = tiles.get(r.id);
      const a = first.get(r.id);
      if (!t || !a) continue;  // 帯から外した札と、帯に戻って作り直した札は滑らせない
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
  // 住所で外した制度（その市・都の外と分かったときも、町田市・横浜市と分かって型を外したときも）を数えないと、結果の画面で住所を選んだときに「判定は変わりませんでした」と出るため
  $("board-change").textContent = changeText(moved, now, removed);
  boardPrev = now;
  // 質問へ移るとき、上に留まる見立ての帯の下に問いが来るように
  $("board").classList.toggle("live", Boolean(data.question));
  document.documentElement.style.setProperty("--board-h", `${$("board").offsetHeight + 8}px`);
}

function changeText(moved, now, removed = 0) {
  const parts = [];
  for (const [key, say] of [...BOARD.map(([k, , s]) => [k, s]), ...OFF_BOARD]) {
    const names = moved.filter((r) => now[r.id] === key).map((r) => r.name);
    if (!names.length) continue;
    // 上に留まる帯を低く保つため、名前を添えるのは「当てはまる見込み」に入った制度だけ（2 件まで）
    const named = key === "eligible"
      ? `（${names.slice(0, 2).join("、")}${names.length > 2 ? ` ほか ${names.length - 2} 件` : ""}）` : "";
    parts.push(`${say} +${names.length}${named}`);
  }
  if (removed > 0) parts.push(`住所で外した制度 −${removed}`);
  return parts.join("・");
}

// ---- 聞き返し ----
function render(data, first = false) {
  renderBoard(data);
  $("center-link").href = data.center_search;  // ページ下の相談先も、お住まいの都道府県の検索にする
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
  $("ask-text").textContent = askText(q);
  $("ask-hint").textContent = q.hint || "";  // 書類で確かめる問いは、どこを見るかを選択肢の前に出す
  fillAnswers($("ask-options"), q, (v) => answer(q, v), (code) => answerPlace(code));
  // 結果に出す制度だけを挙げるので、空になりうる（「確かめていないこと」の欄と同じく、そのときは出さない）
  $("ask-why").textContent = q.affects.length ? `この答えで判定が決まる制度：${affectNames(q.affects)}` : "";
  $("ask-check").textContent = q.check;
  // 選択肢ではなく問いの文に移る（1 つ目の選択肢に色が付くと、先に選んであるように見えるため）
  $("ask-text").focus({ preventScroll: true });
  if (first) revealThenAsk();
  else $("ask").scrollIntoView({ behavior: "smooth", block: "start" });
}

// お住まいは、町田市かどうかではなく市区町村で聞く（市区町村の一覧が取れなかったときは、はい・いいえで聞く）
const asksPlace = (q) => Boolean(q.place && prefs.length);
const askText = (q) => (asksPlace(q) ? q.place.text : q.text);
const affectNames = (list) => list.slice(0, 3).join("、") + (list.length > 3 ? ` ほか ${list.length - 3} 件` : "");

// 問いの答え方を box に並べる。結果の画面の「まだ確かめていないこと」でも同じ形にする
function fillAnswers(box, q, pick, pickPlace) {
  box.replaceChildren();
  if (asksPlace(q)) {
    // 選んだ所は、入口で選んだときと同じに扱う
    box.className = "options";
    const pref = el("select", { "aria-label": "都道府県" }, el("option", { value: "" }, "都道府県"),
      ...prefs.map((p) => el("option", { value: p.code }, p.name)));
    const city = el("select", { "aria-label": "市区町村", disabled: "" }, el("option", { value: "" }, "市区町村"));
    // 選んだだけでは送らない（閉じた欄で ↓ を押しても change が起きるブラウザがあり、別の市区町村が送られうるため）
    const go = el("button", { class: "option", disabled: "", onclick: () => pickPlace(city.value) }, "この市区町村で進める");
    pref.addEventListener("change", () => { state.presses += 1; fillCities(pref.value, city); go.disabled = true; });
    city.addEventListener("change", () => { state.presses += 1; go.disabled = !city.value; });
    box.append(el("div", { class: "places" }, pref, city), go,
      el("button", { class: "option dk", onclick: () => pick(DONT_KNOW) }, "わからない"));
    return;
  }
  // 選択肢が多い問い（認定の区分）は、短い選択肢を 2 列に並べて縦の長さを抑える
  const two = q.options.length > 6;
  box.className = two ? "options two" : "options";
  for (const o of q.options) {
    const wide = two && o.label.length > 6;
    box.append(el("button", { class: wide ? "option wide" : "option", onclick: () => pick(o.value) }, o.label));
  }
  // 書類で確かめる問いは、「わからない」の代わりに「読んだ値のまま進める」を出す
  box.append(q.keep
    ? el("button", { class: "option dk", onclick: () => pick(q.keep.value) }, q.keep.label)
    : el("button", { class: "option dk", onclick: () => pick(DONT_KNOW) }, "わからない"));
}

// later：結果の画面で、確かめていない読み取りに答えたとき。聞き返しの数に入れず、新しい問いも出さない
function answer(q, value, later = false) {
  const facts = { ...state.facts };
  const suggestions = { ...state.suggestions };
  if (!(q.confirm && value === NOT_THIS)) facts[q.fact] = value;
  delete suggestions[q.fact];
  return step({ facts, suggestions, asked: state.asked + (later ? 0 : 1), presses: state.presses + 1 }, later);
}

// お住まいの問いで市区町村を選んだとき。町田市かどうかはサーバが市区町村から決める
function answerPlace(code, later = false) {
  const suggestions = { ...state.suggestions };
  delete suggestions.lives_in_machida;
  return step({ suggestions, municipality: code, asked: state.asked + (later ? 0 : 1), presses: state.presses + 1 }, later);
}

// 送っている間は選択肢を止める（2 度押しで問いが 1 つ飛ばないように）。答えは応答が届いてから state に入れる
// （通信に失敗して押し直しても、数がずれない）
let busy = false;
async function step(change = {}, stop = false) {
  if (busy) return;
  const mine = state;  // 待つ間に別の相談を始めたら、古い応答は描かない
  const next = { ...state, ...change };
  setBusy(true);
  try {
    const data = await api("/api/step", {
      facts: next.facts, suggestions: next.suggestions, subject: next.subject,
      asked: stop ? 99 : next.asked, municipality: next.municipality,
    });
    if (mine !== state) return;
    Object.assign(state, change);
    render(data);
  } catch (e) {
    if (mine === state) (state.resultShown ? $("later-change") : $("ask-why")).textContent = e.message;
  } finally {
    setBusy(false);
  }
}

function setBusy(on) {
  busy = on;
  // 元から止まっている欄（都道府県を選ぶ前の市区町村など）は、送り終えても止めたままにする
  for (const node of document.querySelectorAll("#ask-options :is(button, select), #later-list :is(button, select), #skip")) {
    if (on && !node.disabled) { node.disabled = true; node.dataset.busy = "1"; }
    else if (!on && node.dataset.busy) { node.disabled = false; delete node.dataset.busy; }
  }
}

// ---- 結果 ----
const SUMMARIZED = ["eligible", "pending", "after_prereq"];  // まとめ（/api/summary）が扱う状態。app.py の SUMMARIZED と同じ
const GROUPS = [
  ["eligible", "当てはまる見込み", "申請できそうな制度です。最終的な判断は申請先で行われます。"],
  ["pending", "申請済み・結果待ち", "申請が済んでいる手続きです。結果が届くと、下の「先に手続きをすると使える」制度が使えるようになることがあります。"],
  ["after_prereq", "先に手続きをすると使える", "前の手続きを済ませると、対象になりそうな制度です。"],
  ["unknown", "確かめると使えるかもしれない", "分からない点があり、まだ決められない制度です。確かめ方を添えています。"],
];

// 読み取れなかった事実（unread）は、値打ちの高い順にこの数だけ先に出し、残りはボタンに畳む（T-014）。
// 0 にして全部を畳んでいる：結果の画面の先頭が問いで埋まり、「当てはまる見込み」が下へ押し出されるため（D-0011 で確認中。3 なら先に 3 問出す）
const UNREAD_FIRST = 0;

// 聞き返しのあとも確かめていない読み取り（unconfirmed）と、読み取れなかった事実（unread）。
// 答えると規則だけで判定し直す（Claude は呼ばない）
function renderLater(unconfirmed, unread = []) {
  const item = (q) => {
    const box = el("div");
    fillAnswers(box, q, (v) => answer(q, v, true), (code) => answerPlace(code, true));
    return el("div", { class: "later-item" },
      el("h3", {}, askText(q)),
      q.hint ? el("p", { class: "hint" }, q.hint) : null,
      box,
      q.affects.length ? el("p", { class: "why" }, `この答えで判定が決まる制度：${affectNames(q.affects)}`) : null);
  };
  // 一度「ほか N 件を見る」を押したら、答えて出し直しても開いたままにする
  const first = state.laterOpen ? unread.length : UNREAD_FIRST;
  const nodes = [...unconfirmed.map(item), ...unread.slice(0, first).map(item)];
  if (unread.length > first) {
    const rest = el("div", { hidden: "" }, unread.slice(first).map(item));
    const more = el("button", { class: "link", onclick: () => { rest.hidden = false; more.remove(); state.laterOpen = true; } },
      first ? `ほか ${unread.length - first} 件を見る` : `読み取れなかったことにも答える（${unread.length} 件）`);
    nodes.push(more, rest);
  }
  $("later-list").replaceChildren(...nodes);
  show("later", unconfirmed.length + unread.length > 0);
}

function renderResult(data) {
  const again = state.resultShown;  // 結果の画面で答えて、出し直すとき
  state.resultShown = true;
  renderLater(data.unconfirmed, data.unread);
  $("later-change").textContent = again ? ($("board-change").textContent || "判定は変わりませんでした") : "";
  const seconds = Math.round((Date.now() - state.startedAt) / 1000);
  $("stats").textContent = `聞き返し ${state.asked} 問・押した数 ${state.presses} 回・${seconds} 秒`;
  const groups = $("groups");
  groups.replaceChildren();
  for (const [status, title, hint] of GROUPS) {
    const items = data.results.filter((r) => r.status === status);
    if (!items.length) continue;
    groups.append(el("h2", { class: "group-title" }, title, el("span", { class: "n" }, `${items.length} 件`)));
    groups.append(el("p", { class: "note" }, hint));
    // 確かめると使えるかもしれない制度は多くなりやすいので、あと少し確かめれば決まる順に 5 件だけ先に出す（順は step_view）
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
  // 住所で外れる市・都の制度は出さないので、そこに住んでいれば判定することだけを 1 行で伝える
  // 例：「町田市にお住まいなら、市の制度 10 件も判定します。」（市・都は名前の最後の字）
  const hidden = data.hidden_places || [];
  $("other-city").textContent = hidden.length ? hidden.map((h) =>
    `${h.name}にお住まいなら${hidden.length > 1 ? "" : "、"}${h.name.slice(-1)}の制度 ${h.n} 件`).join("、") + "も判定します。" : "";
  show("other-city", hidden.length > 0);
  show("result");
  if (!again) $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  // まとめが扱う制度が変わったときだけ書き直す（確かめても判定が同じなら、Claude を呼ばない）
  const key = data.results.filter((r) => SUMMARIZED.includes(r.status)).map((r) => r.id).join(",");
  if (state.sample) {
    // まとめは答えによって変わり Claude が書くので、見本では出さない（見出しだけの空のカードも出さない）
    show("summary", false);
    $("stats").textContent += "。見本では、Claude が書くまとめを省いています";
  } else if (key !== state.summaryKey) summarize();
  state.summaryKey = key;
}

function reasonText(r) {
  const f = r.reasons.find((x) => x.state === "F");
  // 「町田市以外にお住まい に当たらない」は二重の否定に読めるので、満たしていない条件として名指す
  return f ? `条件「${f.label}」を満たしていない${f.value ? `（${f.value}）` : ""}` : "満たしていない条件がある";
}

function programCard(r) {
  const head = el("div", { class: "head" },
    el("span", { class: "tag" }, r.level), el("span", { class: "name" }, r.name),
    el("span", { class: `pill ${r.status}` }, r.status_label));
  const sumParts = [head, el("p", { class: "sum" }, r.summary)];
  if (r.waiting_on.length) {
    sumParts.push(el("p", { class: "chain" }, "先に：",
      ...r.waiting_on.map((w, i) => [i ? "、" : "", el("a", { href: `#p-${w.id}`, onclick: (ev) => openCard(ev, w.id) }, w.name),
        w.pending ? "（申請済み・結果待ち）" : ""])));
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
  if (r.apply.center_search) body.append(el("p", {}, centerLink(r.apply.center_search)));
  if (r.procedures.length) body.append(el("h3", {}, "根拠の法令"), procedureView(r));
  if (r.bring.length) body.append(el("h3", {}, "持ち物・書類"), el("ul", { class: "plain" }, r.bring.map((t) => el("li", {}, t))));
  if (r.amount.length) body.append(el("h3", {}, "受けられる内容"), el("ul", { class: "plain" }, r.amount.map((t) => el("li", {}, t))));
  if (r.timing.length) body.append(el("h3", {}, "時期・期限"), el("ul", { class: "plain" }, r.timing.map((t) => el("li", {}, t))));
  body.append(el("h3", {}, "出典"), ...r.sources.map(sourceView));
  return el("details", { class: "card prog", id: `p-${r.id}` }, el("summary", {}, sumParts), body);
}

// 行政手続データの手続。税の控除のように、この制度だけの手続がないときは「使う手続」と書き、控除の根拠に見せない
function procedureView(r) {
  const [d] = r.procedures;
  const rest = r.procedures.length > 1 ? ` ほか ${r.procedures.length - 1} 件` : "";
  const what = d.match === "general" ? `使う手続：${d.name}（${d.law} ${d.article}）` : `${d.law} ${d.article}（${d.name}）`;
  return el("p", {}, what + rest, el("br"), el("span", { class: "note" }, `出典：${r.procedures_source}`));
}

// 申請先に挙げた地域包括支援センターを探すページ（厚生労働省。お住まいの都道府県が分かれば、その県の検索）
function centerLink(url) {
  return el("a", { href: url, target: "_blank", rel: "noopener" }, "地域包括支援センターを探す（厚生労働省 介護サービス情報公表システム）");
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
    const data = await api("/api/summary", { facts: state.facts, subject: state.subject, municipality: state.municipality });
    if (!data.lines.length) return;
    $("summary-lines").replaceChildren(...data.lines.map((l) => el("li", {}, l.text)));
    $("summary-note").textContent = data.engine === "fake"
      ? "試験モード：このまとめは決まった文の組み立てです。判定そのものは規則のプログラムが決めています。"
      : "このまとめは Claude が判定の結果から書きました。判定そのものは規則のプログラムが決めています。";
    show("summary");
  } catch (e) { /* まとめが出なくても結果は読める */ }
}

// ---- 始める ----
chipGroup("pick-who", WHO, false);
chipGroup("pick-age", AGES, false);
chipGroup("pick-worry", WORRIES.map(([label]) => label), true);
$("pref").addEventListener("change", () => { pick.pref = $("pref").value; pick.city = ""; fillCities(); redraft(); });
$("city").addEventListener("change", () => { pick.city = $("city").value; redraft(); });
$("redraft").addEventListener("click", () => redraft(true));
$("text").addEventListener("input", () => {
  const draft = buildDraft();
  const edited = $("text").value !== lastDraft && $("text").value.trim() !== "";
  show("redraft", edited && draft !== "" && draft !== $("text").value);
});
fetch("/static/municipalities.json").then((r) => r.json()).then((data) => {
  prefs = data.prefectures;
  $("pref").append(...prefs.map((p) => el("option", { value: p.code }, p.name)));
}).catch(() => { /* 取れなければ、お住まいは文に書いてもらう */ });
// 見本は取れたときだけ出す
fetch("/api/samples").then((r) => (r.ok ? r.json() : [])).then((list) => {
  if (!list.length) return;
  $("sample-list").replaceChildren(...list.map((s) => el("button", { type: "button", class: "chip", onclick: () => openSample(s) }, s.label)));
  show("samples");
}).catch(() => { /* 見本の行は出さない */ });
$("send").addEventListener("click", send);
$("skip").addEventListener("click", () => step({}, true));
$("again").addEventListener("click", () => {
  state = fresh();
  $("text").value = "";
  resetPick();
  show("extracted", false); show("ask", false); show("result", false);
  clearBoard();
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("text").focus();
});
$("gate-ok").addEventListener("click", () => {
  const word = aikotobaWord($("aikotoba").value);
  if (word === null) {
    $("gate-msg").textContent = "合言葉は半角の英数字で入れてください。日本語の文字は使えません。";
    $("aikotoba").focus();
    $("aikotoba").select();
    return;
  }
  if (!word) { $("aikotoba").focus(); return; }
  aikotoba = word;
  try { sessionStorage.setItem("aikotoba", aikotoba); } catch (e) { /* 保存できなくても続ける */ }
  show("gate", false);
  if (resendAfterGate) { resendAfterGate = false; send(); }
});
$("aikotoba").addEventListener("keydown", (ev) => { if (ev.key === "Enter") $("gate-ok").click(); });
// 合言葉が要る置き場では、書き始める前に聞く（書いて送ってから止められないように）
fetch("/api/health").then((r) => r.json()).then((h) => {
  if (h.passphrase && !aikotoba) show("gate");
}).catch(() => { /* 分からなければ、送ったときに聞く */ });
