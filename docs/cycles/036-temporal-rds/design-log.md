# Temporal の履歴を RDS に残す（036）の設計ログ

## Round 0（2026-10-10、PM と ユーザー）

### きっかけ

QUEUE の「Temporal の履歴を RDS に残す」。`server start-dev` の SQLite はコンテナの中にあり、Fargate のタスクが入れ替わると進行中のワークフローの状態が消える。ユーザーは以前に「PoC は RDS for PostgreSQL、Aurora は本番で」と決めている。

### 調査で分かったこと（設計の根拠）

- `temporalio/temporal`（CLI）の `start-dev` は SQLite しか使えない。`temporalio/auto-setup` は非推奨。本番モードは `temporalio/server`（Alpine、`temporal-server start`）と `temporalio/admin-tools`（`temporal-sql-tool`、`temporal` CLI、スキーマ）に分かれている
- `temporalio/server:1.32.1` と `admin-tools:1.32.1` のどちらにも psql も python も無い。`temporal-sql-tool` は DB の `create` は出来るがロールは作れず、owner も指定できない
- PostgreSQL 15 以降は public スキーマの CREATE が owner 以外に無い（RDS の PostgreSQL 17 も同じ）
- RDS PostgreSQL 15 以降は `rds.force_ssl=1` が既定
- visibility のスキーマは `CREATE EXTENSION btree_gin` を要る
- `db.t4g.micro` の `max_connections` は約 110。Temporal の既定のプール（20/20、10/10）はサービス 5 つ分で溢れる

### 質問と回答

- **Q1 RDS のロール**: (a) Nautobot の master を使い回す / (b) 専用ロール `temporal` → **(b)**。Nautobot の DB と権限の境目を作る
- **Q2 デプロイ**: (a) いまの 0 % / 100 % のまま / (b) 100 % / 200 % の無停止 → **(b)**。履歴が RDS にあるので 2 タスクが並んでも壊れない
- **Q3 UI のポート**: (a) 8233 のまま / (b) UI の既定 8080 に → **(a)**。SSM のポートフォワードの手順と SG の行を変えない
- **Q4 AWS の動作確認**: このサイクルでやる / 後でまとめて → **後でまとめて**（「全ての修正が終わったら一度に動確と修正を済ませて終わらせたい」）。QUEUE の最後の AWS 動作確認の行で
- **Q5 初期化（ロール・DB・スキーマ・namespace）の置き場**: (a) admin-tools の init コンテナ / (b) `db-init` の自前イメージ / (c) temporal のサーバーのコンテナ自身が entrypoint でやる → **(c)**（「cでいい」）
- **psql の版**: RDS の PostgreSQL と同じ大版に揃える（Alpine 3.24.1 に 16 / 17 / 18 がある）。PM は 17（いまの既定）と答えたが、ユーザーが「RDS の PostgreSQL は 18.6 が最新だからそれでいいのでは？」と提案し、AWS のリリースノート（`PostgreSQLReleaseNotes/postgresql-versions.html`）で 18.6 が出ているのを確かめて **RDS を 18 に上げ、psql も `postgresql18-client`** にした。版の正は `ops/up-common.sh` の `POSTGRES_MAJOR` 1 か所

### 却下した案

- **`temporalio/auto-setup`**: 非推奨で、1.2x 以降の更新が止まっている。ユーザーの「auto-setup は使えない？」への答え
- **(a) admin-tools を init コンテナに**: `temporal-sql-tool` はロールを作れず、admin-tools に psql も無い。ロール作成だけ別の手段が要り、コンテナが 4 つになる
- **(b) `db-init` の自前イメージ**: ユーザーの「db-init でスキーマと namespace もつくればいいのでは？」。出来るが、namespace の作成は frontend が上がったあとなので init コンテナからは出来ない（dependsOn の向きが逆）。(c) なら 1 本の entrypoint に畳める
- **`start-dev` + EFS**: SQLite を EFS に置けば残るが、SQLite の NFS 上のロックは公式に非推奨。本番モードの検証にもならない
- **Aurora**: ユーザーの決定で本番向け。PoC は RDS for PostgreSQL
- **RDS の CA をイメージに入れて `SQL_HOST_VERIFICATION=true`**: 正しいが AWS で動かさないこのサイクルでは確かめられない。`false` で入れて、AWS の動作確認で CA 入りに上げる余地を残す
