# うまくいかないとき

← [README](../README.md)

`<prefix>` は `deploy.env` の `OWNER` から作る接頭辞 `<owner>-nwc-poc`。多くは `ops/up.sh` を打ち直せば直る（できているものは飛ばす）。

2026-10-09 より前の記録・ログ・ダッシュボードでは、Splunk の app・保存済みサーチ・sourcetype、Grafana のフォルダ、Nautobot のユーザーと Job の名前が改名前のもの（「名前を nwc に揃える（019）」）。
新旧の対応は `docs/cycles/` の 019 のサイクルの `design.md`（「設計方針」1 の置換の表）。

## `ops/up.sh` / Terraform

| 症状 | 原因と直し方 |
|---|---|
| 手順 0 で `キー「…」は使えない` / `2 回ある` / `は 1 か 0` | `deploy.env` の書き間違い。まだ何も作っていない。`deploy.env.example` と見比べて直す |
| `terraform init` が `x509: certificate signed by unknown authority` | 社内 CA が入っていない（[setup.md](setup.md) の「社内 PC の CA」） |
| 手順 0 で「IAM ユーザーの一時セッション（get-session-token）で入っている」で止まる / apply が `AccessDenied` / `InvalidClientTokenId`（読み取りは通る） | `sts get-session-token` の一時セッションで打っている。長期キーか SSO のプロファイルで打ち直す |
| apply が `explicitly denied` で止まる | 組織の SCP / IAM が止めている。管理者に頼むか、`SKIP_*` で外す（lab は `SKIP_LAB=1`、MSK は `SKIP_STREAM=1`、EMR / S3 Tables は `SKIP_ANALYTICS=1`、Neptune は `SKIP_GRAPH=1`）。打ち直さないなら `ops/down.sh` |
| apply が `EntityAlreadyExists` など「もうある」 | state を消した・別の PC で apply した（up.sh を打った worktree を消した、も同じ）。手で消すか、ECR は import する（続きは表の下の `EntityAlreadyExists`） |
| `does not have an attribute named "…"` | 前のルート（`base/ecr` → `base/core` → …）をこの PC で apply していない、または先に消した。`ops/up.sh` を打ち直す |
| `Error acquiring the state lock` | 同じルートを別のターミナルで打っている。終わるのを待つ |
| `aws_lambda_invocation.kb_index` が失敗（CREATE_KB=1） | KB のベクトルインデックスを VPC の中の Lambda `<接頭辞>-kb-index` が作る。ログは CloudWatch Logs の `/aws/lambda/<接頭辞>-kb-index`（続きは表の下の `kb_index`） |
| 「IaC/terraform/aws-managed/base/core に OpenSearch Serverless の VPC エンドポイントが無い」の precondition で止まる | KB か logs のコレクションを作るのに、base/core に VPC エンドポイントが無い。`ops/up.sh` を通して打つ（`CREATE_KB` か `STORES` の `grafana` を見て base/core に渡す）。ルートを手で apply したなら base/core を `-var create_opensearch_endpoint=true` で打ち直す |
| `ops/down.sh` の stream の destroy が `data.aws_secretsmanager_secret.msk_scram` / `data.aws_kms_alias.msk_scram` の not found で止まる | stream が残っているのに、SCRAM の secret `AmazonMSK_<prefix>-<コレクター>`（`syslog-ng` / `goflow2` / `gnmic` のどれか）か鍵の alias `alias/<prefix>-msk-scram` が無い。`-refresh=false` で stream を消してから `ops/down.sh` を打ち直す（続きは表の下の `msk_scram`） |
| destroy が `provider["registry.terraform.io/opensearch-project/opensearch"]` で止まる | 2026-09-28 より前に作った agent の state（`opensearch_index.kb` 入り）。コミット f7b1688 の `IaC/terraform/aws-managed/agent` で destroy する |
| 手順 0 の後に「2026-09-29 より前の SG（internal）が残っている」で止まる | SG をワークロードごとに分ける前の state。まだ何も作っていない。先に `ops/down.sh` を打つ（Runtime の ENI が残るあいだは VPC・サブネット・`internal` が残るので、時間をおいて打ち直す） |
| `terraform apply` が「state に security_group_ids が無い」（Resource postcondition failed）で止まる | 土台（`IaC/terraform/aws-managed/base/core`）が SG をワークロードごとに分ける前の state。`ops/up.sh` を通さずにルートを直接 apply したときに出る。先に `ops/down.sh` を打ってから `ops/up.sh` |
| `ops/lab-debug.sh up` が「前の形（土台の VPC を使う）のまま」で止まる | 2026-10-04 より前に作ったスタック。`ops/lab-debug.sh down` のあと `up`（今はスタックが自分の VPC を持つ） |
| 2026-10-04 より前のデバッグ用の EC2 が残ったまま `ops/down.sh` で土台（base/core）が消えない（`DeleteConflict` / `DependencyViolation`） | 前の形のスタックは土台のサブネット・SG・境界ポリシーを使っていて、今の `ops/down.sh` はそれを消さない。`ops/lab-debug.sh down` のあと `ops/down.sh` を打ち直す（`ops/up.sh` もそのスタック向けの ECR のエンドポイントを外すので、先に消しておく） |
| `ops/up.sh` が「注意: LAB_DEBUG は使わない」と出す | `deploy.env` から `LAB_DEBUG` の行を消す。デバッグ用の EC2 は `ops/lab-debug.sh up` / `down` |
| `ops/lab-debug.sh` が「… が ROLLBACK_COMPLETE」（ROLLBACK_FAILED / DELETE_FAILED）で止まる | 原因は `aws cloudformation describe-stack-events --stack-name <prefix>-lab-debug`。`ops/lab-debug.sh down` のあと `up` |
| 7-3 で graph の apply が `waiting for Lambda Function (<prefix>-graph-status) create: unexpected state 'Failed' … InsufficientRolePermissions` で止まる | ロールのポリシー（VPC の ENI を作る `ec2:*NetworkInterface*`）が IAM に行き渡る前に Lambda が検査した（2026-10-10 の AWS。ポリシーの作成完了の 1 秒後に Lambda を作っていた）。state では Lambda が tainted になるので、同じ引数で `ops/up.sh` を打ち直すと作り直されて通る（続きは表の下の `graph-status の作成`） |
| 7-4 で analytics の apply が `aws_kinesis_firehose_delivery_stream.alert_events` の `InvalidArgumentException: The security token included in the request is invalid. Ensure that the provided IAM role associated with firehose is not deleted.` で止まる | ロールを作った直後の IAM の反映遅れ（2026-10-09 の AWS）。cycle 026 から `history.tf` の `time_sleep.alert_firehose_iam` で 30 秒待ってから作る。それでも出るなら同じ引数で `ops/up.sh` を打ち直す（通った実績あり）か、`create_duration` を延ばす |
| ビルドの `pip install` が `CERTIFICATE_VERIFY_FAILED` | 社内 CA の差し替え。`ReadTimeoutError` は QEMU が遅いだけなので打ち直す |

- `EntityAlreadyExists`:
  - [architecture/README.md](architecture/README.md) の get-resources で `Project=<prefix>` を探して手で消す。
  - 手順 1 の ECR（`RepositoryAlreadyExistsException`）は `KEEP_ECR=1` で残したものなので、消さずに import する（[deploy.md](deploy.md) の「state を失ったとき」）。
- `kb_index`: 403 や接続できないのは 4 分半まで打ち直してから落ちる。
  - 権限の反映待ちなら `ops/up.sh` を打ち直す。
  - 続くなら `IaC/terraform/aws-managed/base/core` の OpenSearch Serverless の VPC エンドポイント（`create_opensearch_endpoint`）が ACTIVE か見る。
- `msk_scram`:
  - 無くなるのは、2026-10-08（cycle 012）より前のコードで作った stream を今のコードで消すとき、または手で消したとき。
  - destroy は data source を読み直すので止まり、`ops/down.sh` は stream も secret と鍵も残す。
  - `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream destroy -refresh=false -var owner=<OWNER> -var 'gnmi_targets="0.0.0.0:57400"'` で data source を読まずに state のものを消し、`ops/down.sh` を打ち直す。
  - 読めない data source の destroy が `-refresh=false` で通るのは手元の Terraform 1.16 で確かめた。
- `graph-status の作成`:
  - `IaC/terraform/aws-managed/pipeline/graph/sync.tf` の `aws_lambda_function.status` は `aws_iam_role_policy.status` を待つだけで、IAM の反映は待たない。ロールを作った直後の apply（初回と、graph を消してからの作り直し）で起きうる。
  - 2026-10-10 の AWS で 1 回目の `ops/up.sh` がここで止まり、同じ引数の 2 回目で `Resources: 3 added, 0 changed, 1 destroyed`（Lambda の replace）で通った。`GetFunction` は `State=Active`、`LastUpdateStatus=Successful`。
  - 2 回目も止まるなら、`aws lambda get-function --function-name <prefix>-graph-status` の `StateReasonCode` を見る。`InsufficientRolePermissions` 以外なら別の原因。
  - 恒久対策（analytics の Firehose と同じ `time_sleep`）は `docs/cycles/BACKLOG.md` の候補。

## 閉域（`explicit deny`）

| 症状 | 原因と直し方 |
|---|---|
| ワークロードのログに `<サービス>.ap-northeast-1.amazonaws.com` への接続のタイムアウト（`Connect timeout` / `ConnectTimeoutError`） | そのサービスのインターフェース型エンドポイントが無い（VPC にインターネットへの経路が無いので、どこにも出られない）。手順 0 の一覧にあるか見る。無ければ `ops/up.sh` の `endpoints_for` に足し、`IaC/terraform/aws-managed/base/core` の `interface_endpoints` の validation にも足す |
| Neptune（Neptune Analytics）への問い合わせがタイムアウトする・名前が引けない | `public_connectivity = false` のグラフに、土台のインターフェース型エンドポイント `neptune-graph-data`（private DNS）だけで届く作り（続きは表の下の `Neptune`） |
| VPC の中の相手（MSK / ECS のタスク / 機器）への接続がタイムアウトする | 土台の通信の表にその流れが無い。`IaC/terraform/aws-managed/base/core/security_groups.tf` の `sg_flows` に 1 行足して `ops/up.sh` を打ち直す（続きは表の下の `sg_flows`） |
| ワークロードのログに `AccessDenied ... with an explicit deny in an identity-based policy` | その呼び出しが VPC のエンドポイントを通らなかった（`<prefix>-network-perimeter` の Deny。PC から打った CLI などで、VPC の外から呼んだとき）（続きは表の下の `identity-based policy`） |
| `... in a resource-based policy`（S3 / S3 Tables / SNS / SQS / AgentCore） | リソースポリシーの Deny。apply した人と AWS のサービスは外してあるので、ほかの人か、VPC の外の PC から打った。apply した本人の PC から打つか、VPC の中（Web の EC2 に SSM で入る）から打つ |
| apply する人が替わり、バケットや S3 Tables に `AccessDenied` で apply できない | 外すプリンシパルが前の人のまま。前の人が `ops/up.sh` を打ち直すか、管理者がポリシーを消してから、新しい人が `ops/up.sh` を打つ（続きは表の下の `バケットのポリシー`） |
| KB の取り込み（`StartIngestionJob`）が S3 を読めない | KB のロール `<prefix>-kb` はバケットの Deny から外してある。名前を変えたなら `IaC/terraform/aws-managed/base/core/perimeter.tf` の `perimeter_exempt_principals` も変える |

- `Neptune`:
  - 2026-10-05 に AWS で確かめた（`<prefix>-graph-status` が `status` を DOWN / UP に書き、チャットがトポロジを答えた）。
  - 届かないときは手順 0 の一覧に `neptune-graph-data` があるかと、SG `endpoints` を見る。
  - `AccessDenied` なら IAM の `neptune-graph:ReadDataViaQuery` などと、Resource のグラフの ARN を見る。
- `sg_flows`:
  - SG は表に無い通信を VPC の中でも通さない（[architecture/core.md](architecture/core.md) の「SG」）。
  - 拒んだ通信は VPC フローログに出るので、CloudWatch Logs Insights でロググループ `/<prefix>/vpc-flow-logs` に次を打つ（1〜2 分遅れて出る。IP は ENI の一覧で SG を引く）。
    `filter action = "REJECT" | stats count(*) by srcAddr, dstAddr, dstPort, protocol | sort count(*) desc`
  - 送り元が S3 の公開 IP（プレフィックスリスト `com.amazonaws.ap-northeast-1.s3` の範囲）で宛先が 32768 以上のポートの REJECT が少し出る。
    これは閉じた接続に遅れて届いたパケットで、表の漏れではない。
- `identity-based policy`:
  - 呼んだサービスのインターフェース型エンドポイントが手順 0 の一覧にあるか見る。無ければ `ops/up.sh` の `endpoints_for` に足す。
  - 切り分けは `NETWORK_PERIMETER=0 ops/up.sh`（[setup.md](setup.md) の「閉域を一時的に外すとき」）。
- `バケットのポリシー`:
  - 管理者（ルートか、ポリシーを消せる人）は `aws s3api delete-bucket-policy --bucket <バケット>` と `aws s3tables delete-table-bucket-policy --table-bucket-arn <ARN>` でポリシーを消す。
  - ポリシーの取得・変更・削除は Deny から外してあるので、同じアカウントで権限のある人なら VPC の外からでも消せる。

## 画面に入れない

| 症状 | 原因と直し方 |
|---|---|
| `SessionManagerPlugin is not found` | PC に Session Manager plugin が無い |
| `start-session` がタイムアウトする / 名前が解決できない | PC から ssm / ssmmessages に 443 で届いていない（[setup.md](setup.md) の「利用者の PC 側」） |
| `TargetNotConnected` | インスタンスが SSM に登録されていない。起動直後なら数分待つ。下のコマンドで `Online` か見る |
| `AccessDeniedException` | 利用者の IAM ポリシー（[deploy.md](deploy.md) の「利用者に画面を渡す」）と、インスタンスの `Project` タグ |
| しばらく放置すると切れる | アイドル 20 分で切れる。`start-session` をやり直して再読み込みする |

```bash
aws ssm describe-instance-information --region ap-northeast-1 --filters Key=tag:Project,Values=<prefix> --query 'InstanceInformationList[].[InstanceId,PingStatus,AgentVersion]' --output table
```

## チャットの答えがおかしい

Web のログは Web の EC2 で `sudo journalctl -u <prefix>-web -n 100`、起動時の失敗は `/var/log/cloud-init-output.log`。

| 症状 | 原因と直し方 |
|---|---|
| ブラウザが「接続できない」 | Web が落ちている。上のログを見る。`web/ is not in s3://` なら Web の部品が S3 に無いので `ops/up.sh` を打ち直す |
| `ModuleNotFoundError: No module named 'toolkit'` など | 同じ。`ops/up.sh` を打ち直す |
| `KeyError: 'MODEL_ID'` / 404 / `bedrock:InvokeModel` の `AccessDenied`（主体が Web のロール） | EC2 に `app/agentcore/app.py` が置かれている（[architecture/README.md](architecture/README.md) の「どのファイルがどこで動くか」）。`ops/up.sh` を打ち直す。Web のロールに権限を足して直さない |
| 「エージェントの呼び出しに失敗しました」 | journald の `invoke failed:` の行。`AccessDenied` は Runtime の ARN とインスタンスロール、`Could not connect` は bedrock-agentcore のエンドポイント（手順 0 の一覧にあるか、SG `endpoints` が `<prefix>-web` からの 443 を受けているか） |
| 150 秒で失敗する | Runtime が返らなかった。初回は起動が遅いので再送する |
| Runtime のログの `InvokeModel` が `ap-northeast-3` で `AccessDeniedException` | `jp.` のモデルは大阪にも振り分けられる。SCP / Permissions boundary が大阪を止めている |
| 機器の質問に「資料に見当たらない」 | ツールを呼んでいない（Runtime のログに `tools=0`）。機器名をそのまま書いて聞き直す |
| 回答に `参照:` が付かない | `CREATE_KB=1` でない、または取り込みが失敗している。Runtime のログの `retrieve failed` を見る |
| 普通の質問がガードレールの定型文で返る | 誤検知。`IaC/terraform/aws-managed/agent/kb.tf` の `aws_bedrock_guardrail.this` のフィルタを弱め、版を作り直す（[development.md](development.md)） |

## パイプラインと WORKFLOW

| 症状 | 原因と直し方 |
|---|---|
| 回線を落としても Grafana のルール `link_down` が Normal のまま | 通知まで 2 分ほどかかる（[pipeline.md](pipeline.md) の「アラート」の表）。それでも変わらなければ、Grafana の Explore で `snmp_interface_oper_up` が来ているか見る（続きは表の下の `link_down`） |
| `ops/up.sh` の最後に「Grafana のアラートルールの評価を確かめた結果が OK ではない」か「確かめられなかった」、またはデータは来ているのに Grafana のアラートが来ない | ルールの評価がエラーでも `KeepLast` で Normal に見える。`ops/check-grafana.sh`（OSS 版は `--oss`）で今の状態を見る（終了コードは下の「`ops/check-grafana.sh` の終了コード」。続きは表の下の `check-grafana`） |
| Splunk のアラートが出ない | `STORES` に `splunk` があるか（既定で入っている。`STORES` を書いて外していないか）。Splunk の検索でイベントが来ているか、保存済みサーチが動いたかを見る（続きは表の下の `Splunk のアラート`） |
| アラートは出ているのに SNS に届かない（Grafana の Contact points の `nwc-sns` が失敗、Splunk の `sendmodalert` に `ERROR`） | タイムアウトなら `sns` のインターフェース型エンドポイント（手順 0 の一覧）。`AccessDenied` ならタスクロールの `sns:Publish` と、トピックのポリシー（VPC の外からの publish を拒む）（ログは表の下の `SNS`） |
| トポロジに赤い線が出ない | `/aws/lambda/<prefix>-graph-status` のログを見る。呼ばれていなければ送り手か SNS（上の 3 行）（警告ごとの見方は表の下の `graph-status`） |
| `query_history` に出ない通知がある | `/aws/lambda/<prefix>-graph-status` を CloudWatch Logs Insights で見る（`ALERT_EVENT_LOST` と `ALERT_DROPPED` の見方は表の下の `query_history`） |
| Neptune の `status` が変わらず、ログに `Task timed out` か `Neptune に書けなかった` がある | Neptune が遅いか届かない（上の「閉域」の Neptune の行を見る）。履歴の行は Neptune より先に送ってある（`status` が遅れて変わるわけは表の下の `Neptune の status`） |
| BGP / IS-IS の層や機器の `ALARM` が変わらない | 出すのは Grafana と Splunk のアラート（`bgp_down` / `isis_down` / `trap`）。`STORES` に `grafana` も `splunk` も無ければ出ない（仕様）（あるのに変わらないときは表の下の `ALARM`） |
| Grafana のダッシュボード「nwc / SNMP metrics」が空、エージェントの `query_metrics` が何も返さない | `metrics` トピックの IF の統計・CPU・メモリは gnmic が 60 秒ごとに書く（見る所は表の下の `metrics`） |
| `sinks-splunk` / `sinks-grafana` のジョブが数分で落ちて立ち直りを繰り返し、ログに `ValueError: year 173875 is out of range` | gnmic の values の無い event を Telegraf の行として読んでいた（025 で直した）。直す前のイメージ（`snmp_sinks.py` のハッシュ）で立っていないかを見る |
| `gnmi` トピックに on-change の購読（`interface_state` / `bgp_neighbor` / `isis_interface`）の初回値が無い（値が変われば書かれる） | まず gnmic の設定の出力に `buffer-size` / `timeout` があるか（030。送り手が詰まっているあいだの応答を捨てない。古いイメージなら作り直す）。あっても無ければ、gnmic が応答を受けているか（`--debug` の `gNMI Subscribe Response`）と機器の初期同期を見る（[pipeline.md](pipeline.md) の「gnmic の購読」と「gnmic の書き込みと ACL」） |
| トポロジは赤くなるのに修復案が出ない | SNS → SQS か、ワーカー。`WORKFLOW=1` か、起こす種類か（ワークフローを起こすのは `link_down` だけ）を見る（DLQ とワーカーのログは表の下の `修復案`） |
| 承認を押しても `pending` のまま | 反映まで数秒〜20 秒かかる（Web → SQS `<prefix>-decisions` → worker → ワークフロー → `proposal_events` → Athena）。「更新」を押す（実測と、1 分たっても変わらないときは表の下の `pending`） |
| 承認を押したら `expired` になった | ワークフローがもう無かった（worker のタスクが入れ替わった）。処置は打たれない。まだ落ちていれば、次の通知で別の修復案が出る（[workflow.md](workflow.md)） |
| 承認しても approved のまま進まない | ワーカーのイメージが古い。`deploy.env` の `IMAGE_TAG` を上げて `ops/up.sh`（[workflow.md](workflow.md)） |
| 手順 7-2c で「Telegraf か gnmic のサービスが 10 分たっても安定しない」 | タスクが起きては止まっている。サービスは 2 つ（Telegraf の受ける側 `<prefix>-telegraf-dialout` と gnmic の `<prefix>-gnmic`）（止まった理由の見方は表の下の `7-2c`） |
| 手順 7-2d で「syslog-ng か GoFlow2 のサービスが 10 分たっても安定しない」 | 止まらずに先へ進む。サービスは `<prefix>-syslog-ng` と `<prefix>-goflow2`（止まった理由の見方は表の下の `7-2d`） |
| syslog-ng のログに `Topic authorization failed`、GoFlow2 のログに `The client is not authorized to access this topic` が出続ける | ACL が無いこと自体では出ない。出るなら、トピックに ACL があるのに自分のユーザー（`User:syslog-ng` / `User:goflow2`）の分が足りないと見ている（推測。確かめた範囲と見る所は表の下の `authorization failed`） |
| 手順 7-3c で「Nautobot のサービスが 20 分たっても安定しない」 | 止まらずに先へ進み、最後にもう一度同じ注意が出る。初回は DB の migrate のあいだ `web` が HEALTHY にならず、`worker` も起きない（止まった理由の見方は表の下の `7-3c`） |
| syslog の項目（ホスト名・本文など）が崩れる、取れない | syslog-ng の形式（`SYSLOG_STANDARD`）と機器の形式が合っていない。既定は RFC3164（本番の Cisco）、lab の SR Linux は RFC5424（続きは表の下の `SYSLOG_STANDARD`） |
| デバッグ用の EC2 で Telegraf の出力を見たい | `sudo lab telegraf status` / `logs -f`（出力は標準出力。MSK へは送らない。受けるのは trap だけ） |
| gnmic のタスクで `gn get` は通るのに trap / syslog / NetFlow（`ops/netflow_send.py` で送ったもの）が Kafka に来ない | lab の EC2 の DNAT の宛先が古い NLB の IP か、転送が無い。lab の EC2 で `sudo lab forward-status`、無ければ `sudo lab forward`（SSM `/<prefix>/telegraf-address` を読み直す） |
| Grafana / Splunk のポートフォワードがつながらない | 踏み台は Web の EC2（`Online` か上の「画面に入れない」のコマンドで見る）。タスクが動いているか `aws ecs list-services --cluster <prefix>-analytics` と `describe-services` の `runningCount` を見る（続きは表の下の `ポートフォワード`） |
| Kafbat UI のポートフォワードがつながらない、画面が開かない | 下の「Kafbat UI」を見る |
| Grafana に入れない（パスワードが違う） | admin のパスワードは SSM の値（`grafana_password_command`）。タスクが起きたときに読むので、SSM を手で変えたら `aws ecs update-service --force-new-deployment` で作り直す |
| 手順 7-4b で「Splunk が 20 分たっても HEALTHY にならない」 | ロググループ `/ecs/<prefix>-splunk` を見る。初回は設定の展開で 5〜10 分かかる（未確認）。ライセンスに同意していない旨で止まるならタスク定義の `SPLUNK_START_ARGS` / `SPLUNK_GENERAL_TERMS`。Spark のジョブはそのまま起きるので、Splunk が起きたあとで落ちていれば `ops/up.sh` を打ち直す |
| 手順 7-4b で「search head の突き合わせ（app/splunk/peers_check.py）が 6 分たっても ok … にならない」/「まだ 1 回もしていない」 | クラスター（`SPLUNK_AZ_NUM` が 2 か 3）だけ。search head が、いまの indexer を全部は検索できていない（`nwc-peer-check` の行の見方は表の下の `nwc-peer-check`） |
| 手順 7-4b で「注意: indexer のタスクが同じ AZ に 2 台いる」 | 止まらない。AZ に 1 台ずつは Fargate の振り分け任せで、保証ではない。その AZ が落ちると複製が一緒に無くなる。散らし直すなら indexer のサービス `<prefix>-splunk-idx` を `aws ecs update-service --force-new-deployment` で作り直す（散るかは未確認） |

- `link_down`:
  - `snmp_interface_admin_up` が 0 の IF はルールが外す。
  - 来ていなければ gnmic（ECS Exec で入って `gn get`。[pipeline.md](pipeline.md) の「gnmic と Telegraf に入る」）か
    Spark（[pipeline.md](pipeline.md) の「Spark を確かめる」）。
  - SNMP のポーリングではなく gnmic の gNMI の値を見る。
- `check-grafana`:
  - `エラー:` の行がエラーのルール。
    理由は `aws logs tail /ecs/<prefix>-grafana --since 1h --filter-pattern '"Failed to evaluate rule"'`（出なければ `'"level=error"'`）。
  - `未確認（… 401` は admin のパスワードが SSM と違う（表の「Grafana に入れない」）。
    `確かめ始めてから評価されていないルール` は Grafana が起動中か止まっている。
  - `エラーのあったルールのもう 1 回の評価を待っている` で終わったら打ち直す。
  - 「確かめられなかった」（未確認）は評価のエラーとは限らない。
    上の出力の理由（`判定: 未確認（…）`、`SSM Run Command を送れなかった`、`… 秒たっても分からない` など）を見て打ち直す。
  - OK でも、ルールの行の `alerts=` が `NoData` だけなら、ルールのクエリが何も返していない。
    エラーではないので OK になる。メトリクス名・インデックス・ラベルを見る（[pipeline.md](pipeline.md) の「Grafana のアラート」）。
- `Splunk のアラート`:
  - イベントは Splunk の検索 `index=* source="telegraf:snmp_trap"` で見る。
    gNMI の IF の状態は `source="telegraf:interface"`、BGP は `telegraf:bgp_neighbor`、IS-IS は `telegraf:isis_interface`（名前は Telegraf のころのまま）。
  - 保存済みサーチが動いたかは `index=_internal sourcetype=scheduler savedsearch_name=nwc_*`
    （[pipeline.md](pipeline.md) の「Splunk のアラート」）。
- `SNS`: ログは `/ecs/<prefix>-grafana`、Splunk は検索 `index=_internal sourcetype=splunkd sendmodalert nwc_sns`。
- `graph-status`:
  - `UNREGISTERED` の警告は、アラートの機器名・IF 名がトポロジに無い。
    Splunk なら IP を `DEVICE_MAP` で機器名に直せていない。lab に足した機器なら `ops/sync-graph.sh --replace`。
  - `読めないメッセージ（捨てる）` は本文の形が違う（[pipeline.md](pipeline.md) の「アラート」）。
- `query_history`:
  - `filter @message like /ALERT_EVENT_LOST/` に出る行は Firehose に 3 回送っても届かなかったもの
    （メッセージの `ALERT_EVENT_LOST ` のあとが行の JSON そのまま）。
  - 多ければ `kinesis-firehose` のエンドポイント（手順 0 の一覧）と、ロールの `firehose:PutRecordBatch`。
  - `ALERT_DROPPED` は `device_id` か `kind` が無い・`status` が firing / resolved でない通知か、
    行を組めない通知（`starts_at` が epoch ミリ秒など）で、行にしていない（送り手のテンプレートを見る）。
  - Firehose が受けたのに S3 Tables に入らなかった行は土台のバケットの `firehose-errors/alert_events/`。
  - 行は Neptune より先に送るので、Neptune が遅くても応答しなくても、この表の行には影響しない。
- `Neptune の status`:
  - Lambda は例外か timeout で落ち、非同期のやり直し（2 回まで）で Neptune に書き直すので、`status` は遅れて変わる。
    やり直しでも書けなければ `status` は変わらないまま。
  - やり直しの分、履歴の行は二重に入る（`query_history` は `event_id` で落とす）。
- `ALARM`:
  - Grafana は Alerting → Alert rules のルール `bgp_down` / `isis_down` / `trap` の状態、
    Splunk は表の「Splunk のアラートが出ない」の行を見る。
  - Grafana の `bgp_down` / `isis_down` は Explore で `snmp_bgp_neighbor_session_up` / `snmp_isis_interface_oper_up` が来ているかも見る。
- `metrics`:
  - stream の output `gnmic_list_tasks_command` で gnmic のタスクが動いているか、
    ロググループ `/ecs/<prefix>-gnmic` に Kafka のエラー（SCRAM の認証や ACL）が出ていないかを見る。
  - 2026-10-09 の AWS では、Spark のジョブが Kafka の ACL を入れる前から gnmic は `metrics` に書けた
    （`docs/verification/20261009-aws-managed.md` の「B.」）。
  - ACL を入れたあとは未確認（[architecture/resources/telegraf.md](architecture/resources/telegraf.md) の「制約と未確認」）。
  - gnmic が書けているのに空なら Spark（[pipeline.md](pipeline.md) の「Spark を確かめる」）。
- `修復案`:
  - `terraform -chdir=IaC/terraform/aws-managed/workflow output -raw anomaly_dlq_url` のキューに溜まっていれば、
    ワーカーが 5 回読んで処理できなかった。
  - ワーカーのログは `terraform -chdir=IaC/terraform/aws-managed/workflow output -raw worker_logs_command`
    （[workflow.md](workflow.md) の「うまくいかないとき」）。
- `pending`:
  - 2026-10-08 の AWS では、worker が承認を受け取ったのは承認を打ってから 10 秒以内だった
    （[verification/20261008-managed-aws.md](verification/20261008-managed-aws.md) の時刻の表。
    承認を打ったのが 19:46:37〜46、worker が受け取ったのが 19:46:44 / 45）。
  - Athena に出て画面が変わるまでの時間は AWS では未確認。
  - 1 分たっても変わらなければ、worker のログに `decide <proposal_id>` が出ているか、
    DLQ `<prefix>-decisions-dlq` に溜まっていないかを見る（[workflow.md](workflow.md)）。
- `7-2c`:
  - `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream output -raw telegraf_dialout_list_tasks_command`
    （gnmic は `gnmic_list_tasks_command`）に `--desired-status STOPPED` を足して打ち、`aws ecs describe-tasks` の `stoppedReason` を見る。
  - `CannotPullContainerError` / `ResourceInitializationError` は ecr.api / ecr.dkr / logs のエンドポイント（手順 0 の一覧）と S3 の gateway
    （gnmic は起動時に secrets を読むので secretsmanager と ssm も）。
  - 起きてすぐ終わるなら、Telegraf はロググループ `/ecs/<prefix>-telegraf`（ストリーム `dialout/…`）、
    gnmic は `/ecs/<prefix>-gnmic`（ストリーム `gnmic/…`）の最初の行を見る。
  - gnmic が購読先の形・同じ IP・認証情報のどれかで止まっていれば、stream の変数 `gnmi_targets`（`ops/up.sh` が lab の定義から作る）か、
    SSM の `/<prefix>/gnmic/` の下か、Secrets Manager の `AmazonMSK_<prefix>-gnmic`。
  - NLB のヘルスチェック（`8080/tcp`、Telegraf の `outputs.health`。Telegraf が動いていれば 200）が通らないと入れ替えが続く。
    この 2 つのうち NLB の後ろにいるのは Telegraf だけで、gnmic は NLB を持たない。
- `7-2d`:
  - `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream output -raw syslog_ng_list_tasks_command`
    （GoFlow2 は `goflow2_list_tasks_command`）に `--desired-status STOPPED` を足して打ち、`stoppedReason` を見る。
  - `ResourceInitializationError` で secret を取れないなら、secretsmanager のエンドポイント（手順 0 の一覧）か、
    実行ロールの `secretsmanager:GetSecretValue` / `kms:Decrypt`（secret `AmazonMSK_<prefix>-syslog-ng` / `AmazonMSK_<prefix>-goflow2` と鍵 `alias/<prefix>-msk-scram`）。
  - 起きてすぐ終わるならロググループ `/ecs/<prefix>-syslog-ng` / `/ecs/<prefix>-goflow2` の最初の行。
  - SASL の認証で落ちるなら、secret が MSK に付いているか（`aws kafka list-scram-secrets`）。
  - NLB のヘルスチェックは syslog-ng が `5140/tcp`、GoFlow2 が `8081/tcp` の `/__health`。
- `authorization failed`:
  - 2026-10-09 の AWS で、ACL を入れる Spark を起こさない回（`SKIP_ANALYTICS=1`）でも書けて、`authoriz` を含む行は 0。
    `allow.everyone.if.no.acl.found` が効いている（`docs/verification/20261009-aws-managed.md` の「A.」）。
  - ACL は analytics の Spark のジョブが起動時に入れる。入れたあとの振る舞いは AWS では未確認。
  - 書けないあいだ、syslog-ng は syslog をメモリのキュー（既定 10000 件まで。syslog-ng が起こし直すと消える）で持っていて書けるようになったら書き、
    GoFlow2 はその間のフローを捨てる。どちらも落ちず、ヘルスチェックも通る。
  - ジョブが動いているのに出るなら、ジョブの driver の stderr に `ACL: User:syslog-ng WRITE logs, …` の行があるかを見る。
  - 1 つのコレクターだけで出るなら、そのユーザーが自分のトピック以外に書いていないか（ユーザーごとのトピックは [pipeline.md](pipeline.md) の「収集器の ACL」の表。cycle 031）。
  - ジョブが起動で `ClusterAuthorizationException` で落ちているなら、
    EMR の実行ロールの `kafka-cluster:AlterCluster`（`IaC/terraform/aws-managed/pipeline/analytics/access.tf`）。
- `7-3c`:
  - `terraform -chdir=IaC/terraform/aws-managed/pipeline/nautobot output -raw list_tasks_command` に `--desired-status STOPPED` を足して打ち、
    `aws ecs describe-tasks` の `stoppedReason` を見る。
  - ロググループは `/ecs/<prefix>-nautobot`（ストリームはコンテナごとに `web/…` / `worker/…` / `redis/…`）。
  - `CannotPullContainerError` / `ResourceInitializationError` は ecr.api / ecr.dkr / logs のエンドポイント（手順 0 の一覧）と S3 の gateway。
    直したら `ops/up.sh` を打ち直す。
- `SYSLOG_STANDARD`:
  - up.sh は `deploy.env` の `SYSLOG_STANDARD`（空なら RFC3164）を stream の `syslog_standard` に渡す。
    lab のログを見るなら `SYSLOG_STANDARD=RFC5424` にして打ち直す。
  - デバッグ用の EC2 は syslog を受けない。
- `ポートフォワード`:
  - Cloud Map の名前（`grafana.<prefix>.internal` / `splunk.<prefix>.internal`）はタスクが動いていないと引けない。
  - Splunk がクラスター（`SPLUNK_AZ_NUM` が 2 か 3）のとき、画面は search head のサービス `<prefix>-splunk`
    （cluster manager の画面は `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw splunk_cm_port_forward_command`）。
  - 起きないときはロググループ `/ecs/<prefix>-grafana` / `/ecs/<prefix>-splunk`（クラスターではストリームの頭が `splunk` / `splunk-cm` / `splunk-idx`）と
    `stoppedReason`（ECR のエンドポイントと、SSM のパスワードが消えていないか）。
- `nwc-peer-check`:
  - ロググループ `/ecs/<prefix>-splunk` のストリーム `splunk/…` で `nwc-peer-check` の最新の行を見る。`state=ok reason=peers_up:<数>` が正常。
  - `mismatch` は古い indexer を覚えたまま。
    `degraded` は cluster manager が Up と言う indexer がタスク定義の数より少ない（`reason=peers_up:<Up の数>/<あるはずの数>`。止まっている indexer を見る）。
  - `skip` は cluster manager に聞けていない、`error` は search head が自分の peers を読めていない。
  - indexer と cluster manager（`splunk-idx/…` / `splunk-cm/…`）が起きているかを見て、`ops/up.sh` を打ち直す。
  - 2026-10-05 の AWS（`SPLUNK_AZ_NUM=2`）では `state=ok reason=peers_up:2` になった。

### `ops/check-grafana.sh` の終了コード

`ops/up.sh`（OSS 版は `ops/oss/up.sh`）の手順 9-2 の警告も同じ分け方（0 は警告なし、1 と 2 は止めずに黄色の警告）。

- 3 に当たるもの（analytics の state の一覧か、Grafana のクラスター・サービスの名前（`tf output`）が読めないか空）も止めない。
  止めると、配るコマンドとほかの警告の再掲、ポートフォワーディングまで届かないため（ワーカーが安定しない 8-5 と同じ扱い）。
- そのときは `aws ecs wait` も確かめも打たずに、「確かめていない（Grafana のサービスが安定するのも待っていない）」と黄色で警告して最後の案内まで進む。
- どれが読めないかは、その前の terraform のエラーと赤い `NG:` の行に出る。
- OSS 版のクラスターの名前は手順 7-4b で読み、読めなければそこで止まる。

| 値 | 意味 | 当たるもの | 出す案内 |
|---|---|---|---|
| 0 | OK | 全部のルールの打ったあとの評価にエラーが無い | なし |
| 1 | NG | 評価がエラーのルールがある（`判定: NG`） | Grafana のログ（`Failed to evaluate rule`） |
| 2 | 未確認 | 確かめに行ったが結果が分からない。`判定: 未確認`、SSM Run Command を送れない・失敗した・`SSM_RUN_WAIT` 秒（既定 1800）を過ぎた、`判定:` の行が無い（`判定: 未確認` の中身は表の下の `2`） | 確かめ直すコマンド（`ops/check-grafana.sh [--oss]`）。ログの案内は出さない |
| 3 | 確かめる前に止まった | 使い方の誤り、`deploy.env` の誤り、Web の EC2 か Grafana が無い（`tf output` が読めないか空）、`SSM_RUN_WAIT` の値の誤り | `NG:` の赤い行 |

- `2`: `判定: 未確認` になるのは、401 / 403、Grafana に届かない・起動中、待ち切れ、ルールが 0 本、SSM の admin のパスワードが読めない、
  ページのトークンが繰り返すか 100 ページを超える、応答の `status` が `success` でない、のどれか。

### Kafbat UI

Kafbat UI は Web の EC2 の Docker で動く（`127.0.0.1:8082`）。Web の EC2 に入って `systemctl status <prefix>-kafka-ui` と `journalctl -u <prefix>-kafka-ui` を見て、journald の行で分ける。

#### ユニットは動いているのに 8082 につながらない

まだ上がっていない（初回の `ops/up.sh` の直後など。イメージの pull と Java の起動を待つ）。
上がると journald に `Started KafkaUiApplication` が出る。
systemd の `Started <prefix>-kafka-ui.service` はスクリプトが動き出した時点で出るので、上がった合図ではない。

#### `… does not exist (pipeline/stream is not applied …). Not retrying; …` で止まっている（終了コード 75）

SSM のパラメータが無かった。
ユニットは `inactive (dead)` で止まり（`SuccessExitStatus=75`）、自分では起こし直さない（cycle 026 で足した形。2026-10-10 の AWS で `inactive (dead)` / `Result=success` と、`systemctl is-system-running` が `running` のままなのを実測）。
026 より前は `failed` のまま止まり、`systemctl is-system-running` が `degraded` になった（2026-10-09 の AWS。`NRestarts=0`）。
そのときは stream を作ってから Web の EC2 を起こし直すと `running` に戻った。

`does not exist` の前の名前が、最初に無かったパラメータ（読む順は `image`、`bootstrap-servers`、`security-protocol`、`admin-password`）。

- `image` など:
  - Web の EC2 が起きたときに stream がまだ無かった。`SKIP_STREAM=1` の回はそれで正しい。
  - stream を足すときは `SKIP_STREAM` を外して `ops/up.sh` を打ち直す（手順 8-3 の Web の再起動で起きる）。
  - OSS 版の `ops/oss/up.sh` は `SKIP_STREAM` を読まず、いつも stream を作って手順 7-5 で起こす。
- `admin-password`:
  - stream を terraform だけで上げた。
    `image` などは stream の Terraform が作るが、ログインのパスワード `/<prefix>/kafka-ui/admin-password` は `ops/up.sh` の手順 7（OSS 版は `ops/oss/up.sh` の手順 7）が作る。
  - このまま `sudo systemctl start <prefix>-kafka-ui` しても同じ 75 で止まる。`ops/up.sh` を打ち直す。
- `ops/up.sh` が stream の apply（手順 7）のあと、8-3（OSS 版は 7-5）より前で止まった回（graph・nautobot・analytics の apply の失敗など）:
  - 起きたときの 75 のまま残る。
  - 直して `ops/up.sh` を打ち直すか、`sudo systemctl start <prefix>-kafka-ui`。

#### `systemctl status` が `status=75` で止まっているのに、`does not exist` の行が無い

コンテナ（Kafbat UI）が 75 で終わった。
`RestartPreventExitStatus=75` はユニットの main プロセスの終了コードを見る。
スクリプトは `exec docker run` するので、コンテナの終了コードがそのまま main プロセスの終了コードになり、75 なら起こし直さない。
`SuccessExitStatus=75` もあるので、`status=75` でもユニットは failed にならない。

コンテナは `--rm` で消えているので `docker logs` では見られない。
`journalctl -u <prefix>-kafka-ui` の、止まる直前のコンテナの出力を見る。
Kafbat UI が 75 で終わる場面は知られていない（未確認）。

#### `Cannot read … Retrying in 30 s. AWS CLI: …` が 30 秒ごとに出る（終了コード 69）

パラメータが無いのではなく読めない。
後ろの AWS CLI のエラー文で、Web のロールの `ssm:GetParameter`、SSM のエンドポイント、認証情報を見る。

#### ユニットが無い（`Unit <prefix>-kafka-ui.service could not be found`）

user_data の Docker の節が落ちた（ユニットを書く前に落ちたときはこれ。下の `daemon-reload` / `enable --now` で落ちたときもこれになることがある）。
`/var/log/cloud-init-output.log`（か `journalctl -u cloud-final`）の `<prefix>-kafka-ui: setup failed` の行と、その直前のエラー（`dnf install -y docker` など）を見る。

直したら EC2 を再起動する（user_data がもう一度走る）。
Gradio の画面は、この節が落ちても動く。

#### `setup failed` の行があり、ユニットが動いていない（`daemon-reload` / `enable --now` で落ちた）

ユニットは書けたが、そのあとの `systemctl daemon-reload` か `systemctl enable --now <prefix>-kafka-ui` が落ちた。
`systemctl status <prefix>-kafka-ui` は、落ちた場所で `could not be found` か `disabled` になる（どちらになるかは未確認）。

直前のエラーを見て直し、手で続きを打つ: `sudo systemctl daemon-reload && sudo systemctl enable --now <prefix>-kafka-ui`。
EC2 の再起動でもよい。

#### `docker login` / `docker pull` で落ちる

ECR に届いていない。
ecr.api / ecr.dkr のエンドポイント（stream が足す）と S3 の gateway を見る。

#### 起きたのに画面に MSK が出ない

メタデータのホップ数が 2 か（コンテナからインスタンスロールが取れない）と、ロールにポリシー `<prefix>-kafka-ui` が付いているかを見る。

#### SSM のパスワードかイメージを変えた

`sudo systemctl restart <prefix>-kafka-ui`。
スクリプトはパラメータを起動のときに 1 回だけ読むので、restart か EC2 の再起動まで古い値のコンテナが動き続ける。

イメージは、`KAFKA_UI_TAG` を上げて stream を apply すると SSM の `/<prefix>/kafka-ui/image` が変わる。
同じ回の `ops/up.sh` の中では入れ替わらない（手順 4-4 の EC2 の再起動は stream の apply（手順 7）より前で、手順 8-3 の Web の再起動は動いている Kafbat UI に触らない）。
次に打ち直した回の手順 4-4 の再起動で新しい値を読む（OSS 版の `ops/oss/up.sh` も同じ手順 4-4 で再起動する）。

#### 手で止めたのに戻ってくる

`sudo systemctl stop <prefix>-kafka-ui` で止めても（t4g.medium のメモリを Gradio に空けたいときなど）、Web のユニットの `Wants=` が、Web の start / restart のたびに起こす（手順 8-3 の打ち直し、手で打つ `systemctl restart <prefix>-web`）。

止めたままにしたいなら `sudo systemctl mask --runtime <prefix>-kafka-ui`。
戻すのは `sudo systemctl unmask --runtime <prefix>-kafka-ui`（`--runtime` を付けないと `/run` の mask は外れない）。
EC2 の再起動でも mask は消える。

## 2026-10-09 の改名より前に立てた環境

2026-10-09 に Splunk のアプリ、Nautobot の App と API ユーザーと JobHook、S3 Tables の namespace の名前を `nwc` に揃えた（「名前を nwc に揃える（019）」）。
それより前に立てて残した環境に新しい名前のものを上げると、次が起きる。

どれも、先に `ops/down.sh`（手元の compose は `docker/compose/down.sh -v`）で消してから上げれば起きない（[deploy.md](deploy.md)、[docker/compose/README.md](../docker/compose/README.md) の「消す」）。

| 症状 | 原因と直し方 |
|---|---|
| 手元の compose の Splunk で、保存済みサーチ `nwc_*` が動かない（前の名前のアプリが動き続ける）か、同じアラートの `sendmodalert` が 2 回ずつ出る | volume `splunk-etc` に前の名前のアプリが残っている。同じ版のまま上げると新しいアプリが入らず、版を上げると両方が動く。`docker/compose/down.sh -v` で volume ごと消してから上げる（補足は表の下の `splunk-etc`） |
| Nautobot の起動ログに Token の `IntegrityError` が出る。JobHook が 2 つある | Nautobot の RDS に前の名前の API ユーザーと JobHook が残っている。新しい名前のユーザーに同じキーのトークンを作ろうとして一意制約で落ちる（起動は続く）。`ops/down.sh` で RDS ごと消してから上げる。コードを読んだだけで、AWS では未確認 |
| analytics だけを apply し直したあと、workflow が前の namespace を読む | workflow は analytics の state の `table_namespace` を apply のときに読むので、workflow を apply し直すまで前の namespace のまま（`ops/up.sh` を通しで打てば analytics が先なので起きない）。`ops/down.sh` で消してから上げる。コードを読んだだけで、AWS では未確認 |

- `splunk-etc`:
  - compose は SNS の topic を渡さないので、SNS には届かず失敗のログが 2 回出る。
  - ECS の Splunk は volume を持たないので起きない。
  - コードを読んだだけで、再現はしていない。

## 消すとき

| 症状 | 原因と直し方 |
|---|---|
| `DependencyViolation`（SG / サブネット） | Runtime の ENI が残っている（最大 8 時間）。`ops/down.sh` は VPC・サブネット・Runtime の SG を残して終了コード 0 で終わる。残ったものは無料で、次の `ops/up.sh` が使い回すので、そのままでよい（[deploy.md](deploy.md) の「消したあとに残るもの」。続きは表の下の `DependencyViolation`） |
| `ops/down.sh` の最後の一覧に `<prefix>-lab-debug` の VPC やバケットが出る | デバッグ用の EC2 のスタック。`ops/down.sh` は消さないので `ops/lab-debug.sh down` |
| `ops/lab-debug.sh down` が「… を空にできなかった」/「消えなかった」 | 打ち直す。原因は `aws cloudformation describe-stack-events --region ap-northeast-1 --stack-name <prefix>-lab-debug` |
| `ops/down.sh` の最後に残りが出る | タグの API の一覧は消えたリソースも返す（2026-10-08 は何日も前に消えた EMR まで 189 件）。実体が残っているかはサービスごとの API で見る（[deploy.md](deploy.md) の「消したあとに残るもの」）。残っていたのが上の VPC 一式と `KEEP_ECR=1` の ECR だけならそのままでよい。それ以外は手で消す |

- `DependencyViolation`: 2026-10-05 と 2026-10-08 の AWS でもこうなった。消し切るときだけ、ENI が外れてから同じチェックアウトで打ち直す。

## 既知の不具合

2026-10-05 の AWS の動作確認（`AGENT=1 PIPELINE=1 WORKFLOW=1 ENDPOINTS_AZ_NUM=2 SPLUNK_AZ_NUM=2`）で見つけて、まだ直していないもの。直したら、その行を消す。

| 見つけた日 | 症状 | 分かっていること |
|---|---|---|
| 2026-10-05 | 1 本の回線断で修復案が 2 件できる（見つけたときは 4 件） | 回線の leaf 側と spine 側が、別の異常として数えられる。1 件を承認して verified になると、残りは obsolete になった。両端を 1 つにまとめるのは別のサイクル（直した点は表の下の `nwc_trap`） |

- `nwc_trap`: 4 件のうち 2 件の原因だった、Splunk の trap の検索がサブインターフェース（`ethernet-1/1.0`）を除いていない点は直した（手元のテストで確認、AWS では未確認）。

## 経緯

- 2026-10-08（012）: syslog を Telegraf ではなく syslog-ng が受けるようにした。サービス `<prefix>-syslog-ng` / `<prefix>-goflow2` はこのとき足した。
- 2026-10-09（013）: Grafana のルール `link_down` などが見る値を、SNMP のポーリングから gnmic の gNMI の値に替えた。`metrics` トピックの IF の統計・CPU・メモリも gnmic が書く（SNMP のポーリングはやめた）。
- 2026-10-09（013）: デバッグ用の EC2 の Telegraf の `test` / `gnmi` をやめた（受けるのは trap だけ）。
