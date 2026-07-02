# 部局統合（Phase 2: 公式総覧＋部門比較） 設計書

作成日: 2026-07-02
ステータス: 承認済み
前提: Phase 1〜FWCI合計まで main にマージ済み。公式総覧 https://kyoiku-kenkyudb.omu.ac.jp/ は2026-07-02に復旧を確認。

## 目的

公式研究者総覧から**部局・職位・公式日本語氏名**を取得してOpenAlex研究者に名寄せし、(1) ランキングの**部局フィルタ（部門内比較）**、(2) **部局間比較ページ**を実現する。

## 取得元の実査結果（2026-07-02）

- robots.txt なし（404）＝クロール禁止宣言なし。学内利用の比較ツールとして低頻度アクセスで利用（1リクエスト毎に0.5秒スリープ、週1回、全体で約50〜100リクエスト）
- トップページに部局別リンク: `/search?m=affiliation&l=ja&a2=<部局コード7桁>&s=1&o=affiliation`（`a3`=下位組織は使わない。a2単位で全部局を列挙）
- 一覧ページ構造（1人=1カード、`div.card-body.result`）:
  - `.name-kna` カナ氏名（全角空白区切り）
  - `.name-gng a` 漢字氏名、href=`/html/{数字ID}_ja.html`
  - `.name-title` 職位（教授/准教授/講師/助教等）
  - `.org-cd` 所属（例:「大学院文学研究科 哲学歴史学専攻<br/>文学部 哲学歴史学科」）
  - `.kaknh-bnrui` 研究キーワード
- ページング: `pp`=表示件数（10/20/30/50/100）、`s`=開始位置（1始まり）、総件数は「◯件中」表示から取得

## 新テーブル

**roster**: `profile_id`(PK, /html/リンクの数字ID), `name_kanji`, `name_kana`(無ければNULL), `position`, `division`(a2部局ラベル。例: 大学院文学研究科), `org_text`(org-cd原文), `keywords`, `matched_researcher_id`(nullable, index), `updated_at`

同一人物が複数部局に出る場合は最初に出た部局を採用（profile_id重複はスキップ）。

## researchers への反映（名寄せ確定者のみ）

- `department` = roster.division、`position` = roster.position、`name_ja` = roster.name_kanji（全角空白→半角）、`is_official_roster` = True
- **氏名等の優先度: 公式総覧 > KAKEN**。`collector/kaken.py` の `match_members` は `is_official_roster=True` の研究者の name_ja を**設定も消去もしない**ようガードを追加（roster反映が週次syncでKAKENに巻き戻されるのを防ぐ）
- roster側の全再マッチ時は、今回マッチしなかった研究者の department/position/is_official_roster/name_ja（roster由来分）をクリアして整合を保つ（決定的・冪等）

## 名寄せ（二重ブリッジ・学内一意のみ）

1. **漢字ブリッジ**: roster.name_kanji（空白正規化）が researchers.name_ja（KAKEN由来）と一致 → 候補
2. **カナブリッジ**: roster.name_kana → 既存 `collector/nameutil.py` のローマ字変体 → display_name 索引と照合 → 候補
3. 両ブリッジの候補和集合が**ちょうど1人**なら確定。0人・複数人は未マッチのまま
4. 複数のroster行が同一研究者に確定した場合（学内同姓同名）は衝突として**全て未マッチに戻す**（誤同定防止。KAKENの偽陽性事故の教訓）

## 同期フロー

`scripts/sync.py`: authors → works → kaken → **roster**（クロール＋全洗い替え。0件なら警告して既存保持） → **roster名寄せ＋researchers反映** → metrics。
rosterクロール失敗（ネットワーク・構造変化）は警告して他ステージ継続（KAKENと同じ方針）。

## 閲覧Webの変更

### ランキング（`/`）— 部門内比較

- `department` クエリパラメタ（部局名の完全一致）を追加。指定時は該当部局の研究者のみ表示、内訳は「◯◯: 全X人中Y人を表示」
- フィルタUI: ソート行の隣に部局ドロップダウン（`SELECT DISTINCT department` から生成、未指定=全学）
- 不正な部局名は無視（全学表示にフォールバック）

### 部局間比較（`/departments`）

- 表: 部局 × （**名寄せ済み人数**、3年論文数合計、人あたり論文数、総被引用合計、FWCI合計の部局計、**人あたりFWCI合計**、top10%論文合計、科研費総額合計）
- 並び順: 人あたりFWCI合計の降順固定（v1はソートUIなし）
- 部局名クリックで該当部局のランキング（`/?department=...`）へ
- 注記: 「公式総覧と名寄せできた研究者のみの集計（規模の異なる部局は人あたり列で比較）」。全学の名寄せ率（全◯人中◯人）を表の上に表示
- ナビに「部局」リンク追加

### 研究者詳細

- 部局・職位をヘッダ（氏名の下）に表示（あれば）

## 依存追加

`beautifulsoup4==4.12.3`（MedAIDigestと同じピン。一覧HTMLのパースに使用）

## エラー処理

- HTTP失敗は既存パターンの指数バックオフ（1,2,4,8,16秒・最大6回）。パースできないカードはスキップ＋警告
- 総件数と収集数の突合（不一致は警告）。0件時は洗い替えスキップ（全消しガード）

## テスト

- rosterパーサ: 実HTML構造のfixture（カナ・漢字・職位・所属・profile_id抽出、カナ無しカード、壊れカードスキップ）
- 名寄せ: 漢字ブリッジ・カナブリッジ・和集合一意・複数roster行衝突の巻き戻し・未マッチ時のクリア
- kaken match_membersのis_official_rosterガード（rosterのname_jaを消さない・上書きしない）
- web: departmentフィルタ・ドロップダウン・/departmentsの集計値・人あたり計算・部局リンク
- 実クロール後のスポットチェック（部局数・roster人数・名寄せ率）

## スコープ外（YAGNI）

- a3（専攻・学科）単位の絞り込み、職位別フィルタ
- 個人ページ（/html/{id}_ja.html）のクロール（一覧で足りる。ローマ字氏名・研究分野詳細が将来必要になったら再訪）
- /departmentsのソートUI・グラフ
