# Cycle 005 oss-on-ecs 実装の記録: マネージドを OSS に置き換えた環境を作る

この記録は、実装が main に入ったあとに PM セッション（fable-5.1）が git の履歴と頑健化ループの結果から書いた。実装はエンジニアセッション 4 つが worktree で分担し、PM がローカルの main へマージした。

## Round 1

実装モデル: エンジニアセッション（opus 5.5 以下の既定。版の記録は無い）/ effort: 未記録
セルフレビュー（`/robust`）: PM の fable-5.1 / effort: high

### 実装

- commit の範囲: `d7616d6`（切り替えの環境変数。無ければ今と 1 文字も違わない）〜 `dea7aaf`（`oss/ops/up.sh` が Grafana を作り、agent のイメージを Neo4j のドライバー入りで作る）。design.md「実装ステップ」1〜7 の順。
- 変更ファイル 145（`git diff --name-only d7616d6^..78be1e4 -- oss/ spark/ neo4j/ ops/ tests/test_oss.py tests/test_oss_ops.py agent/ workflow/ web/ grafana/`）。中身は design.md「変更対象ファイル」。

### 設計から逸脱した点

- Kafbat UI に Spark のコンシューマーの lag は出ない（Spark の Kafka ソースは consumer group を作らず、offset を checkpoint に持つ）。design.md の検証 5 に注記した。
- Nautobot の Job から Neo4j への同期は未接続（Nautobot のイメージに Neo4j のドライバが無い）。lab の定義からの同期 `ops/sync-graph.sh --oss` で代える。BACKLOG。

### テスト

`bash ops/check.sh`（2026-10-07、`78be1e4`。`terraform/` と `oss/terraform/` の fmt と validate、スクリプトの構文、tests/）:

```
通過 324 / 失敗 0

すべて通過
```

### セルフレビュー

`/robust` を PM が回した（Round 1〜5、アンチテーゼ 2 回 + ジンテーゼ。commit `a02fe8a` `9d96d0d` `fec47f1` `5543b78` `8aad8de`）。

- 直したもの: GDS の写し（射影）を必ず drop し、射影が失敗したら drop を打たず、Neo4jError だけ drop する。Neo4j の失敗を `agent/topology.py` が握り、Web の編集も `graph.errors()` で受ける。`ops/sync-graph.sh --oss`。`oss/ops/up.sh` の Neo4j のレイヤーは requirements・pip の platform・python の版のハッシュで作り直し、`LAYER_PYVER` と `sync.tf` の runtime を突き合わせる。Ctrl-C でも drop。
- 未解消の指摘: なし。
- 最終報告に回したもの: BACKLOG の 4 件（マネージド版と並べて立てる、Neo4j の healthCheck、status の Lambda のメモリ、`topology.py` のログの文言）。

### AWS

design.md「AWS」8 項目のうち 7 項目が通った（1 の「並べて立つ」は Fargate の vCPU の上限で未確認）。結果は design.md「AWS で確かめたこと」「down.sh のあと」。
