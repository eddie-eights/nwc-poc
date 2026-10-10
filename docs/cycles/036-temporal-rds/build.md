# Temporal の履歴を RDS に残す（036）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/036-temporal-rds（cd79d00 から）。AWS には触っていない（検証 5 は設計どおり後の AWS 動作確認に回す）。

### 変更ファイル

- ステップ 1: 新規 `docker/images/temporal-server/{Dockerfile,entrypoint.sh,dynamicconfig.yaml}`。`docker/images/temporal/Dockerfile`（ワーカー）はコメントだけ
- ステップ 2: `pipeline/nautobot/{outputs,variables}.tf`・`terraform.tfvars.example`（出力 `db_address` / `db_port` / `db_password_parameter`、`db_engine_version` 17 → 18）、`base/ecr/{main,outputs,variables}.tf`（`temporal-ui`）、`base/core/security_groups.tf`（`sg_flows` 3 行とコメント）
- ステップ 3: `workflow/{ecs,iam,locals,variables}.tf`・`terraform.tfvars.example`（3 コンテナ、healthCheck、min 100 / max 200、`execution_db_passwords`、nautobot の remote state）
- ステップ 4: `ops/up-common.sh`（`TEMPORAL_SERVER_VERSION=1.32.1` / `TEMPORAL_UI_TAG=2.55.0` / `POSTGRES_MAJOR=18`、`build_temporal_server`、`mirror_temporal_ui`、`ensure_temporal_secrets`）、`ops/up.sh`、`ops/oss/up.sh`（nautobot の apply に `-var db_engine_version=$POSTGRES_MAJOR`、workflow の apply に `temporal_image_tag`）
- ステップ 5: `tests/test_workflow.py`、`tests/test_analytics.py`、`tests/test_lab_debug.py`、`tests/test_oss_ops.py`
- ステップ 6: `docs/architecture/resources/temporal.md`（書き直し）、`docs/workflow.md`、`docs/data-stores.md`、`docs/architecture/{core,workflow}.md`、`docs/architecture/resources/{ecr,nautobot}.md`、`docs/deploy.md`、`docs/development.md`、`docs/faq-fukuda-nwc-poc.md`。`README.md` / `docs/oss-variant.md` は該当の記述が無く変えていない

### 設計からの逸脱

- pytest は使わない。このリポジトリのテストは `check()` の自前スクリプトで、全部を `bash ops/check.sh` が回す（検証 1 はそれで取った）
- `docker/images/temporal-server/dynamicconfig.yaml` を足した。`temporalio/server` 1.32.1 の公式 entrypoint は `DYNAMIC_CONFIG_FILE_PATH`（既定 `config/dynamicconfig/docker.yaml`）が無いと起動しない
- entrypoint のパスワードは psql の `-v` ではなく `\getenv pw POSTGRES_PWD` で読む（コマンドラインに出さない）
- master のロールへの所属は `pg_has_role(CURRENT_USER, role, 'SET')` で見て、無ければ `GRANT role TO CURRENT_USER`。PostgreSQL 16 以降、CREATEROLE で作った側は ADMIN だけで SET が無く、`CREATE DATABASE ... OWNER` が通らない（下の検証 2 の非 superuser の確認）
- 検証 2 の `temporal workflow describe -w wf1` は CLI 1.9.1 の表形式に `Status` の行が無い。`-o json` の `status` と `workflow list` で見た
- 検証 2 の `docker logs temporal | grep -c setup-schema` は `docker restart` 後も 1 回目の起動のログを含むので、2 回目の起動の行（`ロール ... を確かめる` の 2 回目以降）だけで数えた
- 検証 3 の `grep -c 'name = "temporal"...'` は terraform fmt が `name      = "temporal"` と揃えるので 0 になる。`grep -cE 'name += "(temporal|ui|worker)"'` で数えた
- nautobot の apply の `-var` は `db_engine_version` を `nautobot_db_az_num` の前に置いた（`tests/test_analytics.py` の AZ_NUM の検査が `nautobot_db_az_num=...` を行末で探すため）
- SG の `workflow` の description（`security_groups.tf:39`、`Temporal dev server and worker ECS task`）は変えていない。description を変えると SG が作り直しになる（同じファイルのコメント）。Nit として残す

### テストの期待値を変えたもの（元の期待値が誤りになった理由）

- `tests/test_workflow.py`: start-dev のコマンドと SQLite のパス → 3 コンテナ、healthCheck、`command` が無いこと（公式の入口を使う）、min 100 / max 200。ミラーの検査 → `build_temporal_server` と `mirror_temporal_ui`、版の一致（`TEMPORAL_UI_TAG` と `temporal_ui_image_tag`、`POSTGRES_MAJOR` と `db_engine_version` と Dockerfile の ARG）。SG の検査 → workflow 宛ての行は自分宛ての 6933〜6939 / 7233〜7239 だけ、`workflow → nautobot_db 5432` がある。理由: 設計で構成を替えたため
- `tests/test_analytics.py`: `EXPECTED_FLOWS` に 3 行。7233 の検査は「workflow 以外から 7233 を開けない」に（自分宛ては設計で開ける）。_SINGLE の理由の文言は ecs.tf のコメントに合わせた
- `tests/test_lab_debug.py`: `dir_tag` の呼び元が 12 → 14 か所（temporal-server を up.sh と oss/up.sh に足した）。temporal-server は context が `docker/images/temporal-server/` そのものなので、`-f` の代わりにディレクトリを渡す形を許す
- `tests/test_oss_ops.py`: SSM の件数 13 → 14（`temporal/db-password`）、secrets 11 → 12、削除 13 → 14。apply の文字列に `temporal_image_tag` と `db_engine_version`

### 検証

#### 1. 文字列の検査

`bash ops/check.sh > check2.log 2>&1`（終了コード 0。ツールがエラーを返さなかった）の要所:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（20 ルートとも OK）
== 3. スクリプトの構文
bash -n: 29 本
構文エラーなし
（4. 模擬テストの各ファイルの末尾）
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 110 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 206 / 失敗 0
通過 66 / 失敗 0
通過 114 / 失敗 0
通過 104 / 失敗 0
通過 355 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

```
$ grep -rn 'start-dev\|SQLite' IaC ops docs README.md --exclude-dir=.terraform | grep -v cycles | grep -v grafana | wc -l
       0
```

#### 2. 手元の docker で起動と永続化

最終のイメージを `docker build -t nwc-temporal-server:test docker/images/temporal-server/`（rc=0）で作り直し、設計のコマンドどおり `pg`（postgres:18）と `temporal` を起こした。

```
$ docker exec temporal temporal operator namespace describe -n default --address 127.0.0.1:7233
（起動から約 12 秒。最初の 1 回は namespace のキャッシュ前で "Namespace default is not found." で exit 1、次の 1 回で以下）
  NamespaceInfo.Name                    default
  NamespaceInfo.State                   Registered
  Config.WorkflowExecutionRetentionTtl  72h0m0s

$ docker exec pg psql -U nautobot -tAc "SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE datname LIKE 'temporal%'"
temporal|temporal
temporal_visibility|temporal

$ docker exec temporal temporal workflow start --type Hello --task-queue q --workflow-id wf1 --address 127.0.0.1:7233
Running execution:
  WorkflowId  wf1
$ docker restart temporal
$ docker exec temporal temporal workflow list --address 127.0.0.1:7233
  Status   WorkflowId  Type     StartTime
  Running  wf1         Hello  3 seconds ago
$ docker exec temporal temporal workflow describe -w wf1 --address 127.0.0.1:7233 -o json
    "status": "WORKFLOW_EXECUTION_STATUS_RUNNING",

$ docker logs temporal 2>&1 | grep -n "entrypoint-rds"
1:entrypoint-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
2:entrypoint-rds: temporal: 初回なので setup-schema -v 0.0
8:entrypoint-rds: temporal: update-schema
166:entrypoint-rds: temporal_visibility: 初回なので setup-schema -v 0.0
172:entrypoint-rds: temporal_visibility: update-schema
358:entrypoint-rds: namespace: default を作った（retention 72h）
365:entrypoint-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
367:entrypoint-rds: temporal: update-schema
372:entrypoint-rds: temporal_visibility: update-schema
380:entrypoint-rds: namespace: default がある
$ docker logs temporal 2>&1 | sed -n '365,$p' | grep -c setup-schema
0
（2 回目の update-schema は "found zero updates from current version 1.19" / "1.14" で UpdateSchemaTask done。エラー無し）

$ docker run -d --name ui --network t036 -e TEMPORAL_ADDRESS=temporal:7233 -e TEMPORAL_UI_PORT=8233 -p 8233:8233 temporalio/ui:2.55.0
$ curl -s -o /dev/null -w '%{http_code}\n' localhost:8233/
200
$ curl -s localhost:8233/api/v1/namespaces/default/workflows
（wf1 が "status": "WORKFLOW_EXECUTION_STATUS_RUNNING" で返る）
```

追加: RDS の master に近い非 superuser（`CREATE ROLE master2 LOGIN CREATEROLE CREATEDB`、DB `m2db` の owner）を master にして別の `temporal2` を起こした。

```
$ docker logs temporal2 2>&1 | grep "entrypoint-rds"
entrypoint-rds: ロール temporal2 と DB temporal2 / temporal2_visibility を確かめる
entrypoint-rds: temporal2: 初回なので setup-schema -v 0.0
entrypoint-rds: temporal2: update-schema
entrypoint-rds: temporal2_visibility: 初回なので setup-schema -v 0.0
entrypoint-rds: temporal2_visibility: update-schema
entrypoint-rds: namespace: default を作った（retention 72h）
$ docker exec pg psql -U nautobot -tAc "...pg_database WHERE datname LIKE 'temporal2%'" -c "...pg_auth_members WHERE r.rolname='temporal2'"
temporal2|temporal2
temporal2_visibility|temporal2
temporal2|master2|f|t|f      （CREATE ROLE で付いた ADMIN だけの所属）
temporal2|master2|t|f|t      （entrypoint の GRANT で付いた SET / INHERIT）
（docker restart temporal2 の 2 回目も setup-schema 無し、ERROR / permission denied 無し）
```

片付け: `docker rm -f -v temporal temporal2 ui pg` → 4 つ消えた、`docker network rm t036` → 消えた。イメージは下の「片付け」。

#### 3. Terraform

```
$ terraform -chdir=workflow init -backend=false -input=false; terraform -chdir=workflow validate -no-color
Success! The configuration is valid.
$ （pipeline/nautobot、base/ecr、base/core も同じ）
Success! The configuration is valid.   ×3
$ terraform fmt -check -recursive -diff workflow pipeline/nautobot base/ecr base/core; echo fmt=$?
fmt=0
$ grep -cE 'name += "(temporal|ui|worker)"' IaC/terraform/aws-managed/workflow/ecs.tf
3
$ grep -n 'deployment_minimum_healthy_percent = 100\|deployment_maximum_percent *= 200' IaC/terraform/aws-managed/workflow/ecs.tf
188:  deployment_minimum_healthy_percent = 100
189:  deployment_maximum_percent         = 200
```

#### 4. ops

```
$ bash -n ops/up-common.sh && bash -n ops/up.sh && bash -n ops/oss/up.sh && echo bashn=0
bashn=0
$ grep -n 'TEMPORAL_SERVER_VERSION=\|TEMPORAL_UI_TAG=' ops/up-common.sh
256:TEMPORAL_SERVER_VERSION=1.32.1
257:TEMPORAL_UI_TAG=2.55.0
```

`temporal_ui_image_tag` と `TEMPORAL_UI_TAG`、`POSTGRES_MAJOR` と `db_engine_version` と Dockerfile の `ARG POSTGRES_MAJOR=` の一致は `tests/test_workflow.py` が検査（上の 通過 355 / 失敗 0 に入っている）。

#### 5. AWS

未実行（設計どおり。QUEUE の最後の AWS 動作確認に回す）。

### セルフレビュー

- 自分: opus-5.5 / effort: high（サブエージェントなので切り替えられない）
- 反対弁護人: opus / effort: xhigh（読み取り専用。終わったあと `git status --porcelain -uall` で自分の変更以外が増えていないことを確認）
- 退行の注入: 3 件（下の 1・3・Must 1）。どれも注入で `AssertionError` になり、戻して通ることを確かめた

#### 自分の指摘

1. Should fix [missing tests] `tests/test_workflow.py:594`
   - 破綻シナリオ: entrypoint の `if [ "$has" = "f" ]` を `if true` に変える（毎回 setup-schema -v 0.0）。2 回目の起動で既存の表にぶつかる
   - 確認: 注入しても `test_workflow.py` は `通過 355 / 失敗 0` で通った（元の検査は文字列の有無だけ）
   - 片付け: 直した。to_regclass の行、非コメントの `setup-schema -v 0.0` がちょうど 2 行、`if` ブロックの形を正規表現で縛る。同じ注入で `AssertionError: entrypoint は schema_version が無いときだけ setup-schema…` になり、`cmp` で戻したファイルが元と同じことを確かめてから通過を確認
2. 問題なし（実行して確認）
   - env 名: temporal-server と公式 entrypoint のバイナリから取り出した env 名と ecs.tf の名前が一致
   - 順序: `ensure_temporal_secrets` は `tf_apply workflow` の前（up.sh 1253 / 1254、oss/up.sh 584 / 585）。nautobot の apply が workflow より前（up.sh 1025 < 1254、oss/up.sh 467 < 585）。down は workflow が nautobot より前（down.sh:71 < 74）
   - SSM の削除: `ManagedBy` タグで消える（test_oss_ops の削除 14 件）
   - IAM: `execution_db_passwords` は 2 つの ARN だけ
   - OSS: `IaC/terraform/oss/*` は aws-managed へのシンボリックリンク
   - ブロードキャストアドレス: VPC の `enable_dns_hostnames = true`。公式 entrypoint は `getent hosts $(hostname)` で埋める（`/etc/temporal/entrypoint.sh` を cat）
3. 退行の注入: `security_groups.tf` の `workflow → nautobot_db 5432` の行を消すと test_analytics / test_workflow が落ちる。戻して通過

#### 反対弁護人の指摘と再現

1. Must fix [runtime] `ops/down.sh:71`、`ops/oss/down.sh:52`
   - 破綻シナリオ: `temporal_image_tag` の既定を外したのに destroy で渡していない。`-input=false` なので `No value for required variable` で down.sh が workflow の destroy で止まり、後ろが消えない
   - 再現: `workflow/variables.tf:42` に default が無く、down の 2 本に `temporal_image_tag` が無いことを grep で確認
   - 片付け: 直した。`-var "temporal_image_tag=destroy"` を足し、`tests/test_oss_ops.py:1680` に「既定の無い variable は up の apply と down の destroy の両方で -var で渡す」の汎用の検査を足した。修正を外すと `down [('workflow', ['temporal_image_tag'])]` で落ち、戻して通過。`tests/test_workflow.py` の down.sh の検査にも `-var "temporal_image_tag=destroy"` を足した
2. Should fix [correctness / 文書] `base/core/security_groups.tf:77-83`、`docs/architecture/core.md`
   - 指摘: 自分宛ての 6933〜6939 / 7233〜7239 の理由が「タスクの中のサービス間」になっていたが、同じタスクの中はタスクの IP で話すので SG は効かない。効くのはデプロイ中の新旧タスクの間（同じ DB の `cluster_membership` で 1 つのクラスターに入る）
   - 片付け: コメントと `why` と docs を直した（`why` は "... between the tasks during a deployment"）。`design.md` の 152・169 行とリスク 5 の「2 クラスター」の書き方も不正確 → PM へ（文書だけ）
3. Should fix [data loss / runtime] `pipeline/nautobot/database.tf:20`
   - 指摘: 17 → 18 で `allow_major_version_upgrade` が無いので、PostgreSQL 17 の RDS が残っている環境で `up.sh` を打つと apply が失敗する
   - 再現: database.tf に `allow_major_version_upgrade` が無いことを確認（AWS では打っていない）
   - 片付け: `docs/architecture/resources/nautobot.md` に「先に `ops/down.sh` で消す」を書いた。`design.md:25` の前提「DB は up.sh のたびに作り直すので升級の手順は要らない」は down.sh を挟まないと成り立たない → PM へ
4. Should fix [security / runtime] TLS
   - 指摘: 検証 2 は TLS 無しの postgres で、RDS の `rds.force_ssl=1` 相当で通るか確かめていない
   - 再現（手元の docker）: `postgres:18` を `ssl=on`（snakeoil の証明書）、`hostnossl` を拒む pg_hba で起こした。`PGSSLMODE=disable` の psql は `pg_hba.conf rejects connection ... no encryption` で拒まれた。そこへ `SQL_TLS_ENABLED=true`、`SQL_HOST_VERIFICATION=false`、ECS と同じ env で temporal-server のイメージを起こした
   - 1 回目のログ: 2 つの DB に setup-schema と update-schema、`entrypoint-rds: namespace: default を作った（retention 72h）`
   - `pg_stat_ssl`: `temporal|temporal|t|8`、`temporal|temporal_visibility|t|4`（全接続が TLS。計 12 接続）
   - `temporal workflow start -w wftls` → `workflow list` に `Running  wftls  Hello`
   - `docker restart` 後のログ: update-schema が 2 回と `entrypoint-rds: namespace: default がある` だけ。`workflow list` に `Running  wftls  Hello` が残っていた
   - 片付け: 問題なし（実測）。RDS の証明書の検証はしない設定（`SQL_TLS_DISABLE_HOST_VERIFICATION=true`）のまま
5. Should fix（残リスク）[runtime] 接続数
   - 指摘: `db.t4g.micro` の `max_connections` に Nautobot と Temporal が相乗りする
   - 確認: 手元では Temporal が 12 接続。RDS の上限は AWS で見ていない
   - 片付け: 最後の AWS 動作確認で見る（最終報告に回した）
6. Nit
   - 古いコメント 4 か所（`deploy.env.example:191`、`app/temporal/worker.py:6`・`:35`、`pipeline/nautobot/database.tf:3`）→ 直した（コメントだけ）
   - namespace を作る背景のサブシェルが exec のあとも残る（ゾンビ）、setup-schema が途中で落ちたときの半端な状態、master のパスワードが exec 後も env に残る（exec 前に unset しても効き目が薄い）→ 直さない
   - SG の description（`security_groups.tf:39`、`Temporal dev server ...`）→ 変えると SG が作り直しになるので据え置き
7. 反対弁護人が挙げて自分も退けたもの
   - btree_gin は PostgreSQL 13 以降 trusted
   - healthCheck の猶予の長さ、namespace 作成の競合（再試行で収まる）、PostgreSQL 18 の可否、OSS 側、WORKFLOW が AGENT と PIPELINE を要すること

#### 修正後の検証

下の check.sh の出力は最後の編集（コメント 4 か所）のあとに取った。

```
$ bash ops/check.sh   （通過の行だけ抜き出し）
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. スクリプトの構文
== 4. 模擬テスト
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 110 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 207 / 失敗 0
通過 66 / 失敗 0
通過 114 / 失敗 0
通過 104 / 失敗 0
通過 355 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
すべて通過
```

#### 手元の docker の片付け

- `docker rm -f -v temporaltls pgtls`、`docker network rm t036tls`、`docker rmi nwc-temporal-server:test postgres:18 temporalio/server:1.32.1 temporalio/admin-tools:1.32.1 temporalio/ui:2.55.0`
- `docker ps -a` は空。ネットワークに t036* は無い。イメージに 1.32.1 / postgres:18 / nwc-temporal-server は無い（前からあった postgres:16-alpine / 17-alpine / temporalio/temporal:1.9.1 と ECR タグのものは触っていない）
