# works駆動の研究者補完 設計書

日付: 2026-07-16

## 背景と問題

研究者リストは OpenAlex の `last_known_institutions.id = I4387152983`（大阪公立大学）で取得している。
しかし OpenAlex の機関名寄せは論文ごとに不安定で、所属文字列が "Osaka Metropolitan University"
と正しく書かれていても、旧大阪市立大学・旧大阪府立大学・大阪大学などに誤解決されることがある。
最新論文の所属で決まる `last_known_institutions` が学外機関になった研究者はリストから漏れる。

実例: 三木幸雄教授（医学部・放射線科）。本体著者レコード A5029030083（677本・被引用12,448）は
2026-01の論文の所属が大阪大学に誤解決されたため last_known が大阪大学となりリスト外。
OMU所属と付いた断片 A5123551549（9本）だけが表示され、直近3年の論文数が7本に見えていた。
実際は直近3年でOMU名義69本。この69本自体はworks同期でローカルDBに取得済みであり、
「authorshipsは存在するのに対応する研究者行がないため誰にも集計されない」状態だった。

規模感: 直近3年のworksでも旧市大名義2,381件・旧府大名義1,128件が存在し、同種の漏れは
三木氏に限らない。公式総覧1,382人中808人がOpenAlex未マッチで、その一部がこのパターン。

## 方針

内輪向けサービスであり「大体あっていればOK」。コスパの良い機械的な救済のみ行う。
ローカルに取得済みのworksのauthorship所属情報を使い、対象機関名義の論文を持つ著者を
研究者リストに補完する。名前検索ベースの名寄せ（誤マッチリスク）は行わない。

## 設計

### 1. 対象機関の定数

`collector/sync.py` に定義し、works取得フィルタと補完判定の両方で使う。

| ID | 機関 |
|---|---|
| I4387152983 | 大阪公立大学 |
| I317356780 | 旧 大阪市立大学 |
| I15807432 | 旧 大阪府立大学 |
| I4210166029 | 大阪市立大学病院（附属病院名義の誤解決先） |

高専（I4210154764）は別機関のため含めない。

### 2. スキーマ変更

- `authorships.institution_ids`（TEXT, nullable）: その著者のその論文での所属機関IDを
  パイプ区切りで保存（例 `"I4387152983|I317356780"`）。works APIレスポンスの
  `authorships[].institutions` に既に含まれており、追加取得コストなし。
- `researchers.source`（TEXT, NOT NULL, default `'last_known'`）: 補完由来は `'works'`。
  `sync_authors` の削除パス（退職者除去）から `'works'` 行を除外するために必要。

移行はmigrationを書かず `rm db/researchers.db*` → 全量再同期（README記載の標準手順）。

### 3. works同期のフィルタ拡張

`sync_works` のフィルタを `institutions.id:<4機関のOR>` に変更（OpenAlexのパイプOR構文）。
直近3年の論文は約6,200本 → 9,700本程度に増える見込み。削除パス（窓ベース）は変更不要。

### 4. 補完ステップ `backfill_authors`（新規）

works同期の後に実行する。

1. `authorships.institution_ids` が対象機関と交差し、かつ `researchers` に存在しない
   著者IDを抽出する
2. OpenAlex `authors?filter=ids.openalex:A|A|...` で50件ずつバッチ取得し、
   `source='works'` でupsertする（推定+100リクエスト程度）。
   マージ消滅等で返ってこないIDはスキップしてログに残す
3. 資格を失った `source='works'` 行（対象機関名義のauthorshipが窓から消えた著者）は削除する。
   `source='last_known'` の行には触れない

既存researchers（source='last_known'）と重複する著者は挿入しない（upsert対象は不在IDのみ）。
補完された著者は既存のdedup（同名+ORCID統合）・名簿名寄せ・KAKEN名寄せ・metrics計算に
そのまま乗る。三木氏のケースは dedup で断片と統合され、名簿の「三木 幸雄・医学部」に紐づく。

### 5. パイプライン順（scripts/sync.py）

authors → works → **backfill** → dedup → kaken → roster → metrics。
backfillは他ステージ同様、失敗しても後続を止めない（try/except + rollback + ログ）。

### 6. テスト

既存のオフラインテスト（fake transport / in-memory SQLite）のパターンを踏襲する。

- `parse_work` がauthorshipごとの `institution_ids` を正しく抽出する
- `backfill_authors` の抽出ロジック: 対象機関名義のみ補完、既存研究者は除外、
  資格喪失した `source='works'` 行の削除、`source='last_known'` 行の保護
- `sync_authors` の削除パスが `source='works'` 行を削除しないこと

### 7. リリース後の検証

- 三木幸雄の `works_count_3y` が70前後になること
- 名簿未マッチ数（改修前808人）が有意に減ること
- researchers / works の件数増をログで確認して報告

## スコープ外（やらないこと）

- OpenAlexに著者レコード自体がない教員（和文中心の人文・看護系）の救済 → 打つ手なし
- OpenAlexメガクラスタの生涯論文数汚染の浄化 → 3年窓の指標には実害小。
  生涯値は従来どおり「OpenAlex収録分・参考値」の扱い
- 名簿駆動の名前検索補完（案B）→ 今回の結果を見て必要なら別途
