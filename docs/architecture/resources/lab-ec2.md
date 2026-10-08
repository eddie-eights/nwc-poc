# lab の EC2（containerlab）

← [リソースごとの知見](README.md)

## ひとことで

監視される側のネットワークを 1 台の EC2 の中に作る検証用の lab。containerlab が SR Linux 6 台と TRex 1 台をコンテナで立てる。
Web やエージェントとはつながっていない。使うのは SNMP・gNMI・trap・syslog の発生源としてと、修復のコマンドを打つ先としてだけ。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| EC2 | Amazon Linux 2023 x86_64、`m6i.xlarge`（`m6i.xlarge` / `m6i.2xlarge` / `c6i.2xlarge` / `t3.xlarge` / `t3.2xlarge` から選ぶ。TRex のイメージが amd64 だけのため、2026-10-08 に `t4g` から替えた）、EBS 24 GB。1 台だけ（サブネット a）で、AZ を選ぶキーは無い | `IaC/terraform/aws-managed/pipeline/lab/instance.tf`、変数 `instance_type`、`volume_size` |
| 版 | containerlab 0.79.0、SR Linux 26.7.2、multitool v0.10.0、TRex 2.41。正は `ops/lab-common.sh` | `ops/lab-common.sh`、`IaC/terraform/aws-managed/pipeline/lab/variables.tf`（同じ値） |
| イメージ | ECR の `<prefix>-lab-srlinux`、`<prefix>-lab-multitool`、`<prefix>-lab-trex`（公開のイメージの amd64 の写し） | `IaC/terraform/aws-managed/base/ecr/main.tf`、`ops/lab-common.sh` |
| 材料 | containerlab の rpm とトポロジ。S3 の `lab/`（土台のバケット）に `ops/up.sh` の手順 5-1 が置く | `ops/up.sh`、`app/containerlab/setup.sh` |
| 起動 | systemd の `<prefix>-lab`。起動のたびに S3 の `lab/` を置き直して流す | `instance.tf` のコメント、`app/containerlab/setup.sh` |
| 管理ネットワーク | `203.0.113.0/24`（EC2 の中の docker network。VPC からは見えない） | `IaC/terraform/aws-managed/pipeline/lab/locals.tf` の `mgmt_cidr`、`app/containerlab/splab.clab.yml.in` |
| 入り方 | SSM Session Manager（管理者用のシェルセッション）。中では `sudo lab <コマンド>` | [pipeline.md](../../pipeline.md) の「lab に入る」 |
| スイッチ | `PIPELINE=1`。`SKIP_LAB=1` で外す（`WORKFLOW=1` では外せない） | `ops/up.sh` |
| 費用 | 25 セント/時（`m6i.xlarge`）。止めている間は EBS の保管料だけ | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`）、[pipeline.md](../../pipeline.md) の「止める・起動する」 |

lab の中身:

| 役 | 台数 | 中身 |
|---|---|---|
| スイッチ | 6 台（s-leaf 2、Spine 2、a-leaf 2） | Nokia SR Linux（`ixr-d2l`）。fabric は IS-IS、その上に iBGP EVPN-VXLAN。SNMP の trap と syslog を出す。設定は `app/containerlab/srlinux/<機器>.cli` |
| TRex | 1 台（`dc1-trex-01`） | `trexcisco/trex`（amd64 だけ）。`eth1`〜`eth4` を各 leaf の `ethernet-1/3` へ 1 本ずつ。後段の負荷試験に使う。トポロジを上げても TRex 本体は起きない（`sudo lab trex start`） |
| 回線 | 12 本 | s-leaf・a-leaf と Spine のフルメッシュ（fabric 8 本）、TRex と各 leaf（l2 4 本。LAG も EVPN マルチホーミングも無い） |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → EC2 | SSM Session Manager（`ssm`、`ssmmessages` のエンドポイント） |
| Telegraf の取りにいく側 | タスク → 機器 | SNMP 161/udp と gNMI 57400/tcp。VPC のルートで管理ネットワーク宛てを lab の EC2 に向ける。認証情報は SSM の SecureString |
| Telegraf の受ける側（内部 NLB） | 機器 → EC2 → NLB | trap 162/udp、syslog 5140/udp。機器は `203.0.113.1` へ送り、`lab forward` が NLB へ DNAT する |
| SSM のパラメータ | EC2 → `/<prefix>/telegraf-address`、`/<prefix>/telegraf-source-cidr` | `ssm` のエンドポイント、インスタンスロール（`lab forward` が読む） |
| ECR と S3 | EC2 → イメージ、`lab/` | `ecr.api`、`ecr.dkr` のエンドポイントと S3 の gateway エンドポイント |
| worker（Temporal） | worker → SSM → EC2 | Run Command で `sudo lab heal-main` などを打つ |

## 知見

- **1 台だけで、2 台にはできない。**
  containerlab の 1 台の中に全部の機器があり、管理ネットワークへの VPC のルートもこの 1 台の ENI を向く。2 台にすると別々の lab になる。コードから確かめた理由で、AWS では試していない（2026-10-04）。
  出典: `IaC/terraform/aws-managed/pipeline/lab/instance.tf` のコメント。
- **メモリは SR Linux 6 台と TRex で 11〜13 GB の見込み（推定。EC2 では測っていない）。**
  SR Linux 6 台で 10 GB ほど使う。`m6i.xlarge` は 16 GB。足りないと `containerlab deploy` が readiness で止まるので、`m6i.2xlarge`（32 GB）に上げる。
  出典: [pipeline.md](../../pipeline.md) の「動かないとき」、[app/containerlab/trex/README.md](../../../app/containerlab/trex/README.md)。
- **2026-10-08 より前に ECR に置いた lab のイメージは arm64。**
  ECR のタグは上流の版そのままなので、arm64 だった 2026-10-08 より前のタグ（`lab-srlinux:26.7.2` / `lab-multitool:v0.10.0`）が `KEEP_ECR=1` で残っていると、`ops/up.sh` は写しを飛ばし、x86_64 の EC2 が arm64 のイメージを引いて起きない。1 回だけ `KEEP_ECR=0 ops/down.sh` で ECR ごと消すか、`aws ecr batch-delete-image --repository-name <prefix>-lab-srlinux --image-ids imageTag=26.7.2`（multitool は `<prefix>-lab-multitool` と `v0.10.0`）でタグを消してから `ops/up.sh` する。デバッグ用の EC2 は `ops/lab-debug.sh down` でリポジトリごと消える。
  出典: [ecr.md](ecr.md)。
- **版の正は `ops/lab-common.sh`。**
  terraform の変数とデバッグ用のスタックの既定値も同じ値にしてある。
  出典: `ops/lab-common.sh`、[pipeline.md](../../pipeline.md) の「デバッグ用の EC2（lab + Telegraf を 1 台）」。
- **stream がある回は、EC2 の `source_dest_check` を切ってある。**
  送り元や宛先が管理ネットワーク（`203.0.113.x`）のパケットを、Telegraf のタスクや NLB とのあいだで通すため。変数 `forward_to_telegraf`（既定 false）を、`ops/up.sh` が stream を作るか残すときに true にする。
  出典: `instance.tf` と `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf` のコメント。
- **trap と syslog は、送り元の IP を機器の管理 IP のまま届ける。**
  Docker の MASQUERADE にかけず、NLB も送り元を残す。Spark とエージェントが送り元の IP で機器を引くため。
  出典: `telegraf.tf` のコメント。
- **ポーリングを通す穴は、タスクのサブネットの CIDR で開ける。**
  タスクの IP は作り直すたびに変わるため。CIDR は stream が SSM の `/<prefix>/telegraf-source-cidr` に書く。
  出典: `telegraf.tf` のコメント。
- **stream を後から作っても、lab の EC2 は作り直さない。**
  `ops/up.sh` の手順 7-2b が `lab forward` を打ち直す。
  出典: `telegraf.tf` のコメント。
- **コンテナからは IMDS に届かない。**
  hop limit を 1 にしてあり、docker bridge を越えない。
  出典: `instance.tf` のコメント。
- **`app/containerlab/splab.clab.yml.in` と `app/containerlab/srlinux/*.cli` は手で直さない。**
  `app/containerlab/gen_lab.py` の出力で、`tests/test_sync.py` が出力と同じことを確かめる。台数を変えたら `app/agentcore/data/` の静的データも作り直す。
  出典: [pipeline.md](../../pipeline.md) の「lab を変える」。
- **`app/containerlab/` を変えたら、`ops/up.sh` のあと lab のサービスを再起動する。**
  `sudo systemctl restart <prefix>-lab`。機器や回線を変えたら、そのあと `ops/sync-graph.sh --replace`。
  出典: [pipeline.md](../../pipeline.md) の「変えたとき」。
- **`sudo lab failover` を打つと、trap は 5 秒以内に Kafka に届く。**
  2026-09-27 に EC2 で確認。
  出典: [pipeline.md](../../pipeline.md) の「lab に入る」。
- **SR Linux の SNMP の `ifOperStatus` は、実際の状態より 15〜20 秒遅れる。**
  2026-09-27 の実測。
  出典: [pipeline.md](../../pipeline.md) の「lab に入る」。
- **SR Linux の ifTable は、未使用の物理ポートも全部出す。**
  `ifAdminStatus` が down の行。IF の鍵は `ifName`。Grafana のルールは admin down の行、サブインタフェース、ループバック、管理ポートを見ない。
  出典: [pipeline.md](../../pipeline.md) の「lab に入る」。
- **lab の機器は syslog を RFC 5424 で送る。Telegraf の既定は本番に合わせた RFC3164。**
  lab のログの項目まで見るなら `SYSLOG_STANDARD` を合わせる。
  出典: [pipeline.md](../../pipeline.md) の「lab に入る」。
- **いまは EVPN-VXLAN。SR-MPLS はライセンス待ち。**
  SR Linux のコンテナは SR-MPLS に `ixr6e` / `ixr10e` とライセンスが要る。届いたら `gen_lab.py` を替える。トポロジと Neptune の層は変わらない。
  出典: [pipeline.md](../../pipeline.md) の「lab を変える」。
- **TRex は置いてあるだけで、負荷はまだ撃っていない。**
  この lab で起動するかも確かめていない（2026-10-08）。撃つ手順と確かめていないことは `app/containerlab/trex/README.md`。
  出典: [pipeline.md](../../pipeline.md) の「lab に入る」。
- **デバッグ用の EC2 は別物。**
  `ops/lab-debug.sh` が CloudFormation のスタック `<prefix>-lab-debug` で作る（lab + Telegraf を 1 台、自分の VPC）。`ops/up.sh` / `ops/down.sh` とは別で、Nautobot を使わない。中身の支度は lab の EC2 と同じ `app/containerlab/setup.sh`。
  出典: [pipeline.md](../../pipeline.md) の「デバッグ用の EC2（lab + Telegraf を 1 台）」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 2 台構成 | できない（上の知見。AWS では未確認） |
| `SKIP_LAB=1` のとき | Telegraf の取りにいく側は lab の定義の機器を探しに行き、届かないのでエラーを出して繋ぎ直し続ける（タスクは落ちない）。trap と syslog は来ない |
| 機器の認証情報 | containerlab の既定値を最初の値にする（SSM の SecureString。[ssm-parameter-store.md](ssm-parameter-store.md)） |
| 処置の種類 | worker が打つのは `heal-main`（回線を上げる）と `check`（見るだけ）だけ |

## 関連

- [telegraf.md](telegraf.md)、[ecr.md](ecr.md)、[ssm-parameter-store.md](ssm-parameter-store.md)、[temporal.md](temporal.md)、[nautobot.md](nautobot.md)
- [pipeline.md](../../pipeline.md): 「lab に入る」「デバッグ用の EC2（lab + Telegraf を 1 台）」「lab を変える」「変えたとき」
- [pipeline.md](../pipeline.md): 構成（pipeline）
- [collection.md](../../collection.md): 機器から集めるデータ
