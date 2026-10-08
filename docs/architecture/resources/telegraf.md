# Telegraf（ECS）

← [リソースごとの知見](README.md)

## ひとことで

機器のデータを集めて MSK に書く収集役。Fargate のタスクで動く。
同じイメージを 2 つのサービスに分けてある: 機器から送られてくるものを受ける側（dialout）と、こちらから取りにいく側（dialin）。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| クラスター | `<prefix>-telegraf` | `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf` |
| 受ける側 | サービス `<prefix>-telegraf-dialout`。trap・syslog・MDT を内部 NLB の後ろで受ける。タスク数 = `TELEGRAF_AZ_NUM`（既定 1、1〜3） | `telegraf.tf` の `aws_ecs_service.telegraf_dialout` |
| 取りにいく側 | サービス `<prefix>-telegraf-dialin`。gNMI の購読と SNMP のポーリング。タスクはいつも 1 つ。NLB には付けない | `telegraf.tf` の `aws_ecs_service.telegraf_dialin` |
| タスクの大きさ | Fargate ARM、0.25 vCPU / 0.5 GB | 変数 `telegraf_task_cpu`、`telegraf_task_memory` |
| イメージ | 公式の telegraf 1.40.0 に設定のテンプレートと入口（`tg`）を足したもの。ECR の `<prefix>-telegraf` | `docker/images/telegraf/Dockerfile`、`ops/lab-common.sh`（版の正）、変数 `telegraf_image_tag` |
| NLB | 内部 NLB `<prefix>-tg`。162/udp → タスクの 1162、5140/udp → 5140、57000/tcp → 57000。ヘルスチェックは HTTP 8080（`outputs.health`） | `telegraf.tf` の `local.telegraf_ports`、`aws_lb.telegraf_dialout` |
| 機器の一覧 | SSM `/<prefix>/telegraf-dialin/<lab か nautobot>/gnmi-targets`・`snmp-agents`（String） | `telegraf.tf`、変数 `dialin_targets_from_nautobot` |
| 機器の認証情報 | SSM `/<prefix>/telegraf-dialin/gnmi-username`・`gnmi-password`・`snmp-community`（SecureString。`ops/up.sh` が作る） | `ops/up-common.sh` の `ensure_fixed_secret`（`ops/up.sh` の手順 7 が呼ぶ） |
| スイッチ | `SNMP_POLL`（既定 1。0 でポーリングをやめる）、`SYSLOG_STANDARD`（既定 RFC3164）、`MDT_SOURCE_CIDRS`（既定は空）、`TELEGRAF_AZ_NUM` | `deploy.env.example`、変数 `snmp_poll`、`syslog_standard` |
| ログ | `/ecs/<prefix>-telegraf`（ストリームは `dialout/…` と `dialin/…`） | `telegraf.tf` |
| 費用 | タスク 1 つ 1.2 セント/時 ×（1 + `TELEGRAF_AZ_NUM`）+ NLB 2.43 セント/時（公表単価） | `ops/up.sh` の費用の目安（526〜583 行） |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 機器（lab の SR Linux） | 機器 → lab の EC2 → NLB → 受ける側 | trap 162/udp、syslog 5140/udp。lab の EC2 が DNAT する |
| 本番の Cisco | 機器 → NLB → 受ける側 | MDT の dial-out 57000/tcp（`MDT_SOURCE_CIDRS` に書いた CIDR だけ） |
| 機器 | 取りにいく側 → 機器 | SNMP 161/udp、gNMI 57400/tcp（VPC のルートで lab の EC2 へ） |
| MSK | Telegraf → MSK | 9098/tcp、AWS-MSK-IAM。タスクロール `<prefix>-telegraf-task` |
| SSM | タスクの起動時に読む | 実行ロール `<prefix>-telegraf-exec` の `ssm:GetParameters`（`/<prefix>/telegraf-dialin/*` だけ） |
| Nautobot | Nautobot → SSM → サービス | Job が機器の一覧のパラメータを書き換え、取りにいく側のサービスを作り直す |

## 知見

- **受ける側と取りにいく側を分けた理由は、台数を増やせるかどうかが違うから（2026-10-04）。**
  受ける側は機器の一覧を持たず、増やしても同じものを 2 回書かない。取りにいく側は 2 つ立てると同じ機器から 2 回取って MSK に 2 回書くので、1 つにしてある。
  出典: `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf` の先頭のコメント。
- **DNAT の宛先はタスクではなく NLB の IP にする。**
  タスクの IP は作り直すと変わる。NLB の IP は変わらないので、SSM `/<prefix>/telegraf-address` に書いて lab の EC2 が読む。
  出典: 同上。
- **trap は NLB の 162 で受け、タスクは 1162 で待つ。**
  非 root のコンテナは 1024 未満のポートで待てない。
  出典: 同上。
- **NLB は UDP の送り元の IP を残す。**
  Spark とエージェントは送り元の IP で機器を引く。MDT（TCP）は残さない（機器が MDT の中で node_id を名乗るので要らない）。
  出典: 同上。
- **UDP は応答で生死を見られないので、ヘルスチェックは Telegraf の `outputs.health` を見る。**
  何も書いていないうちは 200 を返すので、ポーリングを止めても通る。
  出典: `telegraf.tf` の `aws_lb_target_group` のコメント、FAQ「SNMP はポーリングと trap のどちらで集めている？ ポーリングは止められる？」。
- **機器の一覧と認証情報は、タスクを起こすときに読む。**
  SSM の値を変えたら、サービスを作り直さないと効かない。Nautobot の Job はパラメータを書き換えたあと `ecs:UpdateService` で作り直す。
  出典: `telegraf.tf` の先頭のコメント、`IaC/terraform/aws-managed/pipeline/nautobot/access.tf`。
- **MSK の IAM 認証には profile の指定が要る。**
  鍵の無い `[default]`（region だけ）を `/tmp/aws_config` に置き、SDK が ECS のタスクロールに落ちるようにしてある。
  出典: [data-stores.md](../../data-stores.md) の「15. ブローカーの渡し方と msk-bootstrap」。
- **syslog の形式は機器に合わせる。**
  既定は RFC3164（本番の Cisco）。lab の SR Linux は RFC5424 なので、lab のログを見るなら `SYSLOG_STANDARD=RFC5424`。合っていないとホスト名や本文が崩れる。
  出典: [troubleshooting.md](../../troubleshooting.md) の「パイプラインと WORKFLOW」、FAQ「Telegraf が受ける syslog の形式（RFC 3164 / RFC 5424）は、どこで切り替える？」。
- **`SNMP_POLL=0` にすると `metrics` トピックが空になる。**
  Grafana のダッシュボード「netops / SNMP metrics」、エージェントの `query_metrics`、Grafana のルール `link_down` が動かなくなる。IF の up / down は trap から Splunk だけが知らせる。
  出典: FAQ「SNMP はポーリングと trap のどちらで集めている？ ポーリングは止められる？」、[troubleshooting.md](../../troubleshooting.md)。
- **切り分けは `tg gnmi` と `tg test`。**
  ECS Exec で取りにいく側に入って打つ。標準出力に出すだけで MSK には送らないので、機器との疎通と MSK との疎通を分けて見られる。
  出典: [pipeline.md](../../pipeline.md) の「Telegraf に入る」、[data-stores.md](../../data-stores.md) の「15.」。
- **機器から Telegraf までは「多くて 1 回」。**
  trap と syslog は UDP で、届かなければそれきり。Telegraf が止まっているあいだの分も失う。
  出典: [data-stores.md](../../data-stores.md) の「届け方の保証」。
- **2026-09-28 までは lab のルートの EC2 で動いていた。**
  出典: `telegraf.tf` の先頭のコメント。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 取りにいく側は 1 タスク・1 AZ | 増やすと二重に書くので増やせない。止まっているあいだの gNMI とポーリングは抜ける |
| MDT の共通の形への変換 | まだ無い（[collection.md](../../collection.md) の「未決定事項」） |
| lab から MDT | 来ない（SR Linux は MDT の dial-out を送れない） |

## 関連

- [msk.md](msk.md)、[lab-ec2.md](lab-ec2.md)、[nautobot.md](nautobot.md)、[ssm-parameter-store.md](ssm-parameter-store.md)
- [pipeline.md](../../pipeline.md): 「Telegraf に入る」
- [collection.md](../../collection.md): 何を集めるか、MDT の方針
