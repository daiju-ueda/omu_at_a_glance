# 大阪公立大学 研究者・業績データ基盤 設計書

作成日: 2026-07-02
ステータス: レビュー待ち

## 目的

研究者比較サービスのバックエンドとして、大阪公立大学（OMU）の研究者一覧と直近3年（2023-07-01以降、ローリング）の論文・被引用データを、無料ソースのみで網羅的に収集・更新するデータ基盤を作る。比較の第一軸は「論文＋被引用数」。UI/サービス層は本設計のスコープ外（データ層が固まってから別途設計）。

## 検証済みの前提（2026-07-02 実測）

- OpenAlex 機関ID `I4387152983`（ROR: 01hvx5h04）= 大阪公立大学
- 直近3年の works: 6,222件 / 現所属 authors: 3,876人
- works レコードに `cited_by_count`, `fwci`, `citation_normalized_percentile`（top 1% / top 10% フラグ含む）, `primary_topic`（分野・サブ分野）が含まれることを確認
- OpenAlex は CC0・APIキー不要・polite pool（mailto付与）で 10 rps / 100k req/日 — 本規模では十分
- researchmap API は無料だが利用申請＋トークン必要（Phase 3）
- OMU公式研究者情報DB https://kyoiku-kenkyudb.omu.ac.jp/ （UEDB/JSP、API無し）が名簿の「正」。2026-07-02 時点で検索サービス一時停止中 — Phase 2 で復旧確認の上、利用規約を確認してから取得

## 採用アプローチ（承認済み: 案B・段階実装）

- **Phase 1 (MVP)**: OpenAlex のみで研究者・論文・被引用を全件取得し、比較可能な状態にする
- **Phase 2**: OMU公式総覧（または researchmap 所属検索）から公式名簿（日本語氏名・部局・職位）を取得し、ORCID→氏名の順で OpenAlex 著者に名寄せ
- **Phase 3（任意）**: researchmap API で和文業績を補完、KAKEN API で科研費を追加

## 技術スタック

MedAIDigest の慣習に合わせる: Python >=3.10,<3.13 / uv / httpx / SQLAlchemy 2.x。
DB は SQLite（単一ファイル、この規模では十分。SQLAlchemy 経由なので将来 Postgres へ移行可能）。

## ディレクトリ構成

```
researchers/
├── pyproject.toml
├── collector/
│   ├── openalex.py      # OpenAlex sync クライアント（cursor paging, backoff）
│   ├── roster.py        # Phase 2: 公式名簿取得
│   └── matcher.py       # Phase 2: 名寄せ（ORCID → 氏名照合）
├── db/
│   ├── models.py        # SQLAlchemy モデル
│   └── researchers.db   # SQLite（gitignore）
├── scripts/
│   ├── sync.py          # CLI: full / incremental sync
│   └── metrics.py       # 研究者別集計の再計算
├── tests/
└── docs/
```

## データモデル

- **researchers**: `openalex_id` (PK), `display_name`, `name_ja` (Phase 2), `orcid`, `department` / `position` (Phase 2), `h_index`, `works_count`, `is_official_roster` (Phase 2 フラグ), `raw_json`
- **works**: `openalex_id` (PK), `doi`, `title`, `publication_date`, `venue`, `type`, `cited_by_count`, `fwci`, `cnp_value` / `is_top1pct` / `is_top10pct`, `topic` / `subfield`, `is_oa`, `raw_json`
- **authorships**: `work_id` + `researcher_id` (複合PK), `author_position`, `is_corresponding`
- **researcher_metrics**（sync 後に再計算する集計テーブル）: 3年 works 数、総被引用数、FWCI 平均/中央値、top10% 論文割合、筆頭/責任著者数
- **sync_state**: ソース別の最終同期時刻・カーソル

`raw_json` を必ず保持し、スキーマ変更時に API 再取得なしで再パースできるようにする。

## 同期フロー

1. **authors sync**: `last_known_institutions.id:I4387152983` を cursor paging で全件 upsert
2. **works sync**: `institutions.id:I4387152983,from_publication_date:<今日-3年>` を全件 upsert、authorships を展開（OMU所属の著者行のみ researchers に紐付け、外部共著者は authorships に author_id のみ保持）
3. **metrics 再計算**: researcher_metrics を洗い替え
4. **incremental**: 2回目以降は `from_updated_date:<前回sync>` で差分のみ。被引用数は OpenAlex 側で随時更新されるため、月1回は works の full re-sync で被引用数を洗い替え
5. cron: 週1回 incremental ＋ 月1回 full（GPU 不使用のため GPU 調整レイヤーは不要）

## エラー処理

- 429/5xx は指数バックオフ（httpx + リトライ）。mailto=ai.labo.ocu@gmail.com を必ず付与
- sync は cursor を sync_state に永続化し中断再開可能に
- 取得完了後に件数を `meta.count` と突合し、乖離があればログに警告（silent failure 禁止）

## 名寄せ（Phase 2）

1. 公式名簿の ORCID と OpenAlex authors の ORCID が一致 → 自動確定
2. 残りは氏名ローマ字（ヘボン式ゆれ・姓名順ゆれを正規化）＋ OMU 所属制約で候補生成
3. 候補が複数 or ゼロのものは `match_review` テーブルに積み、目視確認キューとする（無理に自動確定しない）

## 比較指標に関する設計判断

生の被引用数は分野バイアスが大きいため、サービスの比較軸は **FWCI と citation_normalized_percentile（分野正規化済み）を第一級指標**としてスキーマに保持する。生被引用数も併記する。

集計を提示する際の表現は「OpenAlex 収録分に基づく」と明記する（網羅性はソースの被覆に依存するため、全業績と断定しない）。

## テスト

- fixture JSON（実 API レスポンスの縮小版）でパーサ・upsert・metrics 集計・氏名正規化のユニットテスト
- 小ページ（per-page=5）で実 API を叩くスモークテスト（CI ではスキップ可能なマーク付き）

## スコープ外（YAGNI）

- Web UI / API サーバ（データ層確定後に別設計）
- 特許・書籍・講演（必要になったら Phase 3 以降で researchmap から）
- 他大学対応（機関IDをパラメタ化しておくにとどめる）
