# OMU Researchers Data Pipeline

大阪公立大学の研究者一覧と直近3年の論文・被引用データをOpenAlexから収集するデータ基盤。
設計書: `docs/superpowers/specs/2026-07-02-omu-researchers-data-design.md`

## セットアップ

    uv sync

## 使い方

    uv run python scripts/sync.py   # 全量同期＋メトリクス再計算

## cron（推奨）

    # 週1回 全量同期（月曜 06:00、被引用数の更新も反映）
    0 6 * * 1 cd /srv/apps/researchers && uv run python scripts/sync.py >> logs/sync.log 2>&1

## テスト

    uv run pytest -m "not smoke"   # オフライン
    uv run pytest -m smoke         # 実APIスモーク

## 注意

- 集計値は「OpenAlex収録分に基づく」もの。全業績の断定表現は使わない
- 研究者間比較には生被引用数でなく fwci_mean / top10pct_count を第一に使う
- OpenAlexの from_updated_date フィルタはPremium限定（2026-07確認）のため差分同期は使わない。全量同期でも約55リクエスト・数分で完了する
