# 050 の plan の守りを AWS で 1 回通す（051）

設計: PM(opus-5.5) / effort: high。QUEUE 165。2026-10-12。実装（コードの変更）は無い見込みで、エンジニアは直すものが出たときだけ起動する。

## 背景

- 「SG の作り直しと削除を terraform plan で止める（050）」で、`ops/up.sh` / `ops/oss/up.sh` の手順 0 の SG の守りを `check_sg_plan`（`ops/up-common.sh`）に置き換えた。base/core を `terraform plan -json -input=false -refresh=false -var owner=$OWNER [-var vpc_cidr=…]` で打ち、`aws_security_group` の `planned_change` が `replace` / `delete` なら die する
- 050 は手元の terraform_data と偽の terraform でしか確かめていない。本物の state と AWS の data source で plan が通ること、止めるべきときに止まること、所要時間を AWS で 1 回確かめる（QUEUE 165）
- 前回の AWS の確認（047、`d05242a`）のあとの base/core の変更はコメントだけ（`git diff d05242a..HEAD -- IaC/terraform/aws-managed/base/core/` の `+` / `-` 行はすべて `#` で始まる）。SG の定義は変わっていない

## 現物（2026-10-12 に読んだもの）

- メインのチェックアウトの base/core の state（`terraform state list`）。前回の `ops/down.sh` が Runtime の ENI 待ちで残したもの:

  ```
  data.aws_caller_identity.current
  data.aws_ec2_managed_prefix_list.s3
  data.aws_iam_policy_document.alerts_topic
  data.aws_iam_session_context.deployer
  data.aws_partition.current
  data.aws_ssm_parameter.al2023
  aws_security_group.workload["runtime"]
  aws_subnet.a
  aws_subnet.b
  aws_subnet.c
  aws_vpc.this
  ```

  state があるので、次の `ops/up.sh` の 1 回目から手順 0 の守り（`ops/up.sh:597` の `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]` の中）が走る
- `ops/up.sh:587-612`: `MAIN_VARS` に `VPC_CIDR` があれば `-var vpc_cidr=…` を入れ、`check_sg_plan ${MAIN_VARS[@]+"${MAIN_VARS[@]}"}` を呼ぶ。そのあと `log "1. ECR …"`（`ops/up.sh:615`）。手順 0 の中で AWS に何かを作るコマンドは無い（169〜612 行に `tf_apply` / `put-parameter` / `create-*` が無い）
- die の文面（`ops/up-common.sh` の `check_sg_plan` の末尾）: 「`$TF_DIR/base/core の plan で SG が作り直し・削除になる: <アドレス>（<action>、<reason>）、…`」。runtime が入ると「runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ … も残すので、ENI が消えてから … を打つ。」が足される
- `vpc_cidr` の既定は `10.0.0.0/16`（`deploy.env.example:121-122`、`variables.tf:32-39`。/16〜/24）。`aws_vpc.cidr_block` を変えると VPC が作り直しになり、SG の `vpc_id` が変わるので SG も作り直し（`replace`）になる
- SG はワークロードごとの表 `local.workload_security_groups` の `for_each`（`security_groups.tf:155`）で、AGENT / PIPELINE / WORKFLOW では増減しない（OSS 版の `local.oss` だけで変わる）

## 設計方針

1. **土台だけで立てる。** SG は全部 base/core にあり、機能のフラグで増えない。`AGENT=0 PIPELINE=0 WORKFLOW=0` で base/ecr・base/logs・base/core だけを作る（環境変数は deploy.env より優先。`ops/deploy-env.sh:11`）。MSK・ECS・Neptune などを立てないので、時間と費用は 045 の数分の 1
2. **止まる側は VPC_CIDR を変えて作る。** コードも state も触らずに、`VPC_CIDR=10.20.0.0/16` を渡すだけで全 SG が `replace` になる。手順 0 で止まるので AWS には何も作られない。050 で手順 0 の前へ移した `MAIN_VARS` の `vpc_cidr` もこれで確かめる
3. **4 回打つ。** 残った state（VPC・サブネット・runtime の SG）と、土台を全部立てた state の 2 つで、止まる側と通る側を 1 回ずつ
4. **時刻はログの行に付ける。** `ops/up.sh` の出力を perl で 1 行ごとに時刻を付けてファイルに書く。手順 0 の守りの時間は、base/core の `tf_init` の最後の行から「== 1. ECR」の行まで（state list 3 回 + plan）
5. **打つのは PM。** メインのチェックアウトから、Bash の背景で（ターミナルのタブで打たない。045 で承認待ちのまま 3 時間 43 分止まった）
6. **確認が終わったらすぐ `ops/down.sh`。** 消えたことを AWS MCP で確かめて報告する。時間課金の無い消し残り（VPC・サブネット・SG・SSM Parameter）は報告しない
7. 不具合が出たら、その場で原因を `review.md` に書き、`ops/down.sh` で消してから直すサイクルを設計する（このサイクルの Round 2）。環境は直すあいだ置いておかない

## 変更対象ファイル

- `docs/verification/20261012-aws-managed-051.md`: 新規。検査の記録（045 と同じ形: まとめ / 立てたもの / 項目と結果 / 不具合 / 片付け）
- `docs/cycles/051-aws-sg-plan-verify/build.md` / `review.md`
- `docs/cycles/QUEUE.md`: 165 を完了に
- `IaC/` / `app/` / `ops/` / `tests/`: 変更なし（不具合が出たときだけ）

## 再利用するもの

- `ops/up.sh` / `ops/down.sh`（メインのチェックアウトで）
- 045 の記録の形（`docs/verification/20261011-aws-managed-045.md`）
- AWS MCP（`run_script`）で消えたことを確かめる

## 実装ステップ

すべてメインのチェックアウト（`/Users/eight/Documents/Dev/sandbox/nwc-poc`、`e0827ac` 以降の main）で、`OWNER=efukuda` を付けて打つ。出力は `( … ops/up.sh 2>&1; echo "UP_RC=$?" ) | perl -MPOSIX -pe 'BEGIN{$|=1} print strftime("%H:%M:%S ", localtime)' > <ログ>`。

1. V-1: 残った state で、`VPC_CIDR=10.20.0.0/16 AGENT=0 PIPELINE=0 WORKFLOW=0 NO_DASHBOARD_PORTFORWARD=1 ops/up.sh`
2. V-2: 残った state で、`AGENT=0 PIPELINE=0 WORKFLOW=0 NO_DASHBOARD_PORTFORWARD=1 ops/up.sh`（最後まで）
3. V-3: 土台を立てた state で、V-1 と同じコマンド
4. V-4: 土台を立てた state で、V-2 と同じコマンド
5. V-5: `ops/down.sh`。消えたことを AWS MCP で確かめる
6. 記録を書いて、QUEUE 165 を完了にする

## 検証方法

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| V-1 | 残った state で VPC_CIDR を変える | `UP_RC=1`。`NG: … の plan で SG が作り直し・削除になる: aws_security_group.workload["runtime"]（replace、…）` と runtime の ENI の一言が出る。ログに「== 1. ECR」が無い（何も作っていない）。plan の Warning が出たなら stderr に出て止めていない |
| V-2 | 残った state で通常どおり | 手順 0 の守りが止まらず「== 1. ECR」へ進み、最後まで `UP_RC=0`。base/core の apply が `Apply complete!`（runtime の SG と VPC・サブネットは作り直さない）。守りの秒数を記録 |
| V-3 | 土台を立てた state で VPC_CIDR を変える | `UP_RC=1`。die の一覧に workload の SG が全部（`terraform state list` の `aws_security_group.workload[...]` の数と同じ）`replace` で並び、runtime の一言が出る。「== 1. ECR」が無い。base/core の state の `serial` が V-3 の前後で同じ（変わっていない） |
| V-4 | 土台を立てた state で通常どおり | 守りが止まらず、base/core が `No changes`（または `0 added, 0 changed, 0 destroyed`）、`UP_RC=0`。守りの秒数を記録（state が大きいときの plan の時間） |
| V-5 | `ops/down.sh` | `DOWN_RC=0`。AWS MCP で `Project=efukuda-nwc-poc` の EC2 が全部 `terminated`、VPC エンドポイントが 0、EIP 0 |

## 未確定事項とリスク

1. **`-refresh=false` で data source が読まれるか。** Terraform は `-refresh=false` でも data source を plan の中で読む前提で 050 を組んだ（`data.aws_ssm_parameter.al2023` などの値が要る式がある）。読まれずに `known after apply` が増えても、SG の `replace` 判定に効くのは `vpc_id` / `name` / `description` だけなので守りは成り立つ。V-2 / V-4 で plan のエラーが出ないことで確かめる
2. **V-2 の所要時間。** ECR に `IMAGE_TAG`（既定 `v1`）のイメージが無ければ手順 2 でビルドが走り、30 分ほど伸びる。費用は土台だけの待機料金（`up.sh` が最初に出す目安の額を記録する）
3. **残った runtime の SG に Runtime の ENI がもう無い場合。** V-2 は差分なしで通るだけで影響しない
4. **V-1 / V-3 で手順 0 より前に別の検査で止まる。** `VPC_CIDR=10.20.0.0/16` が `ops/deploy-env.sh` の形の検査で弾かれたら、別の /16 に替える。止まった文言で見分ける（守りの die は「plan で SG が作り直し・削除になる」）
5. **時刻の粒度は秒。** 守りの時間は state list 3 回を含む（各 1 秒前後）。plan だけの時間は分けない
