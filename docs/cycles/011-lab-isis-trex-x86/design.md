# lab を IS-IS で組み直し、各 leaf に TRex をつなぎ、lab の EC2 を x86 にする（011）

設計: PM(fable-5.1) / effort: high

## 背景

- ユーザーの要望（2026-10-08）: 「container lab の構成は ISIS / SR-MPLS。spine01, spine02 が a-leaf01, a-leaf02, s-leaf01, s-leaf02 と繋がり、各 leaf と繋がる trex がある構成にしたい」。BACKLOG 27 と 62 がこれ
- 2026-10-08 の決定（ユーザー「OK」）
  - **SR-MPLS はいまやらない。** SR Linux の `ixr-d2l` では組めず、`ixr-6e` / `ixr-10e` + Nokia のライセンスが要る（公開の入手経路は無い）。ライセンスが届いたら `type: ixr-6e` + `license:` の差し替えで入れる。FRR（gNMI が無い）と vJunos（EC2 の metal が要る。`c5n.metal` $4.896/時）は選ばない。IS-IS の underlay はいまの lab に既にある（instance `main`、L2、p2p）ので、**組み直しの中身は「機器名と役割を a-leaf / s-leaf に揃える」「VM 2 台を TRex に置き換える」「EC2 を x86 にする」の 3 つ**
  - **TRex は fabric の性能試験ではなく、後段（Telegraf → MSK → Spark → 格納先、アラート → SNS → graph の Lambda）の負荷試験に使う。** 負荷試験そのものはまだやらない。配置・プロファイル・測り方だけ整える。trap / syslog は UDP で撃つ。gNMI / metrics は gRPC なので TRex では作れず、Kafka の producer（`kafka-producer-perf-test`）で別に流す
  - **TRex の公式イメージ `trexcisco/trex` は amd64 だけ**（Docker Hub の tag 一覧を 2026-10-08 に確認: `latest` = `2.41`、2018-05、amd64 のみ。3.x は自前ビルドで、このサイクルでは使わない）。**そのため lab の EC2 を x86（`m6i.xlarge`。負荷試験のときは `m6i.2xlarge`）にする。** 別の EC2 は立てない
  - **アーキテクチャは全体で揃えない。** arm64 が必須なのは AgentCore Runtime のエージェントだけ、x86 が必須なのは Splunk と TRex。Fargate のサービスは arm64 のまま。守るのは「同じホストの中は揃える」（lab の EC2 では SR Linux / TRex / Telegraf が全部 x86）。`docs/data-stores.md` の 8「arm64 に揃える」（2026-09-26）はこれに書き換える
- いまの lab（`app/containerlab/gen_lab.py` が生成）: spine 2 + leafsw 2 + leaf 2 の SR Linux 6 台（IS-IS underlay + iBGP EVPN-VXLAN overlay、AS 65100、spine が RR、mac-vrf EVI/VNI 100）と、multitool の VM 2 台（`wan-upstream-01` が leafsw の対に、`dc1-host-01` が leaf の対に、LACP の bond0 で二重化。EVPN の Ethernet Segment）。**物理のつながり（spine 2 台が leaf 4 台と全結合）はユーザーの絵と同じ**で、違うのは leaf の名前と、leaf の下に付くもの

## 設計方針

### 1. 機器名と役割

| いま | あと | 管理 IP | ループバック |
|---|---|---|---|
| `dc1-spine-01/02`（spine） | そのまま | 203.0.113.21〜 | 10.255.0.x |
| `dc1-leafsw-01/02`（leafsw。WAN 側） | `dc1-s-leaf-01/02`（役割 `s-leaf`） | 203.0.113.11〜 | 10.255.1.x |
| `dc1-leaf-01/02`（leaf。DC 側） | `dc1-a-leaf-01/02`（役割 `a-leaf`） | 203.0.113.31〜 | 10.255.2.x |
| `wan-upstream-01` / `dc1-host-01`（multitool） | `dc1-trex-01`（役割 `trex`）1 台 | 203.0.113.101 | 無し |

- 名前は `<site>-<role>-<NN>` のまま。役割にハイフンが入るので、`app/containerlab/lab_topology.py` の `split_name` を「先頭 1 語が site、末尾 1 語が連番、あいだ全部が role」に変える（`parts[0], "-".join(parts[1:-1])`）。`wan-upstream-01` のような site 無しの名前は無くなる
- `ROLE_ORDER` は上から `spine` → `a-leaf` → `s-leaf` → `trex` → `unknown`（topology.json の `_comment`、`app/agentcore/topology.py`、`app/nautobot/netops/nb_map.py`、dashboard の並び順が同じ表を持つ。全部同じ順に直す）
- `VM_ROLES = {"trex"}`（SNMP / gNMI の対象外。`devices.yaml` は `enabled: false`、`device_map` には載せる = trap-test の送り元の名前引きに使う）
- a / s の意味はユーザーに確かめていない（未確定事項 1）。写像は「WAN 側の leafsw → s-leaf」「DC 側の leaf → a-leaf」とし、IP とループバックの割当はいまの値を引き継ぐ（設定の差分を名前だけにして、IS-IS の NET と BGP の RR 構成を変えない）

### 2. TRex

- **`dc1-trex-01` 1 台、ポート 4 本。** `eth1` → `dc1-s-leaf-01 e1-3`、`eth2` → `dc1-s-leaf-02 e1-3`、`eth3` → `dc1-a-leaf-01 e1-3`、`eth4` → `dc1-a-leaf-02 e1-3`（`gen_lab.py` の `host_port = spines + 1` のまま）。「各 leaf と繋がる trex」を 1 台 4 ポートで満たす。4 台にしない理由: TRex は 1 台で 2〜4 GB 取り、SR Linux 6 台（各 1.5〜2.5 GB）と合わせて 16 GB の EC2 では 4 台が入らない（推定。未確定事項 4）
- **LACP / bond0 / Ethernet Segment はやめる。** TRex は自分のポートを af_packet で直接使うので、カーネルの bond の下には置けない。leaf 側は `ethernet-1/3.0` を mac-vrf（VNI 100）に素の subinterface として付ける（LAG と `ethernet-segment` の設定を消す）。**IS-IS の underlay と iBGP EVPN-VXLAN の overlay はそのまま残す**（Grafana / Splunk の `bgp_down` と `isis_down` のアラート、`fail-bgp` / `fail-main` の障害注入、graph の `bgp_session` / `isis_adjacency` / `evpn_instance` 層が全部これに乗っている）。消えるのは `ethernet_segment` 層だけ
- イメージ: `trexcisco/trex:2.41`（Docker Hub、amd64）。`ops/lab-common.sh` に `TREX_TAG=2.41` と upstream を足し、`mirror_lab_images` で ECR `<prefix>-lab-trex` へ写す。ECR のリポジトリは `IaC/terraform/aws-managed/base/ecr/main.tf` の `lab_repositories` に `trex` を足し、デバッグ用の EC2（`IaC/cloudformation/lab-debug.yaml`）にも `<prefix>-debug-lab-trex` を足す（IAM の Resource も）。`docker/compose/lab.sh` と `.env.example` には `TREX_IMAGE`（手元は Docker Hub から直接）
- containerlab のノード: `kind: linux`、`image: __TREX_IMAGE__`、`group: trex`、`privileged: true`（af_packet と DPDK の初期化に要る）。`exec` は `eth1`〜`eth4` の `ip link set up` だけ。**TRex 本体はトポロジを上げても起動しない**（`lab trex start` で `t-rex-64 -i`（対話 = stateless サーバ）を起動する。コンテナの中の `/etc/trex_cfg.yaml` は `lab trex start` が `eth1`〜`eth4` を列挙して書く）。負荷試験を「やる」と決めるまで CPU を取らせない
- 撃つ準備（このサイクルで用意するが、実行しない）。置き場所は `app/containerlab/trex/`
  - `trex_cfg.yaml.in`: ポート 4 本（af_packet。hugepages 無し）
  - `stl/udp_trap.py` と `stl/udp_syslog.py`: TRex の stateless（STL）のプロファイル。宛先 IP / ポートと pps を引数に取る。中身は SR Linux が出す linkDown の trap（`tests/` の既存のサンプルが無ければ `app/telegraf/` の trap の形）と syslog（RFC 5424、`local7`）
  - `kafka_load.sh`: `metrics` / `gnmi` のトピック向けに `kafka-producer-perf-test` を回す（lab の EC2 で `apache/kafka` のイメージから。MSK は IAM 認証なので `aws-msk-iam-auth` の jar と `client.properties` を要する。**ここまでは書くが、動かして確かめるのは負荷試験のとき**）
  - `README.md`: どこへ撃つか（Telegraf の NLB の 1162/udp と 5140/udp）、どう測るか（MSK の `BytesInPerSec` / コンシューマラグ、Spark のバッチの遅れ、Lambda のスロットル、Grafana / Splunk のアラートの遅れ）、`m6i.2xlarge` に上げる手順（`instance_type` の tfvars）
- **負荷の経路は未確定（未確定事項 3）。** TRex のポート 4 本は leaf の mac-vrf（L2 だけ。IRB 無し）に入るので、そこから NLB へは届かない。候補は (a) `eth0`（管理ネット。`lab forward` の iptables で VPC へ出る）を 5 本目のポートとして af_packet で使う、(b) containerlab の `host` endpoint で 5 本目の veth を EC2 の netns に出す。どちらも実機で確かめていないので、このサイクルでは README に両案を書くに留める

### 3. x86 の EC2

| ファイル | いま | あと |
|---|---|---|
| `IaC/terraform/aws-managed/pipeline/lab/variables.tf` | `instance_type` 既定 `t4g.xlarge`、validation は t4g の 3 つ、AMI `al2023-ami-kernel-default-arm64` | 既定 `m6i.xlarge`、validation `["m6i.xlarge", "m6i.2xlarge", "c6i.2xlarge", "t3.xlarge", "t3.2xlarge"]`（メッセージの「arm64」を「x86_64」に）、AMI `al2023-ami-kernel-default-x86_64` |
| `IaC/cloudformation/lab-debug.yaml` | 同上 + `AllowedValues` | 同じ値に揃える（`tests/test_lab_debug.py` が照合する。正規表現 `"(t4g\.\w+)"` は `"([a-z0-9]+\.\w+)"` に） |
| `app/containerlab/setup.sh` / `ops/lab-common.sh` / lab の `outputs.tf` / `variables.tf` の説明 | `containerlab_<v>_linux_arm64.rpm` | `_linux_amd64.rpm` |
| `ops/lab-common.sh` `mirror_image` | `docker pull --platform linux/arm64` 固定 | 第 3 引数 `platform`（既定 `linux/arm64`）。`mirror_lab_images` は `linux/amd64` を渡す。`ops/up.sh` / `oss/ops/up.sh` の redis / kafka-ui の呼び出しは変えない（Fargate は arm64 のまま） |
| `ops/lab-common.sh` `build_telegraf` | `--platform linux/arm64` 固定 | 第 2 引数 `platform`（既定 `linux/arm64`）。`ops/lab-debug.sh` は `linux/amd64` を渡す（デバッグ用の EC2 で Telegraf が同じホストに乗るため）。stream（Fargate）の呼び出しは arm64 のまま |
| `ops/up.sh` :654-659 の buildx の platform 検査 | `linux/arm64` だけ | 変えない（lab のイメージは pull して写すだけで buildx を使わない） |
| 費用の定数 | `ops/up.sh` :539 / :566 の lab = 17（t4g.xlarge 0.173 $/h）、`ops/lab-debug.sh` :14 「約 $0.17/h」 | 25（m6i.xlarge 0.248 $/h。東京、Pricing API 2026-10-08）、「約 $0.25/h」 |
| `terraform.tfvars.example`、`docs/architecture/resources/lab-ec2.md`、`docs/data-stores.md` 8、`docs/setup.md`、`docs/architecture/resources/ecr.md`、`README.md`、`docker/compose/README.md` | t4g / arm64 / 「揃える」 | m6i / x86_64 / 「同じホストの中だけ揃える」 |

`docs/data-stores.md` の 8 は次の趣旨に書き換える: 「アーキテクチャは全体で揃えない。arm64 が必須: AgentCore Runtime のエージェント。x86 が必須: Splunk（Fargate の x86）と TRex（lab の EC2）。Fargate の他のサービスは安い arm64。同じホストの中は揃える（lab の EC2 は SR Linux / TRex / Telegraf が x86）。`--platform` はイメージごとに指定する」

### 4. 静的データと、名前を固定で持っている所

`app/containerlab/gen_lab.py` を打ち直したら、`docs/pipeline.md` の「lab を変える」（:396-417）の手順で `app/agentcore/data/layers.json` を生成し直し、`devices.yaml` と `topology.json` を `python3 app/containerlab/lab_topology.py app/containerlab` の出力から手で直す（ここが唯一のシード。コメントの機器数 / 役割の凡例も直す）。名前と台数を固定で持っている所（Explore で洗い出した。実装時に `grep -rn 'leafsw\|dc1-leaf-\|dc1-host\|wan-upstream\|bond0\|6 台\|8 containers'` で取り漏れを確かめる）:

- `app/containerlab/lab.sh`: `UP_VM` / `ACC_VM` → `TREX=dc1-trex-01`。`BGP_NODE=dc1-a-leaf-01`。`fail-main` / `heal-main` は `dc1-a-leaf-01 ethernet-1/1`。`failover` の経路は `dc1-a-leaf-01 → dc1-s-leaf-01（10.255.1.1）`（spine の next-hop の IP は変わらない）。`vm_ping` と `check` の bond0 の節は消し、代わりに「各 leaf の `ethernet-1/3` の oper-state と、`dc1-trex-01` の `eth1`〜`eth4` が up」を出す。`trap-test` は `$TREX` の netns で `snmptrap`（送り元 203.0.113.101）。`up` の `modprobe bonding` を消す。ヘッダ :9 の「SR Linux 6 = leafsw 2 + spine 2 + leaf 2, VM 2」。新サブコマンド `trex start|stop|status`
- `app/containerlab/setup.sh`: bonding モジュール、rpm 名
- `app/temporal/rules.py` :43 / :77 / :170、`app/agentcore/evidence.py` :114 / :186 / :193 / :203、`proposals.py` :174、`topology.py` :486-529、`tools/tools.json` :30-218、`app/spark/snmp_sinks.py` :306、`app/splunk/netops_alerts/bin/netops_sns.py` :61、`app/dashboard/app.py` :35 / :58-59（`lag（VM - Leaf の LACP）` の選択肢を消す）、`app/dashboard/topology_view.py` :22 / :80 / :123 / :132、`app/nautobot/netops/nb_map.py` :6 / :30-35、`docs/nautobot.md` :138
- `ops/up.sh` :883 `LAB_NODES` のコメント「8。SR Linux 6 + VM 2」→ 7（SR Linux 6 + TRex 1。`grep -cE '^ *kind: (nokia_srlinux|linux)$'` の値も 7 になる）、`oss/ops/up.sh` :298 / :340-345、`IaC/cloudformation/lab-debug.yaml` :2 / :58 の説明、lab の `locals.tf` ヘッダ、`variables.tf` :33
- `docker/compose/lab.sh`（`TREX_IMAGE` を渡す）、`docker/compose/README.md` :14 / :28 / :30、`.env.example`
- docs: `docs/architecture/resources/lab-ec2.md`、`telegraf.md`、`collection.md`、`pipeline.md` :10 / :12 / :29-33 / :88 / :396-417、`deploy.md` :25、`setup.md` :22 / :31 / :34 / :74-78、`troubleshooting.md` :27、`README.md` :4 / :50 / :56、`data-stores.md` :148-154 / :161-167 / :264、`docs/architecture/resources/ecr.md` :35 / :64-71 / :80、`alert-comparison.md`
- テスト（期待値を新しい lab に合わせる）: `tests/test_sync.py` :100-160（gnmi 6 のまま、layers の頂点 / 辺、BGP の対 `{"a-leaf", "s-leaf"}` × spine、ESI は 0、gen_lab の出力集合 = `{"splab.clab.yml.in"} | {srlinux/<6 台>.cli}`）、`test_stream.py` :82-99（`.cli` 6 本、`nokia_srlinux` 6、`linux` 1 で bond0 は無し）、`test_app.py`、`test_graph.py`（golden の `neptune_cypher.json` も）、`test_workflow.py` :187-192 / :736 / :884 / :1229-1269、`test_oss_ops.py` :101 / :904 / :1058-1061 / :1197、`test_oss.py` :492、`test_nautobot.py` :69-71、`tests/check_splunk_image.py`、`test_lab_debug.py` :69-75 / :106（リポジトリ 4 つ）/ :199-215 / :363-367、`test_local_compose.py` :86-90 / :321-353 / :412

## 変更対象ファイル

- `app/containerlab/gen_lab.py`、`splab.clab.yml.in`、`srlinux/*.cli`（6 本を生成し直す。`dc1-leafsw-*.cli` / `dc1-leaf-*.cli` は消す）、`lab_topology.py`、`lab.sh`、`setup.sh`、新規 `trex/`（`trex_cfg.yaml.in`、`stl/udp_trap.py`、`stl/udp_syslog.py`、`kafka_load.sh`、`README.md`）
- `app/agentcore/data/devices.yaml`、`topology.json`、`layers.json`
- 上の 4 で列挙した app / ops / IaC / docs / tests
- `ops/lab-common.sh`、`ops/up.sh`、`oss/ops/up.sh`、`ops/lab-debug.sh`、`IaC/terraform/aws-managed/pipeline/lab/*`、`IaC/terraform/aws-managed/base/ecr/main.tf`（OSS 側は symlink なので付いて来る。`IaC/terraform/oss/base/ecr` に自前の `main.tf` があれば同じく）、`IaC/cloudformation/lab-debug.yaml`
- `docs/cycles/BACKLOG.md` は PM が直す（エンジニアは触らない）

## 再利用するもの

- `gen_lab.py` の `plan()` / `srl_config()` / `clab_template()` の骨格。IS-IS / BGP / mac-vrf の設定はそのまま（LAG / ES の節だけ消す）
- `lab_topology.py` の `build()` のリンク種別の判定（`roles & VM_ROLES` → `l2`、`"spine" in roles` → `fabric`）。`lag` の分岐は残す（データが無いだけ）
- `ops/lab-common.sh` の `mirror_image` / `mirror_lab_images` / `upload_lab` / `ecr_has`
- `lab.sh` の `srl` / `x` / `snmp_if` / `forward` / `hint`
- `docs/pipeline.md` :396-417 の「lab を変える」の手順と「変えたとき」の表

## 実装ステップ

1. `gen_lab.py` を直す（役割名、TRex ノード、LAG / ES の削除、ヘッダの図）→ `uv run python app/containerlab/gen_lab.py --leaves 2 --spines 2` で `splab.clab.yml.in` と `srlinux/*.cli` を生成し直す。古い `.cli` 6 本を `git rm`
2. `lab_topology.py`（`split_name`、`VM_ROLES`、docstring）→ `uv run python app/containerlab/lab_topology.py app/containerlab --layers > app/agentcore/data/layers.json`。`devices.yaml` / `topology.json` を出力に合わせて手で直す
3. `lab.sh` / `setup.sh` / `trex/` を書く
4. x86（表 3 の全部）と ECR の `trex` リポジトリ、`lab-common.sh` の `TREX_TAG` / `mirror_image` / `build_telegraf` の platform 引数
5. 名前を固定で持つ app / docs / tests を直す（上の 4 の一覧 + grep）
6. `bash ops/check.sh` を通す。`terraform validate`。セルフレビュー（`/robust` は PM が回す）

commit は「lab の生成（gen_lab / lab_topology / 静的データ）」「lab.sh / setup.sh / trex/」「x86 と ECR」「名前の参照と docs と tests」の 4 つ程度に分ける。

## 検証方法

期待出力まで書く。AWS には立てない（このサイクルの実機確認は、他のサイクルとまとめて PM が 1 回やる）。

1. `uv run python app/containerlab/gen_lab.py --leaves 2 --spines 2 --out /tmp/x && diff -r /tmp/x app/containerlab` で差分が無い（`tests/test_sync.py` の gen_lab の照合が同じことを見る）
2. `python3 app/containerlab/lab_topology.py app/containerlab | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["devices"]), len(d["links"]), sorted(x["role"] for x in d["devices"]))'` が `7 12 ['a-leaf', 'a-leaf', 's-leaf', 's-leaf', 'spine', 'spine', 'trex']`、links の `kind` が fabric 8 + l2 4（lag 0）
3. `python3 app/containerlab/lab_topology.py app/containerlab --gnmi-targets | grep -c :57400` が 6
4. `--layers` の頂点が `ip_interface 22 / isis_adjacency 16 / bgp_session 16 / evpn_instance 4 / ethernet_segment 0`（いまは ES 4 で合計 62 → 58）。辺はいまの 84 から ES に付く 6 本が減る（実測して `test_sync.py` の数字にする）
5. `grep -cE '^ *kind: (nokia_srlinux|linux)$' app/containerlab/splab.clab.yml.in` が 7。`grep -c bond0 app/containerlab/splab.clab.yml.in` が 0。`grep -l 'ethernet-segment\|lag' app/containerlab/srlinux/*.cli` が空
6. `grep -rn 'leafsw\|dc1-leaf-0\|dc1-host\|wan-upstream\|bond0\|t4g\|linux_arm64.rpm' app ops oss IaC docs tests docker README.md --include='*' | grep -v 'docs/cycles/'` が空（BACKLOG と過去サイクルの design / review は直さない）
7. `uv sync --group dev --group web && bash ops/check.sh` が `すべて通過`（いまの 325 本から、足した分だけ増える。失敗 0）
8. `terraform -chdir=IaC/terraform/aws-managed/pipeline/lab validate` と `.../base/ecr validate`、`terraform fmt -check -recursive IaC/` が通る。`tests/test_lab_debug.py` の CFn と TF の既定値の照合が通る（`InstanceType` 既定 `m6i.xlarge`、`ImageId` 既定 `…x86_64`、`AllowedValues` = TF の validation の list）
9. `bash -n` と shellcheck が `app/containerlab/lab.sh`、`setup.sh`、`trex/kafka_load.sh`、`ops/lab-common.sh` で通る
10. `python3 -c 'import ast; ast.parse(open("app/containerlab/trex/stl/udp_trap.py").read())'`（TRex の API は手元に無いので構文だけ。`import` は関数の中で遅延）

## 未確定事項とリスク

1. **a-leaf / s-leaf の意味をユーザーに確かめていない**（access / server と読んでいる）。写像（WAN 側 → s-leaf、DC 側 → a-leaf）が逆でも、名前と IP の対応が入れ替わるだけで構成は同じ。PM が報告のときに確認する
2. **TRex 2.41（2018）が SR Linux 26.7.2 の隣で動くか、`privileged: true` + af_packet で hugepages 無しに起動するかは未確認。** containerlab の `linux` kind で `privileged` が通ること（containerlab は linux kind に `privileged` を受ける、までは docs で確認済み）。起動の確かめは AWS の実機確認のときに `lab trex start`（このサイクルの合格条件には入れない。負荷試験はやらないので、動かなくても今の lab の機能は損なわれない）
3. **負荷の経路（TRex → NLB）は未確定**（設計方針 2 の末尾）。README に両案。負荷試験を「やる」と決めたサイクルで決める
4. **メモリ。** SR Linux 6 台 + TRex 1 台で 11〜13 GB（推定）。`m6i.xlarge` は 16 GB。TRex が hugepages を要求すると起動しない。足りなければ `m6i.2xlarge`（32 GB、0.496 $/h）に上げる
5. **`lab check` から VM 同士の ping が消える。** L2 の疎通（EVPN の MAC 学習）を確かめる手段が「各 leaf の `ethernet-1/3` up」だけになる。TRex のポート同士は同じコンテナにあるので ping で L2 を通せない（Linux が内部で折り返す）。`lab trex start` のあと TRex のポートから ARP を出し、leaf の `bridge-table` に MAC が 4 つ載るのを見る手順を README に書く（自動の `check` には入れない）
6. **ECR のリポジトリが増える（`<prefix>-lab-trex`、`<prefix>-debug-lab-trex`）。** `base/ecr` の state を持つチェックアウト（verify-oss の worktree と main）で次の `up.sh` が `apply` で足す。`down.sh` は ECR を空にして消すので追加の片付けは要らない
7. **費用。** lab の EC2 が 0.173 → 0.248 $/h（+43%）。1 回の実機確認（2〜3 時間）で +0.2 $ 程度
8. **他のサイクルとの衝突。** 010（Kafbat UI を Web の EC2 に同居）が `lab.sh` に `graph` / `graph-stop` を足す。先に入った方に合わせてマージする（PM が順番を見る）。008 / 009 も `lab.sh` の案内文を触る（BACKLOG 47 / 48）
