// 全国の見取り図の頁（T-134b。前は入口の「このサイトについて」の中にあった）。
// サーバが規則から作った SVG の断片（/japan-map）を差し込む（よそのデータは入らない）。取れなければ出さない。
// タイルは /?pref=NN へのリンクのままにし、入口の app.js の pickFromMap が選ぶ欄を開いて市区町村の欄へ移る
fetch("/japan-map").then((r) => (r.ok ? r.text() : "")).then((html) => {
  const fig = new DOMParser().parseFromString(html, "text/html").querySelector("figure.japan-map");
  if (!fig) return;
  document.getElementById("japan-box").replaceChildren(document.importNode(fig, true));
  document.getElementById("japan").hidden = false;
}).catch(() => { /* 見取り図は出さない */ });
