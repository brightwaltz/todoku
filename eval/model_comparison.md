# 読み取りのモデル比較

実行：2026-11-03 01:54（effort low）。`eval/compare_models.py` が書き出した。2026-11-03 01:57 に、保存した読み取りを今の事例と数え方で数え直した（API は呼んでいない）。正解は事例の answers。取れた率は、3 モデルのどれかが確信「高」で正しく取れた（事例, 事実）79 個を分母にした相対の値。「採点できない」は、事例を書いたあとで足した事実で、事例に答えがないもの。

| モデル | 確信「高」で正しい | 確信「高」で誤り | 「高」の正しさ | 取れた率（相対） | 「低」で正しい候補 | 「低」で違う候補 | 採点できない | 失敗 | 費用（米ドル） | 1 回の秒数（中央値） |
|---|---|---|---|---|---|---|---|---|---|---|
| claude-opus-5-5 | 75 | 1 | 98.7% | 94.9% | 26 | 1 | 0 | 0/30 | 0.428 | 2.76 |
| claude-sonnet-5-5 | 66 | 1 | 98.5% | 83.5% | 23 | 1 | 0 | 0/30 | 0.206 | 1.79 |
| claude-haiku-5-5 | 68 | 1 | 98.6% | 86.1% | 13 | 5 | 1 | 0/30 | 0.010 | 1.44 |

## 誤りと違う候補（事例, 事実：読んだ値 → 事例の答え）

- claude-opus-5-5：c15 care_level（高）：care2 → support2、c30 tax_status（低）：self_exempt → self_taxed
- claude-sonnet-5-5：c15 care_level（高）：care2 → support2、c30 tax_status（低）：self_exempt → self_taxed
- claude-haiku-5-5：c15 care_level（高）：care2 → support2、c15 plans_home_modification（低）：False → True、c26 plans_fixture_replacement（低）：False → True、c28 lives_in_machida（低）：False → True、c30 tax_status（低）：household_exempt → self_taxed、c30 plans_home_modification（低）：False → True
