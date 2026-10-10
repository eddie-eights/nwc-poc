# Temporal の履歴を RDS に残す（036）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

いまの Temporal は `temporalio/temporal:1.9.1`（CLI のイメージ）を `server start-dev` で起こし、SQLite を `/tmp/temporal.db` に書いている（`IaC/terraform/aws-managed/workflow/ecs.tf`）。Fargate のタスクが入れ替わると進行中のワークフローの状態と履歴が消えるので、サービスは `deployment_minimum_healthy_percent = 0 / deployment_maximum_percent = 100` で止めてから起こす形にしてある。この状態を、Nautobot の RDS for PostgreSQL（`pipeline/nautobot` の `aws_db_instance.nautobot`、PostgreSQL 17、`db.t4g.micro`）に Temporal の 2 つのデータベースを足して本番モードの `temporal-server` で起こす形に替える。

| 項目 | いま | このサイクルのあと |
|---|---|---|
| イメージ | `temporalio/temporal:1.9.1`（CLI）をミラー | `temporalio/server:1.32.1` を元に自前でビルド（`docker/images/temporal-server/`） |
| 永続化 | SQLite（コンテナの中。タスクと一緒に消える） | RDS for PostgreSQL 18 の `temporal` と `temporal_visibility` |
| RDS の版 | PostgreSQL 17（`db_engine_version` の既定） | PostgreSQL 18（RDS の最新。18.6 まで出ている） |
| UI | `start-dev` の同梱 UI（8233） | `temporalio/ui:2.55.0` を別コンテナで（8233 のまま） |
| タスク | 2 コンテナ（temporal, worker） | 3 コンテナ（temporal, ui, worker） |
| デプロイ | 0 % / 100 %（止めてから起こす） | 100 % / 200 %（新しいタスクが上がってから古いのを止める） |
| namespace `default` | `start-dev` が自動で作る | temporal コンテナの entrypoint が作る |

ユーザーの決定（経緯は `design-log.md` の Round 0）:

- RDS のロールは Nautobot の master（`nautobot`）を使い回さず、専用の `temporal` を作る。パスワードは `ops/up.sh` が SSM の SecureString `/<prefix>/temporal/db-password` に作る
- デプロイは無停止（min 100 / max 200）。履歴が RDS にあるので 2 タスクが短時間並んでも壊れない
- Temporal UI は 8233 のまま（SSM のポートフォワードの手順と SG の行を変えない）
- 初期化（ロールと DB の作成、スキーマ、namespace）は **temporal のサーバーのコンテナ自身が entrypoint でやる**（案 c）。init コンテナや db-init を増やさない
- RDS の PostgreSQL は 17 → **18** に上げる（ユーザーの提案。RDS の最新の大版で 18.6 まで出ている。Nautobot 3 は 12 以降なら動く。`pipeline/nautobot/database.tf` に `allow_major_version_upgrade` は無いので、17 の RDS が残っている環境で `up.sh` を打つと nautobot の apply が止まる。先に `ops/down.sh` で消してから `up.sh`（AWS は確認後すぐ down.sh する運用なので、通常は 17 が残らない））。psql クライアントも 18 に揃える
- このサイクルでは AWS で動かさない。手元の docker と文字列の検査で確かめ、残りの修正が全部終わったら AWS の動作確認と修正を 1 回でまとめてやる（QUEUE の最後の AWS 動作確認の行）

## 設計方針

### 構成

```
ECS サービス <prefix>-workflow（Fargate、1 タスク、min 100 / max 200）
└ タスク（cpu 1024 / memory 2048）
   ├ temporal  … ECR <prefix>-temporal:<dir_tag>（temporalio/server:1.32.1 + temporal-sql-tool + temporal CLI + psql 18）
   │            entrypoint: ロール/DB の作成 → スキーマ → temporal-server start → namespace default
   │            7233（gRPC）はタスクの外に出さない。healthCheck は namespace default の describe
   ├ ui        … ECR <prefix>-temporal-ui:2.55.0（temporalio/ui のミラー）TEMPORAL_ADDRESS=127.0.0.1:7233、TEMPORAL_UI_PORT=8233
   └ worker    … いまのまま（TEMPORAL_ADDRESS=localhost:7233）。dependsOn temporal HEALTHY
        │
        └ RDS <prefix>-nautobot（PostgreSQL 18）… DB temporal / temporal_visibility（owner: ロール temporal）
```

### temporal のサーバーのイメージ（新規 `docker/images/temporal-server/`）

`docker/images/temporal/Dockerfile` は **worker** のイメージなので触らない。新しいディレクトリを作る。

```dockerfile
ARG TEMPORAL_SERVER_VERSION=1.32.1
FROM temporalio/admin-tools:${TEMPORAL_SERVER_VERSION} AS tools
FROM temporalio/server:${TEMPORAL_SERVER_VERSION}
# RDS for PostgreSQL と同じ大版の psql（ops/up-common.sh の POSTGRES_MAJOR から --build-arg で渡す。RDS の版を変えたらそこだけ直す）
ARG POSTGRES_MAJOR=18
ARG POSTGRESQL_CLIENT_PACKAGE=postgresql${POSTGRES_MAJOR}-client
USER root
RUN apk add --no-cache ${POSTGRESQL_CLIENT_PACKAGE}
COPY --from=tools /usr/local/bin/temporal-sql-tool /usr/local/bin/temporal /usr/local/bin/
COPY --from=tools /etc/temporal/schema /etc/temporal/schema
COPY --chmod=755 entrypoint.sh /etc/temporal/entrypoint-rds.sh
# 公式の entrypoint が読む dynamic config の既定のパス（無いと temporal-server が起動しない）
COPY dynamicconfig.yaml /etc/temporal/config/dynamicconfig/docker.yaml
USER temporal
ENTRYPOINT ["/etc/temporal/entrypoint-rds.sh"]
```

確認済みの事実（2026-10-10、手元の docker で実物を見た）:

- `temporalio/server:1.32.1` は Alpine 3.24.1、user `temporal`（uid 1000）、`/usr/bin/nc` と `getent` がある。entrypoint は `/etc/temporal/entrypoint.sh` で、中身は下に引用
- `temporalio/admin-tools:1.32.1` の `/usr/local/bin` は `tdbg temporal temporal-cassandra-tool temporal-elasticsearch-tool temporal-sql-tool`。スキーマは `/etc/temporal/schema/postgresql/v12/{temporal,visibility}/versioned`。psql も python も無い
- Alpine 3.24.1 の `apk search` に `postgresql16-client-16.15-r0` / `postgresql17-client-17.11-r0`（3634 KiB）/ `postgresql18-client-18.6-r0` がある。RDS は `var.db_engine_version` の既定を `17` → `18` に上げるので 18（18.6-r0）を入れる。RDS 側も 18.6 が最新（AWS の PostgreSQL のリリースノートで確認、2026-10-10）
- `temporalio/ui:2.55.0` の設定は `/home/ui-server/config/docker.yaml` のテンプレートで、`temporalGrpcAddress: {{ env "TEMPORAL_ADDRESS" | default "127.0.0.1:7233" }}`、`port: {{ env "TEMPORAL_UI_PORT" | default "8080" }}`
- `temporalio/auto-setup` は非推奨。`temporalio/temporal`（CLI）の `start-dev` は SQLite しか使えない

### entrypoint の流れ（`docker/images/temporal-server/entrypoint.sh`、sh）

公式の `setup-postgres.sh` と `create-namespace.sh` を 1 本に畳み、ロールと DB の作成だけ psql で足す。全部べき等で、2 回目以降の起動でも同じ道を通る。

1. 必須の env を `: "${POSTGRES_SEEDS:?}"` の形で確かめる（`POSTGRES_SEEDS` / `POSTGRES_USER` / `POSTGRES_PWD` / `NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD`）。`DB_PORT` の既定 5432、`DBNAME` の既定 `temporal`、`VISIBILITY_DBNAME` の既定 `temporal_visibility`、`SQL_TLS_ENABLED` の既定 `true`、`DEFAULT_NAMESPACE` の既定 `default`
2. `nc -z -w 10 "$POSTGRES_SEEDS" "$DB_PORT"` を 30 回 × 5 秒まで待つ（公式は 1 回だけ。RDS が起きる前にタスクが上がることがあるので回す）
3. **psql を Nautobot の master で打ち、ロールと DB を作る**（`PGSSLMODE` は TLS 有効なら `require`、無効なら `prefer`。master のパスワードは `PGPASSWORD`、ロールのパスワードは psql の中で `\getenv` で読む。どちらもコマンドラインに載せない）。`\gexec` で「無いときだけ作る」形にする。`CREATE DATABASE ... OWNER temporal` には master が `temporal` のメンバーである必要があるので先に `GRANT`
   ```sql
   -- PGPASSWORD="$NAUTOBOT_DB_PASSWORD" psql -X -q -v ON_ERROR_STOP=1 -U "$NAUTOBOT_DB_USER" -d nautobot -v role="$POSTGRES_USER" -v db=... -v vdb=...
   \getenv pw POSTGRES_PWD
   SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
     WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
   SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'pw') \gexec
   -- PostgreSQL 16 以降、CREATEROLE で作った側（master）は ADMIN だけ持ち SET が無いので SET で見る
   SELECT format('GRANT %I TO CURRENT_USER', :'role')
     WHERE NOT pg_has_role(CURRENT_USER, :'role', 'SET') \gexec
   SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')
     WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db') \gexec   -- temporal と temporal_visibility の 2 回
   ```
   続けて `temporal_visibility` に接続し、master のまま `CREATE EXTENSION IF NOT EXISTS btree_gin;` を打つ（visibility のスキーマが要る。RDS では master が `rds_superuser` なので作れる。ロール `temporal` では作れない可能性があるので master で先に作る）
4. **スキーマ**は `temporal-sql-tool` を**ロール `temporal`** で打つ。`setup-schema -v 0.0` は `schema_version` を 0.0 に戻してしまうので、**初回だけ**（`psql -tAc "SELECT to_regclass('schema_version') IS NOT NULL"` が `f` のとき）打つ。`update-schema` は毎回打つ（べき等）
   ```sh
   SQL_TLS=$SQL_TLS_ENABLED SQL_TLS_DISABLE_HOST_VERIFICATION=true SQL_PASSWORD=$POSTGRES_PWD \
   temporal-sql-tool --plugin postgres12 --ep "$POSTGRES_SEEDS" -p "$DB_PORT" -u "$POSTGRES_USER" --db "$DBNAME" update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
   # visibility も同じ形で -d .../visibility/versioned
   ```
   （`temporal-sql-tool` の TLS は `--tls` [`$SQL_TLS`]、`--tls-disable-host-verification` [`$SQL_TLS_DISABLE_HOST_VERIFICATION`]、パスワードは `--pw` [`$SQL_PASSWORD`]。admin-tools 1.32.1 の `--help` で確認）
5. **namespace を背景で作る**: `( … ) &` で `nc -z 127.0.0.1 7233` → `temporal operator cluster health --address 127.0.0.1:7233` → `temporal operator namespace describe -n "$DEFAULT_NAMESPACE" || temporal operator namespace create -n "$DEFAULT_NAMESPACE" --retention 72h` を 30 回 × 5 秒まで回す（公式 `create-namespace.sh` と同じ）。失敗したら `exit 1` ではなくログに出すだけ（本体のプロセスは落とさない。healthCheck が namespace を見るので ECS 側で UNHEALTHY になる）
6. `exec /etc/temporal/entrypoint.sh`（公式の entrypoint。`BIND_ON_IP=0.0.0.0` が env で来るので `TEMPORAL_BROADCAST_ADDRESS` を `getent hosts $(hostname)` で埋めて `temporal-server start`）

公式の引用（`temporalio/docker-builds` の `docker/entrypoint.sh`、`docker/setup-postgres.sh`、`docker/create-namespace.sh`。2026-10-10 に取得。実装は字面をこれに合わせる）:

```sh
# entrypoint.sh
: "${BIND_ON_IP:=$(getent hosts "$(hostname)" | awk '{print $1;}')}"
export BIND_ON_IP
if [ "${BIND_ON_IP}" = "0.0.0.0" ] || [ "${BIND_ON_IP}" = "::0" ]; then
    : "${TEMPORAL_BROADCAST_ADDRESS:=$(getent hosts "$(hostname)" | awk '{print $1;}')}"
    export TEMPORAL_BROADCAST_ADDRESS
fi
exec temporal-server start
```

```sh
# setup-postgres.sh（抜粋）
nc -z -w 10 ${POSTGRES_SEEDS} ${DB_PORT:-5432}
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal create
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal setup-schema -v 0.0
temporal-sql-tool --plugin postgres12 --ep ${POSTGRES_SEEDS} -u ${POSTGRES_USER} -p ${DB_PORT:-5432} --db temporal update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
# temporal_visibility も同じ 3 行（-d は .../visibility/versioned）
```

```sh
# create-namespace.sh（抜粋）
while ! nc -z -w 10 "$SERVER_HOST" "$SERVER_PORT"; do … sleep "$SLEEP_SECONDS"; done
while :; do if temporal operator cluster health --address "$TEMPORAL_ADDRESS"; then break; fi; … done
while :; do
  if temporal operator namespace describe -n "$NAMESPACE" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then break; fi
  if temporal operator namespace create -n "$NAMESPACE" --address "$TEMPORAL_ADDRESS" >/dev/null 2>&1; then break; fi
  … sleep "$SLEEP_SECONDS"
done
```

`create` は打たない（DB は psql で owner 付きで作る。`temporal-sql-tool create` は owner を指定できず、PostgreSQL 15 以降は public スキーマの CREATE が owner 以外に無いので、ロール `temporal` が owner でないとスキーマが入らない）。

### サーバーの設定（env。`temporalio/server` の `config_template_embedded.yaml` の postgres12 の枝で確認）

| env | 値 | 根拠 |
|---|---|---|
| `DB` | `postgres12` | テンプレートの枝の選択 |
| `POSTGRES_SEEDS` / `DB_PORT` | nautobot の remote state の `db_address` / `db_port` | 接続先 |
| `POSTGRES_USER` | `temporal` | 専用ロール |
| `POSTGRES_PWD` | ECS の `secrets`（SSM `/<prefix>/temporal/db-password`） | 値は Terraform にも ログにも出ない |
| `DBNAME` / `VISIBILITY_DBNAME` | `temporal` / `temporal_visibility` | 既定と同じ。visibility の接続先・ユーザー・パスワードは `POSTGRES_*` に落ちる（テンプレート 123〜130 行） |
| `SQL_TLS_ENABLED` | `true` | RDS PostgreSQL 15 以降は `rds.force_ssl=1` が既定 |
| `SQL_HOST_VERIFICATION` | `false` | RDS の CA をイメージに入れない（検証は AWS の動作確認で。入れるなら `SQL_CA`） |
| `SQL_MAX_CONNS` / `SQL_MAX_IDLE_CONNS` | `4` / `2`（変数） | 既定 20/20 はサービス 4 つ分で 80。`db.t4g.micro` の `max_connections` は約 110 で Nautobot も使う |
| `SQL_VIS_MAX_CONNS` / `SQL_VIS_MAX_IDLE_CONNS` | `2` / `1`（変数） | 同上（既定 10/10） |
| `NUM_HISTORY_SHARDS` | `4` | 既定。**一度決めたら変えられない**（変えるなら DB を作り直す） |
| `BIND_ON_IP` | `0.0.0.0` | ui と worker が同じタスク内から `127.0.0.1:7233` で届くため。公式 `pg.yml` と同じ |
| `LOG_LEVEL` | `warn` | いまの `--log-level warn` と同じ |
| `NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD` | `nautobot` / ECS の `secrets`（SSM `/<prefix>/nautobot/db-password`） | entrypoint のロール/DB 作成だけに使う |

ポート: frontend 7233 / 6933、internal-frontend 7236 / 6936、history 7234 / 6934、matching 7235 / 6935、worker 7239 / 6939。同じタスクの中は自分の IP 宛てで SG を通らない。portMappings には出さず、SG は `workflow` → `workflow` の自分宛てだけ開ける（デプロイ中に新旧 2 タスクが同じ DB の `cluster_membership` で 1 つのクラスターになり、shard を渡し合うときに通る）。

### ECS のタスク定義（`workflow/ecs.tf`）

- `temporal`: image `local.temporal_image`（ビルドしたタグ）、`essential = true`、portMappings 無し、env と secrets は上の表、`healthCheck = { command = ["CMD-SHELL", "temporal operator namespace describe -n default --address 127.0.0.1:7233 >/dev/null 2>&1 || exit 1"], interval = 10, timeout = 5, retries = 6, startPeriod = 180 }`（HEALTHY = namespace まで出来た、の意味にする。worker が namespace の無い frontend に繋いで落ちる競合を無くす）。awslogs の prefix は `temporal` のまま
- `ui`: image `local.temporal_ui_image`、`essential = false`（UI が落ちてもワークフローは止めない）、portMappings 8233、env `TEMPORAL_ADDRESS=127.0.0.1:7233`、`TEMPORAL_UI_PORT=8233`、awslogs prefix `ui`、`dependsOn = [{ containerName = "temporal", condition = "HEALTHY" }]`
- `worker`: いまのまま。`dependsOn` を `START` → `HEALTHY` に変える
- preconditions に nautobot の remote state（`local.nautobot_db_address != ""` と `local.nautobot_db_password_arn != ""`）を足す
- サービス: `deployment_minimum_healthy_percent = 100`、`deployment_maximum_percent = 200`、SQLite のコメントを消す。`depends_on` に `aws_iam_role_policy.execution_db_passwords` を足す

### IAM（`workflow/iam.tf`）

`aws_iam_role_policy.execution_neo4j` と同じ形で `execution_db_passwords` を足す（`ssm:GetParameters` を `/<prefix>/temporal/db-password` と `/<prefix>/nautobot/db-password` の 2 つの ARN に）。ARN は `local.neo4j_password_arn` と同じ組み立て方。

### SG（`base/core/security_groups.tf` の `sg_flows`）

- `{ from = "workflow", to = "nautobot_db", protocol = "tcp", port = 5432, why = "PostgreSQL - Temporal の履歴（temporal / temporal_visibility）" }`
- `{ from = "workflow", to = "workflow", protocol = "tcp", port = 6933, to_port = 6939, why = "Temporal membership between the tasks during a deployment" }`
- `{ from = "workflow", to = "workflow", protocol = "tcp", port = 7233, to_port = 7239, why = "Temporal gRPC between the tasks during a deployment" }`
- 10 行目のコメント「開けていないもの: Temporal の gRPC 7233」は「タスクの外には開けていない」に直す（self のみ）

### ECR（`base/ecr`）

`workflow_repositories` に `temporal-ui` を足し、`temporal_ui_repository_url` を出力する。`temporal_repository_url` の説明を「ビルドした temporalio/server ベースのイメージ」に直す。

### nautobot の出力（`pipeline/nautobot/outputs.tf`）

`db_address`（`aws_db_instance.nautobot.address`）、`db_port`、`db_password_parameter`（`local.secret_parameters["db-password"]`、SSM のパラメータ名）を足す。値は出さない。

### ops（`ops/up-common.sh` / `ops/up.sh` / `ops/oss/up.sh`）

- `TEMPORAL_TAG=1.9.1` → `TEMPORAL_SERVER_VERSION=1.32.1` と `TEMPORAL_UI_TAG=2.55.0`、`POSTGRES_MAJOR=18`（RDS の大版と psql の大版の正。nautobot の apply に `-var db_engine_version=$POSTGRES_MAJOR`、temporal-server のビルドに `--build-arg POSTGRES_MAJOR=$POSTGRES_MAJOR` で渡す。`variables.tf` の既定も同値にして、テストで一致を見る ── `TEMPORAL_TAG` と `temporal_image_tag` の既定の一致の検査と同じ手筋）
- `mirror_temporal()` → `mirror_temporal_ui()`（`temporalio/ui:$TEMPORAL_UI_TAG` を `<prefix>-temporal-ui:$TEMPORAL_UI_TAG` に）と `build_temporal_server <tag>`（`docker buildx build --platform linux/arm64 --build-arg TEMPORAL_SERVER_VERSION=$TEMPORAL_SERVER_VERSION --build-arg POSTGRES_MAJOR=$POSTGRES_MAJOR -t $REG/$PREFIX-temporal:<tag> --push docker/images/temporal-server/`）。タグは `dir_tag "$TEMPORAL_SERVER_VERSION" docker/images/temporal-server`（`ops/lab-common.sh`。中身が変われば別のタグになり、ECR のタグの上書き禁止と両立する）
- `ensure_temporal_secrets()`: `ensure_secret "/$PREFIX/temporal/db-password" password "Temporal database password (created by $OPS_DIR/up.sh)"`。workflow の apply の前に呼ぶ（`ensure_nautobot_secrets` と同じ場所の並び）
- 8-5 の `tf_apply workflow` に `-var "temporal_image_tag=$TEMPORAL_SERVER_IMAGE_TAG"` を足す。`ecr_has` の判定も新しいタグで
- `ops/down.sh` と `ops/oss/down.sh` は workflow の destroy に `-var "temporal_image_tag=destroy"` を足す（`temporal_image_tag` の既定を外したので、渡さないと `No value for required variable` で destroy が止まる。値は destroy では使われない）。SSM のパラメータは `delete_up_ssm_params` が `/<prefix>/` の ManagedBy のものを消すので新しいものも消える。workflow は nautobot より先に destroy する並びのまま

### 変数（`workflow/variables.tf`）

- `temporal_image_tag`: 既定を外し（up.sh が渡す）、説明を「`ops/up.sh` が `docker/images/temporal-server/` からビルドして push したタグ」に
- `temporal_ui_image_tag`: 新規、既定 `2.55.0`
- `temporal_sql_max_conns = 4` / `temporal_sql_max_idle_conns = 2` / `temporal_visibility_max_conns = 2` / `temporal_visibility_max_idle_conns = 1`
- `task_cpu` / `task_memory` / `desired_count` / `lambda_az_num` の説明から SQLite と「Temporal dev server」を消す。`desired_count` は 0..1 のまま（履歴は RDS にあるので 2 以上にも出来るが、このサイクルでは増やさない。NUM_HISTORY_SHARDS=4 で複数タスクにしても意味が薄い）

### 費用

Fargate のタスクの大きさは変えない（cpu 1024 / memory 2048 のまま。ui コンテナは 50 MB 程度）。RDS は Nautobot のものを共用するので増えない。`docs/deploy.md` の WORKFLOW の時間課金は変わらない。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `docker/images/temporal-server/Dockerfile` | 新規（上） |
| `docker/images/temporal-server/entrypoint.sh` | 新規（上の流れ） |
| `IaC/terraform/aws-managed/workflow/ecs.tf` | 3 コンテナ、healthCheck、min/max、precondition、depends_on |
| `IaC/terraform/aws-managed/workflow/iam.tf` | `execution_db_passwords` |
| `IaC/terraform/aws-managed/workflow/locals.tf` | nautobot の remote state、`nautobot_db_address` / `nautobot_db_port` / `nautobot_db_password_arn` / `temporal_db_password_arn` / `temporal_ui_image` |
| `IaC/terraform/aws-managed/workflow/variables.tf` | 上の変数 |
| `IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf` | `db_address` / `db_port` / `db_password_parameter` |
| `IaC/terraform/aws-managed/pipeline/nautobot/variables.tf` / `terraform.tfvars.example` | `db_engine_version` の既定と例を `17` → `18` |
| `docs/architecture/resources/nautobot.md` 16 行 | 「RDS の PostgreSQL 17」→ 18 |
| `IaC/terraform/aws-managed/base/ecr/main.tf` / `outputs.tf` | `temporal-ui` |
| `IaC/terraform/aws-managed/base/core/security_groups.tf` | `sg_flows` 3 行とコメント |
| `ops/up-common.sh` / `ops/up.sh` / `ops/oss/up.sh` | 版、ビルド、ミラー、secret、apply の var |
| `tests/test_workflow.py` | 522〜527（start-dev → 3 コンテナと healthCheck）、792〜799（ミラー → ビルドとミラー、版の一致）、1433〜1436（SG） |
| `tests/test_analytics.py` | `EXPECTED_FLOWS` に 3 行、139 行の件数 |
| `docs/workflow.md` / `docs/data-stores.md` / `docs/architecture/workflow.md` / `docs/architecture/core.md` / `docs/architecture/resources/temporal.md` / `docs/architecture/resources/nautobot.md` / `docs/deploy.md` / `docs/oss-variant.md` / `docs/faq-fukuda-nwc-poc.md` / `README.md` | SQLite / start-dev の記述を RDS に置き換える（`grep -rn 'SQLite\|start-dev' docs README.md | grep -v cycles | grep -v grafana` で洗う。Grafana の SQLite は別物なので残す） |
| `docs/cycles/QUEUE.md` | PM が書く（エンジニアは触らない） |

OSS 版（`IaC/terraform/oss/...`）は aws-managed へのファイル単位の symlink なので .tf の変更はそのまま効く。`ops/oss/up.sh` だけ別ファイル。

## 再利用するもの

- `ensure_secret`（`ops/up-common.sh` 112 行）と `ensure_nautobot_secrets` の並び
- `dir_tag`（`ops/lab-common.sh` 50 行）と `build_syslog_ng` の `--build-arg` の形、`ecr_has`
- `aws_iam_role_policy.execution_neo4j`（`workflow/iam.tf`）と `local.neo4j_password_arn` の組み立て
- `sg_flows` の `to_port` の書き方（`msk → msk 9092〜9098` の行）
- `ephemeral "aws_ssm_parameter"` / `local.secret_parameters` / `local.secret_arns`（`pipeline/nautobot/database.tf`）
- 公式の `entrypoint.sh` / `setup-postgres.sh` / `create-namespace.sh`（上に引用）

## 実装ステップ

1. `docker/images/temporal-server/{Dockerfile,entrypoint.sh}` を書く。手元で `docker build`（arm64 でそのまま）して、`postgres:18` のコンテナと繋いで起動する（下の検証 2）
2. `pipeline/nautobot/outputs.tf` に 3 出力、`base/ecr` に `temporal-ui`、`base/core/security_groups.tf` に 3 行
3. `workflow/{locals,variables,iam,ecs}.tf` を直す。`terraform init -backend=false && terraform validate` を workflow / nautobot / ecr / core で通す
4. `ops/up-common.sh` / `ops/up.sh` / `ops/oss/up.sh` を直す（`bash -n` を通す）
5. `tests/test_workflow.py` / `tests/test_analytics.py` を新しい形に書き換える。`python3 -m pytest tests -q` がすべて通過
6. docs を直す（上の一覧。`docs/architecture/resources/temporal.md` は書き直し。「ひとことで」「知見」の「タスクは 1 つだけ」「タスクが入れ替わると消える」を今の形に）

## 検証方法

合格条件は出力まで書く。

1. **文字列の検査**: `python3 -m pytest tests -q` が `すべて通過`（failed 0）。`grep -rn 'start-dev\|SQLite' IaC ops docs README.md | grep -v cycles | grep -v grafana` が 0 行
2. **手元の docker で起動と永続化**（PG は TLS 無しで `SQL_TLS_ENABLED=false`。RDS の TLS は AWS の動作確認で）:
   ```sh
   docker network create t036
   docker run -d --name pg --network t036 -e POSTGRES_PASSWORD=masterpw -e POSTGRES_USER=nautobot -e POSTGRES_DB=nautobot postgres:18
   docker build -t nwc-temporal-server:test docker/images/temporal-server/
   docker run -d --name temporal --network t036 -e DB=postgres12 -e POSTGRES_SEEDS=pg -e POSTGRES_USER=temporal -e POSTGRES_PWD=temporalpw \
     -e NAUTOBOT_DB_USER=nautobot -e NAUTOBOT_DB_PASSWORD=masterpw -e SQL_TLS_ENABLED=false -e BIND_ON_IP=0.0.0.0 -e LOG_LEVEL=warn nwc-temporal-server:test
   ```
   - 90 秒以内に `docker exec temporal temporal operator namespace describe -n default --address 127.0.0.1:7233` が exit 0 で `NamespaceInfo.Name  default` を出す
   - `docker exec pg psql -U nautobot -tAc "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname LIKE 'temporal%'"` が `temporal|temporal` と `temporal_visibility|temporal` の 2 行
   - `docker exec temporal temporal workflow start --type Hello --task-queue q --workflow-id wf1 --address 127.0.0.1:7233` → `docker restart temporal` → 再起動後に `temporal workflow describe -w wf1` が `Status  Running` を返す（履歴が RDS 側に残っている。worker は無いので Running のまま）
   - 2 回目の起動のログに `setup-schema` が出ず（`docker logs temporal 2>&1 | grep -c setup-schema` が `0`）、`update-schema` のエラーが無い（べき等）
   - `docker run --rm --network t036 -e TEMPORAL_ADDRESS=temporal:7233 -e TEMPORAL_UI_PORT=8233 -p 8233:8233 temporalio/ui:2.55.0` を上げて `curl -s -o /dev/null -w '%{http_code}' localhost:8233/` が `200`
   - 片付け: `docker rm -f -v temporal pg; docker network rm t036; docker rmi nwc-temporal-server:test`
3. **Terraform**: 4 つのルートで `terraform init -backend=false && terraform validate` が `Success!`。`grep -c 'name = "temporal"\|name = "ui"\|name = "worker"' workflow/ecs.tf` が 3、`deployment_minimum_healthy_percent = 100` と `deployment_maximum_percent = 200` がある
4. **ops**: `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` が exit 0。`grep -n 'TEMPORAL_SERVER_VERSION=\|TEMPORAL_UI_TAG=' ops/up-common.sh` が `1.32.1` と `2.55.0`。`temporal_ui_image_tag` の既定と `TEMPORAL_UI_TAG` が一致（テストで検査）。`POSTGRES_MAJOR` と nautobot の `db_engine_version` の既定が一致し、Dockerfile の `ARG POSTGRES_MAJOR=` の既定とも一致（テストで検査）
5. AWS での動作確認は**しない**（ユーザーの決定。QUEUE の最後の AWS 動作確認の行で、下のリスクをまとめて潰す）

## 未確定事項とリスク

AWS で動かすまで決着しないものは、QUEUE の最後の AWS 動作確認の行に持ち越す（PM が書く）。

1. **PostgreSQL 18 は Temporal の動作確認済みの一覧に無い**（公式は 12〜16 を謳う。17 でも同じ）。スキーマは標準 SQL なので動く見込みで、手元の `postgres:18` で `update-schema` が通ることを検証 2 で見る。AWS では RDS の 18 でも同じものが通るかを見る。駄目なら RDS の `db_engine_version` を 16 に下げる（Nautobot は 16 でも動く）
2. **RDS の TLS**: `SQL_TLS_ENABLED=true` + `SQL_HOST_VERIFICATION=false` で繋がる見込み（Go の `lib/pq` の `sslmode=require` 相当）。`temporal-sql-tool` の `--tls` も同じ。psql は `PGSSLMODE=require`。駄目なら RDS の CA（`global-bundle.pem`）をイメージに入れて `SQL_CA` を指す
3. **`btree_gin` を master で作る**: RDS の master は `rds_superuser` で `CREATE EXTENSION` できる見込み。手元の `postgres:18` では master が superuser なので差が出ない。AWS の動作確認で見る
4. **Fargate の `hostname` と `getent`**: awsvpc では `/etc/hosts` にタスクの IP とホスト名が入るので `TEMPORAL_BROADCAST_ADDRESS` が埋まる見込み。空になると membership が `0.0.0.0` を広告して自分に繋がらない。駄目なら ECS のメタデータ（`$ECS_CONTAINER_METADATA_URI_V4`）から IP を取る分岐を entrypoint に足す
5. **無停止デプロイ中の 2 タスク**: 同じ DB・同じ `NUM_HISTORY_SHARDS` の新旧 2 タスクが短時間並ぶ（`cluster_membership` で 1 つのクラスターになる。SG の self の行はこのため）。Temporal は shard の所有を DB の `shards` テーブルで取り合うので壊れないが、切り替わる数十秒はワークフローの進みが遅れる。PoC では許容
6. **接続数**: `db.t4g.micro` の `max_connections` 約 110 に対し、Temporal は 5 サービス × (4 + 2) = 30、デプロイ中は 60、Nautobot が約 10〜20。収まる見込みだが、AWS の動作確認で `pg_stat_activity` を見る
7. **master のパスワードがサーバーのコンテナに渡る**（案 c の代償）。読めるのは `ecs:ExecuteCommand` できる人と SSM の `GetParameter` できる人で、いまの Nautobot のコンテナと同じ範囲。ロールと DB が出来たあとは使わない
8. **UI 2.55.0 と server 1.32.1 の組み合わせ**: UI は frontend の gRPC を叩くだけで、2.5x 系は 1.2x〜1.3x に対応。手元の docker で 200 を見る（検証 2）
9. **worker の起動の競合**: healthCheck を namespace の describe にしたので、HEALTHY の時点で namespace はある。`startPeriod = 180` 秒の間に RDS が起きていないと UNHEALTHY → タスクの作り直しになる。初回の `up.sh` は nautobot の apply（RDS 作成）が先に終わっているので問題は無い
10. **`temporal operator namespace create` の `--retention`**: 既定 72h（CLI 1.x は `--retention` が任意）。履歴を長く残したいなら変数にする。このサイクルでは 72h
<!-- artifact: /Users/eight/Documents/repo/artifacts/projects/nwc-poc-architecture.html -->
