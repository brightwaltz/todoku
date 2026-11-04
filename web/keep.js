// とどく：結果を残すリンクの符号（T-072）。答え（事実・確かめていない読み取り・呼び方・市区町村）を
// URL の # より後ろ（#r=…）に入れる。# より後ろはブラウザがサーバに送らないので、とどくは何も残さない。
// 符号は「版.本体」。1 は JSON を deflate-raw で縮めて base64url にしたもの、0 は縮めずに base64url にしたもの
// （CompressionStream の無いブラウザで作ったリンク）。開くときは両方を読む。
"use strict";

const KEEP_MAX = 4000;  // これより長い符号は読まない（リンクを作り直してもらう）

const toB64url = (bytes) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const fromB64url = (text) => Uint8Array.from(atob(text.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));

async function through(bytes, stream) {
  return new Uint8Array(await new Response(new Blob([bytes]).stream().pipeThrough(stream)).arrayBuffer());
}

// saved: { s: 呼び方, f: 事実, g: 確かめていない読み取り, m: 市区町村のコード }
async function encodeKeep(saved) {
  const raw = new TextEncoder().encode(JSON.stringify(saved));
  if (typeof CompressionStream === "undefined") return "0." + toB64url(raw);
  return "1." + toB64url(await through(raw, new CompressionStream("deflate-raw")));
}

// 読めない符号は Error を投げる。中身の値が正しいかはサーバ（/api/step の clean_facts）が確かめる
async function decodeKeep(code) {
  if (code.length > KEEP_MAX) throw new Error("too long");
  const dot = code.indexOf(".");
  const version = code.slice(0, dot);
  let bytes = fromB64url(code.slice(dot + 1));
  if (version === "1") bytes = await through(bytes, new DecompressionStream("deflate-raw"));
  else if (version !== "0") throw new Error("unknown version");
  const saved = JSON.parse(new TextDecoder().decode(bytes));
  const isMap = (x) => x !== null && typeof x === "object" && !Array.isArray(x);
  if (!isMap(saved) || typeof saved.s !== "string" || !isMap(saved.f) || !isMap(saved.g) || typeof saved.m !== "string") {
    throw new Error("bad shape");
  }
  return saved;
}

if (typeof module !== "undefined") module.exports = { encodeKeep, decodeKeep, KEEP_MAX };
