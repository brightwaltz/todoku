# 市ごとの事業（rules/local/<団体コード>.yaml）

市区町村の型 5 件（`rules/programs/local-*.yaml`）を、その市の事業に置き換えるためのデータです（T-075）。読み込みと形の検査は `src/todoku/local_rules.py`、テストは `tests/test_local_rules.py`。

- 判定の条件は型のものを使い、ここでは変えません。ここに置くのは、事業の名前・申請先・主な条件の言い換え・出典・調べ方だけです。
- ファイル名と `city` は総務省の団体コード 6 桁（先頭の 0 も書き、`"011002"` のように引用符で囲む）。`name` は `web/municipalities.json` の名前と同じにします。
- 市の制度を一つずつ判定している市（町田市・横浜市）には置きません。
- 見つからなかった型は `status: not_found` で残します（「その市に無い」とは言わず、型のまま出します）。
- 照らし直しで原文と合わなかったものは置きません。直したときは `recheck: fixed` と、何を直したかを `note` に書きます。
- 条件は出典の文を写さず、自分の言葉で 1 行 60 字まで・5 行までにします。原文の引用（`quote`）は、利用条件が PDL1.0 の頁だけです（`rules/programs` と同じ決まり）。

```yaml
city: "011002"
name: 札幌市
site: city.sapporo.jp          # found の出典は、このホスト名の頁を 1 つは含む
programs:
  - template: local-haishoku   # 置き換える型の id
    status: found              # found か not_found
    name: （その市の事業の名前）
    where: （申請先）
    conditions:
      - （主な条件の言い換え）
    sources:
      - url: https://www.city.sapporo.jp/...
        publisher: 札幌市
        title: （頁の題名）
        license: 利用条件を確かめていない（引用しない）
        checked: 2026-11-05
    review:
      read_by: Managed Agent（モデル名）   # 誰が公式頁を読んで書いたか
      recheck: match                      # match（原文の行と合っていた）か fixed（照らし直しで直した）
      date: 2026-11-05
  - template: local-iro
    status: not_found
    review: {read_by: Managed Agent（モデル名）, recheck: match, date: 2026-11-05}
```

政令指定都市 20 市の団体コード：札幌市 011002・仙台市 041009・さいたま市 111007・千葉市 121002・川崎市 141305・相模原市 141500・新潟市 151009・静岡市 221007・浜松市 221309・名古屋市 231002・京都市 261009・大阪市 271004・堺市 271403・神戸市 281000・岡山市 331007・広島市 341002・北九州市 401005・福岡市 401307・熊本市 431001（横浜市 141003 は市の制度を一つずつ判定しているので置かない）。
