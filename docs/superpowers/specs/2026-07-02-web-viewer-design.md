# OMU研究者比較 閲覧Web MVP 設計書

作成日: 2026-07-02
ステータス: 承認済み（実装前）
前提: Phase 1 データ基盤（`docs/superpowers/specs/2026-07-02-omu-researchers-data-design.md`）が main にマージ済みで、`db/researchers.db` に researchers 3,876 / works 6,208 / researcher_metrics が入っている。

## 目的

`db/researchers.db` の研究者比較データを、まず本人/研究室内（LAN・Tailscale経由）でブラウザ閲覧できるようにする。読み取り専用。一般公開・認証はスコープ外（公開時は既存 Caddy に reverse_proxy ブロックを1つ足すだけの構成にしておく）。

## アプローチ（承認済み: 案B）

FastAPI + Jinja2 のサーバーサイドレンダリング3ページ。JSビルドなし、CSS1枚。
検討済み代替案: Datasette（コード0行だが比較UXを作り込めず発展性なし）、Streamlit（速いが公開サービスに不向きで作り直しになる）。

## 技術スタック

- 追加依存（MedAIDigestと同じピン留め）: `fastapi==0.115.6`, `uvicorn==0.34.0`, `jinja2==3.1.5`
- DBアクセスは既存の `db/models.py`（SQLAlchemy）を再利用。読み取り専用
- SQLite は WAL 有効済みのため、週次 sync 書き込み中でも閲覧可能

## ディレクトリ構成（追加分）

```
web/
├── __init__.py
├── app.py        # FastAPIアプリ生成＋3ルート（薄く保つ）
├── queries.py    # DB読み取りクエリ関数（appから分離しユニットテスト可能に）
├── templates/
│   ├── base.html       # 共通レイアウト＋フッター（データ出典・最終同期日）
│   ├── ranking.html
│   ├── researcher.html
│   └── search.html
└── static/
    └── style.css
deploy/
└── omu-researchers-web.service   # systemd unit（インストール手順はREADME）
```

## ページ仕様

### 1. `/` ランキング

- `researcher_metrics` × `researchers` を JOIN
- クエリパラメタ:
  - `sort`: `fwci_mean`（デフォルト） | `total_citations` | `top10pct_count` | `works_count_3y`。降順固定。不正値はデフォルトにフォールバック
  - `min_works`: 最低論文数フィルタ。デフォルト5（少数論文の高FWCIがランキング上位を占拠するのを防ぐ）。0以上の整数、不正値は5
  - `page`: 1始まり、100件/ページ。非数値・1未満・1,000,000超は1にフォールバック（最終ページ超は空リスト表示、500は出さない）
- 表示列: 順位、氏名（詳細へリンク）、3年論文数、総被引用数、FWCI平均、FWCI中央値、top10%論文数、筆頭数、責任著者数
- ソート時に NULL（FWCI欠損）は末尾
- ヘッダにソート切替リンクと min_works 入力フォーム、総件数表示

### 2. `/researchers/{openalex_id}` 研究者詳細

- 存在しないIDは404ページ
- メトリクスカード: researcher_metrics 全列＋ h_index（researchers由来）
- 外部リンク: OpenAlex 著者ページ（`https://openalex.org/{id}`）、ORCID（あれば）
- 論文リスト: authorships 経由でウィンドウ内 works を被引用数降順に全件。列: タイトル（DOIリンク、DOI無ければOpenAlexリンク）、掲載誌、発行日、被引用数、FWCI、top10%/top1%バッジ、著者位置（first/corresponding表示）

### 3. `/search?q=` 検索

- `display_name` の大文字小文字を無視した部分一致（`name_ja` が入れば OR 条件に追加 — Phase 2 でクエリ側は変更不要になるよう最初から `name_ja` も検索対象に含める）
- 結果は氏名・3年論文数・FWCI平均の表（詳細リンク付き）。0件時はメッセージ表示
- `q` 未指定/空なら検索フォームのみ表示

## 表示上の設計判断

- デフォルトソートは生被引用数でなく **FWCI平均**（分野バイアス対策。データ基盤設計書の方針を踏襲）
- 全ページ共通フッター: 「OpenAlex収録分に基づく（直近3年ローリング）・最終同期: YYYY-MM-DD」— 最終同期日は `sync_state` の `last_synced_at`（works）から取得。「全業績」と断定する表現は使わない
- FWCI等の欠損値は「–」表示。FWCIは小数2桁丸め
- UIラベルは日本語。氏名は Phase 1 ではローマ字（`display_name`）、Phase 2 以降 `name_ja` があれば優先表示（テンプレートは `name_ja or display_name` で最初から対応）

## 配信

- 起動: `uv run uvicorn web.app:app --host 0.0.0.0 --port 8100`（ポート8100は2026-07-02時点で空きを確認済み）
- LAN/Tailscale の IP から `http://<host>:8100/` で閲覧
- systemd unit `deploy/omu-researchers-web.service`（medaidigest-web.service と同形式: User=d-ueda, WorkingDirectory=/srv/apps/researchers, Restart=always）。インストールは README 記載の手動手順（sudo が要るため自動化しない）
- 公開時（スコープ外）: Caddyfile にサブドメイン1ブロック追加で対応可能な構成

## エラー処理

- 研究者ID不存在 → 404テンプレート
- クエリパラメタ不正 → デフォルトへフォールバック（500を出さない）
- DBファイル不存在 → 起動時に明示的なエラーメッセージで fail fast

## テスト

- `tests/test_web.py`: FastAPI TestClient ＋ 一時ファイルのSQLite（fixtureでseed）
  - ランキング: ソート切替が効く・min_worksフィルタが効く・NULL FWCIが末尾
  - 詳細: 正常表示（論文リスト含む）・不存在IDで404
  - 検索: 部分一致ヒット・0件・空クエリ
  - フッターの最終同期日表示
- 既存の `-m "not smoke"` スイートに統合（実APIアクセスなし）

## スコープ外（YAGNI）

- 認証・一般公開・独自ドメイン
- 研究者同士の並列比較ビュー、グラフ/チャート、CSVエクスポート
- 分野（subfield）別ランキング（Phase 2 で部局が入ってから検討）
- JSON API（テンプレート返却のみ）
