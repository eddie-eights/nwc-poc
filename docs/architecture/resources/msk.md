# MSK

← [リソースごとの知見](README.md)

## ひとことで

Telegraf が集めた機器のデータを、いったんためておく Kafka（Amazon MSK の Provisioned）。
書くのは Telegraf、読むのは Spark。ためるのは 24 時間で、履歴の置き場ではない。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| クラスター | `<prefix>-stream`。KRaft（ZooKeeper なし）、Kafka `4.1.x.kraft` | `IaC/terraform/aws-managed/pipeline/stream/msk.tf`、変数 `kafka_version` |
| ブローカー | `kafka.m5.large`（ほかに選べるのは `kafka.m7g.large`）、1 AZ に 1 台、EBS 10 GB | 変数 `broker_instance_type`、`msk.tf` |
| AZ の数 | `MSK_AZ_NUM`（既定 2、2〜3）。ブローカーの数と同じ | `ops/up.sh`、変数 `msk_az_num` |
| 認証と暗号 | IAM 認証だけ（9098）。クライアントとの間もブローカー同士も TLS | `msk.tf` の `client_authentication`、`encryption_info` |
| ブローカーの設定 | `auto.create.topics.enable=true`、`default.replication.factor` = ブローカーの数、`min.insync.replicas` = その 1 つ下、`num.partitions=2`、`log.retention.hours=24` | `msk.tf` の `aws_msk_configuration` |
| ブローカーのログ | CloudWatch Logs のロググループ `/<prefix>/msk`、保存 7 日 | 変数 `log_retention_days`、`msk.tf` |
| スイッチ | `PIPELINE=1` で作る。`SKIP_STREAM=1` で作らない（analytics も作らない） | `deploy.env.example` |
| 費用 | 57 セント/時（2 台。1 台増やすごとに +27） | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`） |

トピックと中身:

| トピック | 入っているもの | 書く Telegraf |
|---|---|---|
| `metrics` | SNMP のポーリングの結果（measurement `system` / `interface` など。IF の状態とカウンタ）。`SNMP_POLL=0` では空 | 取りにいく側 |
| `gnmi` | gNMI の購読（BGP のセッション、IS-IS の IF などの on_change とサンプル） | 取りにいく側 |
| `mdt` | Cisco の MDT の dial-out（本番向け。lab からは来ない） | 受ける側 |
| `traps` | SNMP の trap（linkDown / linkUp など） | 受ける側 |
| `logs` | 機器の syslog | 受ける側 |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Telegraf（受ける側 / 取りにいく側） | Telegraf → MSK | 9098/tcp、SASL_SSL + AWS_MSK_IAM。タスクロール `<prefix>-telegraf-task` |
| Spark（EMR Serverless） | Spark ← MSK | 9098/tcp、同じ認証。ジョブの実行ロール |
| ブローカー同士 | MSK ↔ MSK | 9092〜9098/tcp |
| Kafbat UI（Web の EC2 の Docker） | Web → MSK | 9098/tcp、同じ認証。Web の EC2 のロール（stream が足すポリシー `<prefix>-kafka-ui`）は、トピックの読み書き・作成・変更・削除と、グループを見ることまで |
| SSM | MSK → パラメータ | ブートストラップの文字列を `/<prefix>/msk-bootstrap`（String）に書く |

## 知見

- **MSK は 1 AZ にできない。**
  ブローカーを置くサブネットは 2 つ以上の AZ に要る（AWS の決まり）。ほかのリソースの既定が 1 AZ でも、MSK だけは既定 2。
  出典: FAQ「もう 2 AZ に置いてあるものは、1 AZ にできるか」、`IaC/terraform/aws-managed/pipeline/stream/variables.tf` の `msk_az_num`。
- **Kafka 4.x（KRaft）では `kafka.t3.small` が使えない。**
  `CreateCluster` が Unsupported InstanceType で拒む（2026-09-18 に見た）。`kafka.m5.large` が受け付けられる中でいちばん小さい。
  出典: `IaC/terraform/aws-managed/pipeline/stream/variables.tf` の `broker_instance_type` の説明。
- **4.1.x が Standard ブローカーの最新で、4.2.x は Express ブローカーだけ。**
  出典: `IaC/terraform/aws-managed/pipeline/stream/variables.tf` の `kafka_version` の説明。
- **トピックは最初の書き込みで自動でできる。**
  `auto.create.topics.enable=true`。そのため Telegraf のタスクロールに `kafka-cluster:CreateTopic` が要る。Spark も、無いトピックを起動時に作る（`ensure_topics`）ので、`SNMP_POLL=0` で `metrics` が無くても落ちない。
  出典: [data-stores.md](../../data-stores.md) の「15. ブローカーの渡し方と msk-bootstrap」、FAQ「SNMP はポーリングと trap のどちらで集めている？ ポーリングは止められる？」。
- **`min.insync.replicas` はブローカーの数の 1 つ下。**
  2 台なら 1 なので、1 台止まっても書ける。
  出典: `IaC/terraform/aws-managed/pipeline/stream/msk.tf` の `aws_msk_configuration` の上のコメント。
- **ブートストラップの文字列は、クラスターを作り終えるまで決まらない。**
  Telegraf はタスク定義の環境変数 `KAFKA_BROKERS`、Spark はジョブの引数 `--bootstrap` でもらう。どちらも SSM は読まない。`/<prefix>/msk-bootstrap` は手で確かめるときのために残してある。
  出典: [data-stores.md](../../data-stores.md) の「15.」。
- **Telegraf から Kafka は「少なくとも 1 回」。**
  `required_acks = 1` なので、受け取ったリーダーが複製の前に落ちた分は失う。返事が届かず送り直した分は重複する。idempotent producer は使っていない。
  出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。
- **Telegraf は Kafka のキーを付けない。**
  同じ系列がパーティションに散らばる。Spark の `HTTP_SEND=executor` で Prometheus に送るときは、送る前に系列で分け直している。
  出典: `deploy.env.example` の `HTTP_SEND` の説明。
- **MSK の IAM 認証には `aws:SourceVpc` の条件キーが無い。**
  閉域の Deny には入れていない。口は VPC の中にしか無い。
  出典: [core.md](../core.md) の「閉域」。
- **Kafka の画面として Kafbat UI が Web の EC2 で動く。**
  stream を作る回はスイッチなしで動く（接続先を `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` が SSM に書き、Web の EC2 のユニット `<prefix>-kafka-ui` が Docker で起こす。010 から。費用は Web の EC2 を t4g.medium にした差の約 2 セント/時で、土台に入っている）。見るだけにはしていない（トピックの追加・変更・削除、メッセージの送信ができる）。画面はログインあり、Web の EC2 を踏み台にして `http://localhost:8082/` で開く。ヘルスチェックの `/actuator/health` は Kafka に届かなくても UP を返すので、コンテナが動いていても MSK につながっているとは限らない。
  出典: [pipeline.md](../../pipeline.md) の「Kafka の画面（Kafbat UI）を開く」、[005 の経緯](../../cycles/005-oss-on-ecs/design-log.md)。
- **コンソールでトピックの一覧は見られるが、メッセージの中身は見られない。**
  出典: FAQ「MSK にも Kafbat UI みたいな GUI はある？」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Kafbat UI が MSK に IAM でつながるか | ECS のタスクだったときは 2026-10-05 に AWS で確かめた（タスクのログに `Metrics updated for cluster`）。Web の EC2 のインスタンスロール（010）では未確認 |
| Kafbat UI の画面に入れるか、画面からトピックを足せるか、ロールの権限で足りるか | 未確認（2026-10-05 の動作確認では画面を開いていない。手元の Docker で起動と画面まで） |
| MSK のコンソールの topic の機能がこのクラスターで開けるか | 未確認（条件には合うはず。FAQ の同じ Q） |
| 保存期間 | 24 時間。Spark を 24 時間より長く止めると、そのあいだの分は読めない |
| AZ 間の転送料 | 費用の数字に入れていない |

OSS 版（`IaC/terraform/oss/pipeline/stream`）には MSK が無く、代わりに `kafka.tf` が Apache Kafka（KRaft）を ECS に 3 台立てる（9092、認証なし。データは EFS）。Telegraf と Kafbat UI の接続先は同じファイルをシンボリックリンクで使い、書き先だけが変わる（Kafbat UI は OSS 版でも Web の EC2 で動き、Kafka の 9092 に平文でつなぐ）。[oss-variant.md](../../oss-variant.md)、[マネージドを OSS に置き換えた環境を作る（005）の設計](../../cycles/005-oss-on-ecs/design.md)。

## 関連

- [telegraf.md](telegraf.md)、[emr-serverless.md](emr-serverless.md): 書く側と読む側
- [data-stores.md](../../data-stores.md): 「MSK とクライアントのつなぎ」「届け方の保証」
- [pipeline.md](../pipeline.md): パイプラインの構成
