# lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/037-syslog-nlb-ingress（origin/main bbfe6ea から）。commit は実装 8659abc と、セルフレビューの docs の直しとこの build.md を入れる commit。AWS には触っていない。

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/security_groups.tf`（OSS 版はシンボリックリンクで同じ）: lab → telegraf_dialout_nlb の 5140/udp の行から `only = "egress"` を外し、`why` を `syslog forwarded for the switches, or logger on the lab EC2` に。コメントを「NetFlow / sFlow / syslog は両側（…NetFlow は ops/netflow_send.py、syslog は logger）」に。ほかの行・空行は動かしていない
- `tests/test_analytics.py`: `EXPECTED_FLOWS` の 5140 の tuple の only を `""` に、134 行目のコメントに syslog を足した
- `docs/troubleshooting.md`: 133 行目の表の行、「届かなかったわけ」の 1（037 で両側にした、hcl を直した後の形に）と 2（NLB の IP へ直接送るなら DNAT は要らない）、「見分け方」の VPC フローログ、「正しい送り方」に 2 つ目（EC2 から NLB の IP へ直接）と期待・切り分け
- `docs/faq-fukuda-nwc-poc.md`: 266 行目を「037 までは送信だけだった」に、正しい送り方を 2 つに、末尾の参照の文言
- `docs/architecture/core.md`: SG の表に `lab | telegraf_dialout_nlb | 2055/udp、6343/udp、5140/udp` の行
- `docs/architecture/resources/vpc-perimeter.md`: 表の括弧に syslog、原則の下に例外の 1 文
- `docs/architecture/resources/lab-ec2.md`: 表の括弧と、表の下に syslog を直接送る 1 行

### 設計からの逸脱

- `security_groups.tf`: 設計は 113 行目（5140）と 114 行目（コメント）をその場で直す指定だったが、コメントが 5140 の行にもかかるよう 2 行の順を入れ替えた（コメント → 5140 → 2055 → 6343）。ほかの行は動かしていない。
- 検証 2 の期待 `grep -c 'only = "egress"'` = 1 は、56 行目の原則のコメントに同じ文字列があるので実際は 2。データ行は 162 の 1 行だけ（下の出力）。設計の数え違い。
- 検証 5 のコマンドは、`/<prefix>` の手書きと既定のリージョンが無いシェルを避けるため、`. /etc/*-lab.env` で `AWS_REGION` と `PARAM_PREFIX` を読み、`--region "$AWS_REGION" --name "$PARAM_PREFIX/telegraf-address"` にした（`lab.sh forward` と同じ読み方。env は `pipeline/lab/templates/lab_user_data.sh.tftpl:22` が書く）。
- 検証 5 の期待「Athena で `logs` を `appname = 'acl-probe'` で引く」は、Athena に `logs` という表も `appname` 列も無い（表は `raw_telemetry`、`appname` は `tags_json` の中）ので、docs には「Kafbat UI でトピック `logs`。analytics を立てていれば `raw_telemetry` の `topic = 'logs'` の `tags_json`」と書いた。
- 設計の変更対象に無い `docs/troubleshooting.md:133`（表の行）と `:437`（「VPC Flow Logs … 作っていない」）を直した。前者は「直接送ると SG と DNAT に乗らない」が 037 後は誤り、後者は `base/core/flow_logs.tf:51` に `aws_flow_log.vpc` が条件なしであるので誤り。
- 未確定事項 2: SSM `/<prefix>/telegraf-address` は NLB のサブネット a のプライベート IP（`pipeline/stream/outputs.tf:33` の description）。
- 未確定事項 3: `tests/test_oss.py` に 5140 / `egress` の期待は無い（`grep -c -e 'egress' -e '5140' tests/test_oss.py` は 0）。`oss.tf:80-81` は `oss_replaced` の行だけ外す。

### 検証

1. 直す前の失敗（SG を直し、`EXPECTED_FLOWS` を直す前の `tests/test_analytics.py` で）:

```
AssertionError: 通信の表は決めた流れだけ（多い: [('lab', 'telegraf_dialout_nlb', 'udp', 5140, 5140, '')] 足りない: [('lab', 'telegraf_dialout_nlb', 'udp', 5140, 5140, 'egress')]）
```

   直した後、最後の編集のあとの `./ops/check.sh`（素の `python3 -m pytest` は pytest が無い。`tests/README.md` のとおり全部は `ops/check.sh`）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/core  OK
（ほか 19 ルートも OK）
== 3. スクリプトの構文
bash -n: 29 本
構文エラーなし
== 4. 模擬テスト
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 110 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 206 / 失敗 0
通過 66 / 失敗 0
通過 114 / 失敗 0
通過 104 / 失敗 0
通過 333 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

   （549 が `test_analytics`。途中の Traceback は各テストが模擬の失敗を起こしている分）

2. `grep -c 'only = "egress"'` / `'only = "ingress"'`:

```
2
1
56:  # only = "egress" / "ingress" は片側だけを書く行。…（コメント）
112:      { from = "lab", to = "telegraf_dialout_nlb", protocol = "udp", port = 162, only = "egress", why = "SNMP traps forwarded for the switches" },
```

3. `cd IaC/terraform/aws-managed/base/core && terraform init -backend=false && terraform validate`:

```
Success! The configuration is valid.
```

4. `grep -n '送信だけ' docs/troubleshooting.md docs/faq-fukuda-nwc-poc.md`: 3 行で、どれも過去の文脈。

```
docs/troubleshooting.md:133: …2026-10-09 は lab の EC2 から NLB へ直接送り、NLB の SG（`lab` からは送信だけだった）で落ちた見込み。…
docs/troubleshooting.md:419: …NLB の 5140 の行は `only = "egress"`（送信だけ）で、NLB 側に受ける規則が無かった。…
docs/faq-fukuda-nwc-poc.md:266: …lab の EC2 の SG からの行は 037 までは送信だけだった。…
```

5. AWS では未実行（設計どおり）。手順は `docs/troubleshooting.md` の「正しい送り方」の 2 つ目。QUEUE の動作確認で打つ。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので切り替えられない）。反対弁護人: opus / effort xhigh（文脈を渡し、読み取り専用。返ったあと `git status --porcelain -uall` は自分の docs の変更 2 本だけで、増えたファイルは無し）
- 指摘と片付け
  - Should fix [runtime] `docs/troubleshooting.md:458`・`docs/faq-fukuda-nwc-poc.md:278`: 設計の `aws ssm get-parameter --name "/<prefix>/…"` は region を渡さず、lab の EC2 は `AWS_REGION` を `/etc/<prefix>-lab.env` にしか持たない（`lab.sh:309-310` も `--region "$AWS_REGION"` を付ける）。シェルに既定のリージョンが無ければ失敗する。→ 直した（`. /etc/*-lab.env` と `--region`）。AWS で打っていないので、失敗するかは読んだだけ
  - Should fix [correctness/docs]（反対弁護人）`docs/troubleshooting.md:437`・`:464`: 「VPC Flow Logs（作っていない）」は誤り。`base/core/flow_logs.tf:51` に条件なしの `aws_flow_log.vpc`、`troubleshooting.md:71-74` に REJECT のクエリがある（ファイルを読んで再現）。動作確認で入らなかったとき唯一 SG の落ちを示せる手段を外させる。→ 直した。同じ誤りが `design.md:73`（未確定事項 1）にあるが、設計は PM の文書なので触っていない（PM へ）
  - Nit [docs]（反対弁護人）`docs/troubleshooting.md:463`: 「Athena で `logs` を `appname = …`」は打てない（`pipeline/analytics/variables.tf:55` の表は `raw_telemetry`、`syslog-ng.conf.in:57` で appname は `tags.appname`）。→ 自分の足した行なので直した。差分の外の `troubleshooting.md:372`（「S3 Tables の `logs`」）と `:435`（`appname = '1'`）は残した
  - Nit [保守性]（反対弁護人）`pipeline/stream/telegraf.tf:16`・`:20`、`security_groups.tf:106-107`: NLB は管理ネットワークの CIDR から受ける、とだけ書くコメント。動作は変わらない。→ 範囲外（telegraf.tf）と、並行する 036 と同じファイルの別の行なので直さず、最終報告に回す
  - Nit [docs]（反対弁護人）`docs/pipeline.md:106`、`docs/collection.md:32`、`docs/deploy.md:95`: EC2 から直接送る試し方が NetFlow だけ。誤りではない。→ 直さず最終報告に回す
  - Nit [docs]（反対弁護人）`docs/architecture/core.md:89-90`: 原則の箇条に、vpc-perimeter.md が書いた例外が無い（表の 82 行目にはある）。→ 直さず最終報告に回す
- 「問題なし」とした観点と根拠
  - テストが退行を縛るか: SG に `only = "egress"` を戻して `python3 tests/test_analytics.py` → `AssertionError: 通信の表は決めた流れだけ（多い: [(…5140, 5140, 'egress')] 足りない: [(…5140, 5140, '')]）`。戻して通過 549
  - 既存ルールの作り直し: `sg_rules` の鍵は `from-to-protocol-port`（`security_groups.tf:125`）で `only` を含まない。egress の description（why）の変更は、terraform-provider-aws の `vpc_security_group_ingress_rule.go` で `description` に RequiresReplace が無く Update が `ModifySecurityGroupRules` を呼ぶ（ソースを読んだだけ。plan は打っていない）ので in-place
  - 開けすぎ: 足すのは lab の SG を参照した NLB の 5140/udp の受信 1 本だけ。CIDR の行は増えない（diff を読んだだけ）
  - OSS 版: `ops/check.sh` の 2 で `IaC/terraform/oss/base/core OK`、`test_oss` 通過
