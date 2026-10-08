# lab を IS-IS で組み直し、各 leaf に TRex をつなぎ、lab の EC2 を x86 にする（011）— build

## Round 1

実装モデル: Opus 5.5 / effort: xhigh（既定は high。自分のセッションの effort は set_session_effort で変えられないので xhigh のまま）

起点は docs/cycle-006-design の 29dd7ca。ブランチは feat/lab-isis-trex-x86。AWS には立てていない。

### commit と変更ファイル

| commit | 中身 | 変更ファイル |
|---|---|---|
| 0cd4db5 | 実装ステップ 1〜2。gen_lab / lab_topology / 静的データ | `app/containerlab/gen_lab.py`、`lab_topology.py`、`splab.clab.yml.in`、`srlinux/*.cli`（leafsw → s-leaf、leaf → a-leaf の 4 本を改名、spine 2 本）、`app/agentcore/data/devices.yaml` / `topology.json` / `layers.json` |
| 8642a44 | 実装ステップ 3。lab.sh / setup.sh / trex/ | `app/containerlab/lab.sh`、`setup.sh`、新規 `trex/`（`README.md`、`kafka_load.sh`、`trex_cfg.yaml.in`、`stl/udp_trap.py`、`stl/udp_syslog.py`）、`docker/compose/lab.sh`、`docker/compose/.env.example` |
| 6d3e554 | 実装ステップ 4。x86 と ECR | `IaC/terraform/aws-managed/pipeline/lab/*`（instance.tf / locals.tf / outputs.tf / variables.tf / tfvars.example / tftpl）、`base/ecr/*`、`pipeline/analytics/emr.tf`（コメント）、`IaC/cloudformation/lab-debug.yaml`、`ops/lab-common.sh`、`ops/lab-debug.sh`、`ops/up.sh`、`oss/ops/up.sh`、`app/containerlab/setup.sh`、`docker/images/telegraf/Dockerfile`（コメント）、`.env.example`、`deploy.env.example`、`README.md`、`docker/compose/README.md`、docs 11 本、`tests/test_lab_debug.py`、`tests/test_analytics.py` |
| e8eed52 | 実装ステップ 5。名前の参照と docs と tests | app 10 本（evidence / proposals / topology / lab_topology / dashboard の app と topology_view / nb_map / snmp_sinks / netops_sns / temporal の rules）、`tools/tools.json`、`docker/compose/README.md` / `compose.yaml`、`ops/up.sh`（コメントを 1 行に戻す）、docs 15 本、tests 13 本（`check_splunk_image.py` は名前だけ） |
| 4cb51a6 | セルフレビューの直し（指摘 1・2 と足りないテスト） | `ops/lab-common.sh`（`LAB_ARCH` と `*_ECR_TAG`）、`ops/up.sh`、`oss/ops/up.sh`、`ops/lab-debug.sh`、`pipeline/lab/variables.tf` / `tfvars.example`、`IaC/cloudformation/lab-debug.yaml`、`app/agentcore/topology.py` / `app/temporal/rules.py`（`END_ROLES`）、docs 5 本（ecr / lab-ec2 / data-stores / deploy / workflow）、tests 5 本（app / lab_debug / nautobot / sync / workflow） |
| 9997082 | セルフレビューの直し（指摘 3 の残り。LAG / bond / ES のテスト） | `tests/test_sync.py` |

### 設計からずれた点

- **検証 1:** 出力先は `/tmp/x` でなく scratchpad の `genlab_x`（このセッションの決まりで `/tmp` を使わない）。比べ方は同じ
- **検証 3:** design のコマンド `grep -c :57400` は 1 になる。`--gnmi-targets` はカンマ区切りの 1 行を出すので、`grep -c` は行数の 1 を数える（起点 29dd7ca でも 1）。期待値の 6 は個数なので `grep -o :57400 | wc -l` で取り直して 6
- **検証 6:** 空にならない。追跡しているファイルで 65 行（下のログ）。全部が「日付の付いた過去の記録」か「lab 以外の t4g」で、直すべき残りは無い
  - 古い lab の名前 27 行: `docs/verification/20261008-*.md`（20 行。実機確認の記録）、`docs/alert-comparison.md` の 2026-10-08 の実測表（4 行。:143 に「組み直す前の名前」と注記を足した）、2026-10-05 のチャットの確認の記録（`agentcore-bedrock.md:111` / `neptune-analytics.md:95` / `data-stores.md:211`）
  - `t4g` 38 行: Web の EC2（`t4g.small`）、RDS（`db.t4g.micro`）、Neptune Database の履歴（`db.t4g.medium`）、費用の履歴。lab の EC2 に触れる 6 行は全部「2026-10-08 に t4g から替えた」の履歴か、2026-10-08 の OSS の実機確認の記録
  - design の `grep -rn` は gitignore の `.terraform/` のプロバイダのバイナリにも当たる（「Binary file … matches」3 行）。3 本目の `grep -rln` はそのバイナリを読み続けて終わらず、止めて `git grep` で取り直した（v6b）
- **検証 9:** `bash -n` は 4 本とも通るが、shellcheck は `kafka_load.sh` だけ通り、`lab.sh` / `setup.sh` / `lab-common.sh` は指摘が出る。指摘は起点 29dd7ca と同じもの（本文で比べて増減なし。下の sc_compare）。design の「通る」は起点でも成り立っていなかった
- **TRex のアドレス:** design に無いので決めた。ポート n は `10.100.0.(11+n)/24`、default_gw は組の相手（n^1）。`low_end: true`（1 コア）。`trex_cfg.yaml.in` の「af_packet は 2.37 から」は TRex の文書から読んだだけで、2.41 での起動は未確認（未確定事項 2）
- **STL の宛先ポート:** design の README の記述は「NLB の 1162/udp」だが、NLB の listener は 162 でタスクの 1162 へ渡す（stream の nlb）。`udp_trap.py` の既定は `dport=162`、syslog は `5140`
- **multitool:** design どおり残した（`lab_repositories` に trex を足すだけ、デバッグ用のリポジトリ 4 つ）。いまの lab で linux kind の既定の multitool を使うノードは無い
- **dashboard の段:** `topology_view.py` に `ROW_OF` を足し、s-leaf を a-leaf と同じ段に置いた（別の段だと spine から s-leaf への線が a-leaf の箱の下を通る）
- **Telegraf の Dockerfile:** コメントを 1 行足しただけだが、Telegraf のタグは中身のハッシュなので変わり、次の `up.sh` が stream の Telegraf も作り直す
- **lab の名前が残る所:** `telegraf.conf.in` / `lab_gnmi.star` の lag の記述、`lab_topology.py` の lag の分岐（design の「残す」どおり）、`tests/test_sync.py:327` の合成の `dc1-leaf-{i}`、`tests/test_oss.py` の合成の role `"leaf"`（並び順を見ない検査）は変えていない。golden の `neptune_cypher.json` は変化なし
- **e8eed52 の commit メッセージ:** 最初に「test_graph / test_oss の合成データの role を a-leaf に」と書いたが、test_oss は台数だけだったので amend で直した
- **`tests/check_splunk_image.py` を誤って 1 回起動した:** check.sh に入っていない検査（Splunk を docker でビルドして起こす）。結果を見る前に止め、コンテナ `nwc-splunk-check-46173` と匿名 volume 2 つを `docker rm -f -v` で消した（残りのコンテナ 0、volume 0 を確認）。イメージ `nwc-splunk-check:local` はこの起動より前からあったので残した

### 期待値を変えたテストと、元の期待値が誤りだった理由

元の期待値は 011 より前の lab（VM 2 台、LACP、ES）では正しかった。lab の形が変わったので合わなくなった。

- 台数・本数: 8 台 → 7 台。リンクは 12 本のまま、種別が fabric 8 + lag 4 → fabric 8 + l2 4。layers 62 頂点 / 84 辺 → 58 / 78（ES 4 頂点と、ES に付く 6 辺が消えた。起点の数は scratchpad に取り出した 29dd7ca の lab_topology.py で実測）
- `test_app` の what_if device_down（`dc1-a-leaf-01`）は warn → ok。前は host が bond の 2 本で両 leaf につながり、leaf を 1 台落とすと host が 1 本になって冗長切れだった。いまは TRex が 4 本、spine も 4 本で、1 台落としても 2 本以上残る
- `test_app` の what_if link_down（`dc1-a-leaf-01#ethernet-1/1`、全部 UP）は ok → warn（`redundancy_lost` = a-leaf-01）、「e1-1 が DOWN のまま e1-2 を落とす」は warn → danger（`newly_isolated` = a-leaf-01）。e8eed52 では TRex への回線を通り道と冗長の本数に数えていたが、TRex はポートの間で転送しないので誤り（セルフレビューの指摘 2、4cb51a6 で `END_ROLES` を足して直した）
- `test_graph` の合成データの role `"leaf"` → `"a-leaf"`（並び順を見る検査で、`ROLE_ORDER` に無い役割は unknown の後ろへ行く）。合成の lag の名前 `bond0` → `lag9`（中身は同じ）
- `test_alerts` の lab.sh の使い方は 3〜7 行目（trex の行が 7 行目に増えた）。trap-test は TREX の netns から、送り元 203.0.113.101
- `test_local_compose` は `TREX_IMAGE` を含む 4 つを sudo env で渡し、.env に `TREX_IMAGE` が無ければ止まることを見る（新しい検査）
- `test_analytics` の lab の費用 17 → 25 セント、`test_lab_debug` の AllowedValues の正規表現を t4g から x86 に、リポジトリ 3 → 4 つ

### 検証方法 1〜10

1〜6・8〜10 のログは、4cb51a6 のあとに同じスクリプト（verify.sh / v6b.sh / verify2.sh / sc_compare.sh）で取り直したもの。e8eed52 で取ったものとの違いは `test_lab_debug.py` の本数（85 → 90）と `ops/lab-common.sh` の shellcheck の行番号（`*_ECR_TAG` の 4 行が増えて 19 / 23 / 24 / 25 → 27 / 31 / 32 / 33。指摘の本文は同じ）だけ。9997082 は `tests/test_sync.py` だけで、1〜6・8〜10 はこのファイルを読まない。

#### 1〜5（verify.sh）

```text
### 1
$ /opt/homebrew/bin/uv run python app/containerlab/gen_lab.py --leaves 2 --spines 2 --out <scratchpad>/genlab_x >/dev/null && diff -r <scratchpad>/genlab_x app/containerlab | grep -v '^Only in app/containerlab'
(rc=1)

$ diff -r <scratchpad>/genlab_x app/containerlab | grep -c '^Only in <scratchpad>/genlab_x'
0
(rc=1)

### 2
$ python3 app/containerlab/lab_topology.py app/containerlab | python3 -c 'import json,sys,collections; d=json.load(sys.stdin); print(len(d["devices"]), len(d["links"]), sorted(x["role"] for x in d["devices"])); print(dict(collections.Counter(l["kind"] for l in d["links"])))'
7 12 ['a-leaf', 'a-leaf', 's-leaf', 's-leaf', 'spine', 'spine', 'trex']
{'fabric': 8, 'l2': 4}
(rc=0)

### 3
$ python3 app/containerlab/lab_topology.py app/containerlab --gnmi-targets | grep -c :57400
1
(rc=0)

### 4
$ python3 app/containerlab/lab_topology.py app/containerlab --layers | python3 -c 'import json,sys,collections; d=json.load(sys.stdin); c=collections.Counter(v["label"] for v in d["vertices"]); print({k: c.get(k, 0) for k in ("ip_interface","isis_adjacency","bgp_session","evpn_instance","ethernet_segment")}, "vertices", len(d["vertices"]), "edges", len(d["edges"]))'
{'ip_interface': 22, 'isis_adjacency': 16, 'bgp_session': 16, 'evpn_instance': 4, 'ethernet_segment': 0} vertices 58 edges 78
(rc=0)

$ python3 app/containerlab/lab_topology.py app/containerlab --layers | diff - app/agentcore/data/layers.json && echo layers.json と同じ
layers.json と同じ
(rc=0)

### 5
$ grep -cE '^ *kind: (nokia_srlinux|linux)$' app/containerlab/splab.clab.yml.in
7
(rc=0)

$ grep -c bond0 app/containerlab/splab.clab.yml.in
0
(rc=1)

$ grep -l 'ethernet-segment\|lag' app/containerlab/srlinux/*.cli
(rc=1)

```

期待との突き合わせ:

- 1: 差分の行が無い（1 本目の rc=1 は grep -v が 1 行も出さなかったこと）、`genlab_x` にだけあるファイル 0。一致
- 2: `7 12 ['a-leaf', 'a-leaf', 's-leaf', 's-leaf', 'spine', 'spine', 'trex']`、fabric 8 + l2 4。一致
- 3: 個数は 6（下の 3 補足）。`grep -c` の 1 は上の「ずれた点」
- 4: ip_interface 22 / isis_adjacency 16 / bgp_session 16 / evpn_instance 4 / ethernet_segment 0、58 頂点 / 78 辺、`layers.json` と同じ。一致（`test_sync.py` も 58 / 78）
- 5: 7、0、空。一致

#### 6（design のコマンドと、追跡しているファイルだけで取り直したもの）

```text
### 6
$ grep -rn 'leafsw\|dc1-leaf-0\|dc1-host\|wan-upstream\|bond0\|t4g\|linux_arm64.rpm' app ops oss IaC docs tests docker README.md --include='*' | grep -v 'docs/cycles/' | cut -c1-160
ops/up.sh:383:#   しかも使えるクラスに既定の db.t4g.micro が無い（Amazon RDS User Guide「Multi-AZ DB cluster deployments for Amazon RDS」、
ops/up.sh:531:# 土台 = 2（Web の EC2 の t4g.small 2.2。NAT Gateway は 2026-09-28 から作らない）、
ops/up.sh:539:# lab = 25（EC2 の m6i.xlarge 24.8。2026-10-08 に Price List API で確認。TRex が amd64 だけなので x86_64。2026-10-08 までの 17 �
ops/up.sh:540:#   2026-10-04 までの Neptune Database の db.t4g.medium は 14 だった。レプリカも同じ単価なので × NEPTUNE_AZ_NUM）、
ops/up.sh:557:# nautobot = 13（Fargate ARM 2 vCPU / 4 GB のタスク 1 つ 9.9 + RDS の db.t4g.micro 2.5 と gp3 20 GB 0.4。公表単価からで、Price Li
IaC/terraform/aws-managed/pipeline/nautobot/locals.tf:9:# Costs about 0.13 USD per hour (Fargate ARM 2 vCPU / 4 GB + RDS db.t4g.micro) - ops/down.sh destroys th
IaC/terraform/aws-managed/pipeline/nautobot/database.tf:37:  # （書き込み 1 台 + 読める待機系 2 台）で、別のリソース（aws_rds_cluster）�
IaC/terraform/aws-managed/pipeline/nautobot/variables.tf:75:  description = "RDS instance class of the Nautobot database (about 0.03 USD per hour for db.t4g.mic
IaC/terraform/aws-managed/pipeline/nautobot/variables.tf:77:  default     = "db.t4g.micro"
IaC/terraform/aws-managed/pipeline/nautobot/variables.tf:81:  description = "1 or 2. 2 makes the database a Multi-AZ DB instance (multi_az = true: a synchronous
IaC/terraform/aws-managed/pipeline/nautobot/terraform.tfvars.example:16:# db_instance_class   = "db.t4g.micro"
Binary file IaC/terraform/aws-managed/pipeline/lab/.terraform/providers/registry.terraform.io/hashicorp/aws/6.64.0/darwin_arm64/terraform-provider-aws_v6.64.0_x
IaC/terraform/aws-managed/pipeline/graph/neptune.tf:3:# db.t4g.medium 1 台）から置き換えた: 機器が増えたときに影響範囲や中心性をグ�
IaC/terraform/aws-managed/pipeline/graph/terraform.tfvars.example:11:# instance_class      = "db.t4g.medium"
Binary file IaC/terraform/aws-managed/base/core/.terraform/providers/registry.terraform.io/hashicorp/aws/6.64.0/darwin_arm64/terraform-provider-aws_v6.64.0_x5 m
IaC/terraform/aws-managed/base/core/variables.tf:78:  description = "Chat web EC2. Gradio with numpy and pandas needs about 400 MB of memory, so t4g.small (2 GB
IaC/terraform/aws-managed/base/core/variables.tf:80:  default     = "t4g.small"
IaC/terraform/aws-managed/base/core/variables.tf:83:    condition     = contains(["t4g.micro", "t4g.small", "t4g.medium"], var.instance_type)
IaC/terraform/aws-managed/base/core/variables.tf:84:    error_message = "instance_type must be t4g.micro, t4g.small or t4g.medium (arm64)."
IaC/terraform/aws-managed/base/core/terraform.tfvars.example:16:# instance_type     = "t4g.small"
Binary file IaC/terraform/aws-managed/base/ecr/.terraform/providers/registry.terraform.io/hashicorp/aws/6.64.0/darwin_arm64/terraform-provider-aws_v6.64.0_x5 ma
docs/pipeline.md:373:- 構成は ECS Fargate（ARM 2 vCPU / 4 GB）の 1 タスクに web（uWSGI）・Celery worker（Job を回す）・Redis の 3 コンテ�
docs/nautobot.md:44:  WEB --- DB[("RDS PostgreSQL<br/>db.t4g.micro<br/>台帳")]
docs/nautobot.md:55:| RDS | PostgreSQL `db.t4g.micro` | 台帳。`ops/down.sh` で消える。`NAUTOBOT_DB_AZ_NUM=2` なら Multi-AZ（別の AZ に待機系。
docs/data-stores.md:168:- **Fargate のほかのサービスは安い arm64。** `cpu_architecture = "ARM64"`（[IaC/terraform/aws-managed/workflow/ecs.tf](../I
docs/data-stores.md:211:Neptune そのものの仕組みと、この PoC での使い方の関係。2026-10-04 に Neptune Database から Neptune Analytics へ
docs/data-stores.md:228:- **費用:** 無料枠は無く、動いているあいだずっと時間で課金される。最小の 16 m-NCU で約 $0.58/h（東�
docs/collection.md:166:| lab に Cisco の機器を足すか | 足せば MDT の受け口（`cisco_telemetry_mdt`）と Cisco の YANG の名前を lab で試�
docs/faq-fukuda-nwc-poc.md:341:  - 待機の費用: 約 $0.20/h → 約 $0.23/h（エンドポイントを土台と共用しなくなった。2026-10-04 に EC2
docs/faq-fukuda-nwc-poc.md:2149:| いまのクラスが使えない | Multi-AZ DB クラスターで使えるインスタンスクラスに、いまの `db.t4g.
docs/deploy.md:30:| （Nautobot） | Nautobot 3.2.6（`IaC/terraform/aws-managed/pipeline/nautobot`。ECS Fargate ARM 2 vCPU / 4 GB の 1 タスクに web・Cel
docs/verification/20261008-managed-aws.md:107:| lab の起動（status / forward-status / check） | 通った | 19:42Z の `sudo lab status` でコンテナ 8 
docs/verification/20261008-managed-aws.md:109:| Telegraf → MSK → Spark → AMP（uid `amp`） | 通った | `count(snmp_interface_ifOperStatus)` = 396。fai
docs/verification/20261008-managed-aws.md:114:| fail-main → Grafana のアラートが SNS に届く | 通った | graph-status の Lambda が 19:46:06〜07 �
docs/verification/20261008-managed-aws.md:115:| fail-main → Temporal の `investigate-<anomaly_id>` が始まる | 通った | worker は 19:46:02 に `starte
docs/verification/20261008-managed-aws.md:119:| heal-main → 解消の signal がワークフローに届く | 通った | 19:47:36 に worker が `resolved in
docs/verification/20261008-managed-aws.md:123:| fail-bgp → bgp_down（Grafana） | 通った | 19:50:00 に `source: grafana, firing, bgp_down` を 2 件。�
docs/verification/20261008-managed-aws.md:129:| trap-test → trap（Splunk） | 通った | 19:50:01 に `source: splunk, firing, trap, dc1-host-01, .1.3.6.1.4
docs/verification/20261008-managed-aws.md:139:| 19:43:55 | `sudo lab fail-main`（dc1-leaf-01 ethernet-1/1 ⇄ dc1-spine-01 の fabric を落とす） |
docs/verification/20261008-managed-aws.md:144:| 19:46:37〜46 | dc1-leaf-01 の修復案を承認する |
docs/verification/20261008-managed-aws.md:153:| 19:48:50 | `sudo lab fail-bgp`（dc1-leaf-01 ⇄ 10.255.0.1 の iBGP） |
docs/verification/20261008-oss-aws.md:88:  - Web の EC2: i-0242b23c74d0aa5bc（t4g.small）。踏み台として、SSM Run Command で検査のスクリプト�
docs/verification/20261008-oss-aws.md:89:  - lab の EC2: i-00b48e9b3412d3ce4（t4g.xlarge）
docs/verification/20261008-oss-aws.md:99:    - RDS（Nautobot。db.t4g.micro）
docs/verification/20261008-oss-aws.md:166:| ワークフローが始まる | 通った | 21:30:03 に worker が `started investigate-dc1-spine-01#link_down#eth
docs/verification/20261008-oss-aws.md:168:| Grafana のルール | 通った | 21:30:35 に trap（dc1-host-01）、link_down（spine-01 ethernet-1/3、leaf-01 
docs/verification/20261008-oss-aws.md:169:| Neo4j の status と Web のトポロジ | 通った | 21:30:35 にインターフェース leaf-01#ethernet-1/1 と
docs/verification/20261008-oss-aws.md:180:| dc1-leaf-01 の中心性を上位 5 台で（centrality） | spine-01 / -02 が degree 4・closeness 0.7、leaf-01 /
docs/verification/20261008-oss-aws.md:181:| dc1-leaf-02 の直近 60 分のログ（search_logs） | 19 件、最新は 21:26:49Z の syslog（warning） | `too
docs/verification/20261008-oss-aws.md:199:- 21:35:05 に REST API で dc1-leaf-01 の status を Maintenance にした（PATCH 200）。
docs/verification/20261008-oss-aws.md:201:  - Neo4j の dc1-leaf-01 に `maintenance: True` が付いた。変更履歴（`change` ノード）が 17 → 18 件
docs/verification/20261008-oss-aws.md:247:| 21:27:57〜58 | `sudo lab trap-test` と `sudo lab fail-main`（dc1-leaf-01 ethernet-1/1 ⇄ dc1-spine-01 の fabric
docs/verification/20261008-oss-aws.md:254:| 21:31:38〜48 | dc1-leaf-01 の修復案を承認する（21:31:46 に worker が受け取る） |
docs/verification/20261008-oss-aws.md:260:| 21:35:05 | Nautobot で dc1-leaf-01 を Maintenance に。JobHook が Neo4j に書く |
docs/verification/20261008-oss-aws.md:356:  - EC2: lab の t4g.xlarge 約 $0.173/h、Web の t4g.small 約 $0.022/h
docs/verification/20261008-oss-aws.md:358:  - RDS: db.t4g.micro 約 $0.025/h
docs/architecture/resources/lab-ec2.md:14:| EC2 | Amazon Linux 2023 x86_64、`m6i.xlarge`（`m6i.xlarge` / `m6i.2xlarge` / `c6i.2xlarge` / `t3.xlarge` / `t3.2xl
docs/architecture/resources/nautobot.md:16:| DB | RDS の PostgreSQL 17、`db.t4g.micro`、gp3 20 GB。バックアップ無し、最後のスナップショッ
docs/architecture/resources/nautobot.md:57:  2 は Multi-AZ の DB インスタンス（待機系 1 台。読めない）。3 AZ は Multi-AZ DB クラスター
docs/architecture/resources/neptune-analytics.md:49:  それまでは Gremlin で `db.t4g.medium` 1 台（約 $0.14/h と数えていた）。機器が増えた
docs/architecture/resources/neptune-analytics.md:95:| AWS の上での動作 | 2026-10-05 に AWS で確かめた。Lambda `<prefix>-graph-status` が `status` 
docs/architecture/resources/web-ec2.md:15:| インスタンス | t4g.small、Amazon Linux 2023（arm64。AMI は SSM の公開パラメータから読む）、g
docs/architecture/resources/web-ec2.md:21:| 費用 | 2.2 セント/時（t4g.small。土台は合わせて約 2 セント/時 + エンドポイント） | `ops/
docs/architecture/resources/agentcore-bedrock.md:111:| チャットの通し | 2026-10-05 に AWS で、チャットが「dc1-leaf-01 の接続先は」に正�
docs/alert-comparison.md:143:機器の名前は 2026-10-08 に lab を組み直す前のもの（`dc1-leaf-01` は今の `dc1-a-leaf-01`、`dc1-spine-01` は同
docs/alert-comparison.md:149:| `dc1-leaf-01#link_down#ethernet-1/1` | 297 秒（02:41:56） | 121 秒（02:39:00） | −176 秒 | 無い |
docs/alert-comparison.md:151:| `dc1-leaf-01#isis_down#ethernet-1/1.0` | 234 秒（02:40:53） | 242 秒（02:41:01） | +8 秒 | 無い |
docs/alert-comparison.md:161:  `dc1-leaf-01#link_down#ethernet-1/1.0`（02:40:00）と `dc1-spine-01#link_down#ethernet-1/3.0`（02:42:01）。Grafana には対
(rc=0)

$ grep -rn 'leafsw\|dc1-leaf-0\|dc1-host\|wan-upstream\|bond0\|t4g\|linux_arm64.rpm' app ops oss IaC docs tests docker README.md --include='*' | grep -v 'docs/cycles/' | grep -c .
82
(rc=0)

$ grep -rln 'leafsw\|dc1-leaf-0\|dc1-host\|wan-upstream\|bond0\|linux_arm64.rpm' app ops oss IaC tests docker README.md
```

```text
$ git grep -n '<検証 6 の式>' -- app ops oss IaC docs tests docker README.md ':!docs/cycles/' | wc -l   （追跡しているファイルだけ。.terraform のバイナリを除く）
      65

== t4g 以外の語（lab の古い名前）の当たり
docs/alert-comparison.md:143:機器の名前は 2026-10-08 に lab を組み直す前のもの（`dc1-leaf-01` は今の `dc1-a-leaf-01`、`dc1-spine-01` は同じ）。
docs/alert-comparison.md:149:| `dc1-leaf-01#link_down#ethernet-1/1` | 297 秒（02:41:56） | 121 秒（02:39:00） | −176 秒 | 無い |
docs/alert-comparison.md:151:| `dc1-leaf-01#isis_down#ethernet-1/1.0` | 234 秒（02:40:53） | 242 秒（02:41:01） | +8 秒 | 無い |
docs/alert-comparison.md:161:  `dc1-leaf-01#link_down#ethernet-1/1.0`（02:40:00）と `dc1-spine-01#link_down#ethernet-1/3.0`（02:42:01）。Grafana には対応する�
docs/architecture/resources/agentcore-bedrock.md:111:| チャットの通し | 2026-10-05 に AWS で、チャットが「dc1-leaf-01 の接続先は」に正しく答え
docs/architecture/resources/neptune-analytics.md:95:| AWS の上での動作 | 2026-10-05 に AWS で確かめた。Lambda `<prefix>-graph-status` が `status` を DOWN /
docs/data-stores.md:211:Neptune そのものの仕組みと、この PoC での使い方の関係。2026-10-04 に Neptune Database から Neptune Analytics へ置き換�
docs/verification/20261008-managed-aws.md:107:| lab の起動（status / forward-status / check） | 通った | 19:42Z の `sudo lab status` でコンテナ 8 つが run
docs/verification/20261008-managed-aws.md:109:| Telegraf → MSK → Spark → AMP（uid `amp`） | 通った | `count(snmp_interface_ifOperStatus)` = 396。fail-bgp の�
docs/verification/20261008-managed-aws.md:114:| fail-main → Grafana のアラートが SNS に届く | 通った | graph-status の Lambda が 19:46:06〜07 に `source
docs/verification/20261008-managed-aws.md:115:| fail-main → Temporal の `investigate-<anomaly_id>` が始まる | 通った | worker は 19:46:02 に `started investig
docs/verification/20261008-managed-aws.md:119:| heal-main → 解消の signal がワークフローに届く | 通った | 19:47:36 に worker が `resolved investigate-
docs/verification/20261008-managed-aws.md:123:| fail-bgp → bgp_down（Grafana） | 通った | 19:50:00 に `source: grafana, firing, bgp_down` を 2 件。ルールの
docs/verification/20261008-managed-aws.md:129:| trap-test → trap（Splunk） | 通った | 19:50:01 に `source: splunk, firing, trap, dc1-host-01, .1.3.6.1.4.1.8072.2.
docs/verification/20261008-managed-aws.md:139:| 19:43:55 | `sudo lab fail-main`（dc1-leaf-01 ethernet-1/1 ⇄ dc1-spine-01 の fabric を落とす） |
docs/verification/20261008-managed-aws.md:144:| 19:46:37〜46 | dc1-leaf-01 の修復案を承認する |
docs/verification/20261008-managed-aws.md:153:| 19:48:50 | `sudo lab fail-bgp`（dc1-leaf-01 ⇄ 10.255.0.1 の iBGP） |
docs/verification/20261008-oss-aws.md:166:| ワークフローが始まる | 通った | 21:30:03 に worker が `started investigate-dc1-spine-01#link_down#ethernet-1/3 
docs/verification/20261008-oss-aws.md:168:| Grafana のルール | 通った | 21:30:35 に trap（dc1-host-01）、link_down（spine-01 ethernet-1/3、leaf-01 ethernet-1
docs/verification/20261008-oss-aws.md:169:| Neo4j の status と Web のトポロジ | 通った | 21:30:35 にインターフェース leaf-01#ethernet-1/1 と spine-01#
docs/verification/20261008-oss-aws.md:180:| dc1-leaf-01 の中心性を上位 5 台で（centrality） | spine-01 / -02 が degree 4・closeness 0.7、leaf-01 / -02・lea
docs/verification/20261008-oss-aws.md:181:| dc1-leaf-02 の直近 60 分のログ（search_logs） | 19 件、最新は 21:26:49Z の syslog（warning） | `tool search_l
docs/verification/20261008-oss-aws.md:199:- 21:35:05 に REST API で dc1-leaf-01 の status を Maintenance にした（PATCH 200）。
docs/verification/20261008-oss-aws.md:201:  - Neo4j の dc1-leaf-01 に `maintenance: True` が付いた。変更履歴（`change` ノード）が 17 → 18 件。足さ�
docs/verification/20261008-oss-aws.md:247:| 21:27:57〜58 | `sudo lab trap-test` と `sudo lab fail-main`（dc1-leaf-01 ethernet-1/1 ⇄ dc1-spine-01 の fabric を落と
docs/verification/20261008-oss-aws.md:254:| 21:31:38〜48 | dc1-leaf-01 の修復案を承認する（21:31:46 に worker が受け取る） |
docs/verification/20261008-oss-aws.md:260:| 21:35:05 | Nautobot で dc1-leaf-01 を Maintenance に。JobHook が Neo4j に書く |

== t4g の当たり（ファイルごとの件数）
IaC/terraform/aws-managed/base/core/terraform.tfvars.example:1
IaC/terraform/aws-managed/base/core/variables.tf:4
IaC/terraform/aws-managed/pipeline/graph/neptune.tf:1
IaC/terraform/aws-managed/pipeline/graph/terraform.tfvars.example:1
IaC/terraform/aws-managed/pipeline/nautobot/database.tf:1
IaC/terraform/aws-managed/pipeline/nautobot/locals.tf:1
IaC/terraform/aws-managed/pipeline/nautobot/terraform.tfvars.example:1
IaC/terraform/aws-managed/pipeline/nautobot/variables.tf:3
docs/architecture/resources/lab-ec2.md:1
docs/architecture/resources/nautobot.md:2
docs/architecture/resources/neptune-analytics.md:1
docs/architecture/resources/web-ec2.md:2
docs/collection.md:1
docs/data-stores.md:2
docs/deploy.md:1
docs/faq-fukuda-nwc-poc.md:2
docs/nautobot.md:2
docs/pipeline.md:1
docs/verification/20261008-oss-aws.md:5
ops/up.sh:5

== t4g のうち lab の EC2 に触れている行
docs/architecture/resources/lab-ec2.md:14:| EC2 | Amazon Linux 2023 x86_64、`m6i.xlarge`（`m6i.xlarge` / `m6i.2xlarge` / `c6i.2xlarge` / `t3.xlarge` / `t3.2xlarge` か�
docs/collection.md:166:| lab に Cisco の機器を足すか | 足せば MDT の受け口（`cisco_telemetry_mdt`）と Cisco の YANG の名前を lab で試せ、IOS XE
docs/faq-fukuda-nwc-poc.md:341:  - 待機の費用: 約 $0.20/h → 約 $0.23/h（エンドポイントを土台と共用しなくなった。2026-10-04 に EC2 の単価
docs/verification/20261008-oss-aws.md:89:  - lab の EC2: i-00b48e9b3412d3ce4（t4g.xlarge）
docs/verification/20261008-oss-aws.md:356:  - EC2: lab の t4g.xlarge 約 $0.173/h、Web の t4g.small 約 $0.022/h
ops/up.sh:539:# lab = 25（EC2 の m6i.xlarge 24.8。2026-10-08 に Price List API で確認。TRex が amd64 だけなので x86_64。2026-10-08 までの 17 は t4g.xla
```

#### 3（補足）と 8〜10（verify2.sh）

```text
### 3（補足）
$ python3 app/containerlab/lab_topology.py app/containerlab --gnmi-targets
"203.0.113.31:57400", "203.0.113.32:57400", "203.0.113.11:57400", "203.0.113.12:57400", "203.0.113.21:57400", "203.0.113.22:57400"
(rc=0)

$ python3 app/containerlab/lab_topology.py app/containerlab --gnmi-targets | grep -o :57400 | wc -l
       6
(rc=0)

### 8
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/lab init -backend=false -input=false >/dev/null && terraform -chdir=IaC/terraform/aws-managed/pipeline/lab validate
Success! The configuration is valid.

(rc=0)

$ terraform -chdir=IaC/terraform/aws-managed/base/ecr init -backend=false -input=false >/dev/null && terraform -chdir=IaC/terraform/aws-managed/base/ecr validate
Success! The configuration is valid.

(rc=0)

$ terraform fmt -check -recursive IaC/
(rc=0)

$ /opt/homebrew/bin/uv run --group dev python tests/test_lab_debug.py | tail -2
ok app/telegraf/telegraf.sh は bash として読める
通過 90 / 失敗 0
(rc=0)

$ grep -n -A6 '^  InstanceType:' IaC/cloudformation/lab-debug.yaml | grep -E 'Default|AllowedValues'; grep -n -A4 '^  ImageId:' IaC/cloudformation/lab-debug.yaml | grep Default
56-    Default: m6i.xlarge
57-    AllowedValues: [m6i.xlarge, m6i.2xlarge, c6i.2xlarge, t3.xlarge, t3.2xlarge]
53-    Default: /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64
(rc=0)

$ grep -n -A8 'variable "instance_type"' IaC/terraform/aws-managed/pipeline/lab/variables.tf | grep -E 'default|contains'
33-  description = "7 containers (Nokia SR Linux x6, TRex x1). x86_64 because TRex is amd64 only. Each SR Linux node takes about 1.5-2.5 GB of RAM at boot and TRex 2-4 GB, so m6i.xlarge (4 vCPU / 16 GB) is the default; use m6i.2xlarge (8 vCPU / 32 GB) for load tests."
35-  default     = "m6i.xlarge"
38-    condition     = contains(["m6i.xlarge", "m6i.2xlarge", "c6i.2xlarge", "t3.xlarge", "t3.2xlarge"], var.instance_type)
(rc=0)

### 9
$ bash -n app/containerlab/lab.sh && /opt/homebrew/bin/uv tool run --from shellcheck-py shellcheck app/containerlab/lab.sh && echo 'app/containerlab/lab.sh: bash -n / shellcheck 通過'

In app/containerlab/lab.sh line 33:
GNMI_USERNAME=admin
^-----------^ SC2209 (warning): Use var=$(command) to assign output (or quote to assign string).


In app/containerlab/lab.sh line 55:
ENV_FILE=$(ls /etc/*-lab.env 2>/dev/null | head -1 || true)
           ^---------------------------^ SC2012 (info): Use find instead of ls to better handle non-alphanumeric filenames.


In app/containerlab/lab.sh line 56:
[ -n "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a
                                  ^---------^ SC1090 (warning): ShellCheck can't follow non-constant source. Use a directive to specify location.


In app/containerlab/lab.sh line 222:
      if r=$(route 2>/dev/null) && grep -q "172.16.0.12" <<<"$r" && ! grep -q "172.16.0.4\b" <<<"$r"; then
         ^-- SC2178 (warning): Variable was used as an array but is now assigned a string.
                                                             ^-- SC2128 (warning): Expanding an array without an index only gives the first element.
                                                                                                 ^-- SC2128 (warning): Expanding an array without an index only gives the first element.

For more information:
  https://www.shellcheck.net/wiki/SC1090 -- ShellCheck can't follow non-const...
  https://www.shellcheck.net/wiki/SC2128 -- Expanding an array without an ind...
  https://www.shellcheck.net/wiki/SC2178 -- Variable was used as an array but...
(rc=1)

$ bash -n app/containerlab/setup.sh && /opt/homebrew/bin/uv tool run --from shellcheck-py shellcheck app/containerlab/setup.sh && echo 'app/containerlab/setup.sh: bash -n / shellcheck 通過'

In app/containerlab/setup.sh line 9:
ENV_FILE=$(ls /etc/*-lab.env 2>/dev/null | head -1 || true)
           ^---------------------------^ SC2012 (info): Use find instead of ls to better handle non-alphanumeric filenames.


In app/containerlab/setup.sh line 11:
set -a; . "$ENV_FILE"; set +a
          ^---------^ SC1090 (warning): ShellCheck can't follow non-constant source. Use a directive to specify location.

For more information:
  https://www.shellcheck.net/wiki/SC1090 -- ShellCheck can't follow non-const...
  https://www.shellcheck.net/wiki/SC2012 -- Use find instead of ls to better ...
(rc=1)

$ bash -n app/containerlab/trex/kafka_load.sh && /opt/homebrew/bin/uv tool run --from shellcheck-py shellcheck app/containerlab/trex/kafka_load.sh && echo 'app/containerlab/trex/kafka_load.sh: bash -n / shellcheck 通過'
app/containerlab/trex/kafka_load.sh: bash -n / shellcheck 通過
(rc=0)

$ bash -n ops/lab-common.sh && /opt/homebrew/bin/uv tool run --from shellcheck-py shellcheck ops/lab-common.sh && echo 'ops/lab-common.sh: bash -n / shellcheck 通過'

In ops/lab-common.sh line 1:
# lab の材料（イメージの版・containerlab の rpm・S3 に置く lab/）。ops/up.sh（IaC/terraform/aws-managed/pipeline/lab と stream の Telegraf）と
^-- SC2148 (error): Tips depend on target shell and yours is unknown. Add a shebang or a 'shell' directive.


In ops/lab-common.sh line 27:
LAB_SYSLOG_STANDARD=RFC5424
^-----------------^ SC2034 (warning): LAB_SYSLOG_STANDARD appears unused. Verify use (or export if used externally).


In ops/lab-common.sh line 31:
LAB_GNMI_USERNAME=admin
^---------------^ SC2034 (warning): LAB_GNMI_USERNAME appears unused. Verify use (or export if used externally).
^---------------^ SC2209 (warning): Use var=$(command) to assign output (or quote to assign string).


In ops/lab-common.sh line 32:
LAB_GNMI_PASSWORD='NokiaSrl1!'
^---------------^ SC2034 (warning): LAB_GNMI_PASSWORD appears unused. Verify use (or export if used externally).


In ops/lab-common.sh line 33:
LAB_SNMP_COMMUNITY=public
^----------------^ SC2034 (warning): LAB_SNMP_COMMUNITY appears unused. Verify use (or export if used externally).

For more information:
  https://www.shellcheck.net/wiki/SC2148 -- Tips depend on target shell and y...
  https://www.shellcheck.net/wiki/SC2034 -- LAB_GNMI_PASSWORD appears unused....
  https://www.shellcheck.net/wiki/SC2209 -- Use var=$(command) to assign outp...
(rc=1)

### 10
$ python3 -c 'import ast; ast.parse(open("app/containerlab/trex/stl/udp_trap.py").read()); ast.parse(open("app/containerlab/trex/stl/udp_syslog.py").read()); print("構文 OK")'
構文 OK
(rc=0)

```

shellcheck の指摘を起点 29dd7ca と比べたもの（sc_compare.sh。ファイル名と行番号を除いた本文で diff）:

```text
== app/containerlab/lab.sh: base 6 / now 6
   同じ指摘だけ（増減なし）
== app/containerlab/setup.sh: base 2 / now 2
   同じ指摘だけ（増減なし）
== ops/lab-common.sh: base 6 / now 6
   同じ指摘だけ（増減なし）
== ops/lab-debug.sh: base 3 / now 3
   同じ指摘だけ（増減なし）
== ops/up.sh: base 15 / now 15
   同じ指摘だけ（増減なし）
== oss/ops/up.sh: base 15 / now 15
   同じ指摘だけ（増減なし）
```

期待との突き合わせ:

- 8: validate 2 つ Success、`fmt -check` は差分なし、`test_lab_debug.py` 通過 90 / 失敗 0。CFn の既定 `m6i.xlarge` / `…x86_64`、AllowedValues と TF の validation の list が同じ。一致
- 9: `bash -n` は 4 本とも通る。shellcheck は `kafka_load.sh` だけ通り、他の 3 本は起点と同じ指摘（上の「ずれた点」）
- 10: 構文 OK。一致

#### 7（uv sync --group dev --group web && bash ops/check.sh）

先頭（1〜3 の節）:

```text
Resolved 86 packages in 3ms
Audited 82 packages in 5ms

== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. ops スクリプトの構文
構文エラーなし
```

テストごとの本数（`tests/test_*.py` 15 本。並びは check.sh の glob 順）:

```text
test_alerts.py: 通過 137 / 失敗 0
test_analytics.py: 通過 489 / 失敗 0
test_app.py: 通過 161 / 失敗 0
test_dashboard_config.py: 通過 3 / 失敗 0
test_graph.py: 通過 72 / 失敗 0
test_kb_index.py: 通過 7 / 失敗 0
test_lab_debug.py: 通過 90 / 失敗 0
test_local_compose.py: 通過 82 / 失敗 0
test_nautobot.py: 69 項目すべて通過
test_oss.py: 通過 167 / 失敗 0
test_oss_ops.py: 通過 148 / 失敗 0
test_oss_roll.py: 通過 66 / 失敗 0
test_stream.py: 通過 75 / 失敗 0
test_sync.py: 通過 99 / 失敗 0
test_workflow.py: 通過 325 / 失敗 0
```

末尾:

```text
通過 325 / 失敗 0

すべて通過
check.sh rc=0
```

9997082 のあとに取った（scratchpad の check4.log）。前の作業（down-echo-and-graph-output の worktree。29dd7ca にマージされた側）で取った check.sh の本数と比べると、app 158 → 161、lab_debug 84 → 90、local_compose 81 → 82、nautobot 68 → 69、sync 96 → 99 で、ほかは同じ。合計 1976 → 1990。足した検査 14:

- e8eed52 の 3: what_if の冗長切れの新しい筋、TREX_TAG の照合、.env に TREX_IMAGE が無ければ止まる
- 4cb51a6 の 10: what_if の TRex を通り道にしない 2 つ（app）、`LAB_ARCH` / `*_ECR_TAG`・ECR に有るかを `*_ECR_TAG` で見る・mirror の置き先・lab-debug.sh が渡すタグ・lab_repositories と mirror の置き先が同じ（lab_debug 5）、`ROW_OF`（nautobot）、`ROLE_ORDER` と TRex との回線の lag / l2（sync 2）
- 9997082 の 1: ES の頂点と辺（sync）。TRex との回線の検査は TRex の bond も見るように書き換えた（本数は同じ）

29dd7ca そのものでは取っていない。

### セルフレビュー

- 自分: Opus 5.5 / xhigh。入力は design.md と e8eed52 のコード
- 反対弁護人: opus（Agent の general-purpose、読み取り専用）。渡したのは design.md / build.md のパス、変更ファイルの一覧、選んだ方針と迷った点 9 つ。返ってきたあとの `git status --porcelain -uall` は build.md の 1 行だけで、増えたファイルは無い
- 反対弁護人の指摘は Must 1 / Should 3 / Nit 7。全部を自分で再現してから片付けた。自分の退行注入では、見張りの無い所が 4 つ見つかった（うち 3 つは指摘 3 と重なる）
- 直しは 4cb51a6・9997082 と、`trex/README.md` / `trex_cfg.yaml.in` の文言（このあとの commit）。文言の 2 本はどのテストも読まない（`git grep -n 'trex/README\|pgrep' -- tests/` で 0 件）。`trex_cfg.yaml.in` はコメントだけで、lab.sh の `trex_cfg` で展開して `yaml.safe_load` で読めた（ポート 4、`10.100.0.11`〜`.14`、gw は組の相手）

#### 指摘 1（Must）[ECR / x86 移行]: 前の版の arm64 イメージを x86 の EC2 が引く

- 場所: `ops/lab-common.sh` の `mirror_lab_images`、`ops/up.sh:624-625`、`oss/ops/up.sh:163-165,209`、`ops/lab-debug.sh:138-140`（e8eed52 の行）
- 破綻: `KEEP_ECR=1` で前の版の ECR を残すと、arm64 で置いた `lab-srlinux:26.7.2` / `lab-multitool:v0.10.0` が残る。`ecr_has` は「ある」と見てミラーを飛ばし、x86 の EC2 が arm64 を引いて exec format error で起きない
- 確かめた: コードと `docs/verification/20261008-managed-aws.md`（KEEP_ECR で 11 リポジトリを残した記録）を読んだ。AWS は叩いていない（いま ECR に何があるかは見ていない）
- 片付け: 直した（4cb51a6）。ECR のタグを `<上流の版>-amd64`（`*_ECR_TAG`）にした。`ecr_has`・ミラー・lab-debug.sh・TF / CFn の既定値がこのタグを使う。古いタグは残っても誰も引かず、次の up.sh が `-amd64` を置き直す。退行 F1a〜F1h（下の表）は全部 test_lab_debug が落とす

#### 指摘 2（Should）[correctness / what_if]: TRex を Leaf どうしの通り道に数える

- 場所: `app/agentcore/topology.py:346-363`、`app/temporal/rules.py:81-150`、`tests/test_app.py:236-239`（e8eed52）
- 破綻: TRex は 4 台の Leaf につながる。spine-01 が DOWN のまま device_down spine-02 を打つと、本当は Leaf がばらばらになるのに、TRex 経由でつながっていると見て warn を返す。全部 UP で e1-1 を落としたときの冗長切れも見落とす。e8eed52 のテストはこの誤った意味を期待値にしていた
- 確かめた: e8eed52 と HEAD の `topology.impact` に同じ静的データ（e8eed52 から変わっていない）を渡して比べた（e8_whatif.log）:

```text
spine-01 DOWN で spine-02 を落とす / e8eed52: warn iso=[] red=['dc1-a-leaf-01', 'dc1-a-leaf-02', 'dc1-s-leaf-01', 'dc1-s-leaf-02']
spine-01 DOWN で spine-02 を落とす / HEAD: danger iso=['dc1-a-leaf-02', 'dc1-s-leaf-01', 'dc1-s-leaf-02'] red=[]
全部 UP で a-leaf-01 e1-1 を落とす / e8eed52: ok iso=[] red=[]
全部 UP で a-leaf-01 e1-1 を落とす / HEAD: warn iso=[] red=['dc1-a-leaf-01']
e1-1 DOWN で e1-2 を落とす / e8eed52: warn iso=[] red=['dc1-a-leaf-01']
e1-1 DOWN で e1-2 を落とす / HEAD: danger iso=['dc1-a-leaf-01'] red=[]
```

- 片付け: 直した（4cb51a6）。`END_ROLES`（trex）を通り道にも冗長の本数にも数えない。期待値の変更は上の「期待値を変えたテスト」に書いた。F2a〜F2d は test_app / test_workflow が落とす
- 残り（範囲外。PM へ BACKLOG の候補として回す）:
  - worker の `rules.impact` には役割が渡らないので、worker は TRex を通り道に数える。docstring と `docs/workflow.md:30` に書いた
  - いまの処置は回線を上げる heal-main と、見るだけの check しか無い。heal-main を、役割あり / なしで 2^19 = 524288 通り（機器 7 + 回線 12 の UP / DOWN）比べた（healmain_all.log）
    - (役割あり, なし) = (ok, ok) 519463、(danger, ok) 1126、(danger, danger) 1850、(ok, danger) 1849
  - 回線を上げるだけで danger が出るのは、変更後の本流を「いちばん大きいかたまり」で選ぶから。上げてかたまりの大きさが入れ替わると、前の本流の機器が newly_isolated に数えられる
  - 011 より前（29dd7ca。機器 8 + 回線 12）でも、2^20 = 1048576 通りのうち 14959 通りが danger になる（healmain_old.log）。011 で入った不具合ではない
  - `docs/workflow.md:30` と `docs/architecture/resources/temporal.md:102` の「警告は出ない」も 011 より前からの誤り
  - 直し方の案: 変更後の本流は、前の本流の機器をいちばん多く含むかたまりにする

#### 指摘 3（Should）[missing tests]: 残した LAG / bond / ES の分岐を見張るテストが無い

- 場所: `app/containerlab/lab_topology.py`（`VM_ROLES`、回線の lag / l2、`EXEC_MASTER_RE`、ES の頂点と辺）、`app/dashboard/topology_view.py`（`ROW_OF`）、`app/agentcore/topology.py`（`ROLE_ORDER`）
- 破綻: いまの lab には LAG / bond / ES が無い。design で「残す」とした分岐を壊しても、どのテストも落ちない
- 確かめた: `VM_ROLES` を起点の値に戻しても test_sync は rc=0（regress.log の 0）。自分で注入した 3（`ROW_OF`）と 4（`ROLE_ORDER`）も落ちなかった
- 片付け: テストを足した（4cb51a6、9997082）。test_sync は lab の写しに LAG / bond / ES を足して読む。F3a〜F3j は全部落ちる

#### 指摘 4（Should）[runtime / TRex の起動]: 設定の書き方と起動を確かめていない

- 場所: `app/containerlab/trex/trex_cfg.yaml.in:3,10`、`app/containerlab/lab.sh:101-112,325-341`、`app/containerlab/splab.clab.yml.in:80-91`
- 指摘の中身（反対弁護人も「記憶による推測」）:
  - 素の IF 名では起きない
  - テンプレートは「hugepages は要らない」と言い切るのに、README はそれを未確認に挙げている
  - CMD がシェルだけでコンテナが落ちる
  - イメージに `ip` が無い
- 確かめた:
  - IF 名: TRex の文書（trex_book。scratchpad に取得）では、af_packet は 2.37 から Linux の IF 名をそのまま取る。2.41 はこれに当たる
  - コマンド: registry から層のファイル名を流し読みした（trex_layers.log）。`usr/sbin/ip` と `usr/bin/pgrep` / `pkill` / `find` がある
  - 版: `/var/trex/` の v2.36 / v2.39 は後の層の whiteout で消え、残るのは v2.41 だけ（trex_wh.log）
  - CMD: イメージは amd64 だけで、`Cmd ['/bin/bash']`、Entrypoint は無い（trex_config.json）。containerlab 0.79.0 はコンテナを `Tty: true` / `OpenStdin: true` で作る（`runtime/docker/docker.go:608-609` を curl で取得して grep）。CMD がシェルだけのイメージで再現した（tty_test.log。`alpine:latest` の `/bin/sh`）:

```text
Tty なし: exited exit=0 tty=false stdin=false
Tty + OpenStdin: running exit=0 tty=true stdin=true
残り: 0
```

- 片付け:
  - 反証できた分は、README に「手元で確かめたこと」として足した
  - README の未確認の一覧から `pgrep` / `pkill` / `find` を外した
  - テンプレートのコメントは「TRex の文書では」と出典を書き、未確認だと書き足した
  - 2.41 が af_packet・hugepages 無しでこの lab で起きるかは、lab の EC2 が要るので確かめられない。README の「確かめていないこと」に残し（design の未確定事項 2）、最終報告に回した

#### 自分の退行注入

写しの中で 1 か所ずつ壊し、見張るはずのテストが落ちるかを見た。写しは毎回消した。

- 基準: 壊していない写しで、対象のテストが全部 rc=0
- regress.log の 5〜7 は、写しを tar で作ったので基準が落ちていた（test_local_compose / test_oss が rc=1）。git clone で作り直し、regress2.log で取り直した

e8eed52 の時点（regress.log / regress2.log）:

| # | 壊したもの | 見たテスト | 結果 |
|---|---|---|---|
| 0 | `lab_topology.VM_ROLES` を起点の値に | sync / nautobot / app / graph | 落ちない → 指摘 3（F3a） |
| 1 | `split_name` を 2 語目だけの役割に | sync | 落ちる |
| 2 | `nb_map.VM_ROLES` を起点の値に | nautobot | 落ちる |
| 3 | `ROW_OF` を消す | nautobot / dashboard_config / app | 落ちない → テストを足した（F3b） |
| 4 | `ROLE_ORDER` から trex を抜く | app / graph / nautobot | 落ちない → テストを足した（F3c） |
| 5 | docker/compose/lab.sh の `TREX_IMAGE` の検査を消す | local_compose | 落ちる |
| 6 | lab.sh の `${TREX_IMAGE:?}` を `$TREX_IMAGE` に | local_compose / lab_debug / alerts | 落ちない → Nit（下） |
| 7 | base/ecr の lab のリポジトリから trex を抜く | lab_debug / oss | 落ちない → テストを足した（F1h） |

6 を Nit にした根拠（case6.log）: `TREX_IMAGE` が無いと、壊した側でも `set -u` で同じ所で止まる。変わるのは文言（`parameter null or not set` → `unbound variable`）だけ:

```text
== head render（TREX_IMAGE なし）
./lab.sh: line 134: TREX_IMAGE: parameter null or not set
rc=1  splab.clab.yml: なし  docker の呼び出し:        0
== regr render（TREX_IMAGE なし）
./lab.sh: line 135: TREX_IMAGE: unbound variable
rc=1  splab.clab.yml: なし  docker の呼び出し:        0
```

直したあと（regress3.log は 4cb51a6、regress4.log は 9997082 の中身）。全部落ちる:

| # | 壊したもの | 落ちたテスト |
|---|---|---|
| F1a | ECR のタグを上流の版のまま（`-amd64` なし） | lab_debug |
| F1b / F1c | ops/up.sh / oss/ops/up.sh の `ecr_has` が上流の版を見る | lab_debug |
| F1d | lab-debug.sh が CFn に上流の版を渡す | lab_debug |
| F1e / F1f | variables.tf / lab-debug.yaml の既定値に `-amd64` なし | lab_debug |
| F1g | ミラーが arm64 を引く | lab_debug |
| F1h | base/ecr の lab のリポジトリから trex を抜く | lab_debug |
| F2a | `END_ROLES` を空に（TRex を中継に戻す） | app、workflow |
| F2b | かたまりを広げるときに端（TRex）を通す | app |
| F2c | 冗長の本数に TRex への回線を数える | app |
| F2d | rules.py だけ `END_ROLES` を空に（2 つの impact が食い違う） | workflow |
| F3a | `lab_topology.VM_ROLES` を起点の値に | sync |
| F3b | `ROW_OF` を消す | nautobot |
| F3c | `ROLE_ORDER` から trex を抜く | sync |
| F3d | ES の頂点を作らない | sync |
| F3e | 同じ ESI の ES どうしを segment でつながない | sync |
| F3f | ES から lag の IF への over を引かない | sync |
| F3g | parse_srl が ES の multi-homing-mode を読まない | sync |
| F3h | `EXEC_MASTER_RE` を壊す（VM の bond のメンバーを読まない） | sync |
| F3i | 回線の種別で VM 側の bond を見ない | sync |
| F3j | 回線の種別でスイッチ側の LAG を見ない | sync |

#### 問題なしとした観点と根拠

- **名前の割り方:** 機器名を `-` で割るのは `lab_topology.split_name` だけ（反対弁護人が `git grep`）。壊すと test_sync が落ちる（自分の注入 1）
- **古い役割名:** leaf / leafsw / host / upstream が残るのはテストの合成データだけ（検証 6 の v6b と、反対弁護人の `git grep`）
- **lab.sh:**
  - usage の 2〜7 行目はテストの行番号と合う
  - heal-main の対象は `ACTION_CHANGES` と同じ `dc1-a-leaf-01#ethernet-1/1`
  - failover の次 hop（172.16.0.4 / .12）も合う
  - 根拠は反対弁護人が読んだことと、test_alerts / test_workflow が通ること
- **trex_cfg:** 展開した YAML が読め、gw は組の相手（上の展開と、反対弁護人の Python での再現）
- **x86 の範囲:**
  - amd64 を渡すのは lab の 3 つとデバッグ用の Telegraf だけ。Fargate・AgentCore・EMR・Lambda・Web の EC2 は arm64 のまま（反対弁護人が読んだだけ）
  - lab 以外の t4g は検証 6 で全部見た
- **ECR の配線:** trex のリポジトリ、IAM の `-lab-*`、CFn の EcrPull（TrexRepository を含む 4 つ）を確かめた。根拠は test_lab_debug と F1h
- **静的データ:** 生成器の出力と完全に一致（cmp_topo.py。機器 7、回線 12、layers 58 / 78、device_map、SNMP / gNMI の対象。検証 1）
- **費用と STL:** 費用 17 → 25 セントはどこも同じ値（test_analytics）。STL の宛先 162 の理由は上の「ずれた点」
- **テスト:** 反対弁護人が e8eed52 で test_*.py を回し、失敗 0。こちらは 9997082 で check.sh 全体を回した（上の 7）

#### Nit（直していない。最終報告に回す）

- `docker/compose/lab.sh:12` は、どのサブコマンドでも `TREX_IMAGE` を要る。011 より前の `.env` では `down` / `check` も止まるが、`docker/compose/README.md:52` に「既存の `.env` に足す」の一文が無い
- 残した lag / ES の名残: Telegraf の `evpn_es` の購読と `lab_lag_speed`、nb_map の lag の分岐、ダッシュボードの文言。残すか消すかは次で決める
- どのノードも使わない multitool が、`gen_lab.py` の kinds、ミラー、`.env` の必須キーに残っている
- `design.md:27` の「nb_map.py に ROLE_ORDER の表がある」は誤り。表は `app/agentcore/topology.py` にある
- `kafka_load.sh` の gnmi のレコードは、peer がどのノードでも 10.255.0.1（spine-01 自身も）。前提が揃っていないので、いまは動かないスクリプト（README の「前提」）
- 孤立の同点は名前で切る（011 より前からの決め方）。Spine を 2 台とも落とすと Leaf 4 台がばらばらになるが、`newly_isolated` には 3 台しか出ない。verdict は danger のままなので Nit にした（tiebreak.log）:

```text
spine-01 DOWN で spine-02 を落とす / topology（チャット）: danger iso=['dc1-a-leaf-02', 'dc1-s-leaf-01', 'dc1-s-leaf-02'] red=[]
spine-01 DOWN で spine-02 を落とす / rules（worker）: danger iso=['dc1-a-leaf-02', 'dc1-s-leaf-01', 'dc1-s-leaf-02'] red=[]
```

- Telegraf の Dockerfile のコメントで `TELEGRAF_TAG` が変わり、次の up.sh で作り直しになる（上の「ずれた点」）
- lab.sh の `${TREX_IMAGE:?}` は見張られていない（自分の注入 6）
