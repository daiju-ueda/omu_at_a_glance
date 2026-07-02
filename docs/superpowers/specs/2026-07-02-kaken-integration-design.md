# KAKEN科研費統合 設計書

作成日: 2026-07-02
ステータス: 承認済み
前提: Phase 1＋Tier 1指標拡充が main にマージ済み。CiNii/KAKEN appid は取得済みだが2026-07-02時点で有効化待ち（「Invalid APPID」）。appidの文字列は本人確認済み。

## 目的

科研費の獲得状況（代表/分担・種目・配分額）を無料のKAKEN APIから収集し、比較軸「資金獲得力」を追加する。副産物として、KAKENの漢字氏名・カナを使った**日本語氏名の名寄せブリッジ**を作り、詳細ページ・検索の日本語対応を先行実現する（Phase 2の公式総覧名寄せの土台）。

## 確認済みの制約

- researchmap v2 APIは**機関単位のJST申請**が原則（OAuth2 JWT Bearer、APIキーは機関発行）。個人では実質取得不可のため**無期限保留**。受賞・書籍等はresearchmap経由では収集しない
- KAKEN opensearch: `https://kaken.nii.ac.jp/opensearch/?appid=...`（XML応答）。詳細な検索パラメタの公開ドキュメントが薄いため、**機関絞り込みパラメタ名はappid有効化後の実レスポンスで確定**する（実装はTDD・fixtureで先行）
- appidは秘匿情報として `.env`（gitignore済みにする）に `KAKEN_APPID=...` で保存。コードにハードコードしない。コミット・ログ出力もしない

## 収集対象

所属機関=大阪公立大学の科研費課題のうち、**研究期間が直近3年ウィンドウ（`window_start(今日)`〜今日）と重なるもの**全件。ページングは rw（件数）/ st（開始位置）方式を想定（実APIで確定）。

## 新テーブル

- **grants**: `award_id`(PK, 課題番号), `title`, `category`(種目: 基盤研究(B)等), `start_year: int`, `end_year: int`, `total_amount: int`(円, 直接+間接の総配分額。不明は0), `raw_json`(取得XMLをdict化して保存), `updated_at`
- **grant_members**: `award_id`+`erad_id`(複合PK; erad_idはe-Rad研究者番号。無い場合は氏名から生成した代替キー `name:<漢字氏名>`), `name_kanji`, `name_kana`, `role`("principal" | "co_investigator"), `matched_researcher_id`(OpenAlex著者IDへの名寄せ結果, nullable)

grantsのupsert・削除同期は行わず**全洗い替え**（全削除→再挿入）。件数規模は数千件・毎回1分程度の想定で、状態管理が最も単純。

## 名寄せ（カナ→ローマ字→OpenAlex display_name）

1. `collector/nameutil.py`: かな→ヘボン式ローマ字変換（外部依存なし・変換表実装）。長音の表記ゆれを変体として展開（例: オウ→ou/o/oh、ウウ→u/uu、ンB/M/P→n/m）
2. grant_memberのカナから `given family` / `family given` の全変体を生成し、OpenAlexの `display_name` を正規化（小文字化・ハイフン/ピリオド除去）した索引と照合
3. **学内で一意にマッチした場合のみ**自動確定（`matched_researcher_id` セット）。複数候補・ゼロ件はNULLのまま保持（無理に確定しない）
4. 一意マッチした研究者の `researchers.name_ja` にKAKENの漢字氏名を書き込む（`is_official_roster` はFalseのまま。Phase 2の公式総覧が入ったら上書きされる位置づけ）。カナが無い課題メンバーは名寄せ対象外

名寄せはKAKEN sync後に毎回全再計算（決定的・冪等）。

## メトリクス追加（researcher_metrics）

- `kaken_pi_count: int` — matched_researcher_idが本人 かつ role=principal の課題数
- `kaken_copi_count: int` — 同、co_investigator
- `kaken_total_amount: int` — **代表課題のみ**の total_amount 合計（円）。分担課題の額は本人の獲得額と言えないため合算しない（誠実な比較のため）

compute_metrics内でgrants/grant_membersから集計（テーブルが空なら全て0）。

## 同期フロー変更

`scripts/sync.py`: authors → works → **kaken**（`KAKEN_APPID` 未設定または「Invalid APPID」応答なら警告してスキップ、他ステージは継続） → metrics（名寄せ→researchers.name_ja反映→集計）。
`.env` の読み込みは `os.environ` ＋ `.env` ファイルの素朴なパーサ（`KEY=VALUE` 行）で行い、新規依存は追加しない。

## 閲覧Webの変更

- ランキング: **科研費総額** ソート列を追加（`SORT_COLUMNS` 6キー目 = `kaken_total_amount`）。表示は万円単位（`{amount//10000:,}万円`、0は「–」）
- 研究者詳細: 科研費（代表）/（分担）/配分総額 をメトリクスカードに追加
- フッター注記に追記: 「科研費はKAKEN収録分・配分額は課題総額（代表課題のみ合算・按分なし）」
- 日本語氏名は既存の `name_ja or display_name` 表示・検索がそのまま効く（Web側の変更不要）

## エラー処理

- appid無効（403）はリトライしない（起動時に1回判定してスキップ）。5xx/429は既存クライアントと同じ指数バックオフ
- XMLパース失敗は課題単位でスキップしログ警告（silent failure禁止）
- 取得0件のときは grants を洗い替えない（既存データ保持＋警告）— 空応答での全消し事故ガード（OpenAlex側と同じ方針）

## テスト

- nameutil: かな→ローマ字（長音・拗音・促音・ん）、変体展開
- kakenパーサ: fixture XML（実APIレスポンスの縮小版。有効化前は仕様ベースの暫定fixtureで書き、有効化後の実レスポンスで差分があればfixtureを更新）
- 名寄せ: 一意マッチ確定・複数候補は保留・カナ無しスキップ・name_ja書き込み
- metrics: pi/copi/amount集計、grants空でも壊れない
- web: 科研費列ソート・万円表示・詳細カード
- 実API検証（appid有効化後）: 機関検索パラメタ確定・件数確認・初回同期

## スコープ外（YAGNI）

- 分担課題の按分計算、年度別配分額の時系列、審査区分・キーワード分析
- researchmap連携（機関申請が通ったら再訪）
- 前身機関（大阪市立大学・大阪府立大学）名義の課題（大阪公立大学名義のみ）
