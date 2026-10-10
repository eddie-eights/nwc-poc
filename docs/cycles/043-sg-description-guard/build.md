# 043 の実装記録（description を変えた SG が残っていれば up.sh を止め、telegraf の SG の説明を揃える）

## Round 1

実装モデル: opus-5.5 / effort: high

### 実装内容

- `ops/up-common.sh`: `has_resources()` の後に `SG_DESCRIPTION_ROOTS`（workflow:workflow / telegraf_dialout:pipeline/stream / telegraf_dialout_nlb:pipeline/stream）、`sg_description_in_code`（`security_groups.tf` から `^    <key> *= "…"$` の最初の 1 行）、`sg_description_in_state`（`tf base/core state show -no-color` の最初の `description = "…"`。state に無ければ 1）、`check_sg_descriptions`（コードに文言が無ければ die、state に無い・同じ・ルートの tfstate が無い・state list が空なら通す、それ以外は die）
- `ops/up.sh` / `ops/oss/up.sh`: 守りの塊（`if [ -f "$TF_DIR/base/core/terraform.tfstate" ]`）の末尾、telegraf_dialin の `fi` の後で `check_sg_descriptions` を呼ぶ
- `IaC/terraform/aws-managed/base/core/security_groups.tf`: `:24` のコメントを設計方針 4 の文言に、telegraf_dialout / telegraf_dialout_nlb の description を設計どおりに
- docs: `cml-sandbox.md:558`、`architecture/core.md`、`architecture/resources/vpc-perimeter.md`、`troubleshooting.md`（設計方針 5）
- テスト: `tests/test_workflow.py`（検証 1・2・4 の 10 check）、`tests/test_oss_ops.py`（検証 3 の 1 check）

### 変更ファイル

- IaC/terraform/aws-managed/base/core/security_groups.tf
- docs/architecture/core.md
- docs/architecture/resources/vpc-perimeter.md
- docs/cml-sandbox.md
- docs/troubleshooting.md
- ops/oss/up.sh
- ops/up-common.sh
- ops/up.sh
- tests/test_oss_ops.py
- tests/test_workflow.py

### 設計からの逸脱

- `tests/test_oss_ops.py` の `up.sh（通し）` の土台（REPO の写し）に `IaC/terraform/aws-managed/base/core/security_groups.tf` と OSS 版のシンボリックリンクを足した（設計の変更対象には「呼び出し位置の check」だけ）。
  - 原因: 写しに `security_groups.tf` が無く、`sg_description_in_code` の sed が rc=2（No such file）を返し、`set -euo pipefail` の `code=$(…)` で up.sh が sed のエラーのまま終わっていた（偽の terraform は SG の `state show` に rc=1 を返すので、写しがあれば比較は飛ばされて通る）。
  - 守り側は変えていない（コードに文言が無いときに die するのは設計方針 1 のとおり）。
- `sg_description_in_state` は設計の `head -n 1` ではなく sed の `{s//\1/p;q;}` で最初の 1 行を取る（パイプを 1 段減らしただけで結果は同じ。M9 で縛った）。

### 検証（最後の編集の後に取り直した出力）

1〜4. 足した check（`tests/test_workflow.py` / `tests/test_oss_ops.py` の該当行。一時ディレクトリのパスは略さず実出力）

```
ok up.sh は check_sg_descriptions を tf_init base/core の後、log "1. ECR リポジトリ" の前に 1 回呼ぶ（cycle 043。いま: 1 回）
ok ops/up-common.sh の SG_DESCRIPTION_ROOTS は workflow:workflow / telegraf_dialout:pipeline/stream / telegraf_dialout_nlb:pipeline/stream で、各キーは security_groups.tf の local.security_groups にあり、各ルートの locals.tf が security_group_ids["<キー>"] を読む（いま: [('workflow', 'workflow'), ('telegraf_dialout', 'pipeline/stream'), ('telegraf_dialout_nlb', 'pipeline/stream')]）
ok check_sg_descriptions: state の description がコードと同じなら、付けるルートが残っていても通る（state list も init も打たない。いま: rc=0 '' ['tf base/core state show -no-color aws_security_group.workload["workflow"]', 'tf base/core state show -no-color aws_security_group.workload["telegraf_dialout"]', 'tf base/core state show -no-color aws_security_group.workload["telegraf_dialout_nlb"]']）
ok check_sg_descriptions: workflow の description が古く（041 より前）、workflow の state にリソースがあれば die する（いま: rc=1 'DIE: /var/folders/4s/p22bd3594pz2db9zs56m6b580000gn/T/tmpnk9mthy9/tf/base/core の state の SG workflow の description（Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)）がコード（Temporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)）と違い、workflow がその SG を付けている。description を変えると作り直しで、付けたままでは消せない（DependencyViolation）。先に ops/down.sh で消す（workflow だけ先に消してもよい）。まだ何も作っていない'）
ok check_sg_descriptions: description が古くても、workflow の terraform.tfstate が無い（init も打たない）か state list が空なら通る（いま: rc=0 / 0 '' ''）
ok check_sg_descriptions: base/core の state にその SG が無い（state show が失敗）なら通る（いま: rc=0 ''）
ok check_sg_descriptions: telegraf_dialout_nlb の description が古く（043 より前）、stream が残っていれば die する（いま: rc=1 'DIE: /var/folders/4s/p22bd3594pz2db9zs56m6b580000gn/T/tmprs4gf_8z/tf/base/core の state の SG telegraf_dialout_nlb の description（Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)）がコード（Internal NLB in front of the Telegraf dial-out, syslog-ng and GoFlow2 tasks (IaC/terraform/aws-managed/pipeline/stream)）と違い、pipeline/stream がその SG を付けている。description を変えると作り直しで、付けたままでは消せない（DependencyViolation）。先に ops/down.sh で消す（pipeline/stream だけ先に消してもよい）。まだ何も作っていない'）
ok check_sg_descriptions: 表のキーの綴りが security_groups.tf に無ければ素通りせず die する（いま: rc=1 'DIE: /var/folders/4s/p22bd3594pz2db9zs56m6b580000gn/T/tmpd5hm4_m_/tf/base/core/security_groups.tf に SG workflowx の description が無い（ops/up-common.sh の SG_DESCRIPTION_ROOTS の綴りを直す）。まだ何も作っていない'）
ok sg_description_in_code は実物の security_groups.tf から表の 3 キーとも空でない 1 行を返し、Python の正規表現で取った文言と一致する（いま: {'workflow': 'Temporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)\n', 'telegraf_dialout': 'Telegraf dial-out ECS task - SNMP traps behind the NLB (IaC/terraform/aws-managed/pipeline/stream)\n', 'telegraf_dialout_nlb': 'Internal NLB in front of the Telegraf dial-out, syslog-ng and GoFlow2 tasks (IaC/terraform/aws-managed/pipeline/stream)\n'}）
ok telegraf_dialout の SG の description に MDT と syslog が無く SNMP traps があり（012 で syslog_ng、013 で gnmic に移った）、telegraf_dialout_nlb の description に syslog-ng と GoFlow2 がある（NLB の後ろは 3 つ。cycle 043）
ok ops/oss/up.sh は check_sg_descriptions（description を変えた SG の守り。ops/up-common.sh）を tf_init base/core の後、ECR より前に 1 回呼ぶ（マネージド版と同じ。cycle 043）
```

5. 文言

```
$ grep -rln 'and MDT' IaC docs --include='*.tf' --include='*.md'
docs/cycles/043-sg-description-guard/design.md
docs/cycles/041-nlb-docs-and-workflow-sg/build.md
docs/cycles/041-nlb-docs-and-workflow-sg/review.md
docs/cycles/QUEUE.md
$ grep -n 'NLB の SG が' docs/cml-sandbox.md
rc=1
$ grep -c check_sg_descriptions docs/...
docs/architecture/core.md:1
docs/architecture/resources/vpc-perimeter.md:1
docs/troubleshooting.md:1
```

6. 全テスト（`uv run --group dev --group web python tests/<file>.py`。素の `python3` は yaml が無く動かないので `ops/check.sh` と同じ形）、`bash -n`、`terraform fmt`

```
tests/test_agentcore.py rc=0   通過 168 / 失敗 0
tests/test_alerts.py rc=0      通過 168 / 失敗 0
tests/test_analytics.py rc=0   通過 549 / 失敗 0
tests/test_collectors.py rc=0  通過 79 / 失敗 0
tests/test_dashboard_config.py rc=0 通過 3 / 失敗 0
tests/test_graph.py rc=0       通過 78 / 失敗 0
tests/test_kb_index.py rc=0    通過 7 / 失敗 0
tests/test_lab_debug.py rc=0   通過 110 / 失敗 0
tests/test_local_compose.py rc=0 通過 145 / 失敗 0
tests/test_nautobot.py rc=0    （通過の行を出さない）
tests/test_oss.py rc=0         通過 177 / 失敗 0
tests/test_oss_ops.py rc=0     通過 208 / 失敗 0   （042 の時点 207 + 1）
tests/test_oss_roll.py rc=0    通過 66 / 失敗 0
tests/test_stream.py rc=0      通過 114 / 失敗 0
tests/test_sync.py rc=0        通過 104 / 失敗 0
tests/test_workflow.py rc=0    通過 446 / 失敗 0   （042 の時点 436 + 10）
bash -n ops/up.sh rc=0
bash -n ops/oss/up.sh rc=0
bash -n ops/up-common.sh rc=0
$ terraform fmt -check -recursive IaC/terraform/aws-managed/base/core
fmt rc=0
```

（rc はファイルごとの実行の終了コード。「通過 N / 失敗 0」は各ログの行を grep したもの。agentcore と alerts は同じ 168。）

7. AWS: 未実行（設計どおり。QUEUE 147 でまとめて見る）

### セルフレビュー

- 自分: opus-5.5 / effort: high（サブエージェントの中で effort は切り替えられない）
- 反対弁護人: opus / effort: xhigh（文脈を渡して 1 回。読み取り専用。終了後の作業ツリーの状態は 10 本の M と build.md の ?? だけで、増えたものは無し）

#### 実測で縛ったもの（ミューテーション。最後のコードの編集の後に取り直し、全部落ちることを確認してから元に戻した）

```
M1 up.sh の呼び出しを消す: rc=1 AssertionError: up.sh は check_sg_descriptions を … の前に 1 回呼ぶ（cycle 043。いま: 0 回）
M2 oss/up.sh の呼び出しを消す: rc=1 AssertionError: ops/oss/up.sh は check_sg_descriptions … を tf_init base/core の後、ECR より前に 1 回呼ぶ
M3 比較をやめる（いつも continue）: rc=1 AssertionError: check_sg_descriptions: workflow の description が古く … die する（いま: rc=0 ''）
M4 tfstate の有無を見ない: rc=1 AssertionError: … terraform.tfstate が無い（init も打たない）か state list が空なら通る（いま: rc=1 / 0 'DIE: …
M5 state list の空を見ない: rc=1 AssertionError: … state list が空なら通る（いま: rc=0 / 1 '' 'DIE: …
M6 コードに文言が無くても die しない: rc=1 AssertionError: … 表のキーの綴りが security_groups.tf に無ければ素通りせず die する（いま: rc=0 ''）
M7 表から telegraf_dialout_nlb を落とす: rc=1 AssertionError: ops/up-common.sh の SG_DESCRIPTION_ROOTS は workflow:workflow / …
M8 dialout の description を元に戻す: rc=1 AssertionError: telegraf_dialout の SG の description に MDT と syslog が無く SNMP traps があり …
M9 state の sed が最初の行で止まらない（q を消す）: rc=1 AssertionError: check_sg_descriptions: state の description がコードと同じなら … 通る（いま: rc=1 'DIE: …
```

- sed は BSD（`/usr/bin/sed`）で確かめた。
  - state 側は、入れ子の `description = "c"` があっても最初の 1 行だけを取る。
  - コード側は 3 キーとも 1 行を返し、`workflowx` は空を返す。
  - GNU sed は手元に無く、未実行。`{s//\1/p;q;}` は POSIX の範囲で、Linux で動かす経路は無い。

#### 指摘と片付け

1. **Must fix（未解消。`/cycle-design` へ差し戻す）**
   - [design.md との整合性 + runtime bugs]
   - 場所: すべて設計方針 1・5 のとおりに作ったもの。
     - `ops/up-common.sh` の `check_sg_descriptions`。ルートの tfstate が無い・`state list` が空なら通す条件と、die 文の「（<root> だけ先に消してもよい）」
     - `docs/troubleshooting.md:26`
     - `tests/test_workflow.py` の「ルートが空なら通る」check
   - 破綻シナリオ:
     1. 古い description の state のまま、案内どおりルートだけ destroy してから `ops/up.sh` を打つと、守りは通る。
     2. base/core の apply が SG を destroy → create にする。
     3. そのとき、残る SG のルールが古い SG を `referenced_security_group_id` で参照している。
        - telegraf_dialout_nlb: lab の送信（162/5140/2055/6343）と、telegraf_dialout / syslog_ng / goflow2 の受信
        - telegraf_dialout: msk と endpoints の受信
        - workflow: web の送信 8233、nautobot_db と endpoints の受信
     4. このため `DeleteSecurityGroup` が DependencyViolation になる。
     - 3 キーとも、通るのは base/core の workload の SG まで消す `ops/down.sh` の全消しだけ。
   - 再現:
     - provider 6.64.0（`base/core/.terraform.lock.hcl:5`）の `internal/service/ec2/vpc_security_group_ingress_rule.go` を WebFetch で読んだ。
       - RequiresReplace は `security_group_id` だけ。
       - `referenced_security_group_id` には plan modifier が無い。
       - ModifyPlan が作り直しにするのは、送り元の種類が変わったときだけ。
       - Update は `ModifySecurityGroupRules`（`ReferencedGroupId`）でその場で更新する。
       - 参照側のルールは新しい SG を作った後に in-place で更新されるので、古い SG を消す時点では参照が残っている。
     - 参照している行は、`security_groups.tf` の `sg_flows` を読んで確かめた。
     - AWS では確かめていない。plan も打っていない（このサイクルは AWS を動かさない）。
     - 041 の `review.md:72-75` も「ほかの SG のルールからの参照が残っているので消せない」と書いている。
   - 直し方の案（決めるのは設計側）:
     - base/core の state の description がコードと違えば、ルートの state を見ずに die する。
     - 案内は `ops/down.sh` の全消しだけにする。
     - troubleshooting の行と「ルートが空なら通る」check は、それに合わせて反転する。
   - 実装で吸収しなかった理由: 原因は設計方針 1（ルートの state を見る条件と die 文）と方針 5（troubleshooting の「そのルートだけ destroy」）にある。cycle-build の決まりどおり差し戻す。

2. **Should fix（設計側。PM へ）**
   - [検証計画] design.md 方針 6（QUEUE 147 に足す行）
   - Runtime の ENI が残るときも、`ops/down-common.sh:140-158` の `destroy_base_core` は VPC・サブネット・runtime の SG 以外を消す（workload の SG は必ず消える）。
   - そのため down 済みから up すると SG は新しく「作られる」だけで、`must be replaced` は出ない。
   - 実物の `state show` で守りが止まらないことを見るには、立った環境で `ops/up.sh` を 2 回目に打つ必要がある。
   - 確認は読んだだけ。QUEUE は PM が書くので直していない。

3. **Should fix（設計側。差し戻しに含める）**
   - [missing tests] 表 `SG_DESCRIPTION_ROOTS` に無い SG の description を変えると、守りを素通りする（design.md のリスク 2 のとおり）。
   - 1 の直し方で全キーを比べれば、表そのものが要らなくなる。

4. **Should fix（直した）**
   - [correctness] `docs/troubleshooting.md:26`
   - 単独 destroy の案内から、必須変数が抜けていた。
     - 全ルート: owner
     - workflow: worker_image_tag / temporal_image_tag
     - stream: gnmi_targets
     - 必須変数は、`variables.tf` の default が無い変数を awk で列挙して確かめた。
   - `ops/down.sh:71,77` と同じ値を足した。OSS 版の `ops/oss/down.sh` も書いた。
   - ただし「ルートだけ消す」道そのものが 1 で成り立たないので、差し戻しの再設計でこの行は書き直しになる。

5. **Nit**
   - [runtime] `ops/up-common.sh` の `sg_description_in_state`
   - 黙って通るもの:
     - `state show` の失敗（`2>/dev/null`）
     - `state list` の失敗
     - `printf | sed …q` の SIGPIPE。出力が 16KB を超えたときに起きるが、workload の SG の出力は数 KB の見積りなので起きない。
   - 逆に止まる側: `state show` が成功しても description の行が取れないと、`state=""` のまま die する。
   - 既存の守り（`state list 2>/dev/null`）と同じ扱いなので据え置く。

6. **Nit**
   - [runtime] `security_groups.tf` が無いと、die ではなく sed のエラーで終わる。
   - 再現: scratchpad の sete.sh（`set -euo pipefail` の下で、関数の中の sed にファイルが無い）。
     - 出力: `sed: /nonexistent/sg.tf: No such file or directory` / rc=1
   - 実物があることは確かめた。
     - マネージド版: `test -f` で存在する。
     - OSS 版: `../../../../terraform/aws-managed/base/core/security_groups.tf` へのリンクで、こちらも存在する。
   - 据え置く。「設計からの逸脱」の原因の書き方は、この実測に合わせて直した。

7. **Nit（OSS・将来）**
   - [保守性] `sg_description_in_code` は `security_groups.tf` しか読まない。
   - `oss.tf:16-23` が差し替える spark などを表に足すと、OSS では毎回止まる。
   - いまの 3 キーでは起きない。

8. **Nit（設計の文言）**
   - design.md:79 の「hashicorp/aws の 5 系」は誤り。lock は 6.64.0。
   - design.md:13 の「description の行は SG 本体の 1 行だけ」も正確でない。state には別リソースのルールの description も入る。
     - 正しく動くのは最初の 1 行を取るからで、これは M9 で縛った。

#### 「問題なし」とした観点と根拠

- 呼び出し位置・表・比較・tfstate / state list の条件・コード無しの die・description の文言: M1〜M9 で縛った（上）。
- エスケープ: AWS の SG の description は `"` も改行も受け付けない。比べる両側とも HCL のリテラル（読んだだけ）。
- 守りの中の `tf_init <root>` の副作用: 走るのは、文言が違い、かつ tfstate があるときだけ。OSS の `-lockfile=readonly` は既存の守りと同じ（読んだだけ）。
- 全テスト・`bash -n`・`terraform fmt`: 上の検証 6。troubleshooting を直した後に次を取り直した。
  - `tests/test_workflow.py`: `通過 446 / 失敗 0`
  - `tests/test_oss_ops.py`: `通過 208 / 失敗 0`

#### 結論

- 未解消の Must fix が 1 件ある（指摘 1）。原因は設計方針 1・5 なので、`/cycle-design` へ差し戻す。指摘 2・3・8 も設計側。
- Round 1 の実装のうち、次のものは差し戻し後も使える。
  - description の修正
  - cml-sandbox.md
  - core.md / vpc-perimeter.md の 1 行
  - 呼び出し位置
  - テストの土台
- 再設計で変わるもの: 守りの条件、die 文、troubleshooting の行、「ルートが空なら通る」check。
