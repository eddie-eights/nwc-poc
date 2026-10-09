# マネージド版の 2 回目の AWS 検証（2026-10-09）。1 回目は IAM のガードで止まり、ガードの解消後の 3 回目で stream まで立てた記録

AWS アカウント 493116771193、ap-northeast-1、`OWNER=efukuda`（接頭辞 `efukuda-nwc-poc`）。時刻は UTC（日本時間は 2026-10-09 の朝）。

ここに書いた「通った」は、出力を見て確かめたものだけ。見ていないものは「未確認」、動かなかったものは「失敗」と書く。

## まとめ

- 3 回目（ガードの解消後）で stream まで立てた。analytics は起こしていない（トークンの予算）。結果は「3 回目」の節。
  - A: SCRAM と IAM が有効、SCRAM のブローカーは 2 つ。ACL の前も `flows` / `logs` に書けた（未確定 8）。ACL のあとは未確認。
  - B: gnmic は 1/1、`telegraf-dialin` は無い。`metrics` の実物はテストの入力と同じ形。`gnmi` のトピックはできていない。
  - C: stream の前は 75 で `failed`（`degraded`）、stream のあとの再起動で `active (running)`。
  - D: TRex 2.41 は m6i.xlarge の af_packet で動いた。
  - E: 未確認。
  - 不具合 1 つ（syslog-ng の portMappings）を `collectors.tf` で直した。
- 以下は 1 回目（IAM のガードで止まった回）のまとめ。

- `ops/up.sh` を 2 回打って、2 回とも途中で落ちた。確かめる項目（A〜E）は E の一部しか見られていない。
  - 1 回目（22:22:47Z〜22:26:06Z、rc=1）: 手元のディスクが一杯で、Docker Desktop の containerd が I/O エラーを出した。イメージを作る手順 2 で落ちた。
  - 2 回目（22:31:09Z〜22:36:58Z、rc=1）: 手順 3 の base/core の apply で、Web の EC2 の `RunInstances` が IAM のポリシー `netops-always-on-guard` に拒まれた。
- ガードが許す EC2 の型は 6 つ（t3.micro / t3.small / t4g.micro / t4g.small / t4g.large / t4g.xlarge）。マネージド版が使う型は、2 つとも入っていない。
  - Web の EC2: t4g.medium（010 で t4g.small から上げた）
  - lab の EC2: m6i.xlarge（011 で x86 にした）
  - 2 つとも 2026-10-08 に変えた。そのあとマネージド版を AWS で立てたのは今回が初めて。
- ガードは IAM のセキュリティ設定で、このリポジトリの外にある。変更していない。許可リストに足すか、IaC 側で型を合わせるかはユーザーの判断待ち。
- 途中まで作った base/core は `ops/down.sh` で消した（rc=0、3 分 23 秒）。VPC ごと消え、残ったのは `KEEP_ECR=1` の ECR だけ。サービスごとに API で確かめた（下の「片付け」）。
- 時間課金のリソースが立っていたのは、2 回目の up の apply から down.sh の終わりまでの約 10 分（VPC エンドポイント 11 本など）。
- A〜E の残りは、ガードの件が決まってから up（`SKIP_ANALYTICS=1`）からやり直す。

## 事前確認（up.sh の前）

### plan

- 打ったのは `scratchpad/v1009/run_plan*.sh`。base/ecr と base/core の plan だけを取り、apply はしていない。
- base/ecr: `Plan: 4 to add, 0 to change, 0 to destroy.`
  - `aws_ecr_repository.lab["trex"]`、`aws_ecr_repository.pipeline["gnmic"]` / `["goflow2"]` / `["syslog-ng"]` の 4 つ。どれも作るだけで、`-/+` は無い。
- base/core: `Plan: 160 to add, 0 to change, 1 to destroy.`
  - `-/+` が 1 つあった。SG `efukuda-nwc-poc-runtime` の作り直し。

```
  # aws_security_group.workload["runtime"] must be replaced
-/+ resource "aws_security_group" "workload" {
      ~ description            = "AgentCore Runtime ENIs (terraform/agent)" -> "AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)" # forces replacement
      ~ id                     = "sg-0bf4c62efe1fc4973" -> (known after apply)
        name                   = "efukuda-nwc-poc-runtime"
```

- SG の `description` は 007 のパス変更（48683dd）で変わり、`forces replacement` になる。
  - state に SG が残っていると、次の up で 1 回作り直しになる。
  - 依頼の決まりで、base/core の `-/+` はそこで止めて PM に聞いた。この SG は前回の down.sh の消し残りで、ENI 0・ルール 0 だったので、PM の判断で作り直しを進めた。
- SG ルール（`aws_vpc_security_group_*_rule`）は 100 件とも `will be created` だった。state にルールが残っていないため。
  - そのため、017 の `security_groups.tf:113-114` の `description` が in-place の更新になるか（`-/+` でないか）は、この環境では未確認。

### 手元のディスク

- 1 回目の up の前の空きは 9.5 GiB だった。
- 1 回目の up は、イメージを作る手順 2 で、Docker Desktop の containerd が書き込みに失敗して落ちた。

```
failed commit on ref "layer-sha256:41b7e4d0bd88...": commit failed: failed to perform sync: sync /var/lib/desktop-containerd/daemon/io.containerd.content.v1.content/ingest/0b7c42e0.../data: input/output error
Error response from daemon: No such image: trexcisco/trex:2.41
write /var/lib/desktop-containerd/daemon/io.containerd.metadata.v1.bolt/meta.db: input/output error
```

- 直し方。
  - この worktree の `.terraform/providers/` の、中身が同じファイル 24 個を APFS のクローン（`cp -c`）に置き換えた（`scratchpad/v1009/clone_providers.py`。置き換えたあと `cmp` で中身が同じことを確かめた）。
  - Docker Desktop を再起動した。
  - PM がほかの worktree の `.terraform/providers/` も同じやり方でクローンにした。空きは 563 GiB になった。tfstate と `.build` には触っていない。
- 1 回目の up の手順 1 で、ECR のリポジトリ 4 つ（上の plan のもの）は作れていた。

## 立てようとしたもの（ops/up.sh の 2 回目）

- 開始は 2026-10-08T22:31:09Z、終了は 22:36:58Z（rc=1）。
- 環境変数は `OWNER=efukuda`、`WORKFLOW=0`、`SKIP_ANALYTICS=1`、`NO_DASHBOARD_PORTFORWARD=1`。ほかは deploy.env のまま。
  - `WORKFLOW=1` は `SKIP_*` と一緒に使えないので、1 回目の up だけ 0 にした。
- 手順 1（base/ecr）: `Apply complete! Resources: 0 added, 0 changed, 0 destroyed.`
- 手順 2（イメージ）: ECR に無いタグだけ作って push した。

| リポジトリ | タグ |
|---|---|
| gnmic | 0.49.0-e4cbb4b2bdec |
| nautobot | 3.2.6-cee19b6d5a0b |
| syslog-ng | 4.29.0-90f5978c4447 |
| telegraf | 1.40.1-d60f691f023a |
| goflow2 / lab-trex / redis | push した（タグはログの `naming to` 行に出ていない） |

- 手順 3（base/core）: 151 件を作ったところで、Web の EC2（`aws_instance.web`、`web.tf:82`）の作成が失敗した。

```
Error: creating EC2 Instance: operation error EC2: RunInstances, https response error StatusCode: 403, RequestID: ac170754-8296-4f5d-b9ab-501e018d694a, api error UnauthorizedOperation: You are not authorized to perform this operation. User: arn:aws:iam::493116771193:user/eito-private-operator is not authorized to perform: ec2:RunInstances on resource: arn:aws:ec2:ap-northeast-1:493116771193:instance/* with an explicit deny in an identity-based policy: arn:aws:iam::493116771193:policy/netops-always-on-guard. Encoded authorization failure message: (略)
```

### ガードの中身

IAM の `GetPolicy` と `GetPolicyVersion` で読んだだけ。変更していない。

- ポリシー `netops-always-on-guard`。既定の版は v3（2026-09-26T18:08:04Z）。グループ `netops-always-on-operators` に付いている。
  - 説明: 「PoC の上限。高額インスタンスと高額サービス、コスト監視の削除を拒否する。MSK は allow_msk、Neptune は allow_neptune で開閉」
  - タグ: `Lab=always-on`、`Project=netops`、`ManagedBy=terraform`。このリポジトリの Terraform ではない。
- EC2 を止めている文。

```json
{
  "Sid": "DenyLargeInstanceTypes",
  "Effect": "Deny",
  "Action": "ec2:RunInstances",
  "Resource": "arn:aws:ec2:*:*:instance/*",
  "Condition": {"StringNotEquals": {"ec2:InstanceType": ["t3.micro", "t3.small", "t4g.micro", "t4g.small", "t4g.large", "t4g.xlarge"]}}
}
```

- マネージド版の型と、ガードの許可リストの突き合わせ。

| EC2 | 型（既定値） | 決めた場所 | ガード |
|---|---|---|---|
| Web | t4g.medium | `base/core/variables.tf:80`（010、3c3e6c0） | 拒まれる（今回の失敗） |
| lab | m6i.xlarge | `pipeline/lab/variables.tf:35`（011、6d3e554） | 拒まれる（手順 6 まで進めば同じ失敗になる） |

- 2026-10-08 の OSS 版の検証が通ったのは、Web が t4g.small、lab が t4g.xlarge で、どちらも許可リストにあったから。
- 2026-10-08 のマネージド版の検証（`20261008-managed-aws.md`）は、010 と 011 より前の型で立てていた。
- 通すための案。どれを採るかはユーザーが決める。
  - ガードの許可リストに t4g.medium と m6i.xlarge を足す（D の TRex も m6i.xlarge が要る。BACKLOG 103 は m6i.xlarge だけを挙げていて、t4g.medium が抜けている）。
  - Web を許可リストにある t4g.large にする。ただし base/core の `instance_type` の validation は t4g.micro / small / medium だけなので、IaC の変更が要る。
  - lab は x86 が要る（TRex が amd64 だけ）。許可リストの x86 は t3.micro / t3.small だけで、足りない。

## 項目と結果

| 項目 | 結果 |
|---|---|
| A. 012 検証 3（SASL/SCRAM、ACL の前の認可） | 未確認。stream まで進まなかった |
| B. 013 検証 7/8（gnmic の event、AMP、障害注入） | 未確認。同上 |
| C. Kafbat UI の計測（016 の未計測 4 件、BACKLOG 85・87） | 未確認。Web の EC2 が立たなかった |
| D. TRex 2.41 の af_packet（BACKLOG 103） | 未確認。lab の m6i.xlarge もガードに拒まれる |
| E. 017: SG ルールの `description` が in-place | 未確認。state にルールが無く、100 件とも `will be created` だった |
| E. 017: SG `efukuda-nwc-poc-runtime` の `description` | `forces replacement`（上の plan の行）。PM の判断で作り直しを進めた |
| E. 017: Nautobot のイメージの作り直し | 通った。タグが `3.2.6-bd81350c5b56`（2026-10-07T18:41:48Z の push）から `3.2.6-cee19b6d5a0b`（2026-10-08T22:33:53Z の push）に変わった |
| E. 017: Spark / Neo4j のイメージの作り直し | 未確認。どちらも OSS 版だけのイメージで（017 の design.md:52、:127）、マネージド版の up.sh は作らない |

## 3 回目（ガードの解消後。エンジニア3 から引き継いだエンジニア2 が打った）

ガード `netops-always-on-guard` は v4 で t4g.medium と m6i.xlarge が足された（PM が確認済み）。そのあとで `OWNER=efukuda WORKFLOW=0 SKIP_ANALYTICS=1 NO_DASHBOARD_PORTFORWARD=1 ops/up.sh` を 2 回打った。analytics は起こしていない。

### plan（up の前）

- base/ecr は `No changes.`、base/core は `Plan: 156 to add, 0 to change, 0 to destroy.`。ECR と base/core に `-/+` は無かった。

### up の 1 本目（up2、01:36:50Z〜02:17:55Z、rc=1）

- base/core は 156 件できた。EC2 は 2 つとも立った。
  - Web: `i-08686500e71cfdebb`、t4g.medium、arm64（01:39:42Z に起動）
  - lab: `i-0cfd6e4b0b772b126`、m6i.xlarge、x86_64（01:47:18Z に起動）
- stream で MSK は `Creation complete after 30m0s`。SCRAM の secret の紐付け、gnmic / GoFlow2 / Telegraf の ECS のサービスもできた。
- syslog-ng の ECS のサービスだけ作れずに落ちた（下の「不具合」の 1）。手順 8 より後は走っていない。

### up の 2 本目（up3、02:20:23Z〜02:36:36Z、rc=0）

- `collectors.tf` を直してから打った。stream は `Apply complete! Resources: 2 added, 0 changed, 1 destroyed.`（syslog-ng のタスク定義の作り直しとサービス）。
- 7-2d で syslog-ng と GoFlow2 のサービスが安定した。nautobot（16 件）と graph もできて、最後まで通った。

### A. 012 検証 3（SASL/SCRAM、ACL の前の認可）

| 見たもの | 結果 |
|---|---|
| `DescribeClusterV2` の `ClientAuthentication.Sasl` | `{"Scram":{"Enabled":true},"Iam":{"Enabled":true}}`。Kafka は `4.1.x.kraft` |
| `ListScramSecrets` | `AmazonMSK_efukuda-nwc-poc-collectors-LPAUji` の 1 つ |
| `GetBootstrapBrokers` の SCRAM | `b-1:9096` と `b-2:9096` の **2 つ**（設計は 3 つと書いたが、ブローカーが 2 台なので 2 つで合っている） |
| scram を足す apply が in-place か | 未確認。前回の stream の state が無く、今回は新規に作った |
| 未確定 8（ACL の前の認可） | 認可のエラーは出ず、書けた。`allow.everyone.if.no.acl.found` が効いている（下） |
| ACL のあと（Spark driver の ACL の行、収集器のエラーが止まる、`UnderReplicatedPartitions`、`fail-main` の device_log、Splunk の `netops:flows`） | 未確認。analytics を起こしていない |

未確定 8 は次のとおり。lab の EC2 から 02:37Z と 02:40:11Z に送った。

- 送ったもの:
  - `logger -n <NLB> -P 5140 -d --rfc5424 acl-probe`（`logger rc=0`）
  - `ops/netflow_send.py <NLB>:2055`（`NetFlow v5 を 1 つ送った: …:2055/udp（10.0.0.1:12345 → 10.0.0.2:443 proto 6、10 パケット 8400 バイト）`）
- `/ecs/efukuda-nwc-poc-syslog-ng` と `/ecs/efukuda-nwc-poc-goflow2` に `authoriz` を含む行は 0。起動の行のあと何も出ていない。
- Kafbat UI の API で見たトピックは `flows` / `logs` / `metrics` の 3 つ（パーティションは各 2）。
  - `flows` には 02:37:28Z と 02:40:11Z の 2 件が入っていた（`"type":"NETFLOW_V5"`, `"src_addr":"10.0.0.1"`, `"dst_port":443`, `"bytes":8400`）。
  - `logs` には機器の syslog が入っていた（02:31:46Z、`dc1-a-leaf-02 sr_linux_mgr … memoryUsageHigh`、`"name":"device_log"`）。
  - `acl-probe` は `logs` に入っていなかった。原因は確かめていない（syslog-ng の受け口の設定は RFC3164 で、`--rfc5424` の行を捨てた可能性がある）。

### B. 013 検証 7/8（gnmic）

| 見たもの | 結果 |
|---|---|
| ECS のサービス | `efukuda-nwc-poc-gnmic` は desired 1 / running 1。`telegraf-dialin` は無い（クラスター `efukuda-nwc-poc-telegraf` のサービスは goflow2 / telegraf-dialout / gnmic / syslog-ng） |
| Kafbat UI の `metrics` | event が入っている（下） |
| Kafbat UI の `gnmi` | **トピックが無い**（`Topic not found`）。on-change の 3 つの購読（interface_state / bgp_neighbor / isis_interface）から 1 件も書かれていない。`auto.create.topics.enable=true` なので、gnmic が書けばできるはず。gnmic のログ 410 行に ERROR は 0、購読の名前を含む行も 0。原因は確かめていない |
| AMP、`fail-main` → `link_down` / `isis_down`、SNS、トポロジの DOWN、`heal-main` | 未確認。analytics を起こしていない |
| 未確定 2（heartbeat-interval）・3（IF 名がトポロジと一致） | 未確認（2 は gnmi の event が無い。3 は analytics の後に見る項目） |
| 未確定 6（`cpu[index=all]`） | `system` の event 13 件のうち 7 件に `cpu_index` のタグがあった |
| 未確定 7（ACL の前の on-change の初回値） | ACL の前でも `gnmi` に 1 件も無い。失われていると言えるが、ACL のせいではない（ACL の前も書けることは A で確かめた） |

`metrics` の実物（02:41Z の `EARLIEST` から 400 件）:

- 内訳: `interface_stats` の values あり 29、values 無し（tags だけ）358、`system` の values あり 13。
- values のキーは YANG のモジュールの接頭辞つきの絶対パス（`/srl_nokia-interfaces:interface/statistics/in-octets` など）。カウンターは**文字列**（`"0"`、`"4"`）。
- tags のキーは `interface_name`、`source`、`subscription-name`、`control_slot`、`cpu_index`。

```json
{"name": "interface_stats", "timestamp": 1791512362072293077, "tags": {"interface_name": "ethernet-1/1", "source": "203.0.113.12", "subscription-name": "interface_stats"}, "values": {"/srl_nokia-interfaces:interface/statistics/carrier-transitions": "0", "/srl_nokia-interfaces:interface/statistics/in-broadcast-packets": "4", "/srl_nokia-interfaces:interface/statistics/in-discarded-packets": "5", "…": "…"}}
{"name": "system", "timestamp": 1791512362092186339, "tags": {"control_slot": "A", "source": "203.0.113.21", "subscription-name": "system"}, "values": {"/srl_nokia-platform:platform/srl_nokia-platform-control:control/srl_nokia-platform-memory:memory/free": "3811790848", "…": "…"}}
{"name": "interface_stats", "timestamp": 1791513682493808462, "tags": {"interface_name": "ethernet-1/58", "source": "203.0.113.32", "subscription-name": "interface_stats"}}
```

- `tests/test_stream.py` の入力（`"/srl_nokia-interfaces:interface/oper-state": "down"`、tags は `interface_name` / `source` / `subscription-name`）と形は同じ。`app/spark/snmp_sinks.py` の `gnmic_message` は、接頭辞を落として最後の要素を取り、値を文字列のまま持ち、values の無い event を捨てる。実物はどれもこの読み替えで足りるので、テストの入力も読み替えも直していない。
- oper-state / admin-state の綴りは、`gnmi` に event が無いので見ていない。

### C. Kafbat UI

| 見たもの | 結果 |
|---|---|
| stream の前の `systemctl status`（02:08Z。01:45:47Z の再起動のあと） | `Active: failed (Result: exit-code)`、`(code=exited, status=75)`。journal は `/efukuda-nwc-poc/kafka-ui/image does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; …` → `Main process exited, code=exited, status=75/TEMPFAIL` → `Failed with result 'exit-code'`。`NRestarts=0`、`UnitFileState=enabled`。この起動で 2 回（01:45:57 と 01:46:18）とも 75 |
| 75 で止まっているときの `is-system-running` | `degraded`（BACKLOG 85） |
| stream のあと | up3 の 4-4 の再起動（起動 02:21:40）で `Started` が 02:21:48、Spring の起動の行が 02:22:18。`active (running)`、`NRestarts=0`、`is-system-running` は `running` |
| 8-3 からポートフォワードで開けるまでの秒数 | 未確認。`NO_DASHBOARD_PORTFORWARD=1` で打った。参考に、再起動から Spring の起動の行まで 38 秒 |
| AL2023 の `unmask --runtime`、reboot 後の `enable --now` の 69 の待ち（BACKLOG 87） | 未確認。69 を出す状況（SSM や docker の一時的な失敗）を作っていない |

API はセッションマネージャー経由で Web の EC2 の中から叩いた。admin のパスワードは `/run/efukuda-nwc-poc-kafka-ui.env` からシェルの変数に読み、出力に出していない。

### D. TRex 2.41 の af_packet（BACKLOG 103）

m6i.xlarge の lab で動いた。

- `lab trex start`: `TRex を起こした（ポート eth1 eth2 eth3 eth4、設定 /etc/trex_cfg.yaml、出力 /var/log/trex.log、プロファイル /opt/nwc-trex/stl）`（rc=0）
- 25 秒後の `lab trex status`: `set driver name net_af_packet`、`Number of ports found: 4`、`zmq publisher at: tcp://*:4500`
- `lab trex stop`: rc=0

### E. 017

- SG ルールの `description` が in-place か: 未確認。前回の down.sh で state が空になり、今回の plan では SG ルールが全部 `will be created` だった。
- Spark / Neo4j のイメージ: 未確認（OSS 版だけのイメージ。前回の記録のとおり）。

## 片付け（ops/down.sh）

- 開始は 22:39:47Z、終了は 22:43:10Z（rc=0）。かかった時間は 3 分 23 秒。
- 環境変数は `OWNER=efukuda` だけ。ほかは up.sh と同じ deploy.env（`KEEP_ECR=1`）。
- base/core: `Destroy complete! Resources: 155 destroyed.`
  - 2 回目の up で作った 151 件と、前回の down.sh の消し残り 4 件（VPC とサブネット 3 つ）。SG `efukuda-nwc-poc-runtime` は up の apply で作り直していたので、151 件に入る。
  - VPC エンドポイントは 11 本とも消えた（いちばん遅い ssm が 2 分 42 秒）。
  - `Runtime の ENI の確認: VPC=vpc-02ec7950cb98632a0 残り=なし`。ENI が無かったので、今回は VPC ごと消えた。
- workflow / analytics / nautobot / graph / stream / lab / agent は state にリソースが無く、何も消さなかった。
- Runtime のロググループ、SSM のパラメータ、MSK の SCRAM の secret、KMS の鍵は「無い」と出た。手順 7 まで進んでいないので、どれも作っていない。
- 最後のタグ API の一覧は「残り 186 件」と出した。タグ API の一覧は当てにならないので、下でサービスごとに API で確かめた。

### 消えたことの確認（22:43:51Z と 22:44:28Z、AWS MCP の `run_script`。`scratchpad/v1009/gone.py`）

| サービス | 結果 |
|---|---|
| VPC（`efukuda-nwc-poc-vpc`） | 無し |
| VPC エンドポイント（Project タグ） | 無し |
| フローログ（Project タグ） | 無し |
| NAT ゲートウェイ / Elastic IP | 無し |
| EC2 のインスタンス（terminated 以外） | 無し |
| NLB / Cloud Map の名前空間 | 無し |
| ECS クラスター | 無し |
| MSK | 無し |
| EMR Serverless のアプリ（TERMINATED 以外） | 無し |
| AMP（ワークスペース） | 無し |
| Neptune Analytics（グラフ） | 無し |
| Lambda | 無し |
| S3 のバケット | 無し |
| CloudWatch のロググループ | 無し |
| IAM のロール / インスタンスプロファイル（`efukuda-nwc-poc` で始まるもの） | 無し |
| SSM のパラメータ（`/efukuda-nwc-poc/`） | 無し |
| Secrets Manager（`AmazonMSK_efukuda-nwc-poc`、削除予約を含む） | 無し |
| KMS のエイリアス（`alias/efukuda-nwc-poc`） | 無し（今回は鍵を作っていないので、PendingDeletion の鍵も無い） |
| ECR | 残った（15 リポジトリ）。`KEEP_ECR=1` による |

- ECR は前回の 11 に、今回足した 4 つ（gnmic / goflow2 / lab-trex / syslog-ng）が加わった。

### 3 回目の片付け（ops/down.sh、02:42:53Z〜03:10:03Z、rc=0）

- 環境変数は `OWNER=efukuda` だけ（deploy.env の `KEEP_ECR=1`）。27 分 10 秒。
- 消えたかは AWS MCP でサービスごとの API を見た（03:11Z）。
  - EC2 のインスタンス、EIP、NLB、ECS のクラスター、MSK、VPC エンドポイント、NAT、S3、`efukuda-nwc-poc` を含むロググループ、MSK の SCRAM の secret、EMR Serverless、AMP、Lambda、IAM のロール、AgentCore の Runtime は、どれも 0。
  - ECR は 15 リポジトリが残った。`KEEP_ECR=1` による。
  - VPC の中の ENI が 1 つ残った（`eni-0d8dd804c19130a69`、InterfaceType `agentic_ai`、in-use）。AgentCore の Runtime を消したあと AWS 側が外すもので（down.sh の表示は最大 8 時間）、ENI に時間課金は無い。
  - MSK の SCRAM の KMS の鍵は、down.sh の仕様どおり 7 日の PendingDeletion になる（エイリアスは消えた）。

## 不具合

1. syslog-ng の ECS のサービスが作れない（3 回目の up の 1 本目）。
   - 出力: `InvalidParameterException: … Container port 5140 is used in more than one port mapping`（load balancer の付いたサービスで、同じ containerPort を udp と tcp の 2 行に書いていた）。
   - 直した: `IaC/terraform/aws-managed/pipeline/stream/collectors.tf` の syslog-ng の portMappings から tcp の行を消し、udp の 1 行にした。awsvpc では portMappings に無いポートも SG が通せば届くので、NLB の tcp のヘルスチェックは変わらない。`terraform fmt -check` は rc=0、`tests/test_collectors` は通過 79 / 失敗 0。up の 2 本目でサービスができて安定した。
2. `gnmi` のトピックができない（gnmic の on-change の購読が 1 件も書いていない）。原因は確かめていない（PM に判断を仰ぐ）。
3. `logger --rfc5424` で送った syslog が `logs` に入らない。原因は確かめていない（PM に判断を仰ぐ）。

## docs のずれ

- BACKLOG 103 は「always-on のガード `allowed_instance_types` に m6i.xlarge を足す」と書いている。Web の t4g.medium（010）も足す必要がある。BACKLOG は PM が直す。
