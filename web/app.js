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
let resendAfterGate = false;
let needGate = false;   // この置き場は Claude を呼ぶ口に合言葉を求めるか（/api/health）  // 合言葉で止まった相談を、合言葉のあとで送り直す
class GateError extends Error {}

function fresh() {
  return { subject: "ご本人", facts: {}, suggestions: {}, asked: 0, presses: 0, startedAt: 0, engine: "",
    municipality: "", sample: false, event: false, restored: false, resultShown: false, summaryKey: "", laterOpen: false,
    oneOnly: false, held: null, trail: [], thread: [] };  // oneOnly・held：1 問だけ答える道（T-072b）。held は答えた後に止めておく次の問いの応答
    // trail：押した数ごとの見立ての数（T-082。手間と分かることの図）
    // thread：答えた問いと選んだ答えの並び（T-126b。吹き出しのやりとりで見せる。サーバには送らない）
}

function freshPick() {
  return { who: "", age: "", worries: new Set(), pref: "", city: "" };
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "style") node.style.cssText = v;  // style 属性は CSP（'unsafe-inline' なし）で止まるので CSSOM で付ける
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
// 読み上げに知らせる（#status はいつもある領域。空にしておくと、次に同じ文を入れても読まれる）
function say(text) { $("status").textContent = text; }
// 押したボタンが消えると、フォーカスが body に落ちて読み上げ・キーボードの人が先頭から辿り直すことになるので、次に読む所へ移す（T-030）
const seen = (node) => Boolean(node) && node.getClientRects().length > 0;
function focusOn(node) {
  if (!node) return;
  if (!node.matches("a[href], button, summary, input, select, textarea") && !node.hasAttribute("tabindex")) node.setAttribute("tabindex", "-1");
  node.focus({ preventScroll: true });
}
// 送る間に止めたボタンからは、フォーカスが body に落ちる。送るのに失敗してボタンを戻したら、押したボタンへ戻す
// （合言葉の欄など、ほかへ移したときはそのまま。T-062）
function backTo(node) {
  if (node !== document.body && seen(node) && document.activeElement === document.body) focusOn(node);
}

function askGate(wrong) {
  $("gate-msg").textContent = wrong
    ? "合言葉が違います。入れ直してください。"
    : "ご自分の相談を送るには合言葉が要ります。上の見本は合言葉なしで見られます。";
  show("gate-open", false);
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
  // 選んだ出来事の文も後ろにつなぐ（T-134。出来事を押した後にどなた・お住まいを選んでも、出来事の文を消さない）
  const events = eventList.filter((e) => eventPicked.has(e.id)).map((e) => e.text).join("");
  if (!pick.who && !pick.worries.size && !events) return "";
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
  return parts.join("") + events;
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
  show("city-link-box", false);
  markPref();
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

// 入口の出来事（T-071）を絵で選ぶ（T-124）。押すたびに選ぶ・外すを切り替え、家の絵に同じ絵を足し、
// 選んだ出来事の文をつないで下書きにする。「選んだ出来事で始める」で、その文から言える事実だけで最初の問いへ進む（Claude を呼ばない）
let eventList = [];
const eventPicked = new Set();
const SCENE_EMPTY = "出来事を選ぶと、家の絵に足されていきます。";

// 札の名前は文節の切れ目（が・に・く の後）でだけ折り返す。CSS の word-break: keep-all と組にして、
// 「一人暮 / らしになった」のような語の途中の折り返しを防ぐ（T-124b。決まった 6 枚の名前に合わせた簡単な決まりで、
// 名前を足したら札の写真で確かめる。収まらない長い句は overflow-wrap: anywhere で札の中で折れる）
function phraseLabel(label) {
  const span = el("span", {});
  label.split(/(?<=[がにく])(?=.)/).forEach((part, i) => {
    if (i) span.append(document.createElement("wbr"));
    span.append(part);
  });
  return span;
}

function eventCard(e) {
  const card = el("button", { type: "button", class: "ev-card", "aria-pressed": "false", "data-ev": e.id });
  if (document.getElementById(`art-${e.id}`)) {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", `#art-${e.id}`);
    svg.append(use);
    card.append(svg);
  }
  card.append(phraseLabel(e.label));
  card.addEventListener("click", () => toggleEvent(e.id));
  return card;
}

function toggleEvent(id) {
  if (eventPicked.has(id)) eventPicked.delete(id); else eventPicked.add(id);
  const picked = eventList.filter((e) => eventPicked.has(e.id));  // 札の並びの順で文をつなぐ
  document.querySelectorAll("#event-list .ev-card").forEach((c) => c.setAttribute("aria-pressed", String(eventPicked.has(c.dataset.ev))));
  document.querySelectorAll("#scene .scene-item").forEach((g) => g.classList.toggle("on", eventPicked.has(g.dataset.ev)));
  $("scene-caption").textContent = picked.length ? `選んだ出来事：${picked.map((e) => e.label).join("・")}` : SCENE_EMPTY;
  $("event-start").disabled = !picked.length;
  // 出来事を 1 つ押したら、どなた・年齢・お住まいを選ぶ欄を開く（T-134。最初は書く欄と出来事の札だけ）。開くのは最初の 1 回だけ
  if (picked.length && $("pick-more").hidden) { show("pick-more"); $("pick-more").open = true; }
  redraft();  // 本人が書き換えるまでは、選び直すと下書きを作り直す（選ぶだけの下書きと同じ扱い）
}

async function openEvents() {
  const ids = eventList.filter((e) => eventPicked.has(e.id)).map((e) => e.id);
  if (!ids.length) return;
  const text = eventList.filter((e) => eventPicked.has(e.id)).map((e) => e.text).join("");
  await consultWith(text, () => api("/api/event", { ids, municipality: pick.city }));
}

async function consultWith(text, call) {
  state = fresh();
  forgetKeep();  // 前に開いた・残した結果のリンクを、アドレス欄から外す
  state.startedAt = Date.now();
  const pressed = document.activeElement;
  $("send").disabled = true;
  $("reading").replaceChildren(el("p", {}, "読み取っています…"));
  say("読み取っています…");
  show("reading"); show("extracted", false); show("ask", false); show("pause", false); show("result", false); show("effort-page", false);
  clearBoard();  // 前の相談の見立てから動かさない
  try {
    const data = await call();
    // 読み取ったことの欄へ送る前に消す。送った後に消すと、その高さぶん中身が上へずれ、見出しが画面の上に切れる（T-023）
    show("reading", false);
    say("");
    state.subject = data.subject;
    state.facts = data.facts;
    state.suggestions = data.suggestions;
    state.engine = data.engine;
    state.sample = Boolean(data.sample);
    state.sampleId = data.sample_id || "";
    state.sampleSummary = null;
    state.event = Boolean(data.event);
    state.municipality = data.place ? data.place.code : "";
    renderExtracted(data, text);
    render(data, true);
  } catch (e) {
    resendAfterGate = e instanceof GateError;
    $("reading").replaceChildren(el("p", { class: "error" }, e.message));
    say(e.message);
    show("reading");  // 受け取った後に落ちたときも、知らせが見えるように
    $("send").disabled = false;
    backTo(pressed);
    return;
  }
  $("send").disabled = false;
}

function renderExtracted(data, text) {
  // 出来事のボタンから来たときは、相談の文章を読み取ったのではないので見出しを替える（T-071）
  $("extracted").querySelector("h2").textContent = data.event ? "選んだ出来事から分かったこと" : "相談の文章から読み取ったこと";
  $("said").replaceChildren(...markEvidence(text, data.extracted));
  const list = $("extracted-list");
  list.replaceChildren();
  for (const [i, f] of data.extracted.entries()) {
    list.append(el("li", { style: `--i: ${i}` },
      `${f.name}：${f.label}`,
      f.confidence === "low" ? el("span", { class: "tentative" }, "（あとで確かめます）") : null,
      f.evidence ? el("div", { class: "ev" }, `「${f.evidence}」から`) : null));
  }
  if (!data.extracted.length) {
    list.append(el("li", {}, data.event ? "この出来事だけで決まることはありません。いくつか質問します。" : "読み取れた事実はありませんでした。いくつか質問します。"));
  }
  const made = data.made && data.made.model && data.made.date ? `（${data.made.model}、${data.made.date}）` : "";
  $("engine-note").textContent = data.event
    ? "選んだ出来事の文から、言えることだけをプログラムが置きました。Claude は使っていません。"
    : data.sample
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
// 4 つ目は列の名前の折り返してよい所。スマホの 4 列（名前の幅 65px・14px で 4 字）で「当てはま／る」と切れないように
const BOARD = [
  ["eligible", "当てはまる", "当てはまる見込み", ["当ては", "まる"]],
  ["after_prereq", "先に手続き", "先に手続きをすると使える制度", ["先に", "手続き"]],
  ["unknown", "確かめる", "確かめると使えるかもしれない制度", ["確かめる"]],
  ["no", "当てはまらない", "当てはまらない制度", ["当ては", "まらない"]],
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
  clearMap();
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
    box.append(...BOARD.map(([key, label, , parts]) => el("div", { class: `col ${key}`, "data-col": key },
      el("div", { class: "col-head" }, el("span", { class: "label" }, ...parts.map((t) => el("span", { class: "ph" }, t))),
        el("span", { class: "count" }, "0")),
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
  $("board-change").title = $("board-change").textContent;  // T-136：帯では 1 行で切るので、全文を title にも
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
  state.trail = tally(state.trail, state.presses, data.results);
  renderBoard(data);
  $("center-link").href = data.center_search;  // ページ下の相談先も、お住まいの都道府県の検索にする
  show("pause", false);
  // 1 問だけ答える道では、答えたら次の問いを出さずに止め、ここまでの見立て（上の帯）と「もう 1 問／ここでやめる」を出す
  if (data.question && state.oneOnly && state.asked > 0) { show("ask", false); show("result", false); renderPause(data); }
  else if (data.question) { show("ask"); show("result", false); renderQuestion(data.question, data.max_questions, first); }
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
  // お住まいは 3 問の枠の外で最初に聞く（11/6、全国のどこに住む人でも使うため。questions.next_question の place_first）
  $("ask-count").textContent = q.place ? "はじめに、お住まい（質問の数には入れません）" : `質問 ${state.asked + 1}（多くて ${max} つ）`;
  show("one-box", state.asked === 0 && !q.place);  // 「1 問だけ答える」は最初の問いでだけ選べる（お住まいの問いは数えないので、その次）
  syncOne();
  $("ask-text").textContent = askText(q);
  $("ask-hint").textContent = q.hint || "";  // 書類で確かめる問いは、どこを見るかを選択肢の前に出す
  fillAnswers($("ask-options"), q, (v) => answer(q, v), (code) => answerPlace(code, false, q));
  // 結果に出す制度だけを挙げるので、空になりうる（「確かめていないこと」の欄と同じく、そのときは出さない）
  $("ask-why").textContent = q.affects.length ? `この答えで判定が決まる制度：${affectNames(q.affects)}` : "";
  $("ask-check").textContent = q.check;
  // 選択肢ではなく問いの文に移る（1 つ目の選択肢に色が付くと、先に選んであるように見えるため）
  // #ask の aria-live はやめ、問いの文へのフォーカスの移動で伝える。もう問いの文にあると focus() では読み直されないので、いったん外す
  if (document.activeElement === $("ask-text")) $("ask-text").blur();
  $("ask-text").focus({ preventScroll: true });
  const lastSaid = renderThread();
  if (first) revealThenAsk();
  // 答えが積もったら、いま選んだ答えの吹き出しから見せる（その下に次の問い。上の古いやりとりは画面の外へ流れる）
  else (lastSaid || $("ask")).scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- 吹き出しのやりとり（T-126b）----
// 答えた問い（左・とどく）と選んだ答え（右・あなた）を、今の問いの上に積む。問いの中身・順・判定は変えない
function renderThread() {
  const list = $("thread");
  list.replaceChildren(...state.thread.map((t) => el("li", {},
    el("p", { class: "bubble bot past" }, el("span", { class: "sr-only" }, "質問："), t.q),
    el("p", { class: "bubble you" }, el("span", { class: "sr-only" }, "あなたの答え："), t.a))));
  show("thread", state.thread.length > 0);
  const said = list.querySelectorAll(".you");
  return said.length ? said[said.length - 1] : null;
}

// 押した選択肢の文字。値から引くので、選択肢の並びを変えても合う
function answerLabel(q, value) {
  if (value === DONT_KNOW) return "わからない";
  if (q.keep && value === q.keep.value) return q.keep.label;
  const o = (q.options || []).find((x) => x.value === value);
  return o ? o.label : String(value);
}

function placeLabel(code) {
  for (const p of prefs) {
    const c = (p.cities || []).find(([cc]) => cc === code);
    if (c) return `${p.name}${c[1]}`;
  }
  return code;
}

const withSaid = (q, a, later) => (later ? state.thread : [...state.thread, { q: askText(q), a }]);

// ---- 1 問だけ答える（T-072b）----
// 最初の問いで選ぶと、答えるたびに止まり、その時点の札を見てから「もう 1 問答える」か「ここでやめる」を選べる。
// 押しても問いと判定は変わらない（止まるかどうかだけ）。もう一度押すと、いつもどおり続けて聞く
function syncOne() {
  $("one").setAttribute("aria-pressed", String(state.oneOnly));
  $("one-note").textContent = state.oneOnly ? "答えたら、ここまでの見立てを出して止まります。続けるかはそこで選べます。" : "";
}

function toggleOne() {
  state.oneOnly = !state.oneOnly;
  syncOne();
}

function renderPause(data) {
  state.held = data;
  $("pause-title").textContent = `${state.asked} 問答えました。ここまでの見立てです`;
  // 数は結果の画面の見出し（GROUPS）と同じ名前・並びで出す
  const parts = GROUPS.map(([st, title]) => [title, data.results.filter((r) => r.status === st).length])
    .filter(([, n]) => n).map(([title, n]) => `${title} ${n} 件`);
  const next = data.question.affects.length ? `次の問いで判定が決まる制度：${affectNames(data.question.affects)}` : "";
  $("pause-counts").textContent = [parts.join("・"), next].filter(Boolean).join("。");
  $("pause-msg").textContent = "";
  const left = data.max_questions - state.asked;
  $("pause-more").textContent = `もう 1 問答える（あと ${left} 問まで）`;
  show("pause");
  $("pause").scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "start" });
  focusOn($("pause-title"));
}

// 止めておいた次の問いを出す（サーバには送り直さない。答えると、また止まる）
function pauseMore() {
  const data = state.held;
  if (!data || busy) return;
  state.held = null;
  show("pause", false);
  show("ask");
  renderQuestion(data.question, data.max_questions);
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
// お住まいの問い（q.place）も数に入れない（3 問の枠の外。「わからない」と答えたときも同じ）
function answer(q, value, later = false) {
  const facts = { ...state.facts };
  const suggestions = { ...state.suggestions };
  if (!(q.confirm && value === NOT_THIS)) facts[q.fact] = value;
  delete suggestions[q.fact];
  return step({ facts, suggestions, asked: state.asked + (later || q.place ? 0 : 1), presses: state.presses + 1,
    thread: withSaid(q, answerLabel(q, value), later) }, later);
}

// お住まいの問いで市区町村を選んだとき。町田市かどうかはサーバが市区町村から決める。聞き返しの数には入れない（3 問の枠の外）
function answerPlace(code, later = false, q = null) {
  const suggestions = { ...state.suggestions };
  delete suggestions.lives_in_machida;
  return step({ suggestions, municipality: code, asked: state.asked, presses: state.presses + 1,
    thread: q ? withSaid(q, placeLabel(code), later) : state.thread }, later);
}

// 送っている間は選択肢を止める（2 度押しで問いが 1 つ飛ばないように）。答えは応答が届いてから state に入れる
// （通信に失敗して押し直しても、数がずれない）
let busy = false;
async function step(change = {}, stop = false) {
  if (busy) return;
  const mine = state;  // 待つ間に別の相談を始めたら、古い応答は描かない
  const next = { ...state, ...change };
  const pressed = document.activeElement;
  let failed = false;
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
    failed = mine === state;
    if (mine === state) {
      (state.resultShown ? $("later-change") : seen($("pause")) ? $("pause-msg") : $("ask-why")).textContent = e.message;
      if (!state.resultShown) say(e.message);  // #later-change は読み上げの領域。問いの欄はそうでないので知らせる
    }
  } finally {
    setBusy(false);
    if (failed) backTo(pressed);  // 止めたボタンを戻してから（止まったままの所へはフォーカスが移らない）
  }
}

function setBusy(on) {
  busy = on;
  // 元から止まっている欄（都道府県を選ぶ前の市区町村など）は、送り終えても止めたままにする
  for (const node of document.querySelectorAll("#ask-options :is(button, select), #later-list :is(button, select), #skip, #pause button")) {
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
    const node = el("div", { class: "later-item" },
      el("h3", {}, askText(q)),
      q.hint ? el("p", { class: "hint" }, q.hint) : null,
      box,
      q.affects.length ? el("p", { class: "why" }, `この答えで判定が決まる制度：${affectNames(q.affects)}`) : null);
    // 答えた問いは出し直すと消えるので、何番目だったかを覚えておき、同じ位置に来た問いへフォーカスを移す
    const mark = () => { state.laterAt = laterSpots().indexOf(node.querySelector("h3")); };
    fillAnswers(box, q, (v) => { mark(); answer(q, v, true); }, (code) => { mark(); answerPlace(code, true); });
    return node;
  };
  // 一度「ほか N 件を見る」を押したら、答えて出し直しても開いたままにする
  const first = state.laterOpen ? unread.length : UNREAD_FIRST;
  const nodes = [...unconfirmed.map(item), ...unread.slice(0, first).map(item)];
  if (unread.length > first) {
    const rest = el("div", { hidden: "" }, unread.slice(first).map(item));
    const more = el("button", { class: "link", onclick: () => {
      rest.hidden = false; more.remove(); state.laterOpen = true; focusOn(rest.querySelector("h3"));
    } },
      first ? `ほか ${unread.length - first} 件を見る` : `読み取れなかったことにも答える（${unread.length} 件）`);
    nodes.push(more, rest);
  }
  $("later-list").replaceChildren(...nodes);
  show("later", unconfirmed.length + unread.length > 0);
}

// 「まだ確かめていないこと」の欄で、フォーカスを移せる所（見えている問いの見出しと、畳んだ問いを開くボタン）
const laterSpots = () => [...$("later-list").querySelectorAll(".later-item h3, #later-list > button")].filter(seen);
// 結果の画面の先頭の見出し（「まだ確かめていないこと」、無ければ「当てはまる見込み」など）
const resultHeading = () => [...$("result").querySelectorAll("h2")].find(seen);

// 有料・民間の支え（T-084）。判定はしないので状態の印は付けず、関係しそうな理由だけを添える
function optionCard(o) {
  const src = o.sources.map((s) => el("li", {}, el("a", { href: s.url, target: "_blank", rel: "noopener" }, s.title || s.publisher),
    `（${s.publisher}、${s.checked} 確認）`));
  return el("details", { class: "card option" },
    el("summary", {}, el("span", { class: "tag" }, o.tag), " ", o.name),
    el("p", {}, o.summary),
    o.why.length ? el("p", { class: "note" }, "関係しそうな理由：", o.why.join("・")) : "",
    el("p", {}, "申し込み先：", o.where),
    el("p", {}, "費用の目安：", o.cost || "申し込み先で確かめる"),
    el("ul", { class: "plain note" }, src));
}

function renderOptions(options) {
  $("options-n").textContent = `${options.length} 件`;
  $("options-list").replaceChildren(...options.map(optionCard));
  show("options-box", options.length > 0);
}

// いま先にすること（T-121）：結果の画面の先頭に、判定の結果だけから 1〜TODO_MAX 件を選ぶ（Claude は使わない）。
// 順：(1) 当てはまる見込みのうち、「先に手続き」の制度が待っている手続き（待っている数の多い順）
// (2) ほかの当てはまる見込み（サーバの並び順） (3) 読み取りを確かめる問いが残っていれば、その欄へ
// (4) ここまでで 0 件なら、確かめると使えるかもしれない制度（あと少し確かめれば決まる順）
// T-133：選ぶのは 3 件まで、結果の頁の先頭に出すのは要点の 1 行だけ（当てはまる見込みの札がすぐ下に並ぶので、2 回並べない）
const TODO_SHOWN = 1;
const TODO_MAX = 3;
function todoItems(data) {
  const byId = new Map(data.results.map((r) => [r.id, r]));
  const waits = new Map();
  for (const r of data.results) {
    if (r.status !== "after_prereq") continue;
    for (const w of r.waiting_on) if (!w.pending) waits.set(w.id, (waits.get(w.id) || 0) + 1);
  }
  const met = (r) => r.reasons.filter((x) => x.state === "T").map((x) => x.label).join("・");
  const items = [...waits].filter(([id]) => byId.get(id)?.status === "eligible").sort((a, b) => b[1] - a[1])
    .map(([id, n]) => ({ r: byId.get(id), why: `済ませると、先に手続きが要る制度 ${n} 件が使えるようになります` }));
  for (const r of data.results) {
    if (r.status === "eligible" && !waits.has(r.id)) items.push({ r, why: met(r) ? `当てはまる理由：${met(r)}` : "当てはまる見込みです" });
  }
  if (items.length < TODO_MAX && data.unconfirmed.length) {
    items.push({ later: true, name: "読み取りを確かめる問いに答える", why: `相談の文から読んだことのうち、確かめたいことが ${data.unconfirmed.length} 件あります。答えると判定し直します` });
  }
  if (!items.length) {
    for (const r of data.results.filter((x) => x.status === "unknown")) {
      items.push({ r, why: r.missing.length ? `分からない点：${r.missing.map((m) => m.name).join("、")}` : "確かめると使えるかもしれません" });
    }
  }
  return items.slice(0, TODO_MAX);
}

function renderTodo(data) {
  const items = todoItems(data).slice(0, TODO_SHOWN);
  $("todo-list").replaceChildren(...items.map((t) => el("li", {},
    t.later ? el("a", { href: "#later", onclick: (ev) => { ev.preventDefault(); $("later").scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "start" }); focusOn(laterSpots()[0] || $("later").querySelector("h2")); } }, t.name)
      : el("a", { href: `#p-${t.r.id}`, onclick: (ev) => openCard(ev, t.r.id) }, t.r.name),
    el("span", { class: "todo-why" }, t.why))));
  show("todo", items.length > 0);
}

// ---- これからの流れ（T-120、先回りの提案の最小版）----
// 相談で分かったことと規則だけから、これから来る区切りと、そのときに使える制度を時の順に並べる。判定はしない（状態は判定の結果のまま）。
// 区切りは規則の「先に要る手続き」（waiting_on）と費用の形（cost.self_pay が tax）だけで決め、目安の文は規則の timing の 1 行目をそのまま出す。
// 本人の暮らしの情報は集めず、通知も保存もしない（README の「今後」）。区切りが「いま」だけなら出さない。文言はここだけで差し替える
const AHEAD_NOW = "いま";
const AHEAD_AFTER = "{name}の結果が出たら";
const AHEAD_TAX = "次の確定申告の時期（2〜3月）";
function aheadSteps(data) {
  const shown = data.results.filter((r) => r.status === "eligible" || r.status === "after_prereq");
  const byId = new Map(data.results.map((r) => [r.id, r]));
  const tax = (r) => r.cost?.self_pay === "tax";
  const first = (r) => (r && r.timing && r.timing[0]) || "";
  const steps = [];
  const after = new Map();  // 先に要る手続きの id → その結果を待つ制度（2 つ待つ制度は 1 つ目の下にだけ置く）
  for (const r of shown) {
    if (r.status !== "after_prereq" || tax(r) || !r.waiting_on.length) continue;
    const w = r.waiting_on[0];
    if (!after.has(w.id)) after.set(w.id, { w, items: [] });
    after.get(w.id).items.push({ r, note: first(r) });
  }
  // いまは、ほかの制度が結果を待っている手続きを先に置く（並びはそのほかサーバの順のまま）
  const now = shown.filter((r) => r.status === "eligible" && !tax(r)).sort((a, b) => after.has(b.id) - after.has(a.id));
  if (now.length) steps.push({ when: AHEAD_NOW, note: "", items: now.map((r) => ({ r, note: "" })) });
  for (const { w, items } of after.values()) {
    steps.push({ when: AHEAD_AFTER.replace("{name}", w.name), note: first(byId.get(w.id)), items });
  }
  const taxes = shown.filter(tax).map((r) => ({ r, note: r.status === "after_prereq" && r.waiting_on.length
    ? `先に${r.waiting_on.map((w) => w.name).join("・")}が要ります` : first(r) }));
  if (taxes.length) steps.push({ when: AHEAD_TAX, note: "", items: taxes });
  return steps.some((s) => s.when !== AHEAD_NOW) ? steps : [];
}

function renderAhead(data) {
  const steps = aheadSteps(data);
  $("ahead-list").replaceChildren(...steps.map((s) => el("li", { class: s.when === AHEAD_NOW ? "now" : "" },
    el("h3", {}, s.when),
    s.note ? el("p", { class: "ahead-note" }, `目安：${s.note}`) : null,
    el("ul", { class: "plain" }, s.items.map((t) => el("li", {},
      el("a", { href: `#p-${t.r.id}`, onclick: (ev) => openCard(ev, t.r.id) }, t.r.name),
      t.note ? el("span", { class: "ahead-why" }, t.note) : null))))));
  show("ahead", steps.length > 0);
}

function renderResult(data) {
  const again = state.resultShown;  // 結果の画面で答えて、出し直すとき
  state.resultShown = true;
  renderLater(data.unconfirmed, data.unread);
  renderTodo(data);
  renderMap(data, again);
  renderAhead(data);
  renderEffort();
  show("effort-page", false);
  $("later-change").textContent = again ? ($("board-change").textContent || "判定は変わりませんでした") : "";
  const seconds = Math.round((Date.now() - state.startedAt) / 1000);
  $("stats").textContent = state.restored ? "残したリンクから開いた結果です"
    : `聞き返し ${state.asked} 問・押した数 ${state.presses} 回・${seconds} 秒`;
  // T-133：当てはまる見込みは先頭（地図の上）、ほかは「まだ確かめていないこと」の下。確かめるの札の一覧は畳む（地図の束「＋N」を押すと開く）。
  // 件数を出すのはこの見出しだけ
  const top = $("groups-top");
  top.replaceChildren();
  const groups = $("groups");
  groups.replaceChildren();
  for (const [status, title, hint] of GROUPS) {
    const items = data.results.filter((r) => r.status === status);
    if (!items.length) continue;
    const titleNode = el("h2", { class: "group-title" }, title, el("span", { class: "n" }, `${items.length} 件`));
    const box = status === "unknown" ? el("details", { class: "card soft fold group-unknown", id: "unknown-fold" }, el("summary", {}, titleNode))
      : el("div", { class: `group group-${status}` }, titleNode);
    (status === "eligible" ? top : groups).append(box);
    box.append(el("p", { class: "note" }, hint));
    // 確かめると使えるかもしれない制度は多くなりやすいので、あと少し確かめれば決まる順に 5 件だけ先に出す（順は step_view）
    const first = status === "unknown" ? items.slice(0, 5) : items;
    for (const r of first) box.append(programCard(r));
    if (first.length < items.length) {
      const rest = el("div", { hidden: "" }, items.slice(first.length).map(programCard));
      const more = el("button", { class: "link", onclick: () => { rest.hidden = false; more.remove(); focusOn(rest.querySelector("summary")); } },
        `ほか ${items.length - first.length} 件を見る`);
      box.append(more, rest);
    }
  }
  const no = data.results.filter((r) => r.status === "ineligible" || r.status === "done");
  $("ineligible-title").textContent = `当てはまらない・済んでいる制度（${no.length} 件）`;
  $("ineligible-list").replaceChildren(...no.map((r) => el("li", {},
    `${r.name}：`, r.status === "done" ? "済んでいる" : reasonText(r))));
  show("ineligible-box", no.length > 0);
  renderRegion(data.region);
  renderOptions(data.options || []);
  show("result");
  show("board", false);  // T-133：結果の頁では見立ての帯を出さない（件数は札の一覧の見出しだけ。帯の動きは問いの間だけ）
  if (!again) {
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
    focusOn(resultHeading());
  } else if (state.laterAt !== undefined) {
    // 答えた問いの位置に来た問い（最後の問いなら 1 つ前、欄が空になったら結果の先頭の見出し）へ
    const spots = laterSpots();
    focusOn(spots[Math.min(state.laterAt, spots.length - 1)] || resultHeading());
    state.laterAt = undefined;
  }
  // まとめが扱う制度が変わったときだけ書き直す（確かめても判定が同じなら、Claude を呼ばない）
  // 見本では状態も鍵に入れる（保存したまとめは、制度と状態の組が作ったときと同じときだけ出すため）
  const key = data.results.filter((r) => SUMMARIZED.includes(r.status))
    .map((r) => (state.sample ? `${r.id}:${r.status}` : r.id)).join(",");
  if (state.sample) {
    // まとめは答えによって変わり Claude が書く。見本では、台本の答えで結果まで来たときだけ、前もって Claude が書いて置いた文を出す（T-045）
    if (key !== state.summaryKey || !state.sampleSummary) sampleSummary(key); else showSampleSummary();
  } else if ((state.event || state.restored) && needGate && !aikotoba) {
    // 出来事から始めた道と、残したリンクから開いた結果は、合言葉なしで結果まで来る。
    // まとめ（Claude を呼ぶ口）を送ると、結果の画面で合言葉の欄へ飛ばされるので送らない
    show("summary", false);
    $("stats").textContent += "。合言葉がないので、Claude が書くまとめを省いています";
  } else if (key !== state.summaryKey) summarize();
  state.summaryKey = key;
  showKeep(data);
}

// ---- あなたの地域で確かめられる支え（T-122）----
// 住んでいない地域の制度は数も名前も出さず、選んだ地域で確かめられる支えの数だけを肯定の形で出す。
// 前は住んでいない市の制度の数を「（市の名前）なら、市の制度 10 件も判定します」と出し、ほかの地域の人が「自分は対象外」と読んだ（11/5 の柴田の感想）。
// 数は step_view の region（/city/ の頁の帯と同じ数え方、規則だけから）。文言はここだけで差し替える
const REGION_HEAD = "あなたの地域で確かめられる支え";
const REGION_DETAILED = "この地域は、{what}まで一つずつ書けています。";
const REGION_LINK = "地域の支えの一覧を見る";

function renderRegion(rg) {
  const line = $("region-line");
  if (!rg) { line.replaceChildren(); show("region-line", false); return; }
  const parts = rg.parts.map((x) => `${x.label} ${x.n} ${x.unit}`).join("・");
  // ["都の制度", "市の制度", "市の事業"] → 「都・市の制度・市の事業」（同じ後ろの語はまとめる）
  const kinds = new Map();
  for (const d of rg.detailed) {
    const i = d.indexOf("の");
    kinds.set(d.slice(i), [...(kinds.get(d.slice(i)) || []), d.slice(0, i)]);
  }
  const what = [...kinds].map(([tail, heads]) => heads.join("・") + tail).join("・");
  line.replaceChildren(`${REGION_HEAD} `, el("b", {}, `${rg.total}`), ` 件（${parts}）。`,
    what ? REGION_DETAILED.replace("{what}", what) : "",
    el("a", { href: `/city/${rg.code}` }, REGION_LINK));
  show("region-line", true);
}

// ---- 手間と分かること（T-082）----
// 押した数ごとに、その人の道で返った results（/api/step と、最初の読み取り）を見立ての帯と同じ 4 つの列で数えてためる。
// 済んでいる・結果待ちは帯と同じく数えない。同じ押した数の応答（聞き返しをやめた・ここでやめる）は置き換える。
// 層：型（app.py の LEVELS["local"]）はプラスアルファの層、ほかは公式ページの条件から書いた規則で判定した確かな層。
// 層の数は、当てはまらないを除いた 3 つの列で数える（当てはまらない型を「候補」と数えないため）
const TYPE_LEVEL = "市区町村（型）";
const LIVE_COLS = ["eligible", "after_prereq", "unknown"];
function tally(trail, presses, results) {
  const n = Object.fromEntries(BOARD.map(([key]) => [key, 0]));
  let sure = 0, extra = 0;
  for (const r of results || []) {
    const col = colOf(r.status);
    if (!(col in n)) continue;
    n[col] += 1;
    if (LIVE_COLS.includes(col)) { if (r.level === TYPE_LEVEL) extra += 1; else sure += 1; }
  }
  const entry = { presses, n, sure, extra };
  const last = trail[trail.length - 1];
  return last && last.presses === presses ? [...trail.slice(0, -1), entry] : [...trail, entry];
}

// 棒 1 本が押した数 1 つ。棒の長さは制度の数、区切りは列。数が細くて入らない区切りは字を出さない（読み上げの文には全部入れる）
function renderEffort() {
  const trail = state.trail;
  show("effort-open", trail.length > 1);  // 残したリンクから開いた結果は 1 本だけなので出さない
  if (trail.length < 2) return;
  const total = (t) => BOARD.reduce((sum, [key]) => sum + t.n[key], 0);
  const most = Math.max(...trail.map(total), 1);
  $("effort-bars").replaceChildren(...trail.map((t) => {
    const label = `${t.presses} 回`;
    const bar = el("span", { class: "effort-bar", "aria-hidden": "true" }, ...BOARD.filter(([key]) => t.n[key]).map(([key]) => {
      const seg = el("span", { class: `seg ${key}` }, t.n[key] / most >= 0.07 ? String(t.n[key]) : "");
      seg.style.flexGrow = String(t.n[key]);
      return seg;
    }));
    bar.style.width = `${(total(t) / most) * 100}%`;
    return el("li", { "aria-label": `押した数 ${label}：` + BOARD.map(([key, short]) => `${short} ${t.n[key]} 件`).join("・") },
      el("span", { class: "effort-label", "aria-hidden": "true" }, label), el("span", { class: "effort-track" }, bar));
  }));
  $("effort-legend").replaceChildren(...BOARD.map(([key, short]) =>
    el("li", {}, el("span", { class: `effort-swatch ${key}`, "aria-hidden": "true" }), short)));
  const last = trail[trail.length - 1];
  $("effort-layers").textContent = `${last.presses} 回押した後の、当てはまらないを除く ${last.sure + last.extra} 件のうち、`
    + `確かな層（公式ページの条件から書いた規則で判定した国・都・市の制度）が ${last.sure} 件`
    + (last.extra ? `、プラスアルファの層（どこの市区町村にもある事業の型。お住まいの市の条件では判定していない候補）が ${last.extra} 件です。` : "です。");
}

// ---- 制度の地図（T-081・T-132）----
// 結果の画面に、真ん中に本人、まわりに制度の名前の札を置く。内側に当てはまる見込み（大きい札）、その外に先に手続きが要る制度（小さい札）。
// 先に手続きが要る制度は、前の手続きの札のすぐ外側に寄せて並べ、並び方で順番を示す（矢印は引かない）。
// 確かめる制度は 1 つの束「＋N」にまとめ、押すと札の一覧の確かめるの先頭が開く。描くのは /api/step の results と edges（規則の前提）だけで、Claude は使わない。
// 動きは 1 か所だけ：答えて内側へ移る札が滑る（束から出る札は束の位置から。prefers-reduced-motion では動かさない）
const SVG_NS = "http://www.w3.org/2000/svg";
const MAP_RINGS = [["eligible", 76, "当てはまる"], ["after_prereq", 128, "先に手続き"], ["unknown", 150, "確かめる"]];
const MAP_C = 180;     // 図の真ん中（中身に合わせて viewBox を広げる）
const MAP_ME = 32;     // 真ん中の円の半径
// 札の大きさ：大事なものほど大きい。[字の大きさ, 高さ]。高さは 390px の画面で 24px 以上（WCAG 2.2 の 2.5.8）
const CHIP = { eligible: [15, 32], after_prereq: [13, 26], unknown: [13, 26] };
const MAP_PAD = 4;     // 札どうしの間
const MAP_MIN_W = 320; // 図の枠のいちばん狭い幅（前の 360 の図と同じくらいの字の大きさに見える）
// 札の名前（6 字ほど）。無い制度は名前の括弧の前を 7 字まで
const MAP_SHORT = {
  "fukushi-yogu-konyu": "福祉用具購入", "futan-gendogaku": "負担限度額", "hosougu-hochoki": "補聴器の補装具",
  "iryohi-kojo-omutsu": "おむつ代控除", "jiritsu-shien-iryo": "自立支援医療", "jutaku-kaishu": "住宅改修",
  "kaigo-kyugyo-kyufu": "介護休業給付", "kaigo-kyuka": "介護休暇", "kaigo-nintei": "要介護認定",
  "kogaku-gassan": "高額合算", "kogaku-kaigo-service": "高額介護費", "kogaku-ryoyohi": "高額療養費",
  "local-haikai": "探索・見守り", "local-haishoku": "配食・見守り", "local-iro": "介護慰労金",
  "local-kaigo-yohin": "介護用品", "local-koken": "成年後見", "machida-gps": "探索GPS",
  "machida-haishoku": "配食", "machida-hochoki": "補聴器助成", "machida-jutaku-setsubi": "住宅設備改修",
  "machida-kami-omutsu": "紙おむつ", "machida-kasai-yobo": "火災予防機器", "machida-kyukyu-tsuho": "救急通報",
  "machida-omutsu-kakunin": "おむつ確認書", "machida-shingu": "寝具乾燥", "machida-shogaisha-nintei": "障害者認定書",
  "nichijo-jiritsu-shien": "日常生活支援", "seikatsu-fukushi-shikin": "福祉資金貸付", "shintai-techo-chokaku": "障害者手帳",
  "shogaisha-kojo": "障害者控除", "tokubetsu-shogaisha-teate": "特別障害者手当", "tokyo-silver-pass": "シルバーパス",
  "yokohama-hochoki": "補聴器助成", "yokohama-kami-omutsu": "紙おむつ", "yokohama-keiro-pass": "敬老パス",
  "yokohama-mimamori": "見守り機器", "yokohama-ribiyo": "訪問理美容", "yokohama-shika-kenshin": "訪問歯科健診",
  "yokohama-sos": "SOSネット",
};
const shortName = (r) => MAP_SHORT[r.id] || r.name.split("（")[0].slice(0, 7);
// 費用の札（規則の cost.self_pay）を 3 つにまとめる。札の一覧の 1 行の頭の印の色（地図には使わない）
const COST_GROUP = { none: "free", receive: "free", tax: "free", partial: "pay", loan: "pay", varies: "ask", ask: "ask" };
let mapPts = new Map();  // 制度の id → 札の <a>。同じ札を動かすので作り直さない
let mapBundleAt = null;  // 前に描いた束の位置（束から出る札の動き始め）

function svg(tag, attrs = {}, ...children) {
  const n = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) n.setAttribute(k, v);
  for (const c of children.flat()) if (c !== null && c !== undefined) n.append(typeof c === "string" ? document.createTextNode(c) : c);
  return n;
}

// 札の幅：全角は字の大きさ、半角は 0.6 倍、左右に余白
const chipWidth = (text, size) => [...text].reduce((w, ch) => w + (ch.charCodeAt(0) < 0x2000 ? size * 0.6 : size), 0) + size * 1.2;

// 前提の家族が続くように並べる：results の順に、前提のない制度から、その後ろに前提にしている制度を深さ優先で
function mapOrder(results, edges) {
  const on = new Set(results.map((r) => r.id));
  const kids = new Map();
  const hasParent = new Set();
  for (const [a, b] of edges) {
    if (!on.has(a) || !on.has(b)) continue;
    if (!kids.has(a)) kids.set(a, []);
    kids.get(a).push(b);
    hasParent.add(b);
  }
  const seen = new Set();
  const out = [];
  const visit = (id) => {
    if (seen.has(id)) return;
    seen.add(id);
    out.push(id);
    for (const k of kids.get(id) || []) visit(k);
  };
  for (const r of results) if (!hasParent.has(r.id)) visit(r.id);
  for (const r of results) visit(r.id);  // 念のため（輪になった前提はない。rules.py が検査する）
  return out;
}

// 札の位置：決めた順に、望む角度から左右へ少しずつずらして、置いた札と重ならない所に置く。
// 1 周しても空きが無ければ半径を広げる。先に手続きが要る札は、前の手続きの札の角度を望み、その札より外の半径に置く
function mapLayout(results, edges) {
  const byId = new Map(results.map((r) => [r.id, r]));
  const parents = new Map();
  for (const [a, b] of edges) if (byId.has(a) && byId.has(b)) parents.set(b, [...(parents.get(b) || []), a]);
  const placed = [];  // [x, y, w, h]
  const pos = new Map();
  const free = (x, y, w, h) => Math.hypot(x - MAP_C, y - MAP_C) - Math.max(w, h) / 2 >= 0
    && placed.every(([px, py, pw, ph]) => Math.abs(px - x) * 2 >= pw + w + MAP_PAD * 2 || Math.abs(py - y) * 2 >= ph + h + MAP_PAD * 2);
  const put = (key, want, radius, w, h) => {
    for (let r = radius; ; r += 10) {
      for (let k = 0; k <= 90; k++) {
        const t = want + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * (Math.PI / 90);
        const x = MAP_C + r * Math.cos(t), y = MAP_C + r * Math.sin(t);
        if (free(x, y, w, h)) {
          placed.push([x, y, w, h]);
          pos.set(key, { x, y, w, h, t, r });
          return;
        }
      }
    }
  };
  placed.push([MAP_C, MAP_C, MAP_ME * 2, MAP_ME * 2]);
  const order = mapOrder(results, edges);
  const [, r1] = MAP_RINGS[0], [, r2] = MAP_RINGS[1], [, r3] = MAP_RINGS[2];
  const inner = order.filter((id) => byId.get(id).status === "eligible");
  inner.forEach((id, i) => {
    const [size, h] = CHIP.eligible;
    put(id, -Math.PI / 2 + (2 * Math.PI * i) / inner.length, r1, chipWidth(shortName(byId.get(id)), size), h);
  });
  const outer = order.filter((id) => byId.get(id).status === "after_prereq");
  outer.forEach((id, i) => {
    const [size, h] = CHIP.after_prereq;
    const up = (parents.get(id) || []).map((a) => pos.get(a)).filter(Boolean);
    const want = up.length ? up[0].t : -Math.PI / 2 + (2 * Math.PI * (i + 0.5)) / outer.length;
    const radius = Math.max(r2, ...up.map((p) => p.r + 40));
    put(id, want, radius, chipWidth(shortName(byId.get(id)), size), h);
  });
  const unknown = results.filter((r) => r.status === "unknown").length;
  if (unknown) {
    const [size, h] = CHIP.unknown;
    put("bundle", Math.PI / 2, r3, chipWidth(`＋${unknown}`, size), h);
  }
  return pos;
}

function clearMap() {
  mapPts = new Map();
  mapBundleAt = null;
  const box = $("pmap-svg");
  if (box) box.replaceChildren();
}

function chip(size, h) {
  return [svg("rect", { class: "chip-bg", height: h, rx: h / 2 }), svg("text", { class: "chip-text", "font-size": size }, "")];
}

function setChip(g, text, w, h) {
  const bg = g.querySelector(".chip-bg");
  bg.setAttribute("x", -w / 2);
  bg.setAttribute("y", -h / 2);
  bg.setAttribute("width", w);
  g.querySelector(".chip-text").textContent = text;
}

function renderMap(data, again) {
  const box = $("pmap-svg");
  if (!again) clearMap();
  const results = data.results.filter((r) => MAP_RINGS.some(([s]) => s === r.status));
  const edges = (data.edges || []).filter(([a, b]) => results.some((r) => r.id === a) && results.some((r) => r.id === b));
  const pos = mapLayout(results, edges);
  if (!box.children.length) {
    box.append(
      ...MAP_RINGS.slice(0, 2).map(([status, radius]) => svg("circle", { class: `ring ${status}`, cx: MAP_C, cy: MAP_C, r: radius })),
      svg("g", { class: "pts" }),
      svg("circle", { class: "me", cx: MAP_C, cy: MAP_C, r: MAP_ME }),
      svg("text", { class: "me-label", x: MAP_C, y: MAP_C }, ""));
  }
  box.querySelector(".me-label").textContent = state.subject;
  const layer = box.querySelector(".pts");
  const from = again && !calm() ? mapBundleAt : null;
  const parents = new Map();
  for (const [a, b] of edges) parents.set(b, [...(parents.get(b) || []), a]);
  for (const r of results.filter((x) => x.status !== "unknown")) {
    const [size, h] = CHIP[r.status];
    const p = pos.get(r.id);
    let g = mapPts.get(r.id);
    if (!g) {
      g = svg("a", { href: `#p-${r.id}`, class: "pt" }, svg("title", {}, ""), ...chip(size, h));
      g.addEventListener("click", (ev) => openCard(ev, r.id));
      g.addEventListener("mouseenter", () => pickPoint(g));
      g.addEventListener("focus", () => pickPoint(g));
      mapPts.set(r.id, g);
      layer.append(g);
      if (from) {  // 確かめるの束から出てくる
        g.style.transform = `translate(${from.x}px, ${from.y}px) scale(.6)`;
        g.getBoundingClientRect();
      }
    }
    g.querySelector(".chip-bg").setAttribute("height", h);
    g.querySelector(".chip-bg").setAttribute("rx", h / 2);
    g.querySelector(".chip-text").setAttribute("font-size", size);
    setChip(g, shortName(r), p.w, h);
    g.style.transform = `translate(${p.x}px, ${p.y}px)`;
    g.setAttribute("class", `pt ${r.status}`);
    g.dataset.id = r.id;
    if (parents.has(r.id)) g.dataset.after = parents.get(r.id).join(" ");
    else delete g.dataset.after;
    const say = `${r.name}（${r.status_label}・費用：${r.cost ? r.cost.label : "窓口で確かめる"}）`;
    g.setAttribute("aria-label", say);
    g.dataset.say = say;
    g.querySelector("title").textContent = say;
  }
  for (const [id, g] of mapPts) if (!results.some((r) => r.id === id && r.status !== "unknown")) { g.remove(); mapPts.delete(id); }
  // 確かめるの束
  let bundle = box.querySelector(".bundle");
  const unknown = results.filter((r) => r.status === "unknown");
  if (unknown.length) {
    const [size, h] = CHIP.unknown;
    const p = pos.get("bundle");
    if (!bundle) {
      bundle = svg("a", { href: `#p-${unknown[0].id}`, class: "pt bundle unknown" }, svg("title", {}, ""), ...chip(size, h));
      bundle.addEventListener("click", (ev) => openCard(ev, bundle.dataset.first));
      bundle.addEventListener("mouseenter", () => pickPoint(bundle));
      bundle.addEventListener("focus", () => pickPoint(bundle));
      layer.append(bundle);
    }
    setChip(bundle, `＋${unknown.length}`, p.w, h);
    bundle.style.transform = `translate(${p.x}px, ${p.y}px)`;
    bundle.setAttribute("href", `#p-${unknown[0].id}`);
    bundle.dataset.first = unknown[0].id;
    const say = `確かめる ${unknown.length} 件`;
    bundle.setAttribute("aria-label", say);
    bundle.dataset.say = say;
    bundle.querySelector("title").textContent = say;
    mapBundleAt = p;
  } else if (bundle) {
    bundle.remove();
    mapBundleAt = null;
  }
  // 図の枠は中身にぴったり合わせる（余白が減るぶん、390px の画面でも札の字が大きく見える）
  let [x0, y0, x1, y1] = [MAP_C - MAP_ME, MAP_C - MAP_ME, MAP_C + MAP_ME, MAP_C + MAP_ME];
  for (const { x, y, w, h } of pos.values()) {
    x0 = Math.min(x0, x - w / 2); x1 = Math.max(x1, x + w / 2);
    y0 = Math.min(y0, y - h / 2); y1 = Math.max(y1, y + h / 2);
  }
  const grow = Math.max(0, MAP_MIN_W - (x1 - x0)) / 2;  // 札が少ないときに字が大きくなりすぎないよう、幅は MAP_MIN_W まで広げる
  box.setAttribute("viewBox", [x0 - 4 - grow, y0 - 4, x1 - x0 + 8 + grow * 2, y1 - y0 + 8].map((v) => Math.round(v)).join(" "));
  // 数の凡例
  $("pmap-legend").replaceChildren(...MAP_RINGS.map(([status, , label]) =>
    el("li", {}, el("span", { class: `pmap-swatch ${status}`, "aria-hidden": "true" }), label)));  // 件数は札の一覧の見出しだけ（T-133）
  $("pmap-pick").textContent = "札を押すと、その制度の説明へ移ります。";
  show("pmap-fold", results.length > 0);
}

function pickPoint(g) {
  $("pmap-pick").textContent = g.dataset.say;
}

// ---- 結果を残す（T-072）----
// 答えを URL の # より後ろに入れたリンクを出し、アドレス欄もそれにする（ブックマークや再読み込みでも開き直せる）。
// # より後ろはサーバに送られない。開き直すと /api/step に同じ答えを送り、規則だけで同じ結果を出す（Claude は呼ばない）
const NINTEI = "kaigo-nintei";
const NINTEI_FACT = "care_level";
const KEEP_PREFIX = "#r=";

function forgetKeep() {
  if (location.hash) history.replaceState(null, "", location.pathname + location.search);
  show("keep", false);
}

async function showKeep(data) {
  const mine = state;
  let code;
  try {
    code = await encodeKeep({ s: state.subject, f: state.facts, g: state.suggestions, m: state.municipality });
  } catch (e) { show("keep", false); return; }  // 作れなければ欄を出さない（結果は読める）
  if (mine !== state) return;
  const url = location.origin + location.pathname + location.search + KEEP_PREFIX + code;
  history.replaceState(null, "", url);
  $("keep-url").value = url;
  $("keep-msg").textContent = "";
  // 認定の申請が当てはまる（申請して結果待ちも）人には、認定が出た後にこのリンクで次の手続きを見られると添える
  const nintei = data.results.find((r) => r.id === NINTEI);
  const waiting = Boolean(nintei && (nintei.status === "eligible" || nintei.status === "pending"));
  show("keep-nintei", waiting);
  // リンクから開いた人には、認定の区分を答え直す道を出す（答えると、先に手続きが要った制度を判定し直す）
  show("keep-reask", waiting && state.restored);
  show("keep");
}

async function copyKeep() {
  const url = $("keep-url").value;
  try {
    await navigator.clipboard.writeText(url);
    $("keep-msg").textContent = "リンクを写しました。メモやメールに貼って残してください。";
  } catch (e) {
    // 写せないブラウザでは、欄を選んだ状態にして、自分で写してもらう
    $("keep-url").focus();
    $("keep-url").select();
    $("keep-msg").textContent = "リンクを選びました。長押しやコピーの操作で写してください。";
  }
}

// 認定が出たら：認定の区分を「分からない」に戻し、結果の画面の「まだ確かめていないこと」で答え直せるようにする
function reaskNintei() {
  const facts = { ...state.facts };
  delete facts[NINTEI_FACT];
  state.laterOpen = true;  // 読み取れなかったことの問いは畳んであるので開く。値打ちの順で認定の区分が先頭に来る
  state.laterAt = 0;
  return step({ facts, presses: state.presses + 1 }, true);
}

async function restoreFrom(code) {
  let saved;
  try {
    saved = await decodeKeep(code);
  } catch (e) {
    say("残した結果のリンクを読めませんでした。リンクが途中で切れていないか確かめてください");
    $("reading").replaceChildren(el("p", { class: "error" }, "残した結果のリンクを読めませんでした。リンクが途中で切れていないか確かめてください。"));
    show("reading");
    return;
  }
  state = fresh();
  Object.assign(state, { startedAt: Date.now(), restored: true, subject: saved.s.slice(0, 12) || "ご本人",
    facts: saved.f, suggestions: saved.g, municipality: saved.m });
  const mine = state;
  try {
    const data = await api("/api/step", { facts: state.facts, suggestions: state.suggestions, subject: state.subject,
      asked: 99, municipality: state.municipality });
    if (mine !== state) return;
    show("extracted", false); show("ask", false);
    render(data);
  } catch (e) {
    $("reading").replaceChildren(el("p", { class: "error" }, e.message));
    say(e.message);
    show("reading");
  }
}

function reasonText(r) {
  const f = r.reasons.find((x) => x.state === "F");
  // 「町田市以外にお住まい に当たらない」は二重の否定に読めるので、満たしていない条件として名指す
  return f ? `条件「${f.label}」を満たしていない${f.value ? `（${f.value}）` : ""}` : "満たしていない条件がある";
}

// 費用の負担の 1 行（規則の cost から。Claude は使わない）
function costText(c) {
  const who = c.who_can_apply.length ? `。申請できる人：${c.who_can_apply.join("・")}` : "";
  return `費用：${c.label}${c.share ? `（${c.share}）` : ""}${who}`;
}

function programCard(r) {
  const head = el("div", { class: "head" },
    el("span", { class: "tag" }, r.level), el("span", { class: "name" }, r.name),
    el("span", { class: `pill ${r.status}` }, r.status_label));
  const sumParts = [head, el("p", { class: "sum" }, r.summary)];
  // その市の事業に替えた型（rules/local。T-075b）には印を付ける。判定の条件・状態は型のまま
  if (r.local) sumParts.push(el("p", { class: "local-mark" }, r.local.mark));
  if (r.waiting_on.length) {
    sumParts.push(el("p", { class: "chain" }, "先に：",
      ...r.waiting_on.map((w, i) => [i ? "、" : "", el("a", { href: `#p-${w.id}`, onclick: (ev) => openCard(ev, w.id) }, w.name),
        w.pending ? "（申請済み・結果待ち）" : ""])));
  }
  if (r.cost) sumParts.push(el("p", { class: "cost" },
    el("span", { class: `cost-mark cost-${COST_GROUP[r.cost.self_pay] || "ask"}`, "aria-hidden": "true" }), costText(r.cost)));
  if (r.status === "unknown" && r.missing.length) {
    sumParts.push(el("p", { class: "chain" }, "分からない点：", r.missing.map((m) => m.name).join("、")));
  }
  const body = el("div", { class: "body" });
  if (r.memo) body.append(memoView(r.memo));
  body.append(el("h3", {}, "判定の理由"), el("ul", { class: "reasons" },
    r.reasons.map((x) => el("li", { class: x.state }, x.label, x.value ? el("span", { class: "val" }, `（${x.value}）`) : null))));
  if (r.missing.length) {
    body.append(el("h3", {}, "確かめ方"), el("ul", { class: "plain" }, r.missing.map((m) => el("li", {}, m.check))));
  }
  if (r.at_application.length) body.append(el("h3", {}, "申請のときに確かめられること"), el("ul", { class: "plain" }, r.at_application.map((t) => el("li", {}, t))));
  if (r.local) {
    body.append(el("h3", {}, `${r.local.city}の主な条件（公式頁の言い換え）`),
      el("ul", { class: "plain" }, r.local.conditions.map((t) => el("li", {}, t))),
      el("p", { class: "note" }, `${r.local.read_by}が読んで書き、${r.local.recheck}（${r.local.date}）。`,
        r.local.note ? `直したこと：${r.local.note}` : ""));
  }
  body.append(el("h3", {}, "申請先"), el("p", {}, r.apply.where));
  if (r.apply.how) body.append(el("p", {}, r.apply.how));
  if (r.apply.phone) body.append(...phoneViews(r.apply.phone));
  if (r.apply.center_search) body.append(el("p", {}, centerLink(r.apply.center_search)));
  if (r.procedures.length) body.append(el("h3", {}, "根拠の法令"), procedureView(r));
  if (r.bring.length) body.append(el("h3", {}, "持ち物・書類"), el("ul", { class: "plain" }, r.bring.map((t) => el("li", {}, t))));
  if (r.amount.length) body.append(el("h3", {}, "受けられる内容"), el("ul", { class: "plain" }, r.amount.map((t) => el("li", {}, t))));
  if (r.timing.length) body.append(el("h3", {}, "時期・期限"), el("ul", { class: "plain" }, r.timing.map((t) => el("li", {}, t))));
  body.append(el("h3", {}, "出典"), ...r.sources.map(sourceView));
  return el("details", { class: "card prog", id: `p-${r.id}`, "data-status": r.status }, el("summary", {}, sumParts), body);
}

// 申請メモ（T-074）：窓口へ持っていく記入の下書き。中身は規則と相談の答えから（サーバの memo.py。Claude は使わない）
function memoView(m) {
  const sheet = el("div", { class: "memo-sheet" },
    el("p", { class: "memo-title" }, m.title),
    el("p", { class: "note" }, m.note),
    el("h4", {}, "申請先"), el("p", {}, m.where),
    m.phone ? el("p", {}, `電話：${m.phone}`) : null,
    m.bring.length ? [el("h4", {}, "持ち物（済んだら印を）"), el("ul", { class: "check" }, m.bring.map((t) => el("li", {}, t)))] : null,
    m.fill.length ? [el("h4", {}, "相談の答えから書けること"),
      el("dl", { class: "fill" }, m.fill.flatMap((f) => [el("dt", {}, f.name), el("dd", {}, f.value)]))] : null,
    el("h4", {}, "窓口で最初に言う一言"), el("p", { class: "say" }, m.say));
  return el("details", { class: "memo" }, el("summary", {}, "申請メモを開く（窓口に持っていく下書き）"), sheet,
    el("button", { type: "button", onclick: () => printMemo(sheet) }, "このメモだけ印刷する"));
}

// メモ 1 枚だけを刷る：body の直下の #print-sheet に写し、ほかを紙から外す（style.css の html.print-memo）
function printMemo(sheet) {
  let box = document.getElementById("print-sheet");
  if (!box) { box = el("div", { id: "print-sheet" }); document.body.append(box); }
  box.replaceChildren(sheet.cloneNode(true));
  document.documentElement.classList.add("print-memo");
  window.print();
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

// 電話番号が「／」で並ぶときは 1 件ずつ段落に分ける。1 つの段落で続く行に番号が並ぶと、行の間隔（約 30px）が
// 押せる範囲（.tel::after の上下 14px）より狭く、上の番号の少し下を押すと下の番号にかかるため（T-032）
function phoneViews(phone) {
  return phone.split("／").map((part) => el("p", {}, "電話：", telLinks(part)));
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
  // 畳んだ欄（確かめるの一覧・ほか N 件）の中の札なら、外側から開く
  for (let up = card.parentElement; up && up !== $("result"); up = up.parentElement) {
    if (up.hidden) up.hidden = false;
    if (up.tagName === "DETAILS") up.open = true;
  }
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

async function sampleSummary(key) {
  show("summary", false);
  let data = { lines: [], made: null };
  try {
    data = await api("/api/sample/summary", { id: state.sampleId, facts: state.facts, municipality: state.municipality });
  } catch (e) { /* まとめが出なくても結果は読める */ }
  if (key !== state.summaryKey) return;  // 待つ間に答えが変わった
  state.sampleSummary = data;
  showSampleSummary();
}

function showSampleSummary() {
  const data = state.sampleSummary;
  if (!data || !data.lines.length) {
    // 台本と違う答えか、まとめをまだ作っていない見本（見出しだけの空のカードも出さない）
    show("summary", false);
    $("stats").textContent += "。見本では、Claude が書くまとめを省いています";
    return;
  }
  $("summary-lines").replaceChildren(...data.lines.map((l) => el("li", {}, l.text)));
  const made = data.made ? `（${data.made.date}・${data.made.model}）` : "";
  $("summary-note").textContent = `見本の台本の答えのときだけ出す、前もって Claude が判定の結果から書いたまとめです${made}。判定そのものは規則のプログラムが決めています。`;
  show("summary");
}

// ---- 始める ----
chipGroup("pick-who", WHO, false);
chipGroup("pick-age", AGES, false);
chipGroup("pick-worry", WORRIES.map(([label]) => label), true);
// 市区町村を選んだら、その市区町村で使える制度の一覧（/city/<団体コード>。規則から作る頁）へ 1 タップで開けるようにする（T-080b）
function showCityLink() {
  const pref = prefs.find((p) => p.code === pick.pref);
  const city = pref && pref.cities.find(([code]) => code === pick.city);
  if (city) {
    $("city-link").href = `/city/${encodeURIComponent(city[0])}`;
    $("city-link").textContent = `${pref.name}${city[1]}で使える制度の一覧`;
  }
  show("city-link-box", Boolean(city));
}
function choosePref(code) {
  pick.pref = code; pick.city = ""; fillCities(); redraft(); showCityLink(); markPref();
}
$("pref").addEventListener("change", () => choosePref($("pref").value));
// 全国の見取り図で、選んでいる都道府県のタイルに印を付ける（T-080c）
function markPref() {
  for (const a of document.querySelectorAll("#japan-box a[data-pref]")) {
    if (a.dataset.pref === pick.pref) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
  }
}
// タイルを押すと、その都道府県を選んで市区町村の欄へ移る（/city/ の頁からは /?pref=NN で来る）
function pickFromMap(code, smooth) {
  if (!prefs.some((p) => p.code === code)) return false;
  show("events"); show("pick-more");  // 選ぶ欄は出来事の枠の中で、最初は出していない（T-134）
  $("pick-more").open = true;  // 選ぶ項目は畳んであるので、開いてから市区町村の欄へ移る（T-126）
  $("pref").value = code;
  choosePref(code);
  $("city").scrollIntoView({ behavior: smooth && !calm() ? "smooth" : "auto", block: "center" });
  $("city").focus({ preventScroll: true });
  return true;
}
$("city").addEventListener("change", () => { pick.city = $("city").value; redraft(); showCityLink(); });
$("redraft").addEventListener("click", () => redraft(true));
$("text").addEventListener("input", () => {
  const draft = buildDraft();
  const edited = $("text").value !== lastDraft && $("text").value.trim() !== "";
  show("redraft", edited && draft !== "" && draft !== $("text").value);
});
fetch("/static/municipalities.json").then((r) => r.json()).then((data) => {
  prefs = data.prefectures;
  $("pref").append(...prefs.map((p) => el("option", { value: p.code }, p.name)));
  const asked = new URLSearchParams(location.search).get("pref");
  if (asked) {
    pickFromMap(asked, false);
    history.replaceState(null, "", location.pathname + location.hash);  // 結果を残すリンクに ?pref= を混ぜない
  }
}).catch(() => { /* 取れなければ、お住まいは文に書いてもらう */ });
// 全国の見取り図は T-134b で別の頁（web/japan.html・web/japan.js）へ移した。タイルは /?pref=NN で戻り、上の pickFromMap が選ぶ
// 出来事のボタンも、取れたときだけ出す（T-071）
fetch("/api/events").then((r) => (r.ok ? r.json() : [])).then((list) => {
  if (!list.length) return;
  eventList = list;
  $("event-list").replaceChildren(...list.map(eventCard));
  $("event-start").addEventListener("click", openEvents);
  show("events");
}).catch(() => { /* 出来事の行は出さない */ });
// 見本は取れたときだけ出す
fetch("/api/samples").then((r) => (r.ok ? r.json() : [])).then((list) => {
  if (!list.length) return;
  $("sample-list").replaceChildren(...list.map((s) => el("button", { type: "button", class: "chip", onclick: () => openSample(s) }, s.label)));
  show("samples");
}).catch(() => { /* 見本の行は出さない */ });
$("send").addEventListener("click", send);
$("skip").addEventListener("click", () => step({}, true));
$("one").addEventListener("click", toggleOne);
$("pause-more").addEventListener("click", pauseMore);
$("pause-stop").addEventListener("click", () => step({}, true));  // 聞き返しをやめたのと同じ。結果の画面に残すリンクも出る
$("keep-copy").addEventListener("click", copyKeep);
$("keep-reask").addEventListener("click", reaskNintei);
// 開いているページに別の残した結果のリンクを貼ったとき（# より後ろだけが変わると、ページは読み直されない）
window.addEventListener("hashchange", () => { if (location.hash.startsWith(KEEP_PREFIX)) restoreFrom(location.hash.slice(KEEP_PREFIX.length)); });
// ---- 押した数の図は別の頁（T-133）。結果の頁を隠して出し、戻ると押した入口へ ----
$("effort-open").addEventListener("click", () => {
  show("result", false);
  show("effort-page");
  $("effort-page").scrollIntoView({ block: "start" });
  focusOn($("effort-title"));
});
$("effort-back").addEventListener("click", () => {
  show("effort-page", false);
  show("result");
  $("effort-open").scrollIntoView({ block: "center" });
  focusOn($("effort-open"));
});

$("again").addEventListener("click", () => {
  state = fresh();
  forgetKeep();
  $("text").value = "";
  resetPick();
  show("extracted", false); show("ask", false); show("pause", false); show("result", false);
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
  // 欄ごと隠すので、押したボタン・合言葉の欄からフォーカスが body に落ちる。送り直すなら送るボタンへ
  // （consultWith がそれを押したボタンとして覚え、失敗したらそこへ戻す）、書く前なら相談の欄へ移す（T-063）
  if (resendAfterGate) { resendAfterGate = false; $("send").focus(); send(); }
  else $("text").focus();
});
// Enter の既定の動きを止める。止めないと、keydown の中で相談の欄へ移したフォーカスの先に改行が入る（T-063）
$("gate-open").addEventListener("click", () => {
  show("gate-open", false);
  show("gate");
  $("aikotoba").focus();
});
$("aikotoba").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); $("gate-ok").click(); } });
// 印刷するときは、結果の札（details）を開いて申請先・持ち物・出典まで紙に出し、終わったら開く前に戻す（T-066）。
// 結果の画面のときだけ html に print-result を付け、入力・問い・帯・ボタンを紙から外す（style.css の @media print）
let printOpened = [];
window.addEventListener("beforeprint", () => {
  const onResult = !$("result").hidden;
  document.documentElement.classList.toggle("print-result", onResult);
  printOpened = onResult ? [...$("result").querySelectorAll("details:not([open])")] : [];
  for (const d of printOpened) d.open = true;
});
window.addEventListener("afterprint", () => {
  for (const d of printOpened) d.open = false;
  printOpened = [];
  document.documentElement.classList.remove("print-result");
  document.documentElement.classList.remove("print-memo");
  document.getElementById("print-sheet")?.replaceChildren();
});
// 合言葉が要る置き場では、書き始める前に聞く（書いて送ってから止められないように）
// 残した結果のリンクから開いたときは聞かない（結果は合言葉なしで出る。相談を送るときに聞く）
const keptAtLoad = location.hash.startsWith(KEEP_PREFIX);
const healthReady = fetch("/api/health").then((r) => r.json()).then((h) => {
  needGate = Boolean(h.passphrase);
  // 最初は小さな入口だけを出す（T-126。最初の画面の文字を減らす）。押すか、合言葉なしで送ると欄を出す
  if (h.passphrase && !aikotoba && !keptAtLoad) show("gate-open");
}).catch(() => { /* 分からなければ、送ったときに聞く */ });
// 合言葉が要る置き場かを知ってから開く（知る前に結果を出すと、まとめを送って合言葉の欄へ飛ばされる）
if (keptAtLoad) healthReady.then(() => restoreFrom(location.hash.slice(KEEP_PREFIX.length)));
