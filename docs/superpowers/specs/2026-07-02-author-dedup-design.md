# OpenAlex重複著者の解決（author dedup） 設計書

作成日: 2026-07-02
ステータス: 承認済み（「重複著者の解決に着手」の指示による）
前提: Phase 1〜部局統合まで main にマージ済み。

## 背景

OpenAlexは同一人物に複数の著者IDを割り当てることがある（実例: 中村博亮氏に5つの "Hiroaki Nakamura" レコード）。これにより：
1. 本人の業績が複数レコードに分裂し、ランキングで**全指標が過小評価**される
2. 名寄せ（roster/KAKEN）が「候補複数」で169行が曖昧判定になっている（未マッチ893行の21%、最大の改善余地）

## 危険性と設計原則

同姓同名の**別人**（роман字が同じだけの斉藤/斎藤等）を統合するのは、このプロジェクトが最も避けてきた誤同定事故になる。**名前の一致だけでは絶対にマージしない**。証拠ベースの保守的ルールのみ：

### マージ判定（同一 normalize_name グループ内でのunion-find）

- **結合する証拠**（いずれか）:
  - (a) 同一の非NULL ORCID
  - (b) ウィンドウ内worksの**共著者IDの重なりが2人以上**（同じラボ・分野で活動している強い証拠。別人の同姓同名が2人以上の共通共著者を持つ確率は極小）
  - (c) 同一workを共有（同じ論文に両IDが著者として載っている）
- **分離する証拠**（結合証拠より優先）:
  - 異なる非NULL ORCID同士は**決して**同一クラスタにしない（ORCID衝突があればそのペアの結合をブロック。ブロックされたペアを含むクラスタはunion時に分割不可なので、(b)(c)の証拠があってもORCID相違ペアは結合しない）
- 異なる normalize_name 間では一切マージしない
- 正準レコード（canonical）= クラスタ内で works_count（全期間）最大。同数はopenalex_id辞書順で先

## データモデル

- `researchers.canonical_id: str | None` — NULLなら正準。エイリアスは正準のopenalex_idを指す（1段のみ、チェーンなし）
- マージは**論理統合**: works/authorshipsは元のauthor_idのまま、集計・表示・名寄せの層で正準に畳む。OpenAlex側が将来自力で統合/分割しても、週次全洗い替えで自然に追従する（毎sync再計算・冪等）

## 統合の波及

### collector/dedup.py（新規）

- `apply_dedup(session) -> int` — 全researchersを対象にグループ化→union-find→canonical_id更新（全再計算・冪等）。マージされた人数（エイリアス数）を返す。実行順: works sync後・kaken/roster名寄せ前
- エイリアスの name_ja/department/position/is_official_roster はクリアし、正準側に引き継ぐ（正準側が未設定の場合のみコピー）

### compute_metrics

- author_id→canonicalの写像で集計。メトリクス行は**正準のみ**（エイリアスは researcher_metrics 行を持たない→ランキング・検索・順位母数から自動的に消える）
- works_count等は**DISTINCT work単位**で集計（同一workに複数エイリアスが載る稀ケースの二重計上防止。first/correspondingはOR、著者位置は「firstが1つでもあればfirst扱い」）
- unique_coauthors も共著者IDを正準に畳んでから数える

### 名寄せ（kaken / roster）

- display_name / name_ja の索引は**正準レコードのみ**から構築（エイリアス除外）→ 同一人物の分裂による曖昧が解消
- name_ja等の書き込みも正準へ

### Web

- ランキング・検索・比較・順位: metricsが正準のみになるため自動対応。検索の対象も正準のみ（`canonical_id IS NULL` フィルタ）
- 研究者詳細: エイリアスIDへのアクセスは正準へリダイレクト（302）。正準ページのworksリストはエイリアス含む全authorshipsから収集（DISTINCT work、被引用数順）
- フッター注記等は不変。total_all は正準数になる（実質の研究者数に近づく＝より誠実）

## 検証（実データ・マージ品質が最重要）

- マージ結果のサンプル検査: 大きいクラスタ上位（例: Hiroaki Nakamura×5）が単一人物に畳まれたか、works・FWCI合計が合算されたか
- **誤マージ探知**: マージされたクラスタのうち、roster/KAKENの**異なる漢字**が両エイリアスに紐づいていた形跡がないか（あれば即ロールバック対象としてルール見直し）
- 名寄せ改善の実測: roster matched（490→どこまで伸びるか）とKAKEN matched（1,171→）を前後比較して報告
- ランキング上位の変動確認（分裂していた大物の順位が上がるはず）

## テスト

- union-find: ORCID結合・ORCID相違ブロック・共著者重なり閾値（1人では結合しない）・同一work結合・名前が違えば不結合・canonical選定
- 波及: metricsのDISTINCT work集計・エイリアスにmetrics行なし・名寄せ索引が正準のみ・エイリアスname_ja引き継ぎ
- web: エイリアス→正準リダイレクト・正準ページに全works・検索にエイリアス非表示

## スコープ外（YAGNI）

- OpenAlexへのマージ報告（フィードバック機能）
- 機械学習的な曖昧マッチ（証拠ルールのみ）
- 異名間（旧姓・改名）のマージ
