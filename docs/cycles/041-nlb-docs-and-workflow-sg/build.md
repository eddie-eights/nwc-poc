# NLB の受信と試し方の説明を揃え、workflow の SG の description を直す（041）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/041-nlb-docs-and-workflow-sg（98d5dcc から）。docs・Terraform のコメントと SG の description・テスト 1 つ。AWS と docker には触っていない。

### 変更ファイル

- `tests/test_workflow.py`: 末尾近くに check 1 つ（workflow の SG の description に `dev server` が無く `Temporal server, UI and worker` がある）。`tests/test_core.py` は無いので、`security_groups.tf` を読んでいる `test_workflow.py` の `_sg_tf` を使った
- `IaC/terraform/aws-managed/base/core/security_groups.tf`: 39 行目の workflow の description、117 行目のコメント
- `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf`: 16-17 行目の試し方の括弧、21-23 行目の「162 は受けない」
- `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf`: 「3 つ」→「4 つ」、NetFlow / sFlow の行（10-11 行目）、19 行目の「162 は受けない」
- `docs/troubleshooting.md`: 140 行目（pipeline.md の「gnmic と Telegraf に入る」の `sudo lab forward-status` の箇条書き）、452 行目（`DEVICE_MAP` を引かない）
- `docs/architecture/resources/vpc-perimeter.md`: 62 行目を 2 行に
- `docs/architecture/core.md`: 84 行目と 93 行目の言い回し
- `docs/pipeline.md`: 349-350 行目
- `docs/deploy.md`: 96 行目（SKIP_LAB の節）

### 設計からの逸脱

- `docs/architecture/core.md:93`（「162/udp の trap は lab の SG から受けない」）は設計の 5 か所に無いが、同じ言い回しの 6 か所目だったので揃えた（変更対象ファイルの中。検証 3 の「どの行にも 2 語がある」を満たすため）。
- 検証 3 の期待件数（5 件、vpc-perimeter.md を足して 6 件）は実測 9 件。差は `docs/pipeline.md:274` と `:379`（デバッグ用の EC2 は syslog を受けない。trap と無関係）と、上の core.md:93。trap / 162 の行に絞ると 7 件で、7 件とも「lab の SG からは受けない」と「送る側だけ」がある（下の出力）。
- `pipeline/stream/telegraf.tf:16` は括弧の頭の「lab の SR Linux は NetFlow を送れない。」を残し、その後ろを設計の文面にした。
- `docs/cml-sandbox.md:558` の「NLB の SG が `lab` の SG から受けない」は変更対象ファイルの外なので触っていない。

### 検証

1. 設計方針 6 の check。直す前（check を足しただけ）:

```
$ uv run --group dev --group web python tests/test_workflow.py 2>&1 | tail -4
  File ".../tests/test_workflow.py", line 14, in check
    assert cond, name
           ^^^^
AssertionError: workflow の SG の description に dev server が無く Temporal server, UI and worker がある（036 で temporalio/server + RDS にした。cycle 041）
```

直したあと: `通過 371 / 失敗 0`（下の check.sh の中）。テストファイルの総数は `ls tests/test_*.py | wc -l` で 16。

2. `./ops/check.sh`（最後の編集のあとに取り直した。rc=0）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate … OK の行 20（aws-managed 10 + oss 10）
== 3. スクリプトの構文
bash -n: 31 本
構文エラーなし
== 4. 模擬テスト
通過 168 / 失敗 0 通過 168 / 失敗 0 通過 549 / 失敗 0 通過 79 / 失敗 0 通過 3 / 失敗 0 通過 78 / 失敗 0 通過 7 / 失敗 0 通過 110 / 失敗 0 通過 145 / 失敗 0 68 項目すべて通過 通過 177 / 失敗 0 通過 207 / 失敗 0 通過 66 / 失敗 0 通過 114 / 失敗 0 通過 104 / 失敗 0 通過 371 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

3. grep:

```
$ grep -rn "dev server" IaC/ docs/ --exclude-dir=cycles | wc -l
       0
$ grep -n "受けない" <設計の 6 ファイル> | cut -c1-90
docs/architecture/core.md:84:| lab | telegraf_dialout_nlb | 2055/udp、6343/udp、5140/udp
docs/architecture/core.md:93:  例外は EC2 自身が試しに送る NetFlow / sFlow / sy
docs/pipeline.md:274:  - デバッグ用の EC2 は syslog を受けない（syslog-ng は
docs/pipeline.md:349:    trap は NLB で落ちる（NLB は trap（162/udp）を lab の 
docs/pipeline.md:379:  syslog は受けない（syslog-ng は stream と手元の compose 
docs/architecture/resources/vpc-perimeter.md:63:| lab | telegraf_dialout_nlb | 5140/udp、
IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:22:#                         受け…
IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:19:#               ops/netflow_send.py 
IaC/terraform/aws-managed/base/core/security_groups.tf:117:      # NLB は trap（162/udp…
受けない total=9
trap/162 の行=7
両方の語を含む行=7
trap/162 で両方の語が無い行:
(なし)
```

4. troubleshooting.md と deploy.md:

```
$ grep -n "DEVICE_MAP" docs/troubleshooting.md | cut -c1-40
171:    Splunk なら IP を `DEVICE_MAP
452:- `logs` には `sysName` が EC2 …
$ grep -n "DEVICE_MAP" docs/troubleshooting.md | grep -c "引かず"
1
$ grep -n "SKIP_LAB" -A6 docs/deploy.md | grep -cE '^[0-9]+-\s*(- )?lab の EC2 から'
0
（96 行目は「lab を作らないので、lab の EC2 から `logger` で送る試し方（…）も使えない。syslog を試すなら lab を作る。」）
$ grep -c "forward-status" docs/troubleshooting.md
1
$ grep -n "forward-status" docs/troubleshooting.md | grep -c "「gnmic と Telegraf に入る」"
1
$ grep -n "^## gnmic と Telegraf に入る" docs/pipeline.md
313:## gnmic と Telegraf に入る
```

5. `git diff --stat main -- . ':!docs/cycles'`（docs/cycles の差分は 98d5dcc の design.md と QUEUE.md）:

```
 IaC/terraform/aws-managed/base/core/security_groups.tf | 4 +-
 IaC/terraform/aws-managed/pipeline/lab/telegraf.tf     | 6 +-
 IaC/terraform/aws-managed/pipeline/stream/telegraf.tf  | 6 +-
 docs/architecture/core.md                              | 4 +-
 docs/architecture/resources/vpc-perimeter.md           | 3 +-
 docs/deploy.md                                         | 2 +-
 docs/pipeline.md                                       | 4 +-
 docs/troubleshooting.md                                | 4 +-
 tests/test_workflow.py                                 | 3 +
 9 files changed, 22 insertions(+), 15 deletions(-)
$ git diff --stat main -- app ops | wc -l
       0
```

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので切り替えられない）。反対弁護人: opus / effort xhigh（文脈を渡した。読み取り専用。返ったあと `git status --porcelain -uall` は自分の 10 ファイルだけ）
- Must fix: 0

Should fix（直していない。PM に報告）:

- S1 [事実の誤り・設計由来] `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:10-11`: 設計の文面どおり「届くのは ops/netflow_send.py で試しに送ったぶん」と書いたが、`ops/netflow_send.py` は NetFlow v5 だけ（`:1`、`:19` `DEFAULT_PORT = 2055`）で、lab の EC2 のホストから NLB の DNS 名へ直接送る（`:8`）。203.0.113.1 の DNAT は通らず、6343 の sFlow は何も届かない。「SR Linux は送れない」も sFlow には言い過ぎ（`netflow_send.py:3`「sFlow だけ。コンテナ版で出るかは未確認」）。確かめたもの: `sed -n 1,20p ops/netflow_send.py`
- S2 [文言の矛盾・既存] `docs/deploy.md:95`: SKIP_LAB の節で NetFlow / sFlow は「`ops/netflow_send.py` で送ったときだけ来る」とあるが、lab が無いと NLB は受けない（`security_groups.tf` の NLB の受信は `lab_mgmt` と `lab` だけ）。新しい 96 行目の「も使えない」と並ぶと、95 行目の手段は使えると読める。設計の範囲外（95 行目は変更対象外）
- S3 [運用・設計のリスク 1] `security_groups.tf:14` が「description は変えると作り直しになる（付いている ENI があると消えない）ので、変えるときは down してから」と書いている。041 より前に立てた環境が残っている PC で `ops/up.sh` を打つと、「タスクが一度止まる」ではなく SG の削除が `DependencyViolation` で止まる見込み（未確認。`aws_security_group.workload` に `create_before_destroy` があるかは反対弁護人の読み。`grep create_before_destroy security_groups.tf` は 0 件）。QUEUE 145 に足す文言は「041 より前の環境が残っているなら先に `ops/down.sh`」が合う
- S4 [既存の欠落] `docs/architecture/resources/vpc-perimeter.md` の通信の表に、workflow → nautobot_db 5432、workflow 自分宛ての 6933-6939 / 7233-7239、splunk ↔ splunk 8089 / 9887 / 9997 が無い（`core.md` にはある）。041 は 62-63 行だけを揃えた
- S5 [文言・設計由来] `docs/troubleshooting.md:452`: 「Spark の `with_sysname` は `DEVICE_MAP` を引かず」の `DEVICE_MAP` は Splunk のアラートアクションの環境変数で、Spark は `--device-map`（`app/spark/snmp_sinks.py:168`）。「Splunk の `coalesce('tags.sysName', …)` も同じ」の coalesce は保存検索 `nwc_gnmi` / `nwc_trap` / `nwc_trap_clear` にしかなく（`savedsearches.conf:50,94,132`）、どれも `logs` を読まない。中心の主張（`sysName` があれば機器名に置き換わらない）は正しい（`snmp_sinks.py:461-470`、`nsenter -n` は UTS を替えないので HOST は EC2 のホスト名）

Nit（直していない）:

- N1 `pipeline/stream/telegraf.tf:16-17`: NetFlow / sFlow の箇条の中に syslog の logger が入る（設計の文面）。リンク先「正しい送り方」の 1 つめは TRex の netns から DNAT を通す経路で、「EC2 のホストから NLB へ」は 2 つめだけ
- N2 統一文言の括弧の 2 文目「送り元が機器の管理 IP のままの DNAT だけを受ける」に主語が無く、直前の「lab の SG」が受けると読める（core.md:84、vpc-perimeter.md:63、pipeline.md:349。設計の文面）
- N3 `vpc-perimeter.md:63` のポートの並び 5140/2055/6343 が `core.md:84` の 2055/6343/5140 と違う（設計の文面どおり）
- N4 `security_groups.tf:27`、`:29` の telegraf_dialout / telegraf_dialout_nlb の description も古い（「traps, syslog and MDT」）。041 と同じ理屈で環境が無いうちに直せる。QUEUE 候補
- N5 `tests/test_workflow.py:1632-1634` の check は大文字小文字を区別するので「Dev Server」や「start-dev」に戻しても通る（反対弁護人が注入して確認）
- N6 `docs/cml-sandbox.md:558` の「NLB の SG が `lab` の SG から受けない」は統一文言の外（変更対象外）

問題なしとした観点:

- workflow の description の中身: `IaC/terraform/aws-managed/workflow/ecs.tf` のコンテナが temporal / ui / worker（反対弁護人が読んだ）
- テストが退行を縛る: 直す前に check が落ちた（検証 1 の出力）
- 正規表現の偽陽性・偽陰性: fmt の整列幅の変化では通り、`dev server` に戻すと落ちる。OSS 版は同じ `local.workload_security_groups` を使う（反対弁護人が注入して確認）
- troubleshooting.md:140 のリンク先: `pipeline.md:313` の見出しが実在し、その節の `forward-status` の箇条は 347 行目の 1 つ（grep）
- pipeline.md:350 の「`sysName` が機器の管理 IP（正常時）ではなく EC2 の IP」: RFC3164 の受け口で RFC5424 の行を受けると HOST が送り元の IP（`docs/troubleshooting.md:405-406`、読んだだけ）
- lab/telegraf.tf の NetFlow / sFlow の DNAT: `app/containerlab/lab.sh` の `for p in 2055 6343`（反対弁護人が読んだ）
- fmt / validate / 全テスト: 検証 2

ジンテーゼ: Must fix 0 の結論は変わらない。変わったのは、設計の文面をそのまま写した 2 か所（S1、S5）が事実とずれていることと、SG の作り直しのリスク（S3）が「タスクが一度止まる」より重い見込みであること。どちらも PM の判断（Round 2 で直すか QUEUE に回すか）に回す。
