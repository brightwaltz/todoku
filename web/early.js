// とどく：最初の画面のデータを、app.js の読み込みを待たずに取り始める（T-177）。
// app.js（約 40KB）を待ってから取ると、遅い回線では Google Fonts のファイルと回線を取り合い、
// 都道府県の選択肢が入るまで Fast 3G 相当で 4.9 秒かかっていた。app.js は earlyFetch() でここの応答を 1 回だけ受け取る。
// <link rel="preload" as="fetch"> は WebKit（Safari）で使われず二重に取ったので使わない。
// この script が app.js より後に届いたとき（todokuEarly が既にある）は何もしない（二重に取らない）。
"use strict";

if (!window.todokuEarly) {
  window.todokuEarly = {};
  for (const url of ["/api/events", "/api/samples", "/api/health", "/static/municipalities.json"]) {
    const p = fetch(url);
    p.catch(() => { /* 失敗は app.js の側で受ける（ここでは未処理の知らせを出さない） */ });
    window.todokuEarly[url] = p;
  }
}
