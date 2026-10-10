# temporal の初期化を一回きりのタスクに分け、サーバーのタスク定義から master のパスワードを外す（042）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。ベースは 824bc98）

### 変えたもの

- `docker/images/temporal-server/common.sh`（新規。`/etc/temporal/common-rds.sh`）
  - `entrypoint.sh` と `init.sh` が `.` で読む共通部分。
  - 中身:
    - `POSTGRES_SEEDS` / `POSTGRES_USER` / `POSTGRES_PWD` の `:?` と、既定値。
    - `log()` と、`exit 143` の trap（040）。
    - TLS の導出（`tr` の小文字化と、`SQL_CA` の fail fast）。
    - `wait_for_db()`（nc を 30 回）。
  - DB には触らない。shebang、`set`、`exec` は持たない。
- `docker/images/temporal-server/init.sh`（新規。`/etc/temporal/init-rds.sh`）
  - 冒頭は `set -eu` → `LOG_NAME=init-rds` → common を読む → `NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD` の `:?`。
  - そのあと次の 3 手順を順に走らせ、`log "初期化が終わった"` → `exit 0` で終わる。
    - 1: `wait_for_db`
    - 2: master の psql 2 回。ロール・DB と `btree_gin`。
    - 3: `for pair`。初回だけ `setup-schema -v 0.0`、毎回 `update-schema`。
  - 手順 2・3 は、いままでの entrypoint の中身をそのまま移した。
- `docker/images/temporal-server/entrypoint.sh`
  - master の psql、`sql_tool`、`NAUTOBOT_DB_*` の `:?`、`unset` を消した。
  - 代わりに手順 2 を置いた。
    - ロール `temporal` で、両方の DB の `schema_version.curr_version` を読む。
    - それがイメージの `versioned/` の最新の版と一致するまで待つ。最新の版は `v` を除いて `sort -t. -k1,1n -k2,2n` で並べた最大。
    - 待ちは 10 秒おきに 60 回で、揃わなければ `exit 1`。版が見つからなければ待たずに `exit 1`。
  - namespace（背景）と `exec /sbin/tini` は、そのあとにそのまま残した。
- `docker/images/temporal-server/Dockerfile`: `init.sh` と `common.sh` の COPY を足し、先頭のコメントを直した。
- `IaC/terraform/aws-managed/workflow/ecs.tf`
  - `aws_ecs_task_definition.init` を足した。
    - family は `<prefix>-workflow-init`。Fargate ARM64、256 / 512。
    - 実行ロールはサーバーと同じ。タスクロールは無し。
    - コンテナは `init` 1 つで、`entryPoint ["/etc/temporal/init-rds.sh"]`。
    - env は `local.temporal_db_env` と `NAUTOBOT_DB_USER`。
    - secrets は `POSTGRES_PWD` と `NAUTOBOT_DB_PASSWORD`。
    - `stopTimeout 120`。ログの stream prefix は `init`。healthCheck と portMappings は無し。
  - サーバーのタスク定義:
    - `NAUTOBOT_DB_USER` と `NAUTOBOT_DB_PASSWORD` を外した。env は `local.temporal_db_env` を使う。
    - `startPeriod` を 180 から 300 にした。
    - コメントを直した。
- `IaC/terraform/aws-managed/workflow/locals.tf`: DB 系の env 7 つを `temporal_db_env` に置いた。
- `IaC/terraform/aws-managed/workflow/outputs.tf`: `init_task_definition` / `task_subnet_id` / `task_security_group_id` / `init_logs_command` を足した。
- `IaC/terraform/aws-managed/workflow/iam.tf`
  - コメントを直した。
  - タスクのロールのポリシーに `DenyNautobotParameters`（`ssm:GetParameter*` を `/<prefix>/nautobot/*` で Deny）を足した。セルフレビューの Must 1 の修正（下）。
- `ops/up-common.sh`
  - `run_temporal_init` を足した。
    - `run-task` は `--query '[tasks[0].taskArn, length(failures)]'`。failures が 0 でない、ARN が空、`None` のときは die する。
    - `wait tasks-stopped` は 2 回まで。
    - `describe-tasks` で `exitCode` を見て、0 以外（`None` を含む）なら die する。止まった理由と `init_logs_command` を出す。
  - `build_temporal_server` と `ensure_temporal_secrets` のコメントを直した。
- `ops/up.sh`（8-5）と `ops/oss/up.sh`（8）
  - `tf_apply workflow` の直後に `run_temporal_init` を呼ぶ。
  - up.sh の services-stable の案内から「初回は RDS にスキーマを入れるので」を外した。
- `tests/test_workflow.py`
  - 検証 1 の check を足した。
  - 既存の entrypoint の check のうち、手順 2・3 を見るものは `init.sh` を見る形に移した。
  - 振る舞いの check（sh で entrypoint / init / trap を動かす、run_temporal_init を偽の aws で動かす）を足した。
  - タスクのロールが `/<prefix>/nautobot/*` を Deny している check を足した（Must 1）。
- `tests/test_oss_ops.py`
  - 偽の aws に `ecs run-task` と、init の `describe-tasks` の query を足した。
  - 81 の check の `ecs wait` の絞り込みを services-stable だけにした。
- docs
  - `docs/architecture/resources/temporal.md`（シークレットの行と見える経路の 2 か所に、タスクのロールの Deny を足した）
  - `docs/deploy.md`（8-5 の行と時間課金）
  - `docs/workflow.md`（困ったときの 2 行）
  - `docs/architecture/resources/nautobot.md`（相乗りの行）

### 設計からの逸脱

- `POSTGRES_SEEDS` / `POSTGRES_USER` / `POSTGRES_PWD` の `:?` は `init.sh` ではなく `common.sh` に置いた。
  - 両方の入口で要る検査を、二重に書かないため。
  - init が `POSTGRES_PWD` 無しで止まることは、テストの check で縛った。
- `aws_ecs_task_definition.init` の precondition は、サーバーと同じく `temporal_repository_url` も見る。イメージが同じなので、同じ条件で止める。
- design の変更対象ファイルに無い 2 つを直した。
  - `docs/architecture/resources/nautobot.md`: 相乗りの行が「temporal のコンテナが起動のたびに master で作る」と言っていたため。
  - `tests/test_oss_ops.py`: 偽の aws に無い `ecs run-task` で、`ops/oss/up.sh` のテストが落ちたため。
- `docs/cycles/QUEUE.md` 147 への AWS の確かめ（design の検証 6）は書いていない。PM の指示で、QUEUE は PM が書く。
- 検証 2〜5 は、postgres を `ssl=on`、サーバーと init を `SQL_TLS_ENABLED=true` / `SQL_HOST_VERIFICATION=false` で走らせた。ecs.tf と同じ組み合わせにするため。
- `run_temporal_init` のメッセージの `$out` などは `${out}` と波括弧で囲んだ。直後に全角の括弧が続くと、Mac の bash 3.2 が変数名の続きと読むため。
- ブランチは `worktree-agent-a6b05cfb808993490` のまま（PM の指示）。
- design に無い `iam.tf` の Deny を足した。サーバーのタスクのロールの既存の `Parameters` が `/<prefix>/*` を許すので、master のパスワードを外しても ECS Exec のシェルから SSM で引けたため（セルフレビューの Must 1）。

### 検証

#### 1. テスト（`./ops/check.sh`）

最後の変更（Must 1 の修正、23:17）のあとに走らせ直した。出力は 3065 行あるので、節 1〜3、042 の check（`tests/test_workflow.py` の該当部分）、末尾を貼る。行は 160 字で切った。

```

== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/logs  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/logs  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK
…（節 4 の前半。042 以外の check は略）
ok temporal は start-dev も SQLite も使わない（command 無し。docker/images/temporal-server/ の entrypoint が temporal-server を起こす）
ok temporal は portMappings を持たない（7233〜7239 / 6933〜6939 はタスクの外に出さない）
ok local.temporal_db_env は Nautobot の RDS にロール temporal で TLS でつなぐ 7 つ（NAUTOBOT_DB_ で始まる名前は無い。いま: ['DBNAME', 'DB_PORT', 'POSTGRES_SEEDS', 'POSTGRES_USER', 'SQL_…
ok temporal の env は postgres12 と local.temporal_db_env と接続数・shard・bind で、NAUTOBOT_DB_ で始まる名前もパスワードも無い（いま: ['BIND_ON_IP', 'DB', 'LOG_LEVEL', 'NUM_HISTORY_SHARDS'…
ok サーバーの temporal のコンテナの secrets は POSTGRES_PWD の 1 つだけで、サーバーのタスク定義のどこにも NAUTOBOT_DB_ の名前と master のパスワードの ARN が無い（cycle 042。いま: ['POSTGRES_PWD']）
ok temporal の healthCheck は namespace default の describe（HEALTHY = namespace まで出来た）。startPeriod は上限の 300（init のタスクを待つ間。cycle 042）
ok init のタスク定義は family <prefix>-workflow-init の Fargate ARM64（256 / 512）で、実行ロールはサーバーと同じ、タスクロールは無い
ok init のコンテナは init 1 つで、サーバーと同じイメージを entryPoint /etc/temporal/init-rds.sh で起こし、env は local.temporal_db_env と NAUTOBOT_DB_USER（いま: ['init']）
ok init のタスクの secrets は POSTGRES_PWD と NAUTOBOT_DB_PASSWORD の 2 つ（SSM の ARN。いま: ['POSTGRES_PWD', 'NAUTOBOT_DB_PASSWORD']）
ok init のタスクは healthCheck も portMappings も持たず、stopTimeout 120、ログは同じロググループの stream prefix init
ok workflow の出力に init_task_definition / task_subnet_id / task_security_group_id / init_logs_command（run_temporal_init が読む。サービスと同じサブネットと SG）
ok ui は temporalio/ui のミラーで essential = false、8233 だけを出し、127.0.0.1:7233 を temporal の HEALTHY の後に読む
ok worker は temporal の HEALTHY の後に起き、localhost:7233 につなぐ
ok サービスは新しいタスクを先に立てる（min 100 / max 200。履歴は RDS にある）
ok どのコンテナにも initProcessEnabled が無い（PID 1 は temporal-server のイメージの tini。ECS の init は孤児しか回収せず、namespace を作る背景のプロセスの親は生きている temporal-server なので回収できない。init の /proc/…
ok temporal の stopTimeout は 120（Fargate の上限。初回の setup-schema + update-schema の途中で SIGKILL しない。cycle 040。いま: ['120']）
ok nautobot の state（db_address / db_port / db_password_parameter）を try で読み、無ければ precondition で止まる
ok nautobot の出力にパスワードの値は無い（パラメータ名だけ）
ok 実行ロールは 2 つの DB のパスワード（SSM）だけを ssm:GetParameters で読める。サービスはそのポリシーの後に作る
ok サーバーのタスクのロールは Nautobot の SSM（/<prefix>/nautobot/*。master のパスワードを含む）を明示的に拒む（cycle 042）
ok TEMPORAL_SERVER_VERSION は docker/images/temporal-server/Dockerfile の ARG の既定と同じ
ok POSTGRES_MAJOR は Dockerfile の ARG と pipeline/nautobot の db_engine_version の既定と同じ（RDS と psql の大版）
ok TEMPORAL_UI_TAG は workflow の temporal_ui_image_tag の既定と同じ。temporal_image_tag には既定が無い（up.sh がビルドしたタグを渡す）
ok docker/images/temporal-server/ は Dockerfile と entrypoint.sh と init.sh と common.sh と namespace.sh と dynamic config の 6 つ（cycle 042）
ok Dockerfile は tini を apk で入れ（psql と同じ RUN）、namespace.sh を /etc/temporal/namespace-rds.sh に実行できる形で COPY する（cycle 039）
ok Dockerfile は init.sh を /etc/temporal/init-rds.sh に実行できる形で、common.sh を /etc/temporal/common-rds.sh に COPY する。ENTRYPOINT はサーバーの entrypoint-rds.sh のまま（cycle 042…
ok init.sh は #!/bin/sh → set -eu → LOG_NAME=init-rds → . /etc/temporal/common-rds.sh の順で始まる（いま: ['set -eu', 'LOG_NAME=init-rds', '. /etc/temporal/common-rds.sh'…
ok entrypoint.sh は #!/bin/sh → set -eu → LOG_NAME=entrypoint-rds → . /etc/temporal/common-rds.sh の順で始まる（いま: ['set -eu', 'LOG_NAME=entrypoint-rds', '. /etc/tempo…
ok common.sh は読まれる側（shebang も set も exec も trap 以外の exit も無い）で、DB に触らず（psql も temporal-sql-tool も起こさない）、master のパスワードを知らない
ok init.sh はパスワードをコマンドラインに載せない（psql は \getenv、temporal-sql-tool は SQL_PASSWORD）で、最後は exit 0（サーバーを起こさない）
ok init.sh はロールの SET の権限を見てから GRANT する（PG 16 以降の CREATEROLE の作ったロールは ADMIN だけで、OWNER に指定できない）
ok init.sh は schema_version が無いときだけ setup-schema、毎回 update-schema（スキーマを消さない）
ok init.sh が master のパスワード（NAUTOBOT_DB_PASSWORD）に触るのは :? の検査と master の psql 2 回（ロールと DB / btree_gin）だけ（いま: 3 行）
ok init.sh の temporal-sql-tool と psql の TLS はサーバー本体と同じ SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME から導く（導出は common.sh。ホスト名検証を決め打ちしない）
ok entrypoint.sh は master のパスワードとユーザーに触らず、スキーマを書かない（temporal-sql-tool / CREATE / GRANT / unset が無い。いま見つかったもの: []）
ok entrypoint.sh の psql はロール POSTGRES_USER と POSTGRES_PWD で schema_version.curr_version を読む 1 か所だけ（いま: ['PGPASSWORD="$POSTGRES_PWD" psql -X -q -v ON_ERROR_STOP=…
ok entrypoint.sh はイメージの versioned/ の版（v を除き、数で並べた最大）に両方の DB が揃うまで 10 秒おきに 60 回待ち、揃わなければ init のログの見方を出して exit 1
ok entrypoint は namespace を /etc/temporal/namespace-rds.sh <address> <namespace> <retention> & で起こし、( … ) & のサブシェルを持たない（cycle 039）
ok entrypoint.sh が背景（末尾 &）で起こすのは namespace-rds.sh の 1 行だけ、wait の行は無い（psql / temporal-sql-tool / sleep は前景。いま: & ['/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDR…
ok init.sh が背景（末尾 &）で起こすのは何も無く、wait の行は無い（psql / temporal-sql-tool / sleep は前景。いま: & [] wait []）
ok common.sh が背景（末尾 &）で起こすのは何も無く、wait の行は無い（psql / temporal-sql-tool / sleep は前景。いま: & [] wait []）
ok common.sh は log() の後に trap を 1 つ置き TERM / INT で exit 143。init.sh と entrypoint.sh は自分で trap を持たない（いま: common [12]）
ok init.sh は step=1〜3 を手順 1（wait_for_db）/ 2（master の psql）/ 3（for pair）の直前に順に置く（いま: step [(6, 'step=1'), (8, 'step=2'), (33, 'step=3')] 目印 [7, 10, 34]）
ok entrypoint.sh は step=1〜3 を手順 1（wait_for_db）/ 2（版を待つ while）/ 3（namespace）の直前に順に置く（いま: step [(6, 'step=1'), (14, 'step=2'), (29, 'step=3')] 目印 [7, 18, 30]）
ok common.sh の trap（entrypoint-rds）: SIGTERM で次の行へ進まず exit 143、ログに LOG_NAME と手順の番号が出る（いま: rc=143 '' 'entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 2 のあと。ここまでの手順はべき等な…
ok common.sh の trap（init-rds）: SIGTERM で次の行へ進まず exit 143、ログに LOG_NAME と手順の番号が出る（いま: rc=143 '' 'init-rds: SIGTERM を受けたので初期化を止める（手順 2 のあと。ここまでの手順はべき等なので次の起動でやり直す）…
ok common.sh の trap: step が未設定（手順 1 より前）でも set -u で落ちず exit 143、手順 0 と出る（${step:-0}。いま: rc=143 'entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 0 のあと。ここまでの手順はべき等なので次の起…
ok entrypoint.sh: ロールも表も無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=0 ['psql', 'psql', 'sleep', 'psql', 'psql', 's…
ok entrypoint.sh: psql は全部ロール temporal と POSTGRES_PWD（env）で呼び、パスワードを引数に載せず、master のパスワードは env にも無い（いま: ['psql -X -q -v ON_ERROR_STOP=1 -U temporal -d temporal -…
ok entrypoint.sh: 版が古い（1.9 < 1.19）まま 60 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=1 sleep 60 回 'p.sh（OSS 版は ops/oss/up.sh）を打ち直すか、init のログ（terrafo…
ok entrypoint.sh: イメージに versioned/ の版が無ければ待たずに exit 1（いま: rc=1 [] 'ema/{temporal,visibility}/versioned に版が無い（Dockerfile の COPY）'）
ok init.sh（初回。schema_version が無い）: master でロールと DB と btree_gin、ロールで setup-schema と update-schema を両方の DB に、の順で呼び exit 0（いま: rc=0 ['psql -X -q -v ON_ERROR_STOP=1…
ok init.sh: master のパスワードは master の psql 2 回の PGPASSWORD にだけ渡り、ロールの psql と temporal-sql-tool は POSTGRES_PWD を env で受け、どのパスワードも引数に出ない
ok init.sh（2 回目。schema_version がある）: setup-schema を打たず update-schema だけを両方の DB に（いま: rc=0 ['temporal update-schema -d /etc/temporal/schema/postgresql/v12/tempor…
ok init.sh: NAUTOBOT_DB_PASSWORD が無ければ何も呼ばずに非 0 で止まり、名前を言う（いま: rc=1 [] 'OT_DB_PASSWORD: NAUTOBOT_DB_PASSWORD（RDS の master のパスワード）が無い'）
ok init.sh: POSTGRES_PWD が無ければ何も呼ばずに非 0 で止まる（common.sh の :?。いま: rc=1 []）
ok namespace.sh はパスワードを持たず（unset も NAUTOBOT_DB_PASSWORD も POSTGRES_PWD も無い）、describe → create の順で、引数不足以外はどこで抜けても exit 0（いま: exit ['0', '0', '0', '0', '0']）
ok namespace.sh: 引数（address / namespace / retention）で動き、無ければ create して entrypoint-rds: の接頭辞でログを出す（いま: rc=0 ['nc -z -w 10 10.0.0.1 7233', 'temporal operator clus…
ok namespace.sh: describe が通れば create しない（いま: rc=0 ['nc -z -w 10 10.0.0.1 7233', 'temporal operator cluster health --address 10.0.0.1:7233', 'temporal operator …
ok namespace.sh: create が 30 回通らなくても exit 0（本体の temporal-server は別プロセスで、この終了コードを誰も待たない。いま: rc=0 create 30 回）
ok namespace.sh: frontend の nc が 30 回通らなければ temporal を呼ばずに exit 0（いま: rc=0 nc 30 回 temporal 0 回 'trypoint-rds: namespace: frontend の 7233 が開かない（30 回）。作らずに抜ける'）
ok namespace.sh: cluster health が 30 回通らなければ describe も create もせずに exit 0（いま: rc=0 health 30 回 'ntrypoint-rds: namespace: cluster health が通らない（30 回）。作らずに抜ける'）
ok namespace.sh: 引数が 2 つ（retention が無い）なら何も呼ばずに非 0 で抜け、3 つ目が無いと言う（いま: rc=1 [] '/Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/agent-a6b05cfb808993…
ok init.sh の sql_tool: 引数をそのまま temporal-sql-tool に渡し、SQL_PASSWORD / SQL_TLS は呼んだ側のシェルに残さない（いま: {'_ARGS': '--plugin postgres12 --ep db -p 5432 -u temporal --db t…
ok TLS（ecs.tf と同じ SQL_HOST_VERIFICATION=false）: psql は require、temporal-sql-tool はホスト名を検証しない。CA とサーバー名は渡さない（いま: {'SQL_TLS': 'true', 'SQL_TLS_DISABLE_HOST_VERIFI…
ok TLS: SQL_HOST_VERIFICATION が無ければ false と同じ（いま: {'SQL_TLS': 'true', 'SQL_TLS_DISABLE_HOST_VERIFICATION': 'true', 'PGSSLMODE': 'require', 'PGSSLROOTCERT': ''}）
ok TLS: SQL_HOST_VERIFICATION=true なら psql は verify-full で SQL_CA を根に、temporal-sql-tool はホスト名を検証し CA とサーバー名を受ける（いま: {'SQL_TLS': 'true', 'SQL_TLS_CA_FILE': '/ca.…
ok TLS: SQL_TLS_ENABLED=false なら psql は prefer、temporal-sql-tool は TLS 無し（いま: {'SQL_TLS': 'false', 'SQL_TLS_DISABLE_HOST_VERIFICATION': 'true', 'PGSSLMODE': 'pr…
ok TLS: SQL_TLS_ENABLED / SQL_HOST_VERIFICATION は大文字でも真（True / TRUE → verify-full、ホスト名を検証する。いま: {'SQL_TLS': 'true', 'SQL_TLS_CA_FILE': '/ca.pem', 'SQL_TLS_DISAB…
ok TLS: SQL_HOST_VERIFICATION=true で SQL_CA が無ければ temporal-sql-tool を呼ばずに非 0 で止まり、SQL_CA が要ると言う（いま: rc=1 {} 'init-rds: SQL_HOST_VERIFICATION=true なのに SQL_CA（CA …
…（略）
ok ops/up.sh は run_temporal_init を tf_apply workflow の直後、workflow の services-stable の前に 1 回呼ぶ（いま: 1 回）
ok ops/oss/up.sh は temporal のタグを docker/images/temporal-server/ の中身から作り（dir_tag）、無ければビルド、UI は TEMPORAL_UI_TAG で無ければミラーする
ok ops/oss/up.sh は arm64 の buildx を temporal のビルドでも確かめる（RUN apk がある）
ok ops/oss/up.sh は nautobot に db_engine_version=$POSTGRES_MAJOR を、workflow に temporal_image_tag を渡し、workflow の前にロール temporal のパスワードを作る
ok ops/oss/up.sh は run_temporal_init を tf_apply workflow の直後、workflow の services-stable の前に 1 回呼ぶ（いま: 1 回）
ok run_temporal_init は ecs run-task → ecs wait tasks-stopped → describe-tasks の exitCode の順で、0 以外（None を含む）で die する（いま: [896, 1532, 1836, 2090]）
ok run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（サービスと同じサブネット・SG、公開 IP 無し。いま: rc=0 ['ecs run-task', 'ecs wait', 'ecs describe-tasks…
ok run_temporal_init: exitCode=1（STOPPED…）なら die し、止まった理由と init のログの見方を出す（いま: rc=1 'exited）。ログ: aws logs tail g --log-stream-name-prefix init 。直したら ops/up.sh（OS…
ok run_temporal_init: exitCode=None（STOPPED…）なら die し、止まった理由と init のログの見方を出す（いま: rc=1 'etried）。ログ: aws logs tail g --log-stream-name-prefix init 。直したら ops/up.sh…
ok run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（None…。いま: rc=1 ['ecs run-task'] 'DIE: Temporal の初期化のタスクを起こせない（run-task の返り: None\t1）'）
ok run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（k/c1/abc…。いま: rc=1 ['ecs run-task'] ' の初期化のタスクを起こせない（run-task の返り: arn:aws:ecs:r:1:task/c…
ok run_temporal_init: run-task そのものが失敗すれば待たずに die（権限の手がかりを出す。いま: rc=1 ['ecs run-task'] ' の初期化のタスクを起こせない（上のエラー。ecs:RunTask と実行ロールへの iam:PassRole が要る）'）
ok run_temporal_init: tasks-stopped が 1 回目で時間切れでも 2 回目で止まれば先へ進む（いま: rc=0 ['ecs run-task', 'ecs wait', 'ecs wait', 'ecs describe-tasks']）
ok run_temporal_init: tasks-stopped が 2 回とも時間切れなら describe-tasks を見ずに die（いま: rc=1 ['ecs run-task', 'ecs wait', 'ecs wait'] 'たっても止まらない（ログ: aws logs tail g --log…
…（略）
通過 419 / 失敗 0

== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

- 通過数は 381 → 419（Must 1 の check で 1 つ増えた）。
- 各 check が、その行を壊した中身で落ちることも確かめた（下）。

##### 壊して落ちるか（`<scratchpad>/mutate.py`）

- 方法:
  - `tests/test_workflow.py` の `check()` の assert を「失敗を数えて続ける」に替えて、メモリ上で実行する。
  - 読まれるファイルの中身は、`open()` の返りを差し替えて入れ替える。ファイルは書かない。
- 1 行目の `!! 落ちない [変更なし] 失敗 0` は基準で、何も壊さなければ全部通ることを示す。
- 37 個の壊し方は、すべて 1 つ以上の check で落ちた。
  - 「待ちを無限に」は、check の sh が timeout で切れて落ちた。
- Must 1 の check は、`iam.tf` から `DenyNautobotParameters` の statement を消して `tests/test_workflow.py` を走らせ、その check の AssertionError で落ちることを別に確かめた（`<scratchpad>/mutate-deny.out`。ファイルは元に戻した）。
- 行は 180 字で切った。

```
!! 落ちない [変更なし] 失敗 0
落ちた [server に master のパスワードを戻す] 失敗 1
    - サーバーの temporal のコンテナの secrets は POSTGRES_PWD の 1 つだけで、サーバーのタスク定義のどこにも NAUTOBOT_DB_ の名前と master のパスワードの ARN が無い（cycle 042
落ちた [server に NAUTOBOT_DB_USER を戻す] 失敗 2
    - temporal の env は postgres12 と local.temporal_db_env と接続数・shard・bind で、NAUTOBOT_DB_ で始まる名前もパスワードも無い（いま: ['BIND_ON_IP', 'D
    - サーバーの temporal のコンテナの secrets は POSTGRES_PWD の 1 つだけで、サーバーのタスク定義のどこにも NAUTOBOT_DB_ の名前と master のパスワードの ARN が無い（cycle 042
落ちた [startPeriod 180 に戻す] 失敗 1
    - temporal の healthCheck は namespace default の describe（HEALTHY = namespace まで出来た）。startPeriod は上限の 300（init のタスクを待つ間。cycl
落ちた [init の secrets から master を落とす] 失敗 1
    - init のタスクの secrets は POSTGRES_PWD と NAUTOBOT_DB_PASSWORD の 2 つ（SSM の ARN。いま: ['POSTGRES_PWD']）
落ちた [init の entryPoint を外す] 失敗 1
    - init のコンテナは init 1 つで、サーバーと同じイメージを entryPoint /etc/temporal/init-rds.sh で起こし、env は local.temporal_db_env と NAUTOBOT_DB_U
落ちた [init に healthCheck を足す] 失敗 1
    - init のタスクは healthCheck も portMappings も持たず、stopTimeout 120、ログは同じロググループの stream prefix init
落ちた [init にタスクロール] 失敗 1
    - init のタスク定義は family <prefix>-workflow-init の Fargate ARM64（256 / 512）で、実行ロールはサーバーと同じ、タスクロールは無い
落ちた [init の log prefix を temporal に] 失敗 1
    - init のタスクは healthCheck も portMappings も持たず、stopTimeout 120、ログは同じロググループの stream prefix init
落ちた [temporal_db_env の TLS を false] 失敗 1
    - local.temporal_db_env は Nautobot の RDS にロール temporal で TLS でつなぐ 7 つ（NAUTOBOT_DB_ で始まる名前は無い。いま: ['DBNAME', 'DB_PORT', 'PO
落ちた [output task_subnet_id を消す] 失敗 1
    - workflow の出力に init_task_definition / task_subnet_id / task_security_group_id / init_logs_command（run_temporal_init が読む。サ
落ちた [Dockerfile の init COPY を消す] 失敗 1
    - Dockerfile は init.sh を /etc/temporal/init-rds.sh に実行できる形で、common.sh を /etc/temporal/common-rds.sh に COPY する。ENTRYPOINT は
落ちた [entrypoint で master を使う] 失敗 5
    - entrypoint.sh は master のパスワードとユーザーに触らず、スキーマを書かない（temporal-sql-tool / CREATE / GRANT / unset が無い。いま見つかったもの: ['NAUTOBOT_DB
    - entrypoint.sh: ロールも表も無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=1 [] 'sh:
    - entrypoint.sh: psql は全部ロール temporal と POSTGRES_PWD（env）で呼び、パスワードを引数に載せず、master のパスワードは env にも無い（いま: []）
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 60 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=1 sleep 0 回 'sh: line 34: 
落ちた [entrypoint で temporal-sql-tool] 失敗 2
    - entrypoint.sh は master のパスワードとユーザーに触らず、スキーマを書かない（temporal-sql-tool / CREATE / GRANT / unset が無い。いま見つかったもの: ['temporal-sq
    - entrypoint.sh: ロールも表も無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=127 ['psq
落ちた [entrypoint の版を辞書順に] 失敗 3
    - entrypoint.sh はイメージの versioned/ の版（v を除き、数で並べた最大）に両方の DB が揃うまで 10 秒おきに 60 回待ち、揃わなければ init のログの見方を出して exit 1
    - entrypoint.sh: ロールも表も無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=1 ['psql'
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 60 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=0 sleep 0 回 'entrypoint-rd
落ちた [entrypoint の待ちを 1 つの DB だけ] 失敗 1
    - entrypoint.sh はイメージの versioned/ の版（v を除き、数で並べた最大）に両方の DB が揃うまで 10 秒おきに 60 回待ち、揃わなければ init のログの見方を出して exit 1
落ちた [entrypoint の待ちを無限に] 失敗 1 例外 TimeoutExpired: Command '['sh', '-c', '#!/bin/sh\n# Temporal のサーバーの入口（cycle 036。docker/images/te
    - entrypoint.sh はイメージの versioned/ の版（v を除き、数で並べた最大）に両方の DB が揃うまで 10 秒おきに 60 回待ち、揃わなければ init のログの見方を出して exit 1
落ちた [entrypoint の psql を master で] 失敗 2
    - entrypoint.sh の psql はロール POSTGRES_USER と POSTGRES_PWD で schema_version.curr_version を読む 1 か所だけ（いま: ['PGPASSWORD="$POSTG
    - entrypoint.sh: psql は全部ロール temporal と POSTGRES_PWD（env）で呼び、パスワードを引数に載せず、master のパスワードは env にも無い（いま: ['psql -X -q -v ON_E
落ちた [entrypoint の step=2 を消す] 失敗 1
    - entrypoint.sh は step=1〜3 を手順 1（wait_for_db）/ 2（版を待つ while）/ 3（namespace）の直前に順に置く（いま: step [(6, 'step=1'), (28, 'step=3')
落ちた [entrypoint に trap を足す] 失敗 1
    - common.sh は log() の後に trap を 1 つ置き TERM / INT で exit 143。init.sh と entrypoint.sh は自分で trap を持たない（いま: common [12]）
落ちた [entrypoint が common を読まない] 失敗 5
    - entrypoint.sh は #!/bin/sh → set -eu → LOG_NAME=entrypoint-rds → . /etc/temporal/common-rds.sh の順で始まる（いま: ['set -eu', 'LO
    - entrypoint.sh: ロールも表も無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=127 [] 's
    - entrypoint.sh: psql は全部ロール temporal と POSTGRES_PWD（env）で呼び、パスワードを引数に載せず、master のパスワードは env にも無い（いま: []）
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 60 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=127 sleep 0 回 'sh: line 22
落ちた [init の setup-schema を if の外へ] 失敗 2
    - init.sh は schema_version が無いときだけ setup-schema、毎回 update-schema（スキーマを消さない）
    - init.sh（2 回目。schema_version がある）: setup-schema を打たず update-schema だけを両方の DB に（いま: rc=0 ['temporal setup-schema -v 0.0', 
落ちた [init の update-schema を消す] 失敗 3
    - init.sh は schema_version が無いときだけ setup-schema、毎回 update-schema（スキーマを消さない）
    - init.sh（初回。schema_version が無い）: master でロールと DB と btree_gin、ロールで setup-schema と update-schema を両方の DB に、の順で呼び exit 0（いま:
    - init.sh（2 回目。schema_version がある）: setup-schema を打たず update-schema だけを両方の DB に（いま: rc=0 []）
落ちた [init が exit 0 で終わらない（tini へ）] 失敗 3
    - init.sh はパスワードをコマンドラインに載せない（psql は \getenv、temporal-sql-tool は SQL_PASSWORD）で、最後は exit 0（サーバーを起こさない）
    - init.sh（初回。schema_version が無い）: master でロールと DB と btree_gin、ロールで setup-schema と update-schema を両方の DB に、の順で呼び exit 0（いま:
    - init.sh（2 回目。schema_version がある）: setup-schema を打たず update-schema だけを両方の DB に（いま: rc=1 ['temporal update-schema -d /etc/
落ちた [init のパスワードを -v pw=] 失敗 3
    - init.sh はパスワードをコマンドラインに載せない（psql は \getenv、temporal-sql-tool は SQL_PASSWORD）で、最後は exit 0（サーバーを起こさない）
    - init.sh（初回。schema_version が無い）: master でロールと DB と btree_gin、ロールで setup-schema と update-schema を両方の DB に、の順で呼び exit 0（いま:
    - init.sh: master のパスワードは master の psql 2 回の PGPASSWORD にだけ渡り、ロールの psql と temporal-sql-tool は POSTGRES_PWD を env で受け、どのパスワ
落ちた [init が master のパスワードで sql_tool] 失敗 9
    - init.sh はパスワードをコマンドラインに載せない（psql は \getenv、temporal-sql-tool は SQL_PASSWORD）で、最後は exit 0（サーバーを起こさない）
    - init.sh が master のパスワード（NAUTOBOT_DB_PASSWORD）に触るのは :? の検査と master の psql 2 回（ロールと DB / btree_gin）だけ（いま: 4 行）
    - init.sh: master のパスワードは master の psql 2 回の PGPASSWORD にだけ渡り、ロールの psql と temporal-sql-tool は POSTGRES_PWD を env で受け、どのパスワ
    - init.sh の sql_tool: 引数をそのまま temporal-sql-tool に渡し、SQL_PASSWORD / SQL_TLS は呼んだ側のシェルに残さない（いま: {}）
落ちた [init の :? NAUTOBOT_DB_PASSWORD を外す] 失敗 1
    - init.sh が master のパスワード（NAUTOBOT_DB_PASSWORD）に触るのは :? の検査と master の psql 2 回（ロールと DB / btree_gin）だけ（いま: 2 行）
落ちた [init の psql を背景に] 失敗 1
    - init.sh が背景（末尾 &）で起こすのは何も無く、wait の行は無い（psql / temporal-sql-tool / sleep は前景。いま: & ['PGPASSWORD="$NAUTOBOT_DB_PASSWORD" p
落ちた [common の trap を exit 0] 失敗 4
    - common.sh は log() の後に trap を 1 つ置き TERM / INT で exit 143。init.sh と entrypoint.sh は自分で trap を持たない（いま: common [12]）
    - common.sh の trap（entrypoint-rds）: SIGTERM で次の行へ進まず exit 143、ログに LOG_NAME と手順の番号が出る（いま: rc=0 '' 'entrypoint-rds: SIGTERM 
    - common.sh の trap（init-rds）: SIGTERM で次の行へ進まず exit 143、ログに LOG_NAME と手順の番号が出る（いま: rc=0 '' 'init-rds: SIGTERM を受けたので初期化を止め
    - common.sh の trap: step が未設定（手順 1 より前）でも set -u で落ちず exit 143、手順 0 と出る（${step:-0}。いま: rc=0 'entrypoint-rds: SIGTERM を受けたの
落ちた [common の小文字化を外す] 失敗 1
    - TLS: SQL_TLS_ENABLED / SQL_HOST_VERIFICATION は大文字でも真（True / TRUE → verify-full、ホスト名を検証する。いま: {'SQL_TLS': 'true', 'SQL_TL
落ちた [common の :? POSTGRES_PWD を外す] 失敗 2
    - common.sh は読まれる側（shebang も set も exec も trap 以外の exit も無い）で、DB に触らず（psql も temporal-sql-tool も起こさない）、master のパスワードを知らない
    - init.sh: POSTGRES_PWD が無ければ何も呼ばずに非 0 で止まる（common.sh の :?。いま: rc=1 ['psql -X -q -v ON_ERROR_STOP=1 -U nautobot -d nautobo
落ちた [common の SQL_CA の fail fast を外す] 失敗 1
    - TLS: SQL_HOST_VERIFICATION=true で SQL_CA が無ければ temporal-sql-tool を呼ばずに非 0 で止まり、SQL_CA が要ると言う（いま: rc=0 {'_ARGS': '--plugi
落ちた [up.sh で run_temporal_init を apply の前に] 失敗 1
    - ops/up.sh は run_temporal_init を tf_apply workflow の直後、workflow の services-stable の前に 1 回呼ぶ（いま: 2 回）
落ちた [oss/up.sh から run_temporal_init を消す] 失敗 1
    - ops/oss/up.sh は run_temporal_init を tf_apply workflow の直後、workflow の services-stable の前に 1 回呼ぶ（いま: 0 回）
落ちた [run_temporal_init が exitCode を見ない] 失敗 2
    - run_temporal_init は ecs run-task → ecs wait tasks-stopped → describe-tasks の exitCode の順で、0 以外（None を含む）で die する（いま: [89
    - run_temporal_init: exitCode=None（STOPPED…）なら die し、止まった理由と init のログの見方を出す（いま: rc=0 ''）
落ちた [run_temporal_init が wait を 1 回だけ] 失敗 2
    - run_temporal_init: tasks-stopped が 1 回目で時間切れでも 2 回目で止まれば先へ進む（いま: rc=1 ['ecs run-task', 'ecs wait']）
    - run_temporal_init: tasks-stopped が 2 回とも時間切れなら describe-tasks を見ずに die（いま: rc=1 ['ecs run-task', 'ecs wait'] 'たっても止まらない（
落ちた [run_temporal_init が failures を見ない] 失敗 1
    - run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（k/c1/abc…。いま: rc=1 ['ecs run-task', 'ecs wait', 'ecs
落ちた [run_temporal_init が公開 IP] 失敗 2
    - run_temporal_init は ecs run-task → ecs wait tasks-stopped → describe-tasks の exitCode の順で、0 以外（None を含む）で die する（いま: [89
    - run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（サービスと同じサブネット・SG、公開 IP 無し。いま: rc=0 ['ecs run-t
```

#### 2〜5. 手元の docker

- 使ったもの:
  - イメージ: `nwc-temporal-server:t042`。worktree の `docker/images/temporal-server/` を `--platform linux/arm64` でビルドした。
  - DB: `postgres:18`（`ssl=on`）。
  - ネットワーク: `t042`。
- パスワードは捨ての値を使い、ここでは `<使い捨て>` に伏せた。
- `<scratchpad>/verify042.sh` の出力（`verify042.out`）。次の行は略した。
  - postgres の pull の進捗の 41 行。
  - BusyBox の grep の usage の 2 か所。
- 実行の順は「検証 5 → 3 / 4 → 2 → 3 の続き」。検証 3 のサーバーを先に起こし、待たせた状態で init を打つため。

```
## 準備
$ docker build --platform linux/arm64 --build-arg TEMPORAL_SERVER_VERSION=1.32.1 --build-arg POSTGRES_MAJOR=18 -q -t nwc-temporal-server:t042 /Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/agent-a6b05cfb808993490/docker/images/temporal-server/
sha256:f61ea085191319d2e0df8772c43f42ec065016ab99411f82759fd098c9100ec2
（rc=0）
$ docker network create t042
7c9b8d596b370664c2159be789950bdd724b9f410c894f9e3e72c8b9f26c8271
（rc=0）
$ docker run -d --name pg042 --network t042 -e POSTGRES_USER=nautobot -e POSTGRES_PASSWORD=<使い捨て> -e POSTGRES_DB=nautobot postgres:18 -c ssl=on -c ssl_cert_file=/etc/ssl/certs/ssl-cert-snakeoil.pem -c ssl_key_file=/etc/ssl/private/ssl-cert-snakeoil.key
（postgres:18 の pull の進捗 41 行は略）
Status: Downloaded newer image for postgres:18
b177b8a9fe8be185c2e9697216519bb62220254cb8bdc5d05dd4b230fe6246b5
（rc=0）
$ docker exec pg042 psql -U nautobot -tAc SHOW ssl
on
（rc=0）

## 検証 5: init の前のサーバーを待ちの間に止める
$ docker run -d --name srv042w --network t042 -e DB=postgres12 -e POSTGRES_SEEDS=pg042 -e DB_PORT=5432 -e POSTGRES_USER=temporal -e DBNAME=temporal -e VISIBILITY_DBNAME=temporal_visibility -e SQL_TLS_ENABLED=true -e SQL_HOST_VERIFICATION=false -e SQL_MAX_CONNS=4 -e SQL_MAX_IDLE_CONNS=4 -e SQL_VIS_MA…
d7a0d8c67c26252362c89b83cfe75cf5085c4e6a43e9f7227930e5dc5d635973
（rc=0）
$ time docker stop -t 30 srv042w
（10 秒）
$ docker inspect -f {{.State.ExitCode}} srv042w
143
（rc=0）
$ docker logs srv042w 2>&1 | grep -n entrypoint-rds
1:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（1/60。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14）
2:entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 2 のあと。ここまでの手順はべき等なので次の起動でやり直す）

## 検証 3 / 4: init の前にサーバーを起こす（env に NAUTOBOT_DB_* は無い）
$ docker run -d --name srv042 --network t042 -e DB=postgres12 -e POSTGRES_SEEDS=pg042 -e DB_PORT=5432 -e POSTGRES_USER=temporal -e DBNAME=temporal -e VISIBILITY_DBNAME=temporal_visibility -e SQL_TLS_ENABLED=true -e SQL_HOST_VERIFICATION=false -e SQL_MAX_CONNS=4 -e SQL_MAX_IDLE_CONNS=4 -e SQL_VIS_MAX…
fc9bb41f660adf708e1cb831c1efbe441247db356c8ee70f94724110fee0a4ca
（rc=0）
$ docker logs srv042 2>&1 | grep -n entrypoint-rds
1:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（1/60。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14）
2:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（2/60。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14）
$ docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' srv042 | grep -c '^NAUTOBOT_DB_'
0
$ docker exec srv042 ps -o pid,ppid,user,comm,args
PID   PPID  USER     COMMAND          COMMAND
    1     0 temporal entrypoint-rds.  {entrypoint-rds.} /bin/sh /etc/temporal/entrypoint-rds.sh
   49     1 temporal sleep            sleep 10
   66     0 temporal ps               ps -o pid,ppid,user,comm,args
（rc=0）
$ docker exec srv042 sh -c 'grep -lz NAUTOBOT_DB_PASSWORD /proc/[0-9]*/environ; echo "件数=$(grep -lz NAUTOBOT_DB_PASSWORD /proc/[0-9]*/environ 2>/dev/null | wc -l)"; echo "POSTGRES_PWD がある environ=$(grep -lz POSTGRES_PWD /proc/[0-9]*/environ 2>/dev/null | wc -l)"'（待ちの間）
grep: unrecognized option: z
（BusyBox の grep の usage 29 行は略）
件数=0
POSTGRES_PWD がある environ=0

## 検証 2: init を 2 回
$ docker run --name init042a --network t042 --entrypoint /etc/temporal/init-rds.sh <init の env> nwc-temporal-server:t042
（rc=0）
$ docker logs init042a 2>&1 | grep -n init-rds
1:init-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
2:init-rds: temporal: 初回なので setup-schema -v 0.0
8:init-rds: temporal: update-schema
166:init-rds: temporal_visibility: 初回なので setup-schema -v 0.0
172:init-rds: temporal_visibility: update-schema
355:init-rds: 初期化が終わった
$ docker run --name init042b ...（2 回目、同じ env）
（rc=0）
$ docker logs init042b 2>&1 | grep -n init-rds
1:init-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
3:init-rds: temporal: update-schema
8:init-rds: temporal_visibility: update-schema
13:init-rds: 初期化が終わった
$ docker logs init042b 2>&1 | grep -c setup-schema
0
$ docker exec pg042 psql -U nautobot -d temporal -tAc SELECT curr_version FROM schema_version
1.19
（rc=0）
$ docker exec pg042 psql -U nautobot -d temporal_visibility -tAc SELECT curr_version FROM schema_version
1.14
（rc=0）
$ docker run --rm --entrypoint sh nwc-temporal-server:t042 -c ls /etc/temporal/schema/postgresql/v12/temporal/versioned | tail -n 3; ls /etc/temporal/schema/postgresql/v12/visibility/versioned | tail -n 3
v1.7
v1.8
v1.9
v1.7
v1.8
v1.9
（rc=0）
$ docker exec pg042 psql -U nautobot -tAc SELECT usename, ssl FROM pg_stat_ssl JOIN pg_stat_activity USING (pid) WHERE usename='temporal' GROUP BY 1,2
（rc=0）

## 検証 3（続き）: サーバーが先へ進む
$ docker logs srv042 2>&1 | grep -n entrypoint-rds
1:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（1/60。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14）
2:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（2/60。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14）
3:entrypoint-rds: スキーマは temporal=1.19 / temporal_visibility=1.14（イメージの版と同じ）
7:entrypoint-rds: namespace: default を作った（retention 72h）
$ docker inspect -f {{.State.Health.Status}} srv042
healthy
（rc=0）
$ docker exec srv042 temporal operator namespace describe -n default --address 127.0.0.1:7233 | head -4
  NamespaceInfo.Name                    default
  NamespaceInfo.Id                      a2d0a7ef-deb9-4579-9ab6-fa6fa9fc8dd4
  NamespaceInfo.Description              
  NamespaceInfo.OwnerEmail               

## 検証 4（続き）: 起動後
$ docker exec srv042 ps -o pid,ppid,user,comm,args
PID   PPID  USER     COMMAND          COMMAND
    1     0 temporal tini             /sbin/tini -- /etc/temporal/entrypoint.sh
  108     1 temporal temporal-server  temporal-server start
  196     0 temporal ps               ps -o pid,ppid,user,comm,args
（rc=0）
$ docker exec srv042 sh -c '（上と同じ grep。起動後）'
grep: unrecognized option: z
（BusyBox の grep の usage 29 行は略）
件数=0
POSTGRES_PWD がある environ=0
$ docker exec srv042 cat /tmp/hc_nautobot_count  # healthCheck のプロセスの env にある NAUTOBOT_DB_ の数
0
$ docker exec srv042 sh -c 'tr "\0" "\n" < /proc/1/environ | grep -c ^NAUTOBOT_DB_'
0
```

- `grep -lz` の 2 か所は無効だった（BusyBox の grep に `-z` が無い）。直後の「件数=0」も grep が動いていないので根拠にならない。
- その 2 か所と TLS・版の並びを、`verify042b.sh` で取り直した（下）。
  - pid ごとに `/proc/<pid>/environ` を `tr '\0' '\n'` で行にして数える。
  - `POSTGRES_PWD=1` は、走査が env を読めていることの対照。
- 上の `pg_stat_ssl` の問い合わせは、その時点で temporal の接続が無く空だった。
- 上の `ls | tail -n 3` は辞書順なので v1.9 が最後に見える。数で並べた最大（1.19 / 1.14）は下で取り直した。

```
## 検証 4（起動後。srv042 は init のあとで HEALTHY）
$ docker exec srv042 sh -c '<pid ごとに environ の NAUTOBOT_DB_ と POSTGRES_PWD= の行数>'
1 /sbin/tini -- /etc/temporal/entrypoint.s NAUTOBOT_DB_=0 POSTGRES_PWD=1
108 temporal-server start  NAUTOBOT_DB_=0 POSTGRES_PWD=1
298 sh -c for p in /proc/[0-9]*; do [ -r $p/ NAUTOBOT_DB_=0 POSTGRES_PWD=1
$ docker exec srv042 cat /tmp/hc_nautobot_count  # 5 秒ごとの healthCheck のプロセスが最後に数えた NAUTOBOT_DB_ の行数
0

## 検証 4（待ちの間）: init を打たないサーバーをもう 1 つ（srv042x。DB は init 済みなので別の DB 名にして待たせる）
1:entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5 が run-task する <接頭辞>-workflow-init）を待つ（1/60。スキーマの版 t042none=無し/1.19 t042none_vis=無し/1.14）
$ docker exec srv042x sh -c '<同じ>'
1 /bin/sh /etc/temporal/entrypoint-rds.sh  NAUTOBOT_DB_=0 POSTGRES_PWD=1
27 sleep 10  NAUTOBOT_DB_=0 POSTGRES_PWD=1
28 sh -c for p in /proc/[0-9]*; do [ -r $p/ NAUTOBOT_DB_=0 POSTGRES_PWD=1

## TLS とスキーマの版
$ docker exec pg042 psql -U nautobot -tAc "SELECT usename, ssl, count(*) FROM pg_stat_ssl JOIN pg_stat_activity USING (pid) WHERE usename='temporal' GROUP BY 1,2"
temporal|t|17
$ docker run --rm --entrypoint sh nwc-temporal-server:t042 -c 'for d in temporal visibility; do ls /etc/temporal/schema/postgresql/v12/$d/versioned | sed -n "s/^v//p" | sort -t. -k1,1n -k2,2n | tail -n 1; done'
1.19
1.14
```

- 結果:
  - 検証 2: init は 2 回とも rc=0。
    - 1 回目は両 DB で「初回なので setup-schema」→ update-schema。
    - 2 回目は setup-schema が 0 回で、update-schema だけ。
    - `curr_version` は temporal=1.19 / temporal_visibility=1.14 で、イメージの `versioned/` を数で並べた最大と一致した。
    - ロール temporal の接続は TLS（`temporal|t|17`）。
  - 検証 3:
    - init の前に起こしたサーバーは「待つ（1/60）」「（2/60）」と待ち、落ちなかった。
    - init のあとは「スキーマは … イメージの版と同じ」→ namespace default を作り、healthy になった。`namespace describe` も通った。
  - 検証 4: master のパスワードが無いことを、待ちの間と起動後の両方で確かめた。
    - 待ちの間（srv042x）: PID 1 の sh と `sleep` の environ に `NAUTOBOT_DB_` で始まる行が 0。
    - 起動後（srv042）: tini、temporal-server、走査の sh の environ に `NAUTOBOT_DB_` で始まる行が 0。
    - `--health-cmd` で立てた healthCheck のプロセスの env も 0（`/tmp/hc_nautobot_count`）。
    - コンテナの Config.Env にも 0。
    - namespace-rds.sh は起動後の走査の時点で抜けていたので、pid の一覧に無い。
      - env はタスク定義から来るので、同じく 0 のはず（`tests/test_workflow.py` の namespace.sh の check が、パスワードを持たないことを見る）。
  - 検証 5（サーバー側）: 待ちの間に `docker stop` → 10 秒で ExitCode 143。ログは「SIGTERM を受けたので初期化を止める（手順 2 のあと…）」。
    - 10 秒かかったのは、前景の `sleep 10` が返るのを待ったため（040 と同じく、区切りで抜ける）。
  - 検証 5（init 側。`update-schema` の最中の stop）: 手元では再現していない。**読んだだけ。**
    - init.sh は common.sh の同じ trap を持つ。
    - 手順 3 の `sql_tool` は前景のサブシェルで、背景（`&`）も `wait` も無い（テストの check で縛った）。
    - なので、040 と同じく区切りで 143 になるはず。
    - trap の振る舞い（init-rds の名前で 143）は、`tests/test_workflow.py` の sh の check で確かめた。
  - 検証 6（AWS）: このサイクルではやらない（PM の指示）。QUEUE 147 に足すのは PM。

#### 片付け

検証で作ったものは、検証のあと `<scratchpad>/cleanup042.sh` で消した。

- コンテナ（pg042 / srv042w / srv042 / init042a / init042b）は `docker rm -f -v` で消した。srv042x は verify042b.sh の中で消してあった。
- ネットワーク t042 は `docker network rm` で消した。
- 確認の出力:

```
$ docker ps -a --format '{{.Names}}' | grep -c 042
0
$ docker network ls --format '{{.Name}}' | grep -c t042
0
```

- このスクリプトのイメージの後片付けで事故を起こした（この記録の末尾）。

### セルフレビュー

- 体制:
  - 自分（opus-5.5 / high）が design.md と差分を突き合わせて読み、気になった 7 点を挙げた。
  - 反対弁護人は general-purpose のサブエージェント（opus / xhigh）。design.md・差分・自分の懸念 7 点を文脈として渡し、読み取り専用で走らせた。
  - 反対弁護人のあと、`git status --porcelain -uall` で新しいファイルが増えていないことを確かめた（` M` 15 件と ` A` 2 件だけ）。
- 結果: Must 1（直した）、Should 4（直していない。PM に報告）、Nit 4（直していない）。
  - 自分の懸念 7 点のうち 2 つは、反対弁護人の Should 2・4 と同じもの。残りは「問題なし」か Nit 6 に入れた（下）。
- 格下げは、実行した証拠があるものだけにした。読んだだけのものは、そう書いた。

#### Must 1（直した）: サーバーのタスクのロールから master のパスワードを SSM で読める

- 場所: `IaC/terraform/aws-managed/workflow/iam.tf` の `data "aws_iam_policy_document" "task"` の `Parameters`（既存）。
- 破綻シナリオ:
  - `Parameters` は `ssm:GetParameter` を `/<prefix>/*` で許している。
  - Nautobot の master のパスワードは `/<prefix>/nautobot/db-password` にある（`pipeline/nautobot/locals.tf:111`。`name_prefix` の式は workflow と同じ）。
  - そのため、env と secrets から外しても、ECS Exec のシェル（`enable_execute_command = true`）や worker の boto3 から `aws ssm get-parameter --with-decryption` で引ける。
  - サイクルの目的（036 のリスク 7 を閉じる）が果たせず、temporal.md の「見えるのはロール `temporal` のパスワードだけ」が事実と違っていた。
- 証拠（読んだ）:
  - statement は今回の差分ではなく既存のもの。
  - worker（`app/temporal/awsio.py`）が呼ぶ ssm の API は `send_command` / `get_command_invocation` だけで、`get_parameter` は呼ばない。
- 直し方:
  - `DenyNautobotParameters`（`ssm:GetParameter*` を `/<prefix>/nautobot/*` で Deny）を足した。
  - `/<prefix>/*` の Allow は消していない。消すと、ほかに読んでいる経路が無いことを AWS で確かめる必要があるため。
  - Deny の resource は `local.nautobot_db_password_arn` を使わず、`param_prefix` から組んだ。前者は state が無いと `""` になるため。
  - 実行ロール（init の `secrets` を受け取る `execution_db_passwords`）は別のロールなので、この Deny は効かない。
- 検証:
  - `tests/test_workflow.py` に check を 1 つ足した。
  - Deny の statement を消すとその check が AssertionError で落ち、戻すと `./ops/check.sh` が「すべて通過」（通過 419 / 失敗 0）になった。
  - AWS での実際の拒否（ECS Exec からの `get-parameter` が AccessDenied になること）は未確認。

#### Should 2（直していない）: イメージを戻すと、サーバーが起きない

- 場所: `docker/images/temporal-server/entrypoint.sh:40`（版の完全一致）。
- 破綻シナリオ:
  - `TEMPORAL_SERVER_IMAGE_TAG` を古い版に戻すと、DB の版がイメージの最新より新しくなる。
  - init の `update-schema` は 0 件で exit 0 になり、`run_temporal_init` は通る。
  - サーバーは「まだ」と判定し続け、作り直しを繰り返す。デプロイが終わらず WF_WARN になる。
  - Temporal 本体（VerifyCompatibleVersion）は、DB が新しい場合を許している。
- 証拠（実行した）: `<scratchpad>/sr042b.sh` で、偽の psql が 1.20 を返し、イメージが 1.19 のとき「待つ（60 回で exit 1）」になる。
- 直し方の案: 判定を「DB ≥ イメージ」にする（major / minor を数で比べる）。design は完全一致と書いているので、design 側の判断が要る。

#### Should 3（直していない）: healthCheck が待ちのループより先にタスクを止める

- 場所:
  - `ecs.tf` の healthCheck（`startPeriod` 300、10 秒 × 6 回）
  - `entrypoint.sh` の待ちのループ（10 秒 × 60 回）
  - docs: `docs/workflow.md:158`、`temporal.md:61` / `:120`
- 破綻シナリオ:
  - init が走らない環境では、約 360 秒で ECS がタスクを UNHEALTHY として止める（SIGTERM → exit 143）。
  - 600 秒のループは終わらない。「60 回待っても…」の行と init のログの案内は、ECS では出ない。
  - docs の「10 分待って exit 1」は実際の挙動と違う。
- 証拠: ecs.tf の healthCheck の値とループの回数からの計算だけ（Fargate では未確認）。
- 直し方の案: ループの上限を `startPeriod` より短くするか、毎回の待ちの行に init のログの案内を入れる。そのうえで docs の数字を合わせる。

#### Should 4（直していない）: 認証・TLS の失敗が「まだ無い」に見える

- 場所: `entrypoint.sh:30-33` の `schema_have`（`2>/dev/null || true`）。
- 破綻シナリオ:
  - 次の失敗がどれも空の値になり、ログには「無し」と「init を待つ」としか出ない。init が成功していても init を疑わせる。
    - ロール `temporal` の認証失敗
    - TLS の失敗
    - pg_hba での拒否
- 証拠（実行した）: `<scratchpad>/sr042b.sh` の出力。
  - `認証の失敗: have=[] -> ログは 無し`
  - `TLS の失敗: have=[] -> ログは 無し`
- 直し方の案: stderr を変数に取り、値が空のときにその 1 行目を log に出す（パスワードは出ない）。

#### Should 5（直していない）: init が 2 つ同時に走ると、新しい DB のスキーマが壊れる

- 場所: `ops/up-common.sh` の `run_temporal_init`、`init.sh:57-63`。
- 破綻シナリオ:
  - `run_temporal_init` は、`-workflow-init` がすでに RUNNING かどうかを見ない。die や Ctrl-C のときも stop-task しない。
  - 新しい DB で次の順に起きると壊れる。
    1. init A が `setup-schema` と `update-schema` を終える。
    2. 打ち直しで起きた init B の `setup-schema -v 0.0` で、curr_version が 0.0 に戻る。
    3. 以後の `update-schema` は v1.0 の CREATE TABLE で毎回落ちる。
  - DB を手で直すまで戻らない。
- 証拠（読んだだけ）: `init.sh:57-63`。起きる確率は低い。
- 直し方の案: run-task の前に RUNNING を `list-tasks --family` で確かめる。trap で stop-task する。

#### Nit（直していない）

- 6: `ops/up.sh:1256` / `ops/oss/up.sh:587`。
  - init の die で、Temporal と関係の無い 8-6・9・9-2 まで飛ぶ。
  - WF_WARN の案内が worker のログだけで、temporal のログを案内しない。
- 7: `up-common.sh` の `describe-tasks` 自体が失敗すると、成功したタスクでも「exitCode=?」で die する。
  - 実行した: `<scratchpad>/sr042.sh` で、空の値が `die` になる。
  - 誤診にはなるが、止まる側に倒れる。
- 8: `entrypoint.sh:32` の query に `AND db_name = current_database()` が無い。行が 2 つあると一致しない。
  - 各 DB の `schema_version` には自分の DB の行しか入らないので、手で入れない限り起きない（読んだだけ）。
- 9: `init.sh` は毎回 ALTER ROLE でパスワードを SSM の値に揃える。SSM のパラメータを手で作り直すと、動いているサーバーの新しい接続が失敗する。
  - `ensure_temporal_secrets` は作り直さないので、手で消したときだけ起きる。

#### 問題なしと判断したもの

- `run_temporal_init` の `$'\t'` での分割と `read` の受け方（Mac の bash 3.2）
  - 実行した: `<scratchpad>/sr042.sh`（`BASH_VERSION=3.2.57`）。
    - ARN と failures が正しく分かれる。
    - exitCode が `None`（起動に失敗）のときは die、`0` のときは ok。
  - 反対弁護人は実行できなかったので、こちらで実行した結果で判断した。
- `common.sh` への切り出し（`POSTGRES_PWD` と trap、PG* はどちらの入口でも要る）
  - 実行した: `./ops/check.sh` の振る舞いの check。sh で entrypoint / init / trap を動かす。
- 既存の環境（スキーマが最新）では、サーバーの待ちは初回の読みで通る。
  - 実行した: 検証 2〜5（手元の docker）。
- master のパスワードがサーバーのタスクの env・healthCheck・`/proc/1/environ` に無いこと
  - 実行した: 検証 2〜5（手元の docker）。SSM の経路は Must 1 で塞いだ。

### 残っているもの

- セルフレビューの Should 4 件と Nit 4 件（上）。直していない。PM が扱いを決める。
- Must 1 の Deny が AWS で効くこと（ECS Exec から `/<prefix>/nautobot/db-password` の get-parameter が AccessDenied になること）は未確認。
- 検証 5 の init 側（`update-schema` の最中の `docker stop`）は手元で再現しておらず、読んだだけ。
- 検証 6（AWS）は未実行。QUEUE 147 に足すのは PM。
- 手元の docker のイメージの復旧（下の事故）。PM とユーザーが扱う。

### 手元の docker のイメージを消した事故

docker の後片付けで、手元のイメージ 126 本を消した。後片付けの `cleanup042.sh` は、検証で増えたイメージだけを消すつもりで、前後の一覧の差（`comm -13`）を `docker rmi` に流した。ところが、検証の前の一覧 `images-before.txt` は名前だけ（`{{.Repository}}:{{.Tag}}`）で、比べる側の `images-after0.txt` は ID 付き（`{{.Repository}}:{{.Tag}} {{.ID}}`）で取っていた。そのため全 129 行が「増えた」と出て、検証の前からあった 126 本（`efukuda-nwc-*` の ECR のタグなど）まで rmi した。volume とビルドキャッシュは消していない。一覧を確かめずに rmi に流したのも原因で、次からは前後を同じ format で取り、消す前に件数が想定（ここでは 3 本）を超えたら止める。復旧は PM とユーザーが扱い、このサブエージェントは報告のあと docker に触っていない。

## Round 2

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。ベースは 40bedb1。対象は Round 1 のセルフレビューの Should 2〜5。design.md の設計方針 2〜3 と検証 1 のとおり。Nit 6〜9 は直さない）

### 変えたもの

- `docker/images/temporal-server/entrypoint.sh`（Should 2・3・4）
  - 判定を「DB の版 ≥ イメージの版」にした。`version_ge` で major と minor を数で比べる。どちらかの引数が空、3 つ組、`v` 付き、数でない場合は、stderr に何も出さずに偽を返す。
  - 待ちを 30 回 × 10 秒にした（300 秒。startPeriod と同じ）。
  - 毎回の行と最後の行に `init_hint` を出す。中身は、どの手順が init を run-task するかと、`terraform -chdir=… output -raw init_logs_command` でログを見る方法。
  - psql の出力は `2>&1` で受け、次の 2 つに分ける。
    - 版の行（`sed -n '/^N.N$/p' | head -n 1`）
    - それ以外の、空でない最初の 1 行（`sed -e '/^N.N$/d' -e '/^$/d' | head -n 1`）
  - 失敗した DB の分だけ、`<DB> の psql: <1 行目>` を待ちの行に足す。
  - sed は `{p;q;}` を使わず、`| head -n 1` にした。busybox sed との差を避けるため。
- `ops/up-common.sh` `run_temporal_init`（Should 5）
  - run-task の前に `aws ecs list-tasks --family <family> --desired-status RUNNING` を打つ。前の init が走っていれば、`--tasks $prev`（引用しない。複数の ARN を別々の引数にする）で `wait tasks-stopped` を 2 回まで待つ。それでも止まらなければ `die`。
  - list-tasks そのものが失敗したときも `die`。
- `tests/test_workflow.py`（検証 1 の追加分）
  - 待ちが 30 回で、毎回の行と最後の行に init_hint があること。
  - `version_ge` の単体の check。sh で 15 通りを試し、stderr が空であること。
  - DB がイメージより新しい（1.20 / 1.15、2.0 / 1.14）なら待たない。古い・形が違うなら 30 回待って exit 1。
  - 偽の psql で次を確かめる。
    - 認証が失敗したら、stderr の 1 行目が出る。2 行目とパスワードは出ない。
    - 表が無いときは `ERROR:  relation "schema_version" does not exist` が出て、2 行目の `LINE 1:` は出ない。
    - 行が無い（psql は成功で空）ときと、表が無いときの出方を分ける。
  - `run_temporal_init` で次を確かめる。
    - list-tasks → 前の init の wait → run-task の順になる。
    - 前の init が 2 つあれば、偽の aws が受けた wait の引数の数が 10 になる（`--tasks` に ARN を 2 つの引数で渡す）。
    - None は待たない。
    - 待ちが 1 回目で時間切れなら 2 回目へ進む。2 回とも時間切れなら die。
    - list-tasks が失敗したら die。
- `tests/test_oss_ops.py`: 偽の aws の `list-tasks --family` が空を返すようにした（OSS 版の up.sh の模擬が新しい呼び出しで止まらないように）。
- docs
  - `docs/workflow.md`: 「i/60」「60 回」を「i/30」「30 回（300 秒）」にした。`password authentication failed` は init がロールを作るまでは正常に出ると書いた。init が exitCode 0 で止まった後も続くなら、ロールのパスワードを疑う。
  - `docs/architecture/resources/temporal.md`:
    - 「揃うまで」を「以上になるまで」にした。60 回を 30 回（300 秒）、10 分を 5 分にした。
    - 前の init を待つことを書いた。
    - 認証の失敗の行が、init までは正常に出ることを書いた。
  - `docs/deploy.md` の 8-5: 前の init がまだ走っていれば、先にそれが止まるのを待つと書いた。
  - `IaC/terraform/aws-managed/workflow/ecs.tf:98` のコメント: 「揃うまで」を「以上になるまで（30 回 = startPeriod の 300 秒）」にした。

### 設計からの逸脱

- design.md:36 の待ちの行の例は `ops/up.sh の 8-5 が run-task する … ログは aws logs tail …`。実装は、OSS 版の手順（`ops/oss/up.sh の 8`）と、ロググループ名を引ける `terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command` も足した（ロググループ名は entrypoint からは分からないため）。
- 手順 6（検証 2〜5 の手元の docker）は実行していない。PM の指示（docker は一切使わない。イメージを消した事故の復旧待ち）による。代わりに、sh の振る舞いは偽の psql の scratchpad スクリプトで確かめた（下）。

### 検証

#### 1. テスト（`./ops/check.sh`。最後の編集のあとに取り直した）

```
$ ./ops/check.sh > check.out 2>&1; echo "rc=$?"
rc=0
$ sed -n '1,31p' check.out
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/logs  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/logs  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし

== 4. 模擬テスト
$ grep -n -E '^(==|通過|すべて)' check.out
2:== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
5:== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
27:== 3. スクリプトの構文
31:== 4. 模擬テスト
613:通過 168 / 失敗 0
782:通過 168 / 失敗 0
1333:通過 549 / 失敗 0
1413:通過 79 / 失敗 0
1417:通過 3 / 失敗 0
1497:通過 78 / 失敗 0
1507:通過 7 / 失敗 0
1618:通過 110 / 失敗 0
1764:通過 145 / 失敗 0
2013:通過 177 / 失敗 0
2221:通過 207 / 失敗 0
2288:通過 66 / 失敗 0
2403:通過 114 / 失敗 0
2523:通過 104 / 失敗 0
3076:通過 435 / 失敗 0
3078:== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
3081:すべて通過
$ uv run --group dev --group web python tests/test_workflow.py 2>&1 | tail -n 1
通過 435 / 失敗 0
$ uv run --group dev --group web python tests/test_oss_ops.py 2>&1 | tail -n 1
通過 207 / 失敗 0
```

Round 2 で足した・直した check（`check.out` から）:

```
ok entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行に init のログの見方を出して、最後は exit 1（Round 1 のセルフレビューの Should 2 / 3。cycle 042）
ok entrypoint.sh: 表が無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。…）
ok entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（…）
ok entrypoint.sh: temporal だけ古い（1.18 / 1.14）なら 30 回待って exit 1（いま: rc=1 sleep 30 回）
ok entrypoint.sh: visibility だけ古い（1.20 / 1.13）なら 30 回待って exit 1（いま: rc=1 sleep 30 回）
ok entrypoint.sh: major が小さい（0.99 / 1.14）なら 30 回待って exit 1（いま: rc=1 sleep 30 回）
ok entrypoint.sh: 形が違う（3 つ組）（1.19.1 / 1.14）なら 30 回待って exit 1（いま: rc=1 sleep 30 回）
ok entrypoint.sh: 形が違う（v 付き）（v1.19 / 1.14）なら 30 回待って exit 1（いま: rc=1 sleep 30 回）
ok entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stderr に何も出さず偽（…）
ok entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（…）
ok entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（…）
ok run_temporal_init は ecs list-tasks（前の init）→ その tasks-stopped → ecs run-task → ecs wait tasks-stopped → describe-tasks の exitCode の順で、0 以外（None を含む）で die する（Round 1 のセルフレビューの Should 5。…）
ok run_temporal_init: 前の init が 2 つ走っていれば両方を別々の引数で tasks-stopped に渡して待ってから run-task する（…）
ok run_temporal_init: list-tasks が None（text の空）なら前の init を待たない（…）
ok run_temporal_init: 前の init の待ちが 1 回目で時間切れでも 2 回目で止まれば run-task する（…）
ok run_temporal_init: 前の init が 2 回待っても止まらなければ run-task せずに die（…）
ok run_temporal_init: list-tasks そのものが失敗すれば run-task せずに die（いま: rc=1 ['ecs list-tasks'] 'DIE: 走っている Temporal の初期化のタスク（p-workflow-init）を確かめられない（上のエラー）'）
```

##### 壊して落ちるか（`<scratchpad>/r2/mutate2.py`。ファイルは書かず、open() の返りをメモリ上で入れ替える）

1 行目の「変更なし」は基準で、落ちないのが正しい。ほかの 27 件のうち 26 件が落ちた。落ちなかった 1 件（空行を err から外さない）は、下のセルフレビューの R2-8 に入れた。

```
!! 落ちない [変更なし] 失敗 0
落ちた [[S2] 版の比べを等号に戻す] 失敗 3
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: DB の版がイメージより新しい（1.20 > 1.19、1.15 > 1.14）なら待たずに namespace と tini へ進む（いま: rc=1 ['psql', 'psql', '
    - entrypoint.sh: DB の版がイメージの版とmajor が大きい（2.0 / 1.14）なら待たずに進む（いま: rc=1 sleep 30 回）
落ちた [[S2] version_ge が minor だけ見る] 失敗 3
    - entrypoint.sh: DB の版がイメージの版とmajor が大きい（2.0 / 1.14）なら待たずに進む（いま: rc=1 sleep 30 回）
    - entrypoint.sh: major が小さい（0.99 / 1.14）なら 30 回待って exit 1（いま: rc=0 sleep 0 回）
    - entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stde
落ちた [[S2] version_ge が major だけ見る] 失敗 4
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=0 sleep 0 回 'ypo
    - entrypoint.sh: temporal だけ古い（1.18 / 1.14）なら 30 回待って exit 1（いま: rc=0 sleep 0 回）
    - entrypoint.sh: visibility だけ古い（1.20 / 1.13）なら 30 回待って exit 1（いま: rc=0 sleep 0 回）
落ちた [[S2] version_ge が 3 つ組を弾かない] 失敗 1
    - entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stde
落ちた [[S2] version_ge が v1.19 を弾かない] 失敗 1
    - entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stde
落ちた [[S2] visibility を見ない] 失敗 2
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: visibility だけ古い（1.20 / 1.13）なら 30 回待って exit 1（いま: rc=0 sleep 0 回）
落ちた [[S3] 待ちを 60 回に戻す] 失敗 7
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=1 sleep 60 回 'te
    - entrypoint.sh: temporal だけ古い（1.18 / 1.14）なら 30 回待って exit 1（いま: rc=1 sleep 60 回）
落ちた [[S3] 毎回の行から init_hint を外す] 失敗 2
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: 表が無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=0 [
落ちた [[S3] init_hint から init_logs_command を外す] 失敗 3
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: 表が無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=0 [
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=1 sleep 30 回 'te
落ちた [[S3] sleep を 5 秒に] 失敗 9
    - entrypoint.sh は両方の DB の版がイメージの versioned/ の版（v を除き、数で並べた最大）以上になるまで 10 秒おきに 30 回（startPeriod の 300 秒）待ち、毎回と最後の行
    - entrypoint.sh: 表が無い → 表はあるが版が無い → 1.19 / 1.14 で 2 回待ってから namespace と tini へ進む（v1.9 より v1.19 を新しいとみる。いま: rc=0 [
    - entrypoint.sh: 版が古い（1.9 < 1.19）まま 30 回待つと namespace も tini も起こさず exit 1、init のログの見方を出す（いま: rc=1 sleep 0 回 'tem
落ちた [[S4] psql の stderr を捨てる] 失敗 3
    - entrypoint.sh の psql はロール POSTGRES_USER と POSTGRES_PWD で schema_version.curr_version を読む 1 か所だけで、stderr を捨てずに受
    - entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（いま: rc=0 'entrypoint-rds: 
    - entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（いま: rc=1 'ープ> --lo
落ちた [[S4] 毎回の行に err を出さない] 失敗 1
    - entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（いま: rc=0 'entrypoint-rds: 
落ちた [[S4] 最後の行に err を出さない] 失敗 1
    - entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（いま: rc=1 'ープ> --lo
落ちた [[S4] err を全行に] 失敗 2
    - entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（いま: rc=0 'entrypoint-rds: 
    - entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（いま: rc=1 'ral の ps
落ちた [[S4] 版を 1 行目で取る] 失敗 2
    - entrypoint.sh: psql が認証で落ちたら、待ちの行に DB ごとの stderr の 1 行目を出し（2 行目とパスワードは出さない）、版が揃えば進む（いま: rc=0 'entrypoint-rds: 
    - entrypoint.sh: 表が無い（psql の失敗。2 行目の LINE 1: は出さない）と行が無い（psql は成功で空）を分けて出し、最後の行にも psql の失敗を出す（いま: rc=1 ' not exi
落ちた [[S5] list-tasks を呼ばない] 失敗 11
    - run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（サービスと同じサブネット・SG、公開 IP 無し。いま: rc=0 [
    - run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（None…。いま: rc=1 ['ecs run-task'] 'DIE: Temp
    - run_temporal_init: run-task が failures を返せば（タスクの ARN があっても）待たずに die（k/c1/abc…。いま: rc=1 ['ecs run-task'] ' の初期化
落ちた [[S5] list-tasks の失敗で止まらない] 失敗 1
    - run_temporal_init: list-tasks そのものが失敗すれば run-task せずに die（いま: rc=1 ['ecs list-tasks', 'ecs run-task', 'ecs wai
落ちた [[S5] None を走っている扱い] 失敗 1
    - run_temporal_init: list-tasks が None（text の空）なら前の init を待たない（いま: ['ecs list-tasks', 'ecs wait', 'ecs run-task'
落ちた [[S5] 前の init の待ちを 1 回だけ] 失敗 2
    - run_temporal_init: 前の init の待ちが 1 回目で時間切れでも 2 回目で止まれば run-task する（いま: rc=1 ['ecs list-tasks', 'ecs wait']）
    - run_temporal_init: 前の init が 2 回待っても止まらなければ run-task せずに die（いま: rc=1 ['ecs list-tasks', 'ecs wait'] 'たっても止まらな
落ちた [[S5] 前の init が止まらなくても進む] 失敗 1
    - run_temporal_init: 前の init が 2 回待っても止まらなければ run-task せずに die（いま: rc=0 ['ecs list-tasks', 'ecs wait', 'ecs wait
落ちた [[S5] $prev を引用する（2 つで壊れる）] 失敗 1
    - run_temporal_init: 前の init が 2 つ走っていれば両方を別々の引数で tasks-stopped に渡して待ってから run-task する（いま: rc=0 ['ecs list-tasks'
落ちた [[S5] --family を外す] 失敗 2
    - run_temporal_init は ecs list-tasks（前の init）→ その tasks-stopped → ecs run-task → ecs wait tasks-stopped → descri
    - run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（サービスと同じサブネット・SG、公開 IP 無し。いま: rc=0 [
落ちた [[S5] 前の init を run-task のあとで待つ] 失敗 3
    - run_temporal_init: 前の init が 2 つ走っていれば両方を別々の引数で tasks-stopped に渡して待ってから run-task する（いま: rc=0 ['ecs list-tasks'
    - run_temporal_init: 前の init の待ちが 1 回目で時間切れでも 2 回目で止まれば run-task する（いま: rc=0 ['ecs list-tasks', 'ecs run-task', 
    - run_temporal_init: 前の init が 2 回待っても止まらなければ run-task せずに die（いま: rc=1 ['ecs list-tasks', 'ecs run-task', 'ecs 
落ちた [[S2] version_ge の形の検査を全部外す] 失敗 1
    - entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stde
落ちた [[S2] version_ge がイメージの版（第 2 引数）の形を見ない] 失敗 1
    - entrypoint.sh の version_ge: major.minor を数で比べ（1.20 ≥ 1.19、2.0 ≥ 1.19、1.9 < 1.19）、どちらかが空・3 つ組・v 付き・数でないものは stde
落ちた [[S5] 前の init の ARN を 1 つの引数にまとめる] 失敗 1
    - run_temporal_init: 前の init が 2 つ走っていれば両方を別々の引数で tasks-stopped に渡して待ってから run-task する（いま: rc=0 ['ecs list-tasks'
!! 落ちない [[S4] 空行を err から外さない] 失敗 0
```

##### sh での振る舞い（`<scratchpad>/r2/ep-sh.sh`。偽の psql / nc / sleep / ns / tini で entrypoint.sh を動かす）

- イメージの版は 1.19 / 1.14（`versioned/` は v1.0・v1.9・v1.19 と v1.0・v1.14）。
- 各ケースで、ログの最初と最後の行、終了コード、行数、sleep の回数、起こしたものを出す。行が 1 行のときは、最初と最後に同じ行が 2 回出る。
- パスワード（`pw-role-secret`）と stderr の 2 行目（`second line`）がログに出たら `!!` の行が出る。どのケースでも出ていない。
- 下の出力は `/bin/sh` で取った。`/bin/dash` でも取り、`diff ep-sh.out ep-dash.out` は空だった（rc=0）。

```
=== DB がイメージより新しい（temporal=1.20 visibility=1.15。イメージは 1.19 / 1.14）
entrypoint-rds: スキーマは temporal=1.20 / temporal_visibility=1.15（イメージの版 1.19 / 1.14 以上）
entrypoint-rds: スキーマは temporal=1.20 / temporal_visibility=1.15（イメージの版 1.19 / 1.14 以上）
rc=0 行数=1 sleep=0 起こしたもの: ns 127.0.0.1:7233 default 72h tini -- /etc/temporal/entrypoint.sh 
=== DB がイメージと同じ（temporal=1.19 visibility=1.14。イメージは 1.19 / 1.14）
entrypoint-rds: スキーマは temporal=1.19 / temporal_visibility=1.14（イメージの版 1.19 / 1.14 以上）
entrypoint-rds: スキーマは temporal=1.19 / temporal_visibility=1.14（イメージの版 1.19 / 1.14 以上）
rc=0 行数=1 sleep=0 起こしたもの: ns 127.0.0.1:7233 default 72h tini -- /etc/temporal/entrypoint.sh 
=== DB が古い（1.9 < 1.19）（temporal=1.9 visibility=1.14。イメージは 1.19 / 1.14）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））を待つ（1/30。スキーマの版 temporal=1.9/1.19 temporal_visibility=1.14/1.14）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））が 30 回（300 秒）待っても終わらない（スキーマの版 temporal=1.9/1.19 temporal_visibility=1.14/1.14）。ops/up.sh（OSS 版は ops/oss/up.sh）を打ち直すか、init のログを見る
rc=1 行数=31 sleep=30 起こしたもの: 
=== 認証の失敗（temporal=AUTH visibility=AUTH。イメージは 1.19 / 1.14）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））を待つ（1/30。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14。temporal の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: FATAL:  password authentication failed for user "temporal"。temporal_visibility の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: FATAL:  password authentication failed for user "temporal"）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））が 30 回（300 秒）待っても終わらない（スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14。temporal の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: FATAL:  password authentication failed for user "temporal"。temporal_visibility の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: FATAL:  password authentication failed for user "temporal"）。ops/up.sh（OSS 版は ops/oss/up.sh）を打ち直すか、init のログを見る
rc=1 行数=31 sleep=30 起こしたもの: 
=== TLS の失敗（visibility は揃っている）（temporal=SSL visibility=1.14。イメージは 1.19 / 1.14）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））を待つ（1/30。スキーマの版 temporal=無し/1.19 temporal_visibility=1.14/1.14。temporal の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: SSL error: certificate verify failed）
entrypoint-rds: 初期化のタスク（ops/up.sh の 8-5（OSS 版は ops/oss/up.sh の 8）が run-task する <接頭辞>-workflow-init。ログは terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command（aws logs tail <ロググループ> --log-stream-name-prefix init））が 30 回（300 秒）待っても終わらない（スキーマの版 temporal=無し/1.19 temporal_visibility=1.14/1.14。temporal の psql: psql: error: connection to server at "db" (10.0.0.1), port 5432 failed: SSL error: certificate verify failed）。ops/up.sh（OSS 版は ops/oss/up.sh）を打ち直すか、init のログを見る
rc=1 行数=31 sleep=30 起こしたもの: 
```

`version_ge` を同じ入力に当てた結果（`<scratchpad>/r2/vge.sh`。`<DB の版>:<イメージの版>=<終了コード>`）:

```
/bin/sh: 1.19:1.19=0 1.20:1.19=0 2.0:1.19=0 1.9:1.19=1 0.99:1.14=1 :1.19=1 1.19.1:1.19=1 v1.19:1.19=1 1.:1.19=1 .19:1.19=1 1.x:1.1=1 119:1.19=1 1.19:1.20.1=1 1.19:=1 1.19:v1.19=1 
/bin/dash: 1.19:1.19=0 1.20:1.19=0 2.0:1.19=0 1.9:1.19=1 0.99:1.14=1 :1.19=1 1.19.1:1.19=1 v1.19:1.19=1 1.:1.19=1 .19:1.19=1 1.x:1.1=1 119:1.19=1 1.19:1.20.1=1 1.19:=1 1.19:v1.19=1
```

#### 2〜5. 手元の docker

未実行。PM の指示（docker は一切使わない。イメージを消した事故の復旧待ち）による。Round 2 の変更（待ちの判定・回数・log・`run_temporal_init`）は、Round 1 の検証 2〜5 の結果を変えない範囲だが、イメージの中の busybox の sh / sed で entrypoint.sh を動かしたことはまだ無い（下の R2-3）。

#### 6. AWS

未実行（design.md のとおり、このサイクルではやらない）。

### セルフレビュー

- 体制
  - 自分: opus-5.5 / high。design.md・差分・テストを突き合わせ、上の mutate2.py で check を 1 本ずつ壊した。サブエージェントの中なので effort は切り替えられない。
  - 反対弁護人: general-purpose のサブエージェント（opus / xhigh）。次を渡し、読み取り専用で走らせた。
    - design.md と build.md のパス、変更ファイルの一覧
    - 選んだ方針と理由
    - 不安な箇所: busybox の sed、`2>&1` で WARNING が混ざること、`$prev` を引用しないこと、待ちの秒数と ECS が止める時刻のずれ
  - 反対弁護人のあと、`git status --porcelain -uall` が走らせる前と同じ 8 件の ` M` だけであることを確かめた。
- 結果: Must 0。Should 3 件のうち 2 件を直し、1 件（R2-3）は busybox で確かめられていないので残す。Nit 7 件のうち 4 件を直し、3 件は直していない。

#### R2-1（Should。直した）[missing tests] version_ge の形の検査に check が無い

- 場所: `entrypoint.sh:40-45`。
- 破綻シナリオ: 形の検査（3 つ組・`v` 付き・空）を外しても、テストが通っていた。外すと `[ 1.19.1 -ge … ]` が stderr にエラーを出し、空の版で真偽が崩れる。
- 確かめたこと: 直す前の mutate2.py では「version_ge が 3 つ組を弾かない」「v1.19 を弾かない」「形の検査を全部外す」の 3 件が落ちなかった。
- 直したこと: `tests/test_workflow.py` に version_ge の単体の check を足した（sh で 15 通り、stderr が空）。上の mutate2.out では、3 件とも落ちる。

#### R2-2（Should。直した）[correctness / 運用] 認証の失敗の行が、init の前でも出るのは正常なのに、docs がパスワードを疑わせていた

- 場所: `docs/workflow.md:158`、`docs/architecture/resources/temporal.md:61`、`entrypoint.sh` の schema_read のコメント。
- 破綻シナリオ: 初回の up.sh では、init がロール `temporal` を作るまで、サーバーの psql は失敗する。PostgreSQL はロールが無いときも、ロールの有無を隠すために `password authentication failed` を返す。そのため、正常な待ちの間に出る行を見た人が、SSM のパスワードを作り直しに行く。
- 確かめたこと: PostgreSQL の既知の振る舞い（読んだだけ。手元の docker は使えないので再現していない）。
- 直したこと:
  - docs 2 か所とコメントに「init がロールを作るまでは正常。init が exitCode 0 で止まった後も続くなら、パスワードを疑う」と書いた。
  - テストの偽の psql の ERR を、実際に返る形（`ERROR:  relation "schema_version" does not exist` と 2 行目の `LINE 1:`）にした。

#### R2-3（Should。直していない。残す）[runtime bugs] busybox の sh / sed で動かしていない

- 場所: `entrypoint.sh:36-45`。
- 破綻シナリオ: Alpine のイメージの `/bin/sh` は busybox ash、sed は busybox sed。手元で確かめたのは macOS の `/bin/sh` と `/bin/dash` だけ。busybox で sed の正規表現や `case` の扱いが違えば、版が取れずに 30 回待って exit 1 になり、デプロイが止まる。
- 確かめたこと: 確かめられていない（docker 禁止）。
- やったこと: 差が出やすい `sed -n '/…/{p;q;}'`（ブロックと `q`）を使わず、`sed -n '/…/p' | head -n 1` と `sed -e … -e …` にした。どちらも POSIX の範囲。
- 片付け方: docker か AWS（検証 6）で、イメージの中で entrypoint.sh を動かして確かめる。Should のまま、PM に報告する。

#### R2-4（Nit。直した）[correctness] version_ge がイメージの版（第 2 引数）の形を見ていなかった

- 破綻シナリオ: `versioned/` に `v1.20.1` のような名前ができると、`[` がエラーを出す。
- 直したこと: `for _v in "$1" "$2"` で両方を見るようにした。vge.sh の `1.19:1.20.1=1`、`1.19:=1` と、mutate2 の「イメージの版（第 2 引数）の形を見ない」が落ちることで確かめた。

#### R2-5（Nit。直した）[テスト] `--tasks $prev` が 2 つの引数で渡ることをテストが見分けられなかった

- 直したこと: 偽の aws の wait が引数の数を `wargc` に残すようにし、前の init が 2 つのときに 10 になることを check した。mutate2 の「$prev を引用する」と「ARN を 1 つの引数にまとめる」が落ちることで確かめた。

#### R2-6（Nit。直した）init_hint がロググループ名を引けなかった

- 破綻シナリオ: `terraform output init_logs_command` だけでは、どの root か（`-chdir`）が分からず、`-raw` が無いと引用符つきで出る。
- 直したこと: `terraform -chdir=IaC/terraform/aws-managed/workflow output -raw init_logs_command` にした。

#### R2-7（Nit。直した）entrypoint のヘッダーのコメントが「必ず先に『終わらない』の行が出る」と言い切っていた

- 破綻シナリオ: DB の 5432 が開くまでの待ち（`wait_for_db`）が長いと、ループが 30 回目に届く前に ECS が止める（startPeriod 300 秒 + 6 回 × 10 秒）。
- 直したこと: 「ふつうはその前に出る（DB の 5432 が開くのが遅いと間に合わないこともある）」に直した。

#### Nit（直していない）

- R2-8: psql の出力の空行を err から外す `-e '/^$/d'` に、縛る check が無い（mutate2 の「空行を err から外さない」が落ちない）。
  - err は log にしか使わず、待ちの判定（schema_have）には使わない（読んだだけ）。
  - 外れても、空行から始まる失敗のときに失敗の理由が log に出ないだけ。
- R2-9: `list-tasks --desired-status RUNNING` は、すでに止めに入ったタスク（desiredStatus が STOPPED で、まだ lastStatus が STOPPED でない）を見ない。
  - 誰かが stop-task した直後の init は SIGTERM で trap に入り exit 143 で終わるので、窓は短い（読んだだけ）。
- R2-10: psql の stderr の WARNING 行が 1 行目に来ると、本当の失敗の行の代わりに WARNING が出る。
  - コメントには書いた。振る舞いは変えていない。

#### 問題なしと判断したもの

- 「1.20 に対して 1.19 が通る」と「認証失敗の stderr が log に出る」
  - 実行した: ep-sh.sh（/bin/sh と /bin/dash）と check.sh。
- パスワードと stderr の 2 行目がログに出ないこと
  - 実行した: ep-sh.sh の `!!` の行が無い。check の「2 行目とパスワードは出さない」。
- `$prev` を引用しないこと（ARN は空白を含まない。bash 3.2 で 2 つの引数に分かれる）
  - 実行した: check の wargc = 10。
- 待ちの 30 回 × 10 秒と、ECS が止める時刻（startPeriod 300 秒 + 6 回 × 10 秒）の関係
  - ecs.tf の値とループの数から計算しただけ（Fargate では未確認）。

### 残っているもの

- R2-3: busybox の sh / sed で entrypoint.sh を動かしていない。docker か AWS（検証 6）で確かめる。
- Nit R2-8〜R2-10（上）。直していない。
- Round 1 の Nit 6〜9 は、指示どおり直していない。
- design.md:90（検証 3）に「（1/60）」が残っている。いまは 30 回なので「（1/30）」。design.md は PM が直す。
- 検証 2〜5（手元の docker）と検証 6（AWS）は未実行。

## Round 3

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。ベースは 5a67875。対象は review.md の「Round 2 の PM の確認」の Should 2 だけ。design.md の設計方針 2 の `failures` と検証 1 の末尾の check）

### 変えたもの

- `ops/up-common.sh` `run_temporal_init`
  - run-task の `--query` を `[tasks[0].taskArn, length(failures), failures[0].arn, failures[0].reason, failures[0].detail]` にした。
  - 返りは `IFS=$'\t' read -r task nfail f_arn f_reason f_detail <<<"$out"` で切る。`nfail` が 0 以外、または task が空か None なら die。
  - die の文面は `run-task の返り: taskArn=… failures=N 件。failures[0]: reason=… arn=… detail=…`（無い値は `?`）。
- `tests/test_workflow.py`
  - check を 1 件足した。偽の aws の run-task が failures を返す 2 通りで、die に reason / arn / detail が出ることを見る。
    - `RESOURCE:ENI`
    - 空白を含む `Capacity is unavailable at this time. …`
  - 同じ check で、偽の aws が受けた `--query` を全文で見る。
  - 既存の成功の偽の返り（9 か所）を、実物の text 出力の形 `<taskArn>\t0\tNone\tNone\tNone` に直した（セルフレビューの R3-1）。

### 設計からの逸脱

- PM の文言は「`--query` に `failures[0].[arn, reason, detail]` を足す」だった。実装は入れ子にせず、平らに並べた。
  - 理由: 入れ子だと aws CLI の `--output text` が 2 行に割れて、1 行の read では拾えない。
  - 確かめ方: 127.0.0.1 の偽の ECS エンドポイント（`<scratchpad>/r3/srv.py`、`t.sh`）に aws-cli 2.36.34 を向けて、text 出力を見た。AWS には出ていない。
    ```
    == fail [tasks[0].taskArn, length(failures), failures[0].arn, failures[0].reason, failures[0].detail]
    task=[None] nfail=[1] arn=[arn:aws:ecs:ap-northeast-1:1:container-instance/x] reason=[RESOURCE:ENI] detail=[eni limit reached here]
    == fail [tasks[0].taskArn, length(failures), failures[0].[arn, reason, detail]]
    0000000    N   o   n   e  \t   1  \n   a   r   n ...
    task=[None] nfail=[1] arn=[] reason=[] detail=[]
    == ok [tasks[0].taskArn, length(failures), failures[0].arn, failures[0].reason, failures[0].detail]
    task=[arn:aws:ecs:r:1:task/c1/abc] nfail=[0] arn=[None] reason=[None] detail=[None]
    == noarn [tasks[0].taskArn, length(failures), failures[0].arn, failures[0].reason, failures[0].detail]
    task=[None] nfail=[1] arn=[None] reason=[AGENT] detail=[None]
    ```

### 検証

#### 1. テスト（`./ops/check.sh`。最後の編集のあとに取り直した）

```
$ ./ops/check.sh > check3.log 2>&1; echo rc=$?
rc=0
$ grep -n "通過 4\|failures\[0\] の reason" check3.log; tail -1 check3.log
2841:ok run_temporal_init: run-task の failures があれば die の文面に failures[0] の reason / arn / detail を出す（Fargate のキャパシティ不足などはここにしか出ない。いま: rc=1 ['ecs list-tasks', 'ecs run-task'] 'こせない（run-task の返り: taskArn=None failures=1 件。failures[0]: reason=RESOURCE:ENI arn=arn:aws:ecs:r:1:container-instance/ci-1 detail=ENI limit reached for subnet-1）' / rc=1 'res=1 件。failures[0]: reason=Capacity is unavailable at this time. Please try again later arn=None detail=None）'）
3077:通過 436 / 失敗 0
すべて通過
```

test_workflow.py の通過は 435（Round 2）から 436 になった。

##### 壊して落ちるか（`<scratchpad>/r3/mutate3.py`。ファイルは書かず、`ops/up-common.sh` の open() の返りをメモリ上で入れ替えて test_workflow.py を走らせる）

1 行目の「変更なし」は基準で、落ちないのが正しい。ほかの 10 件はすべて落ちた。そのうち最後の 2 件は、反対弁護人の指摘（R3-1）を受けて足したもの。直す前は 2 件とも `!! 落ちない … 通過 436 / 失敗 0` だった。

```
基準 [変更なし] rc=0 最後の行: 通過 436 / 失敗 0
落ちた [Round 2 の形に戻す（クエリは length(failures) だけ、die は ${out} をそのまま）] rc=1
落ちた [クエリから failures[0].reason を落とす] rc=1
落ちた [クエリを入れ子 failures[0].[arn, reason, detail] にする] rc=1
落ちた [die の文面から reason を消す] rc=1
落ちた [die の文面から arn を消す] rc=1
落ちた [die の文面から detail を消す] rc=1
落ちた [read の IFS を既定（空白も区切る）にする] rc=1
落ちた [failures の件数を見ない（タスクの ARN があれば進む）] rc=1
落ちた [件数を Round 2 の末尾のフィールドで見る（実物の成功 arn\t0\tNone\tNone\tNone で毎回 die）] rc=1
    AssertionError: run_temporal_init: exitCode 0 なら run-task → wait → describe-tasks で抜け、先へ進む（…）
落ちた [クエリの先頭 2 つを入れ替える（task に件数が入る）] rc=1
    AssertionError: run_temporal_init: run-task の failures があれば die の文面に failures[0] の reason / arn / detail を出す（…）
```

最初の 8 件の AssertionError は、どれも今回足した check か、既存の「run-task が failures を返せば（タスクの ARN があっても）待たずに die」の check。

#### 2〜6

未実行。Round 3 の対象外（手元の docker は使わない指示。AWS は 147 の確認）。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので切り替えられない）
- 反対弁護人: opus / effort xhigh（文脈あり。読み取り専用）
  - 返ったあとの `git status --porcelain -uall` は ` M ops/up-common.sh` と ` M tests/test_workflow.py` だけで、ファイルは増えていない。
- 結果: Must 0 / Should 1（直した）/ Nit 3

#### R3-1 Should（直した）

- [missing tests] `tests/test_workflow.py:1301` ほか成功の偽の返り 9 か所と、`:1326`
- 破綻シナリオ: 成功の偽の返りが `<taskArn>\t0` の 2 フィールドのままだった。`--query` も末尾の部分文字列でしか見ていなかった。このため、次の 2 つの改変がどちらも 436 のまま通った。実物ではどちらも、成功のたびに up.sh が die する。
  - 件数を Round 2 の `${out##*$'\t'}` で見る
  - クエリの先頭 2 つを入れ替える
- 再現: mutate3.py に 2 件足して走らせ、`!! 落ちない` を確かめた。
- 直した: 成功の返りを 5 フィールドにし、`--query` を全文で見るようにした。直したあとは 2 件とも落ちる（上の表）。

#### R3-2 Nit（直した。自分の行だけ）

- [正確さ] `ops/up-common.sh:387-388`
- 「ENI の上限は failures にしか出ない」は、Fargate では正確でない。
  - RunTask の `RESOURCE:ENI` は EC2 のコンテナインスタンスの場合。
  - Fargate の ENI の不足は、タスクが作られたあとの `TaskFailedToStart` に出る。これは既存の describe-tasks の die が拾う。
- 反対弁護人が AWS の docs を引いた。自分では docs を読んでいない。
- コメントと check の名前を「Fargate のキャパシティ不足（Capacity is unavailable…）など」に直した。
- `design.md:34` の同じ文言は PM の範囲なので残っている。

#### R3-3 Nit（直さない）

- [runtime] `ops/up-common.sh:390-392`
- 空文字のフィールドがあると、IFS の TAB は空白扱いなので詰まって、後ろの値がずれる。detail に改行があると、2 行目以降が落ちる。
- 反対弁護人が、実物の CLI に `None\t1\t\t\td1` を返させて再現した（`reason=? arn=d1 detail=?`）。
- 据え置く理由: ECS は値の無い項目を null で返し、text では `None` になる（上の `noarn` の出力）。die には必ず着き、出る文面がずれるだけ。

#### R3-4 Nit（直さない。範囲外・既存）

- [文面] `ops/up-common.sh:386`
- run-task が 0 以外で返ったときの die が「ecs:RunTask と iam:PassRole が要る」と断定している。Throttling や InvalidParameter でも同じ文面になる。

#### 問題なしとした観点と根拠

- 反対弁護人が /bin/bash 3.2.57 と `set -euo pipefail` の下で、抜き出した関数を実物の CLI の出力で動かした。どの場合も die に着く（2 件目以降は件数にだけ出る）。
  - failures が 2 件
  - tasks と failures が両方ある
  - tasks も failures も空
  - out が空
  - read が EOF で 1 を返す
- テストの偽の aws も `bash`（PATH の /bin/bash 3.2）で動いている。

### 残っているもの

- `tests/test_oss_ops.py:121` の偽の aws の run-task は、まだ `<arn>\t0` の 2 フィールドを返す。nfail は 0 で読めるので通る。範囲外なので触っていない。
- `design.md:34` の「ENI の上限は failures[].reason にしか出ない」（R3-2）。
- R3-3、R3-4（Nit）。
- 検証 2〜6 は未実行。
