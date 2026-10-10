# temporal の初期化を一回きりのタスクに分け、サーバーのタスク定義から master のパスワードを外す（042）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。ベースは main の `fa99bdf`。

## 背景

- QUEUE 140。いまは temporal のコンテナの entrypoint（`docker/images/temporal-server/entrypoint.sh`）が起動のたびに Nautobot の RDS の master（`NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD`）でロール `temporal` と DB を作り、ロール `temporal` でスキーマを入れ、背景で namespace を作ってから `exec tini` で temporal-server を起こす（036 → 039 → 040）。
- master のパスワードは ECS の secrets でサーバーのタスク定義（`IaC/terraform/aws-managed/workflow/ecs.tf:69`）に渡る。039 で「使った直後に `unset`」にしたが、守れたのは entrypoint のあとに起こすプロセスの `/proc/<pid>/environ` だけで、次の 3 つの経路からは外せない（036 のリスク 7、039 のコールドレビュー Round 1 の Should fix 2、`docs/architecture/resources/temporal.md` の制約の表「ECS Exec と healthCheck」）:
  1. ECS Exec のシェル（タスク定義の env を引き継ぐ）
  2. healthCheck が 10 秒ごとに起こす `temporal operator namespace describe` のプロセス（タスク定義の env を引き継ぎ、uid temporal なのでタスクの中のコードから読める）
  3. `exec tini` までの手順 1〜3 の間の PID 1 の sh の `/proc/1/environ`
- 根本対策は「master のパスワードをサーバーのタスク定義に渡さない」こと。master が要る手順（ロールと DB の作成、`CREATE EXTENSION btree_gin`）を**別のタスク定義（一回きりのタスク）**に移し、サーバーのタスク定義の secrets を `POSTGRES_PWD`（ロール `temporal`）だけにする。これで 3 つの経路がすべて消える。

### 現物で確認した事実（2026-10-10 に PM が読んだ。`fa99bdf`）

- `workflow/ecs.tf:38-91`: temporal のコンテナ。env に `NAUTOBOT_DB_USER=nautobot`（64 行）、secrets に `POSTGRES_PWD`（68 行。`local.temporal_db_password_arn`）と `NAUTOBOT_DB_PASSWORD`（69 行。`local.nautobot_db_password_arn`）。healthCheck は `temporal operator namespace describe -n default --address 127.0.0.1:7233`（interval 10 / timeout 5 / retries 6 / startPeriod 180）。`stopTimeout = 120`（040）。ui（95-96 行）と worker（118-119 行）は temporal の HEALTHY を待つ。`enable_execute_command = true`（187 行）。
- `workflow/locals.tf:214-215`: `nautobot_db_password_arn` は nautobot の state の `db_password_parameter`（SSM の SecureString）、`temporal_db_password_arn` は `/<prefix>/temporal/db-password`。`workflow/iam.tf:49-63`: 実行ロールのポリシー `execution_db_passwords` が 2 つの ARN に `ssm:GetParameters`。
- `entrypoint.sh`（124 行）: 手順 1 nc で DB を待つ → 2 master の psql でロール・DB・`btree_gin` → `unset NAUTOBOT_DB_PASSWORD` → 3 `sql_tool`（`setup-schema -v 0.0` は `schema_version` が無い初回だけ、`update-schema` は毎回）→ 4 `/etc/temporal/namespace-rds.sh … &` → 5 `exec /sbin/tini -- /etc/temporal/entrypoint.sh`。TLS の導出（`PGSSLMODE` と `sql_tool_skip_host_verify`）は 38-59 行。trap は 36 行（040）。
- `Dockerfile`: `temporalio/server` に admin-tools の `temporal-sql-tool` / `temporal` と psql 18、tini を足し、`entrypoint.sh` → `/etc/temporal/entrypoint-rds.sh`、`namespace.sh` → `/etc/temporal/namespace-rds.sh`。`USER temporal`。スキーマは `/etc/temporal/schema/postgresql/v12/{temporal,visibility}/versioned/`。
- `ops/up.sh:1248-1272`（8-5）と `ops/oss/up.sh:580-597`（8）: `ensure_temporal_secrets` → `tf_apply workflow …` → `aws ecs wait services-stable`（2 回まで）→ タスクの IP を表示。OSS 版の `IaC/terraform/oss/workflow` はマネージド版へのリンクなので、タスク定義は 1 つの `ecs.tf` で両方に効く。
- `ops/` と `IaC/terraform/aws-managed/` に `ecs run-task` の前例は無い（`grep -rn "run-task"` が空）。Nautobot の migrate は web のコンテナの entrypoint がやる（`pipeline/nautobot/nautobot.tf:118-119`）。
- `base/core/security_groups.tf:81`: workflow の SG → nautobot_db の 5432（Temporal の履歴）。init のタスクも workflow の SG で起こせば同じ行で届く。
- `tests/test_workflow.py:591-`: `docker/images/temporal-server/` が 4 ファイルであること、entrypoint の内容（`\getenv`、`pg_has_role … 'SET'`、`setup-schema` が if の中に 1 回、`unset` より後で namespace と sql_tool を起こす、背景は namespace-rds.sh だけ、trap の挙動、末尾が `exec /sbin/tini …`）、Dockerfile の COPY を見る check が約 80 個。いまの通過は 381。
- `temporal-sql-tool update-schema` は `schema_version` 表（列は `version_partition` / `db_name` / `creation_time` / `curr_version` / `min_compatible_version`。**実装時にイメージの `schema/postgresql/v12/temporal/versioned/v1.0/*.sql` で列名を確かめる**）の `curr_version` を `versioned/` の最新の版に上げる。

## 設計方針

1. **タスク定義を 2 つにする。** `workflow/ecs.tf` に `aws_ecs_task_definition.init`（family `<prefix>-workflow-init`。Fargate ARM64、cpu 256 / memory 512、実行ロールはサーバーと同じ `aws_iam_role.execution`、タスクロールは無し）を足す。コンテナは `init` 1 つで、イメージはサーバーと同じ `local.temporal_image`、`entryPoint = ["/etc/temporal/init-rds.sh"]`、env はサーバーの DB 系（`POSTGRES_SEEDS` / `DB_PORT` / `POSTGRES_USER` / `DBNAME` / `VISIBILITY_DBNAME` / `SQL_TLS_ENABLED` / `SQL_HOST_VERIFICATION` / `NAUTOBOT_DB_USER`）、secrets は `POSTGRES_PWD` と `NAUTOBOT_DB_PASSWORD` の 2 つ、healthCheck と portMappings は無し、ログは同じロググループで `awslogs-stream-prefix = "init"`、`stopTimeout = 120`。DB 系の env は locals にまとめてサーバーと init の両方で使う（二重に書かない）。
   - サーバーのタスク定義（`aws_ecs_task_definition.workflow`）からは `NAUTOBOT_DB_USER` の env と `NAUTOBOT_DB_PASSWORD` の secrets を外す。secrets は `POSTGRES_PWD` だけになる。
   - `iam.tf` の `execution_db_passwords` はそのまま（実行ロールを両方のタスク定義で使うので 2 つの ARN が要る）。コメントだけ「init のタスクが NAUTOBOT_DB_PASSWORD を、サーバーのタスクが POSTGRES_PWD を読む」に直す。
2. **init のタスクは `ops/up.sh` が `ecs run-task` で起こし、終わるまで待つ。** `ops/up-common.sh` に `run_temporal_init`（マネージド版と OSS 版で共通）を足し、`ops/up.sh` の 8-5 と `ops/oss/up.sh` の 8 で `tf_apply workflow …` の直後、`aws ecs wait services-stable` の前に呼ぶ。
   - 読む output（`workflow/outputs.tf` に足す）: `init_task_definition`（ARN。family:revision）、`task_subnet_id`、`task_security_group_id`（サーバーのサービスと同じ `local.subnet_id` / `local.workflow_sg_id`）、`init_logs_command`（`aws logs tail <group> --log-stream-name-prefix init`）。
   - 起こす前に `aws ecs list-tasks --cluster <cluster> --family <prefix>-workflow-init --desired-status RUNNING` で前の init（打ち直し、Ctrl-C の後）がまだ走っていないかを見て、あれば `aws ecs wait tasks-stopped` で止まるのを待ってから起こす（init が 2 つ同時に走ると、新しい DB では 2 つ目の `setup-schema -v 0.0` が 1 つ目の `update-schema` の結果を 0.0 に戻し、以後の `update-schema` が落ちる）。
   - `aws ecs run-task --launch-type FARGATE --cluster <cluster> --task-definition <arn> --network-configuration "awsvpcConfiguration={subnets=[<subnet>],securityGroups=[<sg>],assignPublicIp=DISABLED}"` → `aws ecs wait tasks-stopped`（1 回で最大 10 分。足りなければ 2 回まで）→ `describe-tasks` で `containers[0].exitCode` が 0 であることを見る。0 でなければ `die` で止める（`lastStatus` / `stopCode` / `stoppedReason` と `init_logs_command` を出す）。`run-task` の返りの `failures` が空でないときも `die`。
   - `up.sh` を打つたびに起こす（いまの entrypoint と同じく毎回べき等。ロールと DB は無いときだけ、`setup-schema` は初回だけ、`update-schema` は毎回）。イメージの版を上げたときのスキーマの更新もここで済む。
3. **サーバーの entrypoint は master を使わず、init が終わるのを待つ。** `entrypoint.sh` の手順 2（master の psql）と 3（`sql_tool`）を `init.sh` に移し、代わりに「init を待つ」を置く。順序は: 1 nc で DB を待つ → 2 **ロール `temporal` で両方の DB の `schema_version.curr_version` がイメージの `versioned/` の最新の版（ディレクトリ名の `v` を除いた最大。`sort -t. -k1,1n -k2,2n`）**以上**になるまで 10 秒ごとに待つ**（major と minor を数で比べる。イメージを古い版に戻すと DB の版がイメージより新しくなるが、Temporal 本体の VerifyCompatibleVersion は DB が新しい側を許すので、ここも許す。ロールが無い・DB が無い・表が無い・版が古い、は全部「まだ」。psql が失敗したときは stderr の 1 行目を log に出す（認証や TLS の失敗を「まだ無い」と誤解させない。パスワードは stderr に出ない）。**30 回（300 秒。healthCheck の `startPeriod` と同じ）で `exit 1`** ── ECS は startPeriod + 6 回 × 10 秒で UNHEALTHY として止めるので、ループがそれより長いと「回数を使い切った」の行が出ない。log は毎回 `初期化のタスク（ops/up.sh の 8-5 が run-task する <prefix>-workflow-init。ログは aws logs tail … --log-stream-name-prefix init）を待つ（i/30）`）→ 3 namespace を背景で → 4 `exec tini`。`NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD` の `:?` と `unset` は消す（env に無いのが正しい状態）。trap（040）は残す。
   - apply と run-task の順序に依存しない: サービスのタスクは init が終わるまで待つだけで、落ちない。手で `terraform apply` だけした環境でも、サーバーのログで「init を待つ」と分かる。
   - healthCheck の `startPeriod` を 180 → 300（上限）にする。init（イメージの取得 + スキーマ）とサーバー（イメージの取得 + 待ち）が同時に走るので、待ちの間の healthCheck の失敗でタスクが作り直されるのを減らす。作り直されても壊れない（べき等）。
4. **共通部分は 1 本に切り出す。** `docker/images/temporal-server/common.sh`（`/etc/temporal/common-rds.sh` に COPY。`entrypoint.sh` と `init.sh` が `.` で読む）に `log()`、trap、`PGHOST` / `PGSSLMODE` / `sql_tool_skip_host_verify` の導出（いまの 33-59 行。`tr` の小文字化と `SQL_CA` の fail fast を含む）、nc で DB を待つ `wait_for_db()` を置く。`init.sh`（`/etc/temporal/init-rds.sh`）は `set -eu` → common を読む → `:?` の検査（`POSTGRES_SEEDS` / `POSTGRES_USER` / `POSTGRES_PWD` / `NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD`）→ `wait_for_db` → いまの手順 2 と 3 をそのまま → `log "初期化が終わった"` → `exit 0`。パスワードはいままでどおりコマンドラインに載せない（`\getenv` と `SQL_PASSWORD`）。
   - `docker/images/temporal-server/` は 6 ファイル（`Dockerfile` / `common.sh` / `dynamicconfig.yaml` / `entrypoint.sh` / `init.sh` / `namespace.sh`）。Dockerfile は `COPY --chmod=755 init.sh /etc/temporal/init-rds.sh` と `COPY common.sh /etc/temporal/common-rds.sh` を足す。
5. **namespace の作成はサーバー側に残す**（QUEUE の文言は「ロール・DB・スキーマ・namespace」だが、namespace は動いている frontend（127.0.0.1:7233）にしか作れず、秘密も要らない。init のタスクへ移すにはサーバーの起動後に 7233 へ届く経路が要り、worker と ui の HEALTHY 待ちの意味も変わる。理由は `design-log.md` の Round 0）。healthCheck と ECS Exec もそのまま（master のパスワードを持たなくなるので経路として消える）。
6. **docs を揃える。** `docs/architecture/resources/temporal.md`（「初期化は temporal のコンテナの entrypoint がやる」の節を「ロール・DB・スキーマは init のタスク、namespace はサーバー」に書き直し、シークレットの行、制約の表の「ECS Exec と healthCheck」の行を「master のパスワードは init のタスクにしか渡らない。サーバーのタスクの ECS Exec / healthCheck / `/proc/1/environ` から見えるのはロール `temporal` のパスワードだけ」に、知見の行に 042 を足す）、`docs/deploy.md`（8-5 の行に init のタスク、Fargate の時間課金に「init は数分だけ」）、`docs/workflow.md:157-158` の困ったときの行（「init を待つ」のログと `init_logs_command`）、`ecs.tf` のコメント（43-45 / 66 / 71 / 79-81 行）、`docs/cycles/QUEUE.md` 147 に AWS で見るものを足す（下の検証 6）。036 のリスク 7 と 039 の design は過去の正本なので触らない。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `docker/images/temporal-server/common.sh` | 新規。log / trap / TLS の導出 / `wait_for_db` |
| `docker/images/temporal-server/init.sh` | 新規。master でロール・DB・`btree_gin`、ロールでスキーマ（いまの entrypoint の手順 2〜3 を移す） |
| `docker/images/temporal-server/entrypoint.sh` | 手順 2〜3 を「init を待つ」に替え、master の env の検査と `unset` を消す |
| `docker/images/temporal-server/Dockerfile` | `init.sh` と `common.sh` の COPY。先頭のコメント |
| `IaC/terraform/aws-managed/workflow/ecs.tf` | `aws_ecs_task_definition.init` を足す。サーバーから `NAUTOBOT_DB_USER` と `NAUTOBOT_DB_PASSWORD` を外す。DB 系の env を locals に。startPeriod 300。コメント |
| `IaC/terraform/aws-managed/workflow/locals.tf` | DB 系の env のリスト |
| `IaC/terraform/aws-managed/workflow/outputs.tf` | `init_task_definition` / `task_subnet_id` / `task_security_group_id` / `init_logs_command` |
| `IaC/terraform/aws-managed/workflow/iam.tf` | コメントだけ |
| `ops/up-common.sh` | `run_temporal_init` |
| `ops/up.sh` / `ops/oss/up.sh` | `tf_apply workflow` の直後に `run_temporal_init` |
| `tests/test_workflow.py` | 下の検証 1 |
| `docs/architecture/resources/temporal.md` / `docs/deploy.md` / `docs/workflow.md` / `docs/cycles/QUEUE.md` | 設計方針 6 |

## 再利用するもの

- いまの `entrypoint.sh` の手順 2〜3（psql の SQL、`sql_tool`、`setup-schema` の初回判定）と TLS の導出、trap（040）。中身は変えずに場所を移す。
- `ops/up-common.sh` の `tf_output` / `die` / `log`、`ops/up.sh` の `aws ecs wait` の「2 回まで待つ」の形（`:1259-1260`）。
- `docs/cycles/039-temporal-entrypoint-hardening/build.md` の手元の docker での確かめ方（postgres コンテナ + イメージの build、`/proc/[0-9]*/environ` の走査、`--health-cmd` で healthCheck のプロセスの env を見る）。

## 実装ステップ

1. `common.sh` / `init.sh` を作り、`entrypoint.sh` を設計方針 3 の形に、Dockerfile を設計方針 4 に直す。
2. `ecs.tf` / `locals.tf` / `outputs.tf` / `iam.tf` を設計方針 1 に直す。`terraform fmt` と `validate`（`./ops/check.sh` が回す）。
3. `ops/up-common.sh` に `run_temporal_init`、`ops/up.sh` と `ops/oss/up.sh` に呼び出しを足す（設計方針 2）。
4. `tests/test_workflow.py` を検証 1 のとおりに直す。既存の entrypoint の check のうち手順 2〜3 を見るものは `init.sh` を見る形に移し、消さない。
5. docs（設計方針 6）。
6. 検証 2〜5 を手元の docker で実行し、`build.md` に出力を貼る。

## 検証方法

1. **`tests/test_workflow.py`**（`./ops/check.sh` の末尾が「すべて通過」。通過数は 381 より増える）。少なくとも次の check を足す。各 check は、その行を壊した中身で失敗することを `build.md` に 1 つずつ示す（ファイルは書かず、メモリ上の文字列の入れ替えでよい）:
   - サーバーのタスク定義の temporal のコンテナの secrets は `POSTGRES_PWD` の 1 つだけで、env にも secrets にも `NAUTOBOT_DB_` で始まる名前が無い。
   - init のタスク定義がある（family `-workflow-init`、`entryPoint` が `/etc/temporal/init-rds.sh`、secrets が `POSTGRES_PWD` と `NAUTOBOT_DB_PASSWORD` の 2 つ、healthCheck と portMappings が無い、`awslogs-stream-prefix` が `init`、実行ロールがサーバーと同じ）。
   - `entrypoint.sh` に `NAUTOBOT_DB_PASSWORD` / `NAUTOBOT_DB_USER` / `-U "$NAUTOBOT` / `temporal-sql-tool` / `setup-schema` が無く、`schema_version` の `curr_version` を `versioned` の最新の版と数で比べる（以上）待ちが 30 回であり、namespace の行は待ちより後、末尾は `exec /sbin/tini -- /etc/temporal/entrypoint.sh`。
   - `init.sh` に既存の check（`\getenv pw POSTGRES_PWD`、`-v pw=` と `--pw` が無い、`pg_has_role(CURRENT_USER, :'role', 'SET')`、`setup-schema -v 0.0` が if の中に 1 回、`drop-schema` / `DROP` が無い、`btree_gin`）が通り、末尾が `exit 0`、背景（末尾 `&`）の行が無い。
   - `common.sh` に `log()`、trap（`exit 143`）、`tr '[:upper:]' '[:lower:]'`、`SQL_CA` の fail fast、`wait_for_db` があり、`entrypoint.sh` と `init.sh` が `. /etc/temporal/common-rds.sh` で読む。
   - Dockerfile が 6 ファイルを所定のパスに COPY する（`init.sh` → `/etc/temporal/init-rds.sh`、`common.sh` → `/etc/temporal/common-rds.sh`）。ディレクトリは 6 ファイル。
   - `ops/up.sh` と `ops/oss/up.sh` で `run_temporal_init` が `tf_apply workflow` より後、`aws ecs wait services-stable` より前の行にある。`ops/up-common.sh` の `run_temporal_init` が `ecs list-tasks`（前の init を待つ）→ `ecs run-task` → `ecs wait tasks-stopped` → `exitCode` の順で、0 以外で `die` する。
   - healthCheck の `startPeriod = 300`。
   - `iam.tf` のタスクのロールに `DenyNautobotParameters`（`ssm:GetParameter*` を `/<prefix>/nautobot/*` で Deny）がある。既存の `Parameters` が `/<prefix>/*` を許しているので、これが無いと ECS Exec のシェルから master のパスワードを `get-parameter` で引ける（Round 1 のセルフレビューの Must 1。`app/temporal` の ssm の呼び出しは `send_command` / `get_command_invocation` だけで、`/<prefix>/nautobot/*` を読む経路は無い）。
   - 待ちの `psql` が失敗したとき（偽の psql で認証失敗を模す）に stderr の 1 行目が log に出て、イメージより新しい版（例 1.20 に対して 1.19）で待ちが通る（Round 1 のセルフレビューの Should 2 / 4）。
2. **手元の docker で init が 2 回通る。** `postgres:18` のコンテナ（039 の build.md と同じ起こし方）に対して、ビルドしたイメージを `--entrypoint /etc/temporal/init-rds.sh` で起こす → exit 0、ログに「初回なので setup-schema」。もう 1 回起こす → exit 0、ログに setup-schema が無く update-schema だけ。`psql` で `schema_version.curr_version` が両 DB で同じ値（イメージの `versioned/` の最新と一致）。
3. **サーバーは init を待つ。** init を打つ前にサーバーのコンテナ（env に `POSTGRES_PWD` だけ。`NAUTOBOT_DB_*` は渡さない）を起こす → ログに「初期化のタスク … を待つ（1/60）」が出て落ちない。その状態で init を打つ → サーバーが先へ進み、`temporal operator namespace describe -n default` が通る。
4. **master のパスワードがどこにも無い。** 検証 3 のサーバーのコンテナで、起動中（待ちの間）と起動後の両方で `grep -lz NAUTOBOT_DB_PASSWORD /proc/[0-9]*/environ` が空（PID 1 の sh、tini、temporal-server、namespace-rds.sh、`--health-cmd` で立てた healthCheck のプロセスのすべて）。
5. **SIGTERM。** 検証 3 の待ちの間に `docker stop` → exit 143 とログ「SIGTERM を受けたので初期化を止める」（trap が common.sh から効く）。init の `update-schema` の最中の `docker stop` は 040 と同じく区切りで 143（手元で再現しにくければ読んだだけと書く）。
6. **AWS（このサイクルではやらない。QUEUE 147 に足す）:** `describe-task-definition` でサーバーのタスク定義の secrets が `POSTGRES_PWD` だけ、`<prefix>-workflow-init` のタスクが exit 0 で止まっている、ECS Exec でサーバーのタスクに入って `grep -lz NAUTOBOT_DB_PASSWORD /proc/[0-9]*/environ` が空、up.sh の 8-5 が init の待ちを含めて通る。

## 未確定事項とリスク

1. **`schema_version` の列名と値の形**（`curr_version` が `1.17` のように `v` 無しか）。実装時にイメージの `versioned/v1.0/*.sql` と手元の DB の実値で確かめる（検証 2）。違えば待ちの比較を実値に合わせる。
2. **`run-task` の権限。** `ops/up.sh` を打つ人（OWNER）に `ecs:RunTask` と実行ロールへの `iam:PassRole` が要る。いまも `ecs wait` / `describe-tasks` を同じ資格情報で打っているので admin 相当と見ている。足りなければ `run-task` の `AccessDenied` が `die` で出る。
3. **init とサーバーの同時起動。** サーバーのタスクは init の間（イメージの取得 + スキーマで 2〜5 分）を待つ。`startPeriod` 300 秒 + 6 回 × 10 秒を超えるとタスクが作り直される（壊れない）。AWS で 147 のときに時間を見る。
4. **手で `terraform apply` だけした環境**では init が走らず、サーバーは 5 分待って `exit 1` を繰り返す（ECS が作り直す）。ログの文言で分かるようにし、`docs/workflow.md` の困ったときに書く。
5. **PR の前の環境との差。** 既存の AWS 環境がある状態で apply すると、サーバーのタスク定義の新 revision（secrets が減る）でサービスが入れ替わる。ロールと DB は既にあるので init は `update-schema` だけで通る。先に `ops/down.sh` は要らない。
6. **Fargate の `exitCode`。** `describe-tasks` の `containers[0].exitCode` は、イメージの取得に失敗したときなど null になる。null も失敗として扱う（`stopCode` / `stoppedReason` を出す）。
