# Kafbat UI を Web の EC2 に同居させ、lab のトポロジ図を見られるようにする（010）

設計: PM(fable-5.1) / effort: high

## 背景

Kafbat UI は `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` が ECS Fargate のタスク 1 つ（0.5 vCPU / 1 GiB、arm64）として立て、Cloud Map の名前空間 `<prefix>-stream.internal` の `kafka-ui` で名前を引き、Web の EC2 を踏み台に `AWS-StartPortForwardingSessionToRemoteHost` で手元の 8082 に出している。Fargate のタスク（$0.02/h）と Cloud Map の名前空間（Route 53 のホストゾーン $0.50/月）を、画面を 1 つ開くためだけに持っている。

2026-10-08 のユーザー決定（BACKLOG 23）: **Fargate のタスクと Cloud Map をやめ、Kafbat UI を Web の EC2 に Docker で同居させる。** EC2 は t4g.medium に上げ、Docker と MSK の IAM 権限と SG の web → MSK 9098 を足す。あわせて **lab の EC2 で `containerlab graph` のトポロジ図を 50080 の SSM ポートフォワードで見られるようにする。**

調査で分かった事実（実物を読んだ。推測は「未確定事項とリスク」に分けた）:

- `kafka_ui.tf` は 12 リソース（ロググループ、Cloud Map の名前空間と service、タスク定義、ECS サービス、exec ロール + attachment + inline、task ロール + inline、perimeter の attachment 2）。`tests/test_stream.py:376` が「ちょうど 12」を見ている
- **Cloud Map の名前空間 `aws_service_discovery_private_dns_namespace.stream` は OSS 版の Kafka も使う。** `IaC/terraform/oss/pipeline/stream/kafka.tf:57` が `kafka-<n>.${local.stream_service_namespace}` を組み、`:129-138` が `aws_service_discovery_service.kafka` をこの名前空間に登録する。`kafka_ui.tf` は OSS 側へシンボリックリンクで共有されている（`locals.tf` / `outputs.tf` / `variables.tf` / `telegraf.tf` / `access.tf` も同じ）
- 画面の認証: `AUTH_TYPE=LOGIN_FORM`、`SPRING_SECURITY_USER_NAME=admin`、パスワードは SSM SecureString `/<prefix>/kafka-ui/admin-password`（`ops/up.sh:921` の `ensure_secret` が stream の apply の前に作る。鍵は `aws/ssm` なので `ssm:GetParameter --with-decryption` だけで読める。`kafka_ui.tf:178` のコメント）
- MSK への認証は `SASL_SSL` + `AWS_MSK_IAM`（`IAMClientCallbackHandler`。資格情報は既定のチェーン = タスクロール）。権限は `msk.tf:46-81` の `kafka_ui_kafka_statements`（Connect / DescribeCluster / topic の読み書き / DescribeGroup）。OSS 版は `kafka.tf:101-106` で Deny `kafka-cluster:*` に差し替え、`oss.auto.tfvars:6` で `PLAINTEXT`
- Web の EC2（`base/core/web.tf:69`）: `t4g.small`、AL2023 arm64、gp3 8 GB、`user_data_replace_on_change = true`（user_data を変えると作り直し）。`variables.tf:77-86` の validation は `t4g.medium` を既に許している。user_data（`templates/web_user_data.sh.tftpl`）は python3.13 を入れ、S3 の `web/` を同期し、systemd の `<prefix>-web.service` で Gradio を 127.0.0.1:8080 に出す。**Docker は入っていない。** コメント以外に ASCII 以外を書かない決まり（`tests/test_workflow.py:646`）
- Web のロール（`web.tf:6`）: `AmazonSSMManagedInstanceCore` と inline `web-assets`（S3 の `web/*`、`ssm:GetParameter` on `/<prefix>/*`）。**ECR の権限は無い。** 他のルートがこのロールにポリシーを足している（`agent/runtime.tf:209`、`workflow/proposals.tf:64`、`stream/access.tf:3`、`graph/locals.tf:44` の graph_writer_role）ので、stream から足すのは前例どおり
- SG（`base/core/security_groups.tf`）: `kafka_ui` が `aws_api_clients`（:45）にいて 443 を持ち、行は `web → kafka_ui 8080`（:67）と `kafka_ui → msk 9098`（:76）、OSS 側は `oss.tf:40` の `kafka_ui → kafka 9092`。**`web → msk` も `web → lab` も無い。** 説明文を変えると SG が作り直される（:13）
- VPC エンドポイント: `ops/up.sh:507` が stream を作るときに `ecr.api ecr.dkr logs` を足す（Kafbat UI はいつも作るので、いつも足される）
- `ops/up.sh`: `KAFKA_UI_TAG=v1.5.0`（:130）、`mirror_image` で ECR の `<prefix>-kafka-ui` に写す（:692）、`ensure_secret`（:921）、`tf_apply pipeline/stream -var kafka_ui_image_tag`（:922）、出力（:1297-1299）。ECS の待ちは Telegraf だけで Kafbat UI には無い。費用は `COST_CENTS=2`（web、:563）と `+ 2  # Kafbat UI（Fargate のタスク 1）`（:572）
- lab の EC2（`pipeline/lab/instance.tf:7`）: `t4g.xlarge`、`app/containerlab/setup.sh` が docker と containerlab 0.79.0 を入れ、`lab.sh` を `/usr/local/bin/lab` に張る。`lab.sh` に `graph` は無く、`clab <args>` の素通しだけ（:135）。`outputs.tf` にポートフォワードの出力は無い
- `containerlab graph` の実物は手元に Linux が無く未確認（下の「未確定事項とリスク」1）

## 設計方針

### A. Kafbat UI を Web の EC2 の Docker で動かす

**配置と起動の順序。** Web の EC2 は `base/core` が作り、MSK の bootstrap は `pipeline/stream` の apply で決まる。順序の逆転は **SSM Parameter Store を経由して解く**: stream が接続先を String のパラメータに書き、Web の systemd ユニットがそれを読めるまで待って起動する。Run Command や user_data の作り直しで stream から EC2 を触らない。

- stream の `kafka_ui.tf` を書き換え、次だけを持つ（ECS / Cloud Map / ロググループ / exec ロール / task ロールは消す）:
  - `aws_ssm_parameter` 3 本（type `String`、tags は他と同じ）
    - `/<prefix>/kafka-ui/image` = `${kafka_ui_repository_url}:${var.kafka_ui_image_tag}`
    - `/<prefix>/kafka-ui/bootstrap-servers` = `local.kafka_ui_bootstrap_servers`
    - `/<prefix>/kafka-ui/security-protocol` = `var.kafka_ui_security_protocol`
  - `aws_iam_role_policy.kafka_ui_web`（`role = local.web_role_name`、`Statement = local.kafka_ui_kafka_statements`）。OSS 版では Deny のまま付く（Web は OSS 版で Kafka を使わないので害は無い）
  - precondition は今のもの（ECR の URL と bootstrap が空でない）を残し、`kafka_ui_sg_id` の precondition は消す
- `local.web_role_name` を `locals.tf` に足す（`data.terraform_remote_state.main.outputs.web_role_name`。graph の `locals.tf:44` と同じ引き方）
- **Cloud Map の名前空間は OSS 版の Kafka が要るので、`aws_service_discovery_private_dns_namespace.stream` と `local.stream_service_namespace` を `IaC/terraform/oss/pipeline/stream/kafka.tf`（OSS 専用。リンクではない）へ移す。** マネージド版には名前空間が無くなる（$0.50/月が消える）。`local.kafka_descriptions.namespace` も kafka.tf へ
- `variables.tf` の `kafka_ui_task_cpu` / `kafka_ui_task_memory` は消す（使う先が無い）。`kafka_ui_image_tag` と `kafka_ui_security_protocol` は残す
- `outputs.tf`: `kafka_ui_service_name` を消し、`kafka_ui_port_forward_command` を **`AWS-StartPortForwardingSession`**（ToRemoteHost ではない）で `--target <web_instance_id> --parameters portNumber=8082,localPortNumber=8082` にする。`kafka_ui_password_command` はそのまま

**Web の EC2 側（`base/core`）。**

- `variables.tf` の `instance_type` の既定を `t4g.medium` にし、description を書き換える（Gradio 約 400 MB + Kafbat UI の JVM 約 1 GB。t4g.small の 2 GB では足りない）
- `web.tf`: ルートボリュームを 8 → 16 GB（Docker と Kafbat UI のイメージ 約 640 MB の展開分。8 GB でも入るが余裕が無い）。IAM の inline `web-assets` に ECR の pull を足す: `ecr:GetAuthorizationToken` on `*`、`ecr:BatchGetImage` / `ecr:GetDownloadUrlForLayer` / `ecr:BatchCheckLayerAvailability` on `arn:aws:ecr:<region>:<account>:repository/<prefix>-kafka-ui`
- `templates/web_user_data.sh.tftpl` に足す（**コメント以外は ASCII だけ**）:
  1. `command -v docker >/dev/null || dnf install -y docker`、`systemctl enable --now docker`
  2. `/usr/local/bin/<prefix>-kafka-ui` スクリプトを書く。やること: `aws ssm get-parameter` で `image` / `bootstrap-servers` / `security-protocol` を読む（無ければ `exit 75`、systemd が再試行）。`admin-password` を `--with-decryption` で読む。`/run/<prefix>-kafka-ui.env` を `umask 077` で書く（`KAFKA_CLUSTERS_0_NAME`、`KAFKA_CLUSTERS_0_BOOTSTRAPSERVERS`、`KAFKA_CLUSTERS_0_PROPERTIES_SECURITY_PROTOCOL`、SASL_SSL のときだけ `..._SASL_MECHANISM=AWS_MSK_IAM` / `..._SASL_CLIENT_CALLBACK_HANDLER_CLASS` / `..._SASL_JAAS_CONFIG`、`AUTH_TYPE=LOGIN_FORM`、`SPRING_SECURITY_USER_NAME=admin`、`SPRING_SECURITY_USER_PASSWORD`、`GITHUB_RELEASE_INFO_ENABLED=false`、`JAVA_OPTS=-XX:MaxRAMPercentage=50`）。`aws ecr get-login-password | docker login --username AWS --password-stdin <registry>`。`docker pull`。`docker run --rm --name <prefix>-kafka-ui --env-file /run/<prefix>-kafka-ui.env -p 127.0.0.1:8082:8080 <image>`（フォアグラウンド。`--rm` で古いコンテナを残さない）
     - 値を `echo` や `set -x` で出さない。env ファイルは `/run`（tmpfs）で、ユニットの `ExecStopPost` で消す
     - `KAFKA_CLUSTERS_0_NAME` は stream が決めていた `local.kafka_cluster_name` をパラメータにしないで、`<prefix>` 固定でよい（画面に出る名前だけ）
  3. systemd ユニット `<prefix>-kafka-ui.service`: `After=docker.service network-online.target`、`Requires=docker.service`、`ExecStart=/usr/local/bin/<prefix>-kafka-ui`、`ExecStopPost=/bin/rm -f /run/<prefix>-kafka-ui.env`、`Restart=always`、`RestartSec=30`、`TimeoutStartSec=0`。`systemctl enable --now`
  - 既存の `web/` が S3 に無いときの `exit 0`（テンプレート :29）より**前**に Docker とユニットの部分を置く（Web の画面が無くても Kafbat UI は動く）
- MSK の IAM 認証は既定の資格情報チェーン → インスタンスロール。IMDSv2 の hop limit 1 のままだと **コンテナの中から IMDS に届かない**（Docker の bridge で 1 hop 増える）。`web.tf:90-94` の `http_put_response_hop_limit` を 2 にする（lab の EC2 で containerlab のコンテナが IMDS を使わないのとは事情が違う。理由をコメントに書く）
- SG（`base/core/security_groups.tf`）: `kafka_ui` を SG の一覧と `aws_api_clients` から消し、行 `web → kafka_ui 8080` と `kafka_ui → msk 9098` を消し、**`{ from = "web", to = "msk", protocol = "tcp", port = 9098, why = "Kafka IAM - Kafbat UI on the web EC2" }`** を足す。`oss.tf:40` は `{ from = "web", to = "kafka", protocol = "tcp", port = 9092, why = "Kafka - Kafbat UI on the web EC2" }` に替える。`kafka_ui` の SG が消えるので base/core の apply で SG が 1 つ減る
- `ops/up.sh`: `mirror_image` / `ecr_has` / `ensure_secret` / `-var kafka_ui_image_tag` は**そのまま**（イメージは ECR 経由のまま。ghcr.io に VPC から届かない）。費用の行を `COST_CENTS=4`（web t4g.medium 約 4.3 セント）にし、`+ 2  # Kafbat UI` の行とコメント（:64, :531, :543-545, :572）を消す。出力（:1297-1299）の説明を「Web の EC2 の Docker」に直す。`add_endpoints ecr.api ecr.dkr logs` は Telegraf の ECS が要るので残す
- `deploy.env.example` の「Kafbat UI（約 $0.02/h）もいつも入る」の文を「Kafbat UI は Web の EC2 に同居（追加の費用は t4g.small → t4g.medium の差 約 $0.02/h）」に直す（`tests/test_stream.py:466` が文字列を見ているので、テストも一緒に）
- `oss/ops/up.sh` の `kafka_ui_image_tag` / `ensure_secret` / 出力（:167, :213-215, :323, :329）は形を変えない。`oss/ops/roll-nodes.sh` と `tests/test_oss_roll.py` から `aws_ecs_service.kafka_ui`（`T_KIND=kafka_ui`）を外す
- `ops/down.sh` は変えない（stream の destroy で SSM パラメータとポリシーが消え、base/core の destroy で EC2 が消える。`admin-password` は今までどおり `delete_up_ssm_params`）

### B. lab の EC2 で `containerlab graph` を見る

- `app/containerlab/lab.sh` に `graph` と `graph-stop` を足す:
  - `graph`: `systemd-run --unit="$NAME_PREFIX-lab-graph" --collect --property=WorkingDirectory=$SRC clab graph -t "$TOPO" --srv 127.0.0.1:50080`（既に動いていれば案内だけ）。終わりに、手元で打つポートフォワードのコマンド（`aws ssm start-session --region <region> --target <self instance id> --document-name AWS-StartPortForwardingSession --parameters portNumber=50080,localPortNumber=50080`。instance id は IMDSv2 から取る）と `http://localhost:50080/` を出す
  - `graph-stop`: `systemctl stop "$NAME_PREFIX-lab-graph"`
  - `lab down` で `graph-stop` も呼ぶ
  - 127.0.0.1 に bind する。SSM のポートフォワードはエージェントがローカルで繋ぐので SG の変更は要らない
  - ヘルプ（:3-6）と `hint` の案内に足す
- `pipeline/lab/outputs.tf` に `graph_port_forward_command` を足す（上と同じコマンド。`aws_instance.lab.id` で）
- `tests/test_lab_debug.py`（または lab のテストがある場所）に、`lab.sh` の `graph` / `graph-stop` が `systemd-run` と `127.0.0.1:50080` を使うこと、`down` が `graph-stop` を呼ぶこと、outputs に `50080` のコマンドがあることを足す。`forward` にある偽の `iptables` / `sudo` の実行の検査と同じ形で、偽の `systemd-run` / `systemctl` を PATH に置いて `graph` / `graph-stop` を実行で確かめる

## 変更対象ファイル

- `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf`（書き換え）、`locals.tf`（`web_role_name`、`kafka_ui_sg_id` 削除）、`outputs.tf`、`variables.tf`
- `IaC/terraform/oss/pipeline/stream/kafka.tf`（名前空間を受け取る）、`IaC/terraform/oss/base/core/oss.tf` は無い → `IaC/terraform/aws-managed/base/core/oss.tf:40`
- `IaC/terraform/aws-managed/base/core/web.tf`、`variables.tf`、`security_groups.tf`、`templates/web_user_data.sh.tftpl`
- `IaC/terraform/aws-managed/pipeline/lab/outputs.tf`、`app/containerlab/lab.sh`
- `ops/up.sh`（費用と出力）、`deploy.env.example`、`oss/ops/roll-nodes.sh`
- tests: `tests/test_stream.py:368-476`（Kafbat UI の節を全部書き直す）、`tests/test_analytics.py:90-108`（SG_KEYS 14 本、EXPECTED_FLOWS の `web → msk 9098`）と `:1503` / `:2013-2016`（費用）、`tests/test_oss.py:989,1008,1500,1553-1557,1861`、`tests/test_oss_roll.py:135,444,564`、`tests/test_oss_ops.py:886-887,1062-1065`（出力の名前は同じなので変わらないはず。確かめる）、`tests/test_workflow.py:644-648`（ASCII の検査はそのまま通ること）、`tests/test_lab_debug.py`
- docs（Explore の一覧。「Fargate」「Cloud Map」「ToRemoteHost」「/ecs/<prefix>-kafka-ui」「t4g.small」「2.2」を直す）: `docs/pipeline.md:151-170`、`docs/deploy.md:26,80,86,99,113,224`、`docs/architecture/core.md:19,34-37`、`docs/architecture/resources/web-ec2.md:15,21,34`、`msk.md:40,71-82`、`vpc-perimeter.md:47-50`、`ssm-parameter-store.md:46,53`（パラメータ 3 本を足す）、`ecr.md:33`、`docs/architecture/pipeline.md:5,31`、`docs/architecture/README.md:66,105`、`docs/data-stores.md:155,299`、`docs/oss-variant.md:27`、`docs/faq-fukuda-nwc-poc.md:2226-2262`、`docs/troubleshooting.md:92`。lab の graph は `docs/lab.md`（あれば）と `docs/deploy.md` の lab の節に 1 段落
- `docs/verification/*.md` は記録なので直さない

## 再利用するもの

- `ops/up-common.sh:91` `ensure_secret`、`ops/lab-common.sh:36,69` `ecr_has` / `mirror_image`（そのまま）
- `app/containerlab/setup.sh:16` の `dnf install -y docker` の形、`lab.sh` の `forward` の偽コマンドによるテストの形
- `pipeline/graph/locals.tf:44` の web ロール名の引き方、`stream/access.tf:3` の「stream から web ロールにポリシーを足す」前例
- `msk.tf:46-81` `kafka_ui_kafka_statements`（権限の中身は変えない。付け先だけ task ロール → web ロール）

## 実装ステップ（commit はこの単位）

1. **stream と base/core の Terraform**（A の kafka_ui.tf / locals / outputs / variables、OSS kafka.tf への名前空間の移動、web.tf / variables / SG / oss.tf、user_data）。`terraform validate` を managed と oss の `base/core` と `pipeline/stream` で通す
2. **ops と費用**（up.sh、deploy.env.example、roll-nodes.sh）
3. **lab の graph**（lab.sh、lab の outputs）
4. **tests と docs**。`bash ops/check.sh` が全部通る

`build.md` に各ステップの実行コマンドと出力を書く。設計に無いことを足したくなったら build.md の「設計との差」に書いて進める（止まらない）。

## 検証方法（期待出力まで）

1. `bash ops/check.sh` の最後が `すべて通過`、exit 0（いま 325 項目。件数は増えてよい）
2. `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream validate` と `.../oss/pipeline/stream`、`.../aws-managed/base/core`、`.../oss/base/core` の 4 つが `Success!`
3. `git grep -n 'service_discovery' IaC/terraform/aws-managed` が **0 件**、`git grep -n 'service_discovery' IaC/terraform/oss/pipeline/stream/kafka.tf` が名前空間と service の 2 か所以上
4. `git grep -n 'aws_ecs\|Fargate' IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` が 0 件
5. `grep -n 'from = "web", to = "msk"' IaC/terraform/aws-managed/base/core/security_groups.tf` が 1 件、`grep -c kafka_ui IaC/terraform/aws-managed/base/core/security_groups.tf` が 0
6. user_data の ASCII の検査: `python3 -c "import sys;[print(i,l) for i,l in enumerate(open('IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl').read().splitlines(),1) if not l.lstrip().startswith('#') and not l.isascii()]"` が何も出さない
7. user_data のスクリプト部分を手元で `bash -n`（テンプレート変数を仮置きして。`tests/` の既存の `tftpl` の検査の形があればそれで）
8. `lab.sh graph` の偽コマンドのテスト: 偽 `systemd-run` が受けた引数に `clab graph -t splab.clab.yml --srv 127.0.0.1:50080` が含まれ、`graph-stop` が偽 `systemctl stop <prefix>-lab-graph` を呼ぶこと
9. `ops/up.sh` の費用の合計の行（`COST_CENTS`）がテスト `tests/test_analytics.py` の期待と一致すること
10. **AWS は未確認のまま渡す。** PM がまとめて `ops/up.sh` で立て、`kafka_ui_port_forward_command` で 8082 が開き admin でログインでき、トピック一覧が出ること、`lab graph` で 50080 にトポロジ図が出ることを確かめる

## 未確定事項とリスク

1. **`containerlab graph` の画面が CDN からアセットを読むかもしれない（未確認）。** VPC はインターネットに出ないので、読むなら白紙になる。実装者は AWS で確かめられないので、containerlab 0.79.0 のソース（`cmd/graph.go` と `graph/` のテンプレート）を読んで、外部 URL（`https://`）を参照していないか build.md に引用する。参照していたら `--static-dir` などの代替を調べて書き、BACKLOG 行の候補として報告する（この cycle では直さない）
2. **IMDS の hop limit。** Docker の bridge 越しに IMDSv2 に届くには hop limit 2 が要る、という理解は AWS の文書「Retrieve instance metadata」の「containers」の記述に基づく。`--network host` にすれば 1 のままでよいが、ポートの衝突を避けるため bridge + hop limit 2 を採る。AWS で未確認
3. **Web の EC2 の作り直し。** user_data と instance_type の変更で `aws_instance.web` が replace される。いまは AWS に何も立っていない（down.sh 済み）ので実害は無いが、docs/deploy.md に「010 以降の最初の apply で Web が作り直される」と 1 行書く
4. **メモリ。** t4g.medium 4 GB に Gradio 約 400 MB + Kafbat UI の JVM（`MaxRAMPercentage=50` で最大約 2 GB）。足りるはずだが AWS で未確認。足りなければ `JAVA_OPTS` を `-Xmx1g` にする
5. **OSS 版の web ロールに Deny の inline が付く。** `kafka_ui_kafka_statements` が Deny `kafka-cluster:*` なので、OSS 版の web ロールに「MSK を使えない」Deny が付く。OSS 版では MSK が無いので無害だが、`tests/test_oss.py` の IAM の検査が新しい inline を拾って落ちるかもしれない。落ちたらその検査を直す
6. **`kafka_ui_bootstrap_servers` が OSS 版では Kafka（ECS）の名前。** stream の apply で Cloud Map の名前が決まるのは今と同じなので順序の問題は無い
7. `tests/test_oss_ops.py:886-887,1062-1065` の `kafka_ui_port_forward_command` は output 名が同じなので通るはず。通らなければ直す
8. **Kafbat UI と Gradio が Web の EC2 のインスタンスロールを共有する（PoC では受容。2026-10-08 に PM が判断、build.md のセルフレビューの指摘 1）。**
   - Kafbat UI のコンテナは IMDSv2（hop limit 2）でロールの資格情報に届く。乗っ取られたときに届く先:
     - 決定のキューへの `sqs:SendMessage`（`IaC/terraform/aws-managed/workflow/proposals.tf` の HITL の境界）
     - `/<prefix>/*` の SSM SecureString
     - Neptune の書き込み
     - InvokeAgentRuntime
   - 逆に Gradio（と Web の EC2 上のどのプロセスも）は、`kafka_ui_web` の権限で MSK のトピックの作成・変更・削除とメッセージの送信ができる
   - 受容する理由:
     - 専用のロールを `awsRoleArn` で AssumeRole しても、元はインスタンスロールなので、Kafbat UI がインスタンスロールを使える事実は消えない
     - `DOCKER-USER` で外向きの通信を絞るのは、PoC には重い
     - MSK の権限は絞らない。2026-10-05 のユーザー決定（`docs/cycles/005-oss-on-ecs/design-log.md` の「Kafbat UI は見るだけにしない」）で、画面からの変更を要るとしている
   - 開き方は 127.0.0.1 と SSM のポートフォワードだけで、画面はログインフォーム
