# lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

2026-10-09 の AWS の動作確認（`docs/verification/20261009-aws-managed.md` の不具合 3）で、lab の EC2 から `logger -n <NLB の IP> -P 5140` で送った試験の syslog が S3 Tables の `logs` に 1 行も入らなかった。機器（SR Linux）からの syslog は入っていた。PR #61 の調査（`docs/troubleshooting.md` の「syslog の試験行が logs に入らない」）で、受け口の syslog-ng は形が合わない行も捨てないことを手元で確かめ、届いていなかったことが分かった。真因の見込みは NLB の SG で、`IaC/terraform/aws-managed/base/core/security_groups.tf` の通信の表 `sg_flows` が次のように片側だけになっている。

```hcl
{ from = "lab_mgmt", to = "telegraf_dialout_nlb", protocol = "udp", port = 5140, why = "syslog from the switches - DNAT on the lab EC2" },
{ from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 5140, only = "egress", why = "syslog forwarded for the switches" },
# NetFlow / sFlow は両側（lab の SR Linux は NetFlow を送れないので、試すときは lab の EC2 のホストが自分の IP から ops/netflow_send.py で送る）
{ from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 2055, why = "NetFlow - forwarded for the switches, or ops/netflow_send.py on the lab EC2" },
{ from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 6343, why = "sFlow - forwarded for the switches, or a test sender on the lab EC2" },
```

- 機器の syslog は lab の EC2 の DNAT（`lab.sh forward`、PREROUTING）を通るので、SG が見る送り元 IP は機器の管理 IP（203.0.113.0/24）のまま。だから NLB の受信は `lab_mgmt` の CIDR で書き、`lab` の SG からの行は送信だけ（`only = "egress"`）にしてあった（`security_groups.tf` 56-57 行目のコメントの原則）。
- lab の EC2 自身が出すパケット（`logger`）は PREROUTING を通らず、送り元は EC2 の IP。NLB の SG に `lab` の SG からの 5140/udp の受信が無いので落ちる。UDP なので `logger` は `rc=0` で終わり、気づけない。
- NetFlow の 2055 と sFlow の 6343 は `only` 無しで両側にあり、同じ EC2 からの `ops/netflow_send.py` は届いた（2026-10-09 の `flows` に 2 件）。
- 2026-10-10 のユーザー決定: AWS の動作確認はこのサイクルでやらず、`docs/cycles/QUEUE.md` の「残った修正をまとめて AWS で動作確認して直す」で一度に見る。

## 設計方針

1. **5140/udp の `lab` → `telegraf_dialout_nlb` の行から `only = "egress"` を外し、NetFlow / sFlow と同じ両側にする。** 送信ルールは今のまま、NLB の SG に `lab` の SG からの受信ルールが 1 本増えるだけ。`sg_rules` の鍵は `from-to-protocol-port` で `only` を含まないので、既存のルールは作り直されない。
2. **trap の 162/udp は片側のまま。** EC2 から直接 trap を送る手順は無く（`lab.sh trap-test` は TRex の netns から送って DNAT に乗せる）、要らない受信は開けない。
3. **docs は「送信だけだから届かない」と書いてある箇所を直し、EC2 から NLB の IP へ直接送る試し方を足す。** 原則（lab が転送する通信は片側を CIDR で書く）は変えず、「EC2 自身が試しに送る流れは両側」という例外が NetFlow / sFlow / syslog の 3 つだと分かるようにする。
4. **AWS では確かめない。** QUEUE の「残った修正をまとめて AWS で動作確認して直す」の行に見るものが既に書いてある（syslog の試験行が `logs` に入ること）。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `IaC/terraform/aws-managed/base/core/security_groups.tf` | 113 行目の 5140 の行から `only = "egress"` を外し、`why` を `syslog forwarded for the switches, or logger on the lab EC2` にする。114 行目のコメントを「NetFlow / sFlow / syslog は両側（lab の EC2 のホストが自分の IP から試しに送る。NetFlow は ops/netflow_send.py、syslog は logger）」に直す。56-57 行目の原則のコメントはそのまま |
| `tests/test_analytics.py` | 133 行目の `("lab", "telegraf_dialout_nlb", "udp", 5140, 5140, "egress")` を `""` にし、134 行目のコメントに syslog を足す（期待の数 `count("{ from = ")` は行数が変わらないのでそのまま） |
| `docs/troubleshooting.md` | 「届かなかったわけ（有力。AWS では確かめていない）」の 1（SG）を「037 で両側にした。AWS で確かめるのは QUEUE の動作確認のとき」に書き換え、hcl の引用を直した後の形にする。2（DNAT）は NLB の IP へ直接送るなら DNAT を通らなくてよい、と整理する。「正しい送り方」に、EC2 から NLB の IP へ直接送る形（下の検証 5 のコマンド）を 2 つ目の選択肢として足す。140 行目の表の行はそのまま |
| `docs/faq-fukuda-nwc-poc.md` | 266 行目の「lab の EC2 の SG からの行は送信だけ」を「037 までは送信だけだった（両側にした。AWS では未確認）」に直し、正しい送り方に EC2 から直接送る形を足す |
| `docs/architecture/core.md` | 81 行目の表の次に `lab → telegraf_dialout_nlb` の行（2055/udp、6343/udp、5140/udp。EC2 のホストが試しに送る分）を足す |
| `docs/architecture/resources/vpc-perimeter.md` | 62 行目の括弧を「NetFlow / sFlow / syslog は lab の EC2 から試しに送る分も」にする。98 行目の原則の下に「例外は EC2 自身が試しに送る NetFlow / sFlow / syslog。両側に書く」を 1 文足す |
| `docs/architecture/resources/lab-ec2.md` | 38 行目の括弧を「NetFlow と syslog の試し方は表の下」にし、表の下に syslog を直接送る 1 行を足す（既存の NetFlow の説明の隣） |
| `docs/cycles/037-syslog-nlb-ingress/build.md` | エンジニアが書く |

## 再利用するもの

- `security_groups.tf` の表の仕組み（`only` を外すだけで受信ルールが生える。`aws_vpc_security_group_ingress_rule.flow` の `for_each`）。
- NetFlow の 2055 の行と `ops/netflow_send.py` の説明が、そのまま syslog の手本。
- `docs/troubleshooting.md` の「正しい送り方」にある `logger … --rfc3164 -t acl-probe` の形。

## 実装ステップ

1. `security_groups.tf` の 113-114 行目を直す。
2. `tests/test_analytics.py` の 133-134 行目を直し、`python3 -m pytest tests/ -q` を通す。
3. `docs/troubleshooting.md` と `docs/faq-fukuda-nwc-poc.md` の該当節を直す。
4. `docs/architecture/core.md`、`resources/vpc-perimeter.md`、`resources/lab-ec2.md` を直す。
5. `build.md` を書く。

## 検証方法

1. `python3 -m pytest tests/ -q` が全部通る。`tests/test_analytics.py` の「通信の表は決めた流れだけ」が、直した表に対して通ること（`EXPECTED_FLOWS` を直す前は「足りない: `('lab','telegraf_dialout_nlb','udp',5140,5140,'')`」で落ちる）。
2. `grep -c 'only = "egress"' IaC/terraform/aws-managed/base/core/security_groups.tf` が `2`（162 の行と、56 行目の原則のコメント。表の行としては 162 だけ）。`grep -c 'only = "ingress"'` は `1`（gnmic の行）のまま。
3. `cd IaC/terraform/aws-managed/base/core && terraform init -backend=false && terraform validate` が `Success`。
4. `grep -n '送信だけ' docs/troubleshooting.md docs/faq-fukuda-nwc-poc.md` が、「037 までは」の文脈の行以外に無い。
5. AWS では確かめない。確かめるときの手順を `troubleshooting.md` に残す（QUEUE の動作確認で打つ）:

   ```bash
   nlb=$(aws ssm get-parameter --name "/<prefix>/telegraf-address" --query Parameter.Value --output text)
   logger -n "$nlb" -P 5140 -d --rfc3164 -t acl-probe "syslog test from the lab EC2"
   ```

   期待: Kafbat UI のトピック `logs` に `"appname":"acl-probe"` で `sysName` が lab の EC2 のホスト名の行が 1 件ある（Athena なら `raw_telemetry` の `topic = 'logs'` の行の `tags_json`。`logs` という表と `appname` の列は無い）。

## 未確定事項とリスク

1. **真因は AWS で確かめていない。** SG のほかに落とす要素が無いことは表と DNAT の仕組みから読んだだけ。QUEUE の動作確認で `logs` に入らなければ、次は VPC Flow Logs（`base/core/flow_logs.tf` にある。REJECT で宛先が NLB の IP、`dstPort` が 5140 の行）か `syslog-ng-ctl stats` で切り分ける。
2. **`/<prefix>/telegraf-address` の値が NLB の DNS 名か IP かを確かめていない。** `lab.sh forward` が DNAT の宛先に使うので IP のはずだが、DNS 名なら `logger -n` にそのまま渡せる（どちらでも動く）。
3. **OSS 版（`IaC/terraform/oss/`）は同じファイルへのシンボリックリンクなので、同時に直る。** `oss.tf` が表を差し替えるのは MSK の行だけで、この行には触らない（`tests/test_oss.py` の期待に 5140 の `only` が無いことを実装の最初に `grep` で確かめる）。
