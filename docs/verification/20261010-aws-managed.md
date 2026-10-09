# マネージド版の AWS 検証、024〜026 の AWS の項目を確かめた回（2026-10-10）

AWS アカウント 493116771193、ap-northeast-1、`OWNER=efukuda`（接頭辞 `efukuda-nwc-poc`）。時刻は UTC（日本時間は 2026-10-10 の未明）。打ったのは PM。

ここに書いた「通った」は、出力を見て確かめたものだけ。見ていないものは「未確認」、動かなかったものは「失敗」と書く。
前の回（`20261009-aws-managed-2.md`）のあとに main に入ったサイクル（lab の小さな直しを片付ける（024）、sinks の空の gnmic を落とす（025）、ops と Kafbat UI と Firehose の小さな直し（026））のうち、「AWS では未確認」と書いて残していた項目を確かめるのが目的。障害注入や格納までは見ない。

## まとめ

- `ops/up.sh` は 2 本目で最後まで通った。1 本目は 7-3（graph）で status Lambda が `InsufficientRolePermissions` になって止まった（下の「不具合」）。同じ引数の 2 本目は tainted の Lambda を作り直して通った。
- 024 の AWS の項目（H / I / K）と 026 の C / D は下の表のとおり。
- 片付けは `ops/down.sh` が 1 回で `DOWN_RC=0`。消えたかはサービスごとの API で確かめ、残るのは ECR 14 本（`KEEP_ECR=1`）と削除を予約した KMS の鍵だけ（下の「片付け」）。

## 立てたもの（ops/up.sh）

環境変数は `OWNER=efukuda PIPELINE=1 AGENT=0 WORKFLOW=0`。ほかは deploy.env のまま（`KEEP_ECR=1`、`IMAGE_TAG=v1_manual`）。作るルートは base/ecr base/core pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot。エンドポイントは 12 本 × 1 AZ。待機だけで約 $2.92/h。

| 本 | 結果 |
|---|---|
| 1 本目 | 7-3 で graph の apply が `waiting for Lambda Function (efukuda-nwc-poc-graph-status) create: unexpected state 'Failed' … InsufficientRolePermissions` で止まった（開始 15:40Z ごろ、NG は 16:20Z ごろ）。base/core 154 件、lab 7 件、stream 63 件（MSK は `29m18s`）は通っていた |
| 2 本目 | 同じ引数の打ち直し。ecr / core / lab / stream は `0 added`、graph は `3 added, 0 changed, 1 destroyed`（Lambda の replace）、nautobot 16 件、analytics 48 件（OpenSearch Serverless の collection が `4m13s`）。7-4b の Splunk は起動、7-5 の Spark の 3 ジョブを起こし、8-3 の Web の再起動、9-2 の Grafana のルールの検査は `判定: OK（4 本とも評価のエラーなし）`（4 本とも `health=ok` / `Normal (NoData)`）。最後まで通って `UP_RC=0`（開始 16:20Z ごろ、終わり 16:48Z ごろ） |

立ったもの: Web の EC2 `i-037ce7bec171ee3de`（t4g.medium）、lab の EC2 `i-0e89e73c378fe3106`（m6i.xlarge）、MSK `efukuda-nwc-poc-stream`、ECS の telegraf クラスターの 4 サービス、Neptune Analytics `g-54a47hwyg3`、Nautobot（RDS と ECS）、analytics 一式。

## 項目と結果

検査は boto3 の API と、SSM Run Command で Web の EC2 と lab の EC2 の中から打った。

### lab の小さな直しを片付ける（024）の「未確認」

| 項目 | 結果 |
|---|---|
| H: ECR の `<prefix>-lab-multitool` が中のイメージごと消える | 通った。1 本目の手順 1 の apply が `aws_ecr_repository.lab["multitool"]: Destruction complete`（`0 added, 0 changed, 1 destroyed`）。`DescribeRepositories(repositoryNames=[efukuda-nwc-poc-multitool])` は `RepositoryNotFoundException`。残る接頭辞付きの ECR は 14 本 |
| H: lab の EC2 が作り直され、7 コンテナが上がる | 通った。lab の apply は 7 件作成（前の回の down.sh で消していたので「作り直し」ではなく新規）。lab の EC2 の `docker ps` で `clab-splab-dc1-{a-leaf-01,a-leaf-02,s-leaf-01,s-leaf-02,spine-01,spine-02,trex-01}` の 7 つが Up。`clab inspect` は SR Linux 6 台が `26.7.2-amd64`、TRex が `2.41-amd64` で running |
| H: デバッグ用のスタックの更新（`MultitoolRepository` と `MultitoolImageTag` の削除） | 未確認。`ops/lab-debug.sh` を打っていない |
| I: lab-debug の Telegraf の amd64 のイメージで起きる | 未確認。同上 |
| K: `aws s3 sync --exclude "clab-*/*"` が `clab-splab/` を送らない | 通った。`s3://efukuda-nwc-poc-kb-493116771193/lab/` は 17 キーで、`clab-` を含むものは 0 |
| A / B / F / J の lab の EC2 の上での振る舞い | 未確認（今回は障害注入を打っていない） |

### ops と Kafbat UI と Firehose の小さな直し（026）の「未確認」

| 項目 | 結果 |
|---|---|
| C: stream の前の Kafbat UI のユニットが `inactive (dead)` で止まり、`is-system-running` が `degraded` にならない | 通った（stream の前）。Web の EC2 で `systemctl is-system-running` → `running`（rc=0）、`systemctl show efukuda-nwc-poc-kafka-ui` → `ActiveState=inactive SubState=dead Result=success ExecMainStatus=75`、`systemctl --failed` は空 |
| C: stream のあとの再起動で Kafbat UI が動く | 通った。2 本目の 4-4 で Web の EC2 が再起動（`uptime -s` → `2026-10-09 16:27:39`。MSK は 1 本目で出来ていた）したあと、`systemctl show efukuda-nwc-poc-kafka-ui` → `ActiveState=active SubState=running Result=success NRestarts=0`、`systemctl is-system-running` → `running`（rc=0）、`docker ps` に `efukuda-nwc-poc-kafka-ui`（`kafka-ui:v1.5.0`）が Up、EC2 の中から `curl http://127.0.0.1:8080/` が 200。8-3 は `systemctl restart <prefix>-web` だけで、EC2 は再起動しない |
| D: analytics の Firehose が `time_sleep` 30 秒のあと 1 回で作れる | 通った。7-4 で `aws_iam_role_policy.alert_firehose: Creation complete after 1s` → `time_sleep.alert_firehose_iam: Creation complete after 30s` → `aws_kinesis_firehose_delivery_stream.alert_events: Creation complete after 16s`。`InvalidArgumentException` は出ず、analytics は 1 回で `48 added, 0 changed, 0 destroyed`。30 秒で足りたのが 1 回なので、遅れが短かっただけの可能性は残る |

## 不具合

### 1. graph の status Lambda が IAM の伝播待ちで `InsufficientRolePermissions` になる（打ち直しで通る）

- 1 本目の 7-3 で `aws_lambda_function.status` が `State=Failed / StateReasonCode=InsufficientRolePermissions`。ログでは `aws_iam_role_policy.status: Creation complete after 1s` の直後（1 秒後）に Lambda を作り始めていた。VPC の ENI を作る権限（`ec2:CreateNetworkInterface` など）が IAM に行き渡る前に Lambda が検査したと見る。閉域の Deny（`perimeter.tf`）に ec2 は無い。
- state では Lambda が tainted になり、2 本目の apply で replace された（`3 added, 0 changed, 1 destroyed`）。`GetFunction` は `State=Active`、`LastUpdateStatus=Successful`。
- analytics の Firehose（2026-10-09 の回、026 で `time_sleep` を入れた）と同じ種類。恒久対策は `docs/cycles/BACKLOG.md` に足した。`docs/troubleshooting.md` の表にも行を足した。
- 1 本目の `UP_RC` は空だった。zsh でパイプの左の終了コードは `PIPESTATUS` でなく `${pipestatus[1]}`（記録の手順のミス。up.sh の不具合ではない）。

## 片付け（ops/down.sh）

`OWNER=efukuda ops/down.sh` を 1 回で `DOWN_RC=0`（開始 16:50Z ごろ、終わり 17:24Z ごろ、約 34 分）。段ごとの destroy は analytics 48 件、nautobot 16 件、graph 11 件、stream 63 件、lab 7 件、base/core 159 件（agent は「state が無い」で飛ばした。`AGENT=0` で立てたので想定どおり）。5-2 で `ops/up.sh` が作った SSM のパラメータ 10 本を消し、5-3 で MSK の SCRAM の secret（`AmazonMSK_efukuda-nwc-poc-collectors`）を消して KMS の鍵の削除を予約した（値は見ていない）。段 6 のタグの一覧は 230 件だが、タグの API は消えたリソースも返すので、これで消えたかは決めない（down.sh 自身がそう出す）。

消えたかはサービスごとの API（boto3。17:30Z ごろ）で見た。

| サービス | 結果 |
|---|---|
| EC2 のインスタンス（`Project=efukuda-nwc-poc`） | 2 台とも `terminated`（`i-0e89e73c378fe3106` / `i-037ce7bec171ee3de`）。同じタグの ENI は 0、EBS は 0、EIP は 0 |
| MSK / ECS のクラスター / Neptune Analytics / RDS / EMR Serverless の application / Firehose / OpenSearch Serverless の collection / Lambda / S3 のバケット / S3 Tables / Cloud Map の namespace / CloudWatch Logs のロググループ / IAM のロール / Secrets Manager / ALB・NLB / CloudFormation のスタック | 接頭辞 `efukuda-nwc-poc` のものは 0 件 |
| ECR | 14 本が残る（`KEEP_ECR=1` の想定どおり。agent / gnmic / goflow2 / grafana / kafka-ui / lab-srlinux / lab-trex / nautobot / redis / splunk / syslog-ng / telegraf / temporal / worker） |
| KMS | `alias/efukuda-nwc-poc-msk-scram` は外れ、鍵（`ef3f6b9e…`）は `PendingDeletion`（2026-10-16 に消える。待つあいだは課金なし） |
