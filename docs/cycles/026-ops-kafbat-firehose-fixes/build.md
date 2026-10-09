# up.sh の片付けと Kafbat UI と Firehose の直しを片付ける（026）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/026-ops-kafbat-firehose-fixes（origin/main 1dc9645 から）。1 件 1 commit（A 4a5f02a / B 91e09c5 / C f82f8f9 / D 782b271）。AWS には触っていない（C と D は **AWS では未確認**）。

### 変更ファイル

- A
  - `ops/up.sh`: 手順 10 の `rm -f "$TF_AWS_CONFIG"` の 1 行を、コメント + `trap - EXIT` + `on_exit` にした（`ops/oss/up.sh` と同じ形）
  - `tests/test_oss_ops.py:1050-1053`: check 1 つ（200 → 201）
- B
  - `tests/test_oss_ops.py:911-929`: `FIXED_SECRET_SH` と INT / TERM の check 2 つ（201 → 203）。`ops/up-common.sh` は直していない（直すところは見つからなかった）
- C
  - `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl`: `SuccessExitStatus=75`、`:31-32` と `:133` のコメント
  - `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf:7-8`: コメント
  - `docs/troubleshooting.md:281-283, 304`、`docs/pipeline.md:367-368`、`docs/architecture/resources/web-ec2.md:26`
  - `tests/test_stream.py:550-561`: 行の一覧と `SuccessExitStatus` の findall、check の名前（108 のまま）
- D
  - `IaC/terraform/aws-managed/pipeline/analytics/history.tf:118-125, 156-157`: `time_sleep.alert_firehose_iam`（30s）と Firehose の `depends_on`
  - `IaC/terraform/aws-managed/pipeline/analytics/versions.tf`: `hashicorp/time ~> 0.13`
  - `IaC/terraform/aws-managed/pipeline/analytics/.terraform.lock.hcl`: `agent/.terraform.lock.hcl` の time 0.14.2 の block を写した（+21 行だけ）
  - `tests/test_analytics.py:426-434`: check 1 つ（519 → 520）

### 設計との違い

1. **B: 偽の在庫から `/x-nwc-oss/gnmic/gnmi-password` を消してから打つ。**
   `OSS_MANAGED_PARAMS`（`tests/test_oss_ops.py:447`）に入っているので、そのままだと `ensure_fixed_secret` が「もうある」で `put-parameter` を打たず、割り込みが起きない。初回（パラメータが無い）の状態を作るため（`:921`）。
2. **設計の行番号がずれている。**
   設計の `tests/test_oss_ops.py:1026-1030`（OSS 版の on_exit）は今 `:1046-1049`。`:1157` は「`ops/up.sh` の手順の並び」と書いてあるが、実際は `ops/oss/up.sh` の並びの check（今 `:1179-1180`）。`ops/up.sh` の並びは A の check（`:1053`）が新たに見る。
3. **テストは `--group dev --group web` で打った。**
   設計の検証方法は `--group dev` だけ。PM の指示の形に合わせた。

### 赤（直す前に落ちることを見た）

A（`ops/up.sh` を直す前）:

```
Traceback (most recent call last):
  File ".../tests/test_oss_ops.py", line 1032, in <module>
    check("ops/up.sh も手順 10 の exec の前に trap を外して同じ on_exit を 1 回呼ぶ（TF_AWS_CONFIG だけを手で消さない。NAUTOBOT_CTX なども消える。cycle 026）",
  ...
AssertionError: ops/up.sh も手順 10 の exec の前に trap を外して同じ on_exit を 1 回呼ぶ（TF_AWS_CONFIG だけを手で消さない。NAUTOBOT_CTX なども消える。cycle 026）
```

B（`_on_exit` を組み込まずに打った。テストには入れていない）:

```
RED INT rc= -2 put-parameter= 1 in_ssm= False left= ['nwc-secret.WIQY7u']
RED TERM rc= -15 put-parameter= 1 in_ssm= False left= ['nwc-secret.6CiuP1']
```

C（tftpl を直す前）:

```
AssertionError: Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127.0.0.1:8082 に出す（stream は base/core より後に作られる）。パラメータが無い（標準エラーに (ParameterNotFound)）と 75 で終わって起こし直さず（RestartPreventExitStatus。cycle 014）、それ以外で読めないと 69 で終わって 30 秒ごとに起こし直す。75 は成功の扱いにして failed に数えない（SuccessExitStatus。degraded にしない。cycle 026）
```

D（history.tf・versions.tf・lock を直す前）:

```
Traceback (most recent call last):
  File ".../tests/test_analytics.py", line 429, in <module>
    check("Firehose はロールとポリシーの反映を time_sleep.alert_firehose_iam（30 秒以上）で待ってから作り、analytics の versions と lock に hashicorp/time がある（cycle 026）",
  ...
AssertionError: Firehose はロールとポリシーの反映を time_sleep.alert_firehose_iam（30 秒以上）で待ってから作り、analytics の versions と lock に hashicorp/time がある（cycle 026）
```

### 検証（D の commit 782b271 のあと。設計の「検証方法」の 1〜8）

1〜3, 8（`bash ops/check.sh` の `4.` の中の 16 本。テストのコードは D のあと変えていない）:

```
ok Firehose はロールとポリシーの反映を time_sleep.alert_firehose_iam（30 秒以上）で待ってから作り、analytics の versions と lock に hashicorp/time がある（cycle 026）
ok ensure_fixed_secret: put-parameter の最中に SIGINT で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す。cycle 026）
ok ensure_fixed_secret: put-parameter の最中に SIGTERM で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す。cycle 026）
ok ops/up.sh も手順 10 の exec の前に trap を外して同じ on_exit を 1 回呼ぶ（TF_AWS_CONFIG だけを手で消さない。NAUTOBOT_CTX なども消える。cycle 026）
ok Kafbat UI は … 75 は成功の扱いにして failed に数えない（SuccessExitStatus。degraded にしない。cycle 026）
  （test_agentcore）通過 161 / 失敗 0
  （test_alerts）通過 168 / 失敗 0
  （test_analytics）通過 520 / 失敗 0
  （test_collectors）通過 79 / 失敗 0
  （test_dashboard_config）通過 3 / 失敗 0
  （test_graph）通過 78 / 失敗 0
  （test_kb_index）通過 7 / 失敗 0
  （test_lab_debug）通過 110 / 失敗 0
  （test_local_compose）通過 138 / 失敗 0
  （test_nautobot）68 項目すべて通過
  （test_oss）通過 174 / 失敗 0
  （test_oss_ops）通過 203 / 失敗 0
  （test_oss_roll）通過 66 / 失敗 0
  （test_stream）通過 108 / 失敗 0
  （test_sync）通過 103 / 失敗 0
  （test_workflow）通過 327 / 失敗 0
```

単独でも打った（`uv run --group dev --group web python tests/<file>.py`）: test_oss_ops `通過 203 / 失敗 0`、test_stream `通過 108 / 失敗 0`、test_analytics `通過 520 / 失敗 0`。

4（`bash ops/check.sh`。rc=0）:

```
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
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
  （上の 16 本）
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

5（OSS 版の analytics を lock を書き換えない形で init。そのあと両方のルートで validate）:

```
$ terraform -chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly -input=false -no-color
- Reusing previous version of hashicorp/aws from the dependency lock file
- Reusing previous version of hashicorp/time from the dependency lock file
- Using previously-installed hashicorp/aws v6.64.0
- Using previously-installed hashicorp/time v0.14.2
Terraform has been successfully initialized!
$ terraform -chdir=IaC/terraform/oss/pipeline/analytics validate -no-color
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics validate -no-color
Success! The configuration is valid.
$ git status --porcelain -uall      # 空（lock は書き換わらない）
```

aws-managed 側は `-lockfile=readonly` を付けずに init した。そのあとも lock の差分は写した time の block（+21 行）だけ（設計のリスク 5）。

6, 7:

```
$ grep -c "^SuccessExitStatus=75$" IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl
1
$ grep -n "failed のまま" IaC/terraform/aws-managed docs/pipeline.md docs/architecture/resources/web-ec2.md -r; echo "rc=$?"
rc=1
```

### 未確認

- **C は AWS では未確認**（設計のリスク 1）。75 で `inactive (dead)` になって `degraded` にならないことは、systemd の文書から推しただけ。
- **D は AWS では未確認**（設計のリスク 3）。30 秒で足りるかは分からない。
- **D の plan は未実行**（設計のリスク 4。AWS に触らない約束のため）。既にある Firehose が作り直されないことは、読んだだけ（Firehose の属性は time_sleep を参照しないので、`depends_on` が変わるだけ）。

### セルフレビュー

自分は opus-5.5 / effort high（xhigh に切り替える手段が無かった）。反対弁護人は opus（`Agent` general-purpose、読み取り専用、文脈あり）。反対弁護人の後の `git status --porcelain -uall` は空。

**退行を注入して、足した check が落ちることを見た**（scratchpad の `mut026.py`。毎回元に戻し、`git status` は空）:

```
[B: ensure_fixed_secret が on_exit の知らない変数に一時ファイルを持つ] → AssertionError: ensure_fixed_secret: put-parameter の最中に SIGINT で止まっても…（cycle 026）
[D1: Firehose の depends_on をポリシーに戻す（time_sleep は残す）] → AssertionError: Firehose はロールとポリシーの反映を time_sleep.alert_firehose_iam…（cycle 026）
[D2: create_duration を 10s にする] → 同じ check で AssertionError
[D3: time_sleep の depends_on からロールを外す] → 同じ check で AssertionError
[O: OSS 版の on_exit が SECRET_INPUT を消さない] rc=1 → AssertionError: ops/oss/up.sh の EXIT の trap は on_exit 関数で、TF_AWS_CONFIG・NAUTOBOT_CTX・ROLL_PLAN・SECRET_INPUT の 4 つを消し…（cycle 023）
```

**問題なしとした観点**

- 設計整合性
  - 設計の方針 A〜D を 1 行ずつ diff と突き合わせた。違いは上の「設計との違い」の 3 つだけ。
- A の on_exit が exec に要る物を消さないか
  - 消さない。`TF_AWS_CONFIG` は `tf()` の中で `env` で渡すだけで export していない。exec する `aws ssm start-session` は読まない。
  - `GRAPH_PID` は `ops/up.sh:985` で空になるので、手順 10 で `wait` は走らない。
  - いずれも読んだだけ。
- C の 69 の扱い
  - 変わらない。ユニットの行は `SuccessExitStatus=75` を足しただけ。
  - kafka-ui の `is-failed` / `reset-failed` / `ActiveState` に頼るところは ops / app / IaC に無い（grep で 0 件）。
- 秘密の扱い
  - B は `SECRET_INPUT` の一時ファイルを `on_exit` が消すことを INT / TERM で確かめた（上の check）。
  - テストの値は `test-value` だけ。

**指摘**

1. **Should fix（直さない）**
   - `[correctness]` `IaC/terraform/aws-managed/pipeline/analytics/history.tf:121-125`
   - `time_sleep.alert_firehose_iam` に `triggers` が無い。state が残ったまま `OWNER` / `PROJECT` を変えて apply すると、ロールと Firehose は名前が変わって作り直される。このとき time_sleep は作り直されず待たない。state はルートごとのローカルファイル（`ops/up-common.sh:22`）で、接頭辞ごとに分かれていない。
   - いつもの流れでは起きない。`down.sh` が analytics ごと time_sleep も消すので、次の `up.sh` でまた待つ（反対弁護人が `ops/down.sh:82` を読んで確認。plan は打てないので実測なし）。
   - 直さない理由: 設計がブロックの形を指定している。直すなら `triggers = { role_id = aws_iam_role.alert_firehose.unique_id }`（反対弁護人の案）。PM の判断に回す。
2. **Nit（直さない）**
   - `[runtime]` `ops/up.sh:1352-1353`（`on_exit` の `:162`）
   - 手順 10 の `on_exit` は trap の中ではなく `set -euo pipefail` の下で普通に呼ばれる。`NAUTOBOT_CTX` の中に消せない物があると `rm -rf` が落ち、ポートフォワードの前で止まる（反対弁護人の指摘 4）。
   - 再現した（scratchpad の `onexit_sete.sh`。`on_exit` を別の bash で `set -euo pipefail` の下で呼んだ）:
     ```
     case1 普通の状態: rc=0 / reached-exec
     case2 全部空: rc=0 / reached-exec
     case3 指す先が無い: rc=0 / reached-exec
     case4 NAUTOBOT_CTX に消せない物: rc=1 / rm: …/ro/f: Permission denied …
     ```
   - Nit にした根拠: `NAUTOBOT_CTX` は `mktemp -d` のあと `cp -R app/nautobot/.` などで埋めるだけ（`ops/up-common.sh:264`）。写し元に書けない物は無い（`find app/nautobot app/agentcore/graph.py app/agentcore/toolkit.py ! -perm -u+w -print | wc -l` が 0）。
   - 止まっても `rm` のエラーが出るだけで、資源は立っている。OSS 版（`ops/oss/up.sh:656-658`）も設計どおり同じ形。
3. **Nit（直さない）**
   - `[test]` `tests/test_analytics.py:427`
   - `create_duration` の正規表現は `"<数>s"` しか受けない。`"1m"` に延ばすと条件を満たしていても落ちる。延ばすときに一緒に直せば足りる。
4. **Nit（取り消し）**
   - `[test]` `tests/test_oss_ops.py:912-929`
   - 反対弁護人の指摘 5: B の検査が差し込むのはマネージド版の `on_exit` で、OSS 版の `on_exit` が `SECRET_INPUT` を消さなくなっても通る。
   - 再現しなかった。OSS 版の `on_exit` から `SECRET_INPUT` の行を消すと、既存の `:1046-1049` の check（cycle 023）が落ちる（上の注入 O）。
5. **Nit（直さない）**
   - `[ops]` `web_user_data.sh.tftpl:89`
   - 75 で止まると `systemctl --failed` にも `degraded` にも出なくなる。意図した変更で、`docs/troubleshooting.md:304` に書いてある。
6. **Nit（直さない）**
   - `[不確かさ]` `history.tf:120-122`
   - 30 秒に根拠が無い（前例の `agent/kb.tf` は 60 秒）。コメントに「推定。足りなければ延ばす」とあり、設計のリスク 3 と同じ。
7. **範囲外（直さない）**
   - `docs/architecture/resources/firehose.md` に IAM の反映待ち（time_sleep）の説明が無い。設計の変更対象ファイルに無いので触っていない。BACKLOG の候補。

**ジンテーゼ**（反対弁護人の前と比べて変わったこと）

- A は「片付けが揃っただけ」ではない。
  - 手順 10 の失敗の仕方が変わった。
  - 前は `NAUTOBOT_CTX` が黙って残った。今は消せなければ止まってエラーを出す。
  - 起きる条件は今の写し元では成り立たない（上の実測）。
- 指摘 1 の確信度を下げた。
  - 「ロールが作り直されると待たない」は成り立つ。
  - ただし、起きるのは state を残したまま接頭辞を変えたときだけ。いつもの down → up では起きない。
  - 実測していないので Should fix のまま残す。

## Round 2

実装モデル: opus-5.5 / effort: high

cold review（Round 1）の Should fix 1 件を、PM の判断で直した（commit d04562b）。Nit は直していない（判断は `review.md`）。AWS には触っていない。

### 直したこと

- `IaC/terraform/aws-managed/pipeline/analytics/history.tf:118-129`
  - `time_sleep.alert_firehose_iam` に `triggers = { role = aws_iam_role.alert_firehose.unique_id }` を足し、コメントに理由を 1 行足した。
  - state を残したまま接頭辞（`OWNER` / `PROJECT`）を変えてロールが作り直されたときも、待ちを作り直して Firehose の前に待つ。
- `tests/test_analytics.py:435-437`
  - D の check の隣に、`triggers` がロールの `unique_id` を参照していることの check を 1 つ足した（520 → 521）。
- `docs/cycles/026-ops-kafbat-firehose-fixes/design.md` の D の 3
  - `triggers` を持つことと理由を、PM の指示として 1 行足した。ほかの部分は変えていない。

### 赤→緑

赤（check を先に足し、`history.tf` に `triggers` が無い状態で打った）:

```
$ uv run --frozen --group dev --group web python tests/test_analytics.py
  ...
  File ".../tests/test_analytics.py", line 19, in check
    assert cond, name
AssertionError: time_sleep.alert_firehose_iam は triggers にロールの unique_id を持ち、state を残したままロールが作り直されたときも待ち直す（cycle 026 の cold review）
```

緑（`triggers` を足したあと）:

```
ok time_sleep.alert_firehose_iam は triggers にロールの unique_id を持ち、state を残したままロールが作り直されたときも待ち直す（cycle 026 の cold review）
通過 521 / 失敗 0
```

### 検証

両方の analytics のルートを、lock を書き換えない形で init して validate した（`.terraform` は `TF_DATA_DIR` で scratchpad に置いた）:

```
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics init -backend=false -lockfile=readonly -input=false
- Reusing previous version of hashicorp/time from the dependency lock file
- Installed hashicorp/time v0.14.2 (signed by HashiCorp)
Terraform has been successfully initialized!
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics validate
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly -input=false
- Reusing previous version of hashicorp/time from the dependency lock file
- Installed hashicorp/time v0.14.2 (signed by HashiCorp)
Terraform has been successfully initialized!
$ terraform -chdir=IaC/terraform/oss/pipeline/analytics validate
Success! The configuration is valid.
```

`bash ops/check.sh`（rc=0）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
  （18 行すべて OK）
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
  （test_agentcore）通過 161 / 失敗 0
  （test_alerts）通過 168 / 失敗 0
  （test_analytics）通過 521 / 失敗 0
  （test_collectors）通過 79 / 失敗 0
  （test_dashboard_config）通過 3 / 失敗 0
  （test_graph）通過 78 / 失敗 0
  （test_kb_index）通過 7 / 失敗 0
  （test_lab_debug）通過 110 / 失敗 0
  （test_local_compose）通過 138 / 失敗 0
  （test_nautobot）68 項目すべて通過
  （test_oss）通過 174 / 失敗 0
  （test_oss_ops）通過 203 / 失敗 0
  （test_oss_roll）通過 66 / 失敗 0
  （test_stream）通過 108 / 失敗 0
  （test_sync）通過 103 / 失敗 0
  （test_workflow）通過 327 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
```

### 未確認

- **AWS では未確認**（Round 1 と同じ）。`triggers` を足したことで、既にある環境では次の apply で `time_sleep` が 1 回作り直されて 30 秒待つ（Firehose は `time_sleep` を参照しないので作り直さない）。plan は打っていない。
- 接頭辞を変えてロールが作り直される場面で待つことは、Terraform の `time_sleep` の `triggers` の振る舞い（値が変わると置き換え）から推しただけで、実測していない。
