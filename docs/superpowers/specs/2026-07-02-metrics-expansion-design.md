# 評価指標拡充（Tier 1）＋ランキング既定表示 設計書

作成日: 2026-07-02
ステータス: 承認待ち（推奨案で先行着手）
前提: Phase 1 データ基盤＋閲覧Web MVP が main にマージ済み。

## 背景・目的

1. ランキング既定表示が「3年で論文5本以上」フィルタにより725人になっており、全体を見たい用途に合わない → **既定を1本にし、母数の内訳を表示する**
2. 比較の評価軸を増やしたい → **追加API取得ゼロで、保存済み raw_json から計算できる Tier 1 指標を一括追加する**

実測済みの根拠（2026-07-02）:
- works_count_3y の分布: ≥1本 3,338人 / ≥3本 1,113人 / ≥5本 725人 / ≥10本 348人
- works.raw_json の authorships に `countries`（例: ["JP"]）と `institutions[].type`（例: "company", "healthcare"）が保存済み
- researchers.raw_json の `summary_stats` に `i10_index` と `2yr_mean_citedness` が保存済み
- works.type に preprint 493 / dataset 35 / software 6 等が存在

## スコープ外（今回やらない）

- KAKEN（要appid申請）・researchmap（要API申請）・特許・臨床試験・altmetrics — 申請が通り次第の別スプリント
- 分野内順位などの相対指標のUI（データはtop_subfieldとして持つ）

## 追加カラム

### works（parse時に raw_json から計算）

- `n_authors: int` — authorships の件数（0あり）
- `is_intl_collab: bool` — いずれかの著者の `countries` に JP 以外が含まれる
- `is_corp_collab: bool` — いずれかの著者の `institutions[].type` が "company"
- `is_authors_truncated: bool` — OpenAlex側のauthorships打ち切りフラグ（100人超の大規模共著論文で著者数が不明になる）

### researchers（parse時に summary_stats から）

- `i10_index: int`（欠損は0）
- `two_yr_mean_citedness: float | None`

### researcher_metrics（compute_metrics で集計、全てウィンドウ内 works ベース）

- `top1pct_count: int` — is_top1pct の論文数
- `fractional_works: float` — Σ(1/n_authors)。n_authors=0 の論文は1として扱う
- `fractional_citations: float` — Σ(cited_by_count/n_authors)。同上
- `avg_authors: float | None` — 平均著者数（論文0本なら None）
- `intl_collab_rate: float | None` — is_intl_collab の割合（論文0本なら None）
- `corp_collab_rate: float | None` — 同、is_corp_collab
- `oa_rate: float | None` — 同、is_oa
- `preprint_count: int` — type=="preprint"
- `dataset_software_count: int` — type in ("dataset", "software")
- `unique_coauthors: int` — ウィンドウ内 works の共著者（authorships の author_id）のユニーク数から本人を除いた数
- `top_subfield: str | None` — ウィンドウ内 works の subfield の最頻値（同数なら辞書順で先のもの、全てNULLなら None）

fractional_works / fractional_citations / avg_authors / 各rate は小数4桁で丸めて保存。

`is_authors_truncated` のworksは著者数不明のためfractional_works/fractional_citations/avg_authorsの計算から除外（他の指標には含める）。

## マイグレーション方針: DBは使い捨て、再構築で対応

SQLiteの既存テーブルに `create_all` は列を追加しない。migration コードは書かず、**DBファイルを削除して full sync で再構築する**（全データはOpenAlex APIから約55リクエスト・数分で再取得可能。raw_json 含め完全に復元される）。README にもこの運用（スキーマ変更時は削除→再同期）を明記する。

## 閲覧Webの変更

### ランキング（`/`）

- **既定 `min_works=1`**（変更前は5）。`min_works` の仕様行は「0〜1,000,000、不正値・範囲外は **1**」に変更
- 総件数表示を「全{フィルタなし人数}人中 {フィルタ後人数}人を表示」形式に（`ranking()` がフィルタなし総数も返す）
- 列に **top1%** と **被引用(著者数補正)**（=fractional_citations、ソート可）を追加
- ソート可能キーに `fractional_citations` を追加（ホワイトリスト5キーに）

### 研究者詳細（`/researchers/{id}`）

メトリクスカードに追加: top1%論文 / 被引用(著者数補正) / 論文数(著者数補正) / 平均著者数 / ユニーク共著者数 / 国際共著率 / 産学連携率 / OA率 / preprint数 / データ・SW数 / 主分野 / i10指数 / 2年平均被引用。
率は「63%」形式（小数0桁）、欠損は「–」。

### 検索（`/search`）

変更なし。

## テスト

- parse: countries/institutions type/n_authors/summary_stats の抽出（国際・企業あり/なし/欠損フィールド）
- metrics: fractional（n_authors=0含む）、rate（論文0本→None）、top_subfield 最頻値と同数タイブレーク、unique_coauthors（本人除外・重複共著者の畳み込み）
- web: 既定min_works=1、内訳表示文言、fractional_citations ソート、詳細カードの新項目表示
- 実DB再構築後に件数・新列の非NULL率をスポットチェック

## 表示上の注意（継承）

- 生被引用数と著者数補正値を併記し、既定ソートは引き続き FWCI平均
- フッターの「OpenAlex収録分に基づく」表記は不変
