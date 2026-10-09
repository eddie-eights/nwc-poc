# Telegraf と gnmic（ECS）

← [リソースごとの知見](README.md)

## ひとことで

機器のデータを集めて MSK に書く収集役。Fargate のタスクで動く。
Telegraf は機器から送られてくる SNMP trap を受ける（受ける側。dialout）。機器の gNMI をこちらから取りにいくのは gnmic（`gnmic.tf`）。
機器の syslog と NetFlow / sFlow は、別のサービス（syslog-ng と GoFlow2。`collectors.tf`）が同じクラスターと NLB で受ける。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| クラスター | `<prefix>-telegraf` | `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf` |
| 受ける側 | サービス `<prefix>-telegraf-dialout`。trap を内部 NLB の後ろで受ける（syslog は syslog-ng、NetFlow / sFlow は GoFlow2）。タスク数 = `TELEGRAF_AZ_NUM`（既定 1、1〜3） | `telegraf.tf` の `aws_ecs_service.telegraf_dialout` |
| gnmic | サービス `<prefix>-gnmic`（同じクラスター）。gNMI の購読 5 つ（状態は on-change でトピック `gnmi`、カウンターは 60 秒ごとに `metrics`）。タスクはいつも 1 つ。NLB には付けない | `IaC/terraform/aws-managed/pipeline/stream/gnmic.tf` の `aws_ecs_service.gnmic`、`app/gnmic/gnmic.yaml.in` |
| タスクの大きさ | Fargate ARM、0.25 vCPU / 0.5 GB（Telegraf も gnmic も） | 変数 `telegraf_task_cpu`、`telegraf_task_memory`、`gnmic.tf` |
| イメージ | Telegraf は公式の telegraf 1.40.1 に設定のテンプレートと入口（`tg`）を足したもの（ECR の `<prefix>-telegraf`）。gnmic は公式の `ghcr.io/openconfig/gnmic` 0.49.0 に設定のテンプレートと入口（`gn`）を足したもの（ECR の `<prefix>-gnmic`） | `docker/images/telegraf/Dockerfile`、`ops/lab-common.sh`（Telegraf の版の正）、`docker/images/gnmic/Dockerfile`、`ops/up-common.sh` の `GNMIC_VERSION`、変数 `telegraf_image_tag`・`gnmic_image_tag` |
| NLB | 内部 NLB `<prefix>-tg`。3 つのサービスの共通の受け口（ポートとヘルスチェックは表の下） | `telegraf.tf` の `local.collector_listeners`・`local.collector_health_checks`、`aws_lb.telegraf_dialout` |
| 機器の一覧 | SSM `/<prefix>/gnmic/<lab か nautobot>/gnmi-targets`（String）。gnmic の購読先 | `gnmic.tf`、変数 `gnmi_targets_from_nautobot` |
| 機器の認証情報 | SSM `/<prefix>/gnmic/gnmi-username`・`gnmi-password`（SecureString。`ops/up.sh` が作る） | `ops/up-common.sh` の `ensure_fixed_secret`（`ops/up.sh` の手順 7 が呼ぶ） |
| スイッチ | `TELEGRAF_AZ_NUM`。`SYSLOG_STANDARD` は syslog-ng のもの。`MDT_SOURCE_CIDRS` は使わない。`SNMP_POLL` は使わない（`deploy.env` に書いてあれば `ops/up.sh` が注意を出すだけ） | `deploy.env.example` |
| ログ | `/ecs/<prefix>-telegraf`（ストリームは `dialout/…`）と `/ecs/<prefix>-gnmic`（`gnmic/…`） | `telegraf.tf`、`gnmic.tf` |
| 費用 | Telegraf と gnmic の分は、タスク 1 つ 1.2 セント/時 ×（`TELEGRAF_AZ_NUM` + gnmic の 1）+ NLB 2.43 セント/時（公表単価）。stream の収集の全体は表の下 | `ops/up.sh` の費用の目安（524〜584 行） |

- NLB の受け口: 162/udp → Telegraf の 1162、5140/udp → syslog-ng の 5140、2055/udp と 6343/udp → GoFlow2。
  ヘルスチェックは Telegraf が HTTP 8080（`outputs.health`）、syslog-ng が TCP 5140、GoFlow2 が HTTP 8081 の `/__health`。
- stream の収集の全体の費用（syslog-ng と GoFlow2 も入れて Fargate のタスク 3 + `TELEGRAF_AZ_NUM` 個と NLB）: `ops/up.sh` の `COST_CENTS` で `(12 × (3 + TELEGRAF_AZ_NUM) + 24 + 5) / 10` セント/時（単位は 0.1 セントで、`+ 5` は四捨五入）。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 機器（lab の SR Linux） | 機器 → lab の EC2 → NLB → 受ける側 | trap 162/udp。lab の EC2 が DNAT する（syslog と NetFlow / sFlow も同じ NLB に来るが、受けるのは syslog-ng と GoFlow2） |
| 機器 | gnmic → 機器 | gNMI 57400/tcp（VPC のルートで lab の EC2 へ。lab の EC2 はタスクのサブネット `/<prefix>/telegraf-source-cidr` から来たものだけ機器へ通す） |
| MSK | Telegraf → MSK | 9098/tcp、AWS-MSK-IAM。タスクロール `<prefix>-telegraf-task` |
| MSK | gnmic → MSK | 9096/tcp、SASL/SCRAM-SHA-512。資格情報は Secrets Manager の `AmazonMSK_<prefix>-gnmic`（ユーザー `gnmic`。cycle 031 で syslog-ng・GoFlow2 と分けた）を ECS の secrets で受ける |
| SSM | gnmic のタスクの起動時に読む | 実行ロール `<prefix>-gnmic-exec` の `ssm:GetParameters`（`/<prefix>/gnmic/*` だけ） |
| Nautobot | Nautobot → SSM → サービス | Job が gnmic の購読先の一覧のパラメータを書き換え、gnmic のサービスを作り直す |

## 知見

- **受ける側と取りにいく側を分けた理由は、台数を増やせるかどうかが違うから。**
  - 受ける側は機器の一覧を持たず、増やしても同じものを 2 回書かない。
  - 取りにいく側は 2 つ立てると同じ機器から 2 回取って MSK に 2 回書くので、1 つにしてある。取りにいく側の gnmic も同じで、gnmic は 1 タスク（gnmic のクラスタリングは使わない）。
  - 出典: `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf` と `gnmic.tf` の先頭のコメント。
- **DNAT の宛先はタスクではなく NLB の IP にする。**
  タスクの IP は作り直すと変わる。NLB の IP は変わらないので、SSM `/<prefix>/telegraf-address` に書いて lab の EC2 が読む。
  出典: 同上。
- **trap は NLB の 162 で受け、タスクは 1162 で待つ。**
  非 root のコンテナは 1024 未満のポートで待てない。
  出典: 同上。
- **NLB は UDP の送り元の IP を残す。**
  Spark とエージェントは送り元の IP で機器を引く。
  出典: 同上。
- **UDP は応答で生死を見られないので、ヘルスチェックは Telegraf の `outputs.health` を見る。**
  何も書いていないうちも 200 を返すので、trap が来なくても通る。
  出典: `telegraf.tf` の `aws_lb_target_group` のコメント。
- **機器の一覧と認証情報は、タスクを起こすときに読む。**
  SSM の値を変えたら、gnmic のサービスを作り直さないと効かない。Nautobot の Job はパラメータを書き換えたあと `ecs:UpdateService` で作り直す。
  出典: `gnmic.tf` の先頭のコメント、`IaC/terraform/aws-managed/pipeline/nautobot/access.tf`。
- **Telegraf の MSK の IAM 認証には profile の指定が要る。**
  鍵の無い `[default]`（region だけ）を `/tmp/aws_config` に置き、SDK が ECS のタスクロールに落ちるようにしてある。
  出典: [data-stores.md](../../data-stores.md) の「15. ブローカーの渡し方と msk-bootstrap」。
- **syslog は syslog-ng が受ける。**
  Telegraf の `inputs.syslog` は外した。形式の `SYSLOG_STANDARD`（既定 RFC3164 は本番の Cisco、lab の SR Linux は RFC5424）も syslog-ng の設定になった。
  出典: FAQ「syslog-ng が受ける syslog の形式（RFC 3164 / RFC 5424）は、どこで切り替える？」。
- **IF の状態とカウンターは gnmic の gNMI で取る。**
  SNMP のポーリングはやめ、`SNMP_POLL` は使わない。gnmic は event の形のまま書き、Spark が Telegraf のころと同じ系列（`snmp_interface_oper_up` など）に読み替える。
  出典: [collection.md](../../collection.md) の「gnmic の event と読み替え」。
- **切り分けは `gn get`。**
  - ECS Exec で gnmic のタスクに入って打つ（stream の output `gnmic_exec_command`）。
  - Kafka と同じ event の形で標準出力に出すだけで Kafka には書かないので、機器との疎通と Kafka との疎通を分けて見られる。
  - Telegraf の `tg test` / `tg gnmi` は、打つと案内を出して終わる。
  - 出典: [pipeline.md](../../pipeline.md) の「gnmic と Telegraf に入る」。
- **gnmic は届かない機器に 10 秒ごとに繋ぎ直し、プロセスは落ちない。**
  2026-10-09 に手元の docker で 80 秒見た（ECS では未確認）。`SKIP_LAB=1` で lab が無くてもタスクは動き続ける。
  出典: [gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）の設計](../../cycles/013-gnmic-drop-dialin/design.md) の 4.。
- **機器から Telegraf までは「多くて 1 回」。**
  trap は UDP で、届かなければそれきり。Telegraf が止まっているあいだの分も失う（syslog-ng の syslog と GoFlow2 の NetFlow / sFlow も同じ）。
  出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| gnmic は 1 タスク・1 AZ | 増やすと二重に書くので増やせない。止まっているあいだのカウンターは抜ける（状態は繋ぎ直したときに今の値を全部送り直す） |
| gnmic の Kafka の ACL | Spark のジョブが起動時に `User:gnmic` へ `gnmi` と `metrics` へ書く ACL を入れる（cycle 012 の `ensure_acls`。ユーザーは cycle 031 でコレクターごとに分けた）。AWS で見たことは表の下 |
| gnmic を ECS で動かした記録 | 2026-10-09 に AWS で動かした。desired 1 / running 1 で、`metrics` に IF の統計と `system` の event が入った。`gnmi` のトピックはできなかった（表の下）。`docs/verification/20261009-aws-managed.md` の「B.」 |
| MDT の受け口 | 外した。本番の Cisco の MDT を受けるなら戻す（[collection.md](../../collection.md)） |

- gnmic の Kafka の ACL（AWS）:
  - マネージドの MSK は IAM と SCRAM の併用なので ACL が要る想定だったが、2026-10-09 の AWS では ACL を入れる前も `metrics` に書けた（`allow.everyone.if.no.acl.found` が効いた。`docs/verification/20261009-aws-managed.md` の「A.」「B.」）。
  - ACL を入れたあとは未確認（OSS 版は認証なしなので当たらない）。
- gnmic を ECS で動かした記録の `gnmi` のトピック: on-change の 3 つの購読から 1 件も書かれていない（gnmic のログに ERROR は 0。原因は確かめていない）。

## 関連

- [msk.md](msk.md)、[lab-ec2.md](lab-ec2.md)、[nautobot.md](nautobot.md)、[ssm-parameter-store.md](ssm-parameter-store.md)
- [pipeline.md](../../pipeline.md): 「gnmic と Telegraf に入る」
- [collection.md](../../collection.md): 何を集めるか、MDT の方針

## 経緯

- 2026-09-28 まで: lab のルートの EC2 で動いていた（出典: `telegraf.tf` の先頭のコメント）。
- 2026-10-04: 受ける側と取りにいく側を分けた。
- 2026-10-08（012）: syslog と NetFlow / sFlow を別のサービス（syslog-ng と GoFlow2）で受けるようにした。
  Telegraf の `inputs.syslog` と MDT の受け口を外し、`SYSLOG_STANDARD` は syslog-ng の設定になった。
- 2026-10-09（013）: 機器の gNMI を gnmic で取りにいくようにし、`<prefix>-telegraf-dialin`（Telegraf の取りにいく側）と SNMP のポーリングをやめた。
  `SNMP_POLL` も使わなくなり、Telegraf の `tg test` / `tg gnmi` もやめた。
