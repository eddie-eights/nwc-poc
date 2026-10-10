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

## Round 2

実装モデル: opus-5.5 / effort: high

### 実装内容

- `ops/up-common.sh`: Round 1 の `SG_DESCRIPTION_ROOTS` と 3 関数を消し、`has_resources` の後に設計方針 1 の 3 関数を置いた。
  - `sg_descriptions_in_code`: `security_groups.tf` の `  security_groups = {` 〜 `  }` を読む。`oss.auto.tfvars` が `project = "nwc-oss"` なら `oss.tf` の `  oss_security_groups = {` 〜 `  }` も読み、後勝ちで上書きする。出力は `<キー>\t<文言>`。
  - `sg_descriptions_in_state`: 標準入力の `terraform show -no-color` から、`# aws_security_group.workload["<キー>"]:` の見出し 〜 行頭 `}` のブロックの、4 空白ちょうどの最初の `description` を取る。無ければ空。
  - `check_sg_descriptions`: `tf base/core show -no-color` を 1 回だけ打つ（失敗なら die）。コードにあるキーだけ比べ、違えば設計どおりの文面で die する。
  - awk は BSD awk で動く書き方（`match` + `substr`、`ENVIRON` でコードの表を渡す）。
- `ops/up.sh` / `ops/oss/up.sh`: 呼び出し位置は Round 1 のまま。上のコメントを設計方針 2 に直した。
- `security_groups.tf:24-26`: コメントを設計方針 4 に直した（description は Round 1 のまま）。
- docs: `core.md:103`、`vpc-perimeter.md:98`、`troubleshooting.md:26` を設計方針 5 に。ルートだけ destroy する案内と必須変数の列挙は消した。`cml-sandbox.md` は触っていない。
- テスト:
  - `tests/test_workflow.py`: 043 の塊を検証 1・2 に合わせて書き直した（18 check。telegraf の description の check は Round 1 のまま）。
  - `tests/test_oss_ops.py`: 偽の terraform に `show` の枝（`# aws_vpc.this:` のブロックだけを返す）を足した。

### 変更ファイル

- IaC/terraform/aws-managed/base/core/security_groups.tf
- docs/architecture/core.md
- docs/architecture/resources/vpc-perimeter.md
- docs/troubleshooting.md
- ops/oss/up.sh
- ops/up-common.sh
- ops/up.sh
- tests/test_oss_ops.py
- tests/test_workflow.py
- docs/cycles/043-sg-description-guard/build.md

### 設計からの逸脱

- `check_sg_descriptions` は、コード側が空（`sg_descriptions_in_code` が失敗するか、1 行も出さない）なら die する（`…security_groups.tf から SG の description が読めない…`）。
  - 理由: 無いと、`local.security_groups` の形が変わったとき全キーが「コードに無いキー」になり、守りが黙って素通りする。
- `sg_descriptions_in_code` は、塊の中に空行・コメント以外で `<キー> = "<文言>"` の形でない行（行末のコメント、式など）があれば、その行を stderr に出して失敗する（上の die になる）。
  - 理由: セルフレビューの指摘 2。1 行だけ黙って落ちると、そのキーの違いを見逃す。
- テストは素の `python3` に yaml が無いので、`uv run --group dev --group web python tests/<file>.py` で打った（Round 1 と同じ形）。
- 検証 5 の grep は `docs/cycles/` を除いて見た（043 の design.md / build.md 自身が旧名と旧文言を引用している）。

### 検証（最後の編集の後に取り直した出力）

1・2. `tests/test_workflow.py` の 043 の check（行は途中で切った）

```
ok up.sh は check_sg_descriptions を tf_init base/core の後、log "1. ECR リポジトリ" の前に 1 回呼ぶ（cycle 043。いま: 1 回）
ok ops/up-common.sh に SG_DESCRIPTION_ROOTS（Round 1 のキーの表）が無く、sg_descriptions_in_code / sg_descriptions_in_state / check_sg_descriptions の 3 関数があり、check_sg_descrip
ok check_sg_descriptions: state の description が全キーともコードと同じなら通り、terraform show を 1 回だけ打ち、init も state list も打たない（いま: rc=0 '' ['tf bas
ok check_sg_descriptions: workflow の description が古い（041 より前）なら、state とコードの文言を出して die し、down.sh の全消しだけを案内する（ルートだけ消
ok check_sg_descriptions: workflow と telegraf_dialout_nlb の 2 つが古ければ、die の文に両方を出す（いま: rc=1 'DIE: …
ok check_sg_descriptions: state の SG のブロックに description の行が無ければ（空）、違うものとして die する（いま: rc=1 'DIE: …
ok check_sg_descriptions: コードに無いキー（キーを変えた古い telegraf。既存の守りの担当）は古い文言でも飛ばして通す（いま: rc=0 ''）
ok check_sg_descriptions: state に workload の SG が 1 つも無い（aws_vpc とルールのブロックだけ）なら通す（いま: rc=0 ''）
ok check_sg_descriptions: terraform show が失敗したら黙って通さず die する（いま: rc=1 'Error: fake show failure\nDIE: …
ok check_sg_descriptions: security_groups.tf から文言が 1 つも読めない（local.security_groups の形が変わった）なら、全キーを飛ばして素通りせず die する（terrafor
ok check_sg_descriptions: security_groups.tf の 1 行だけが読めない（行末のコメントなど）なら、そのキーを黙って飛ばさず、読めない行を出して die する（ter
ok check_sg_descriptions: OSS 版（oss.auto.tfvars が project = "nwc-oss"）は oss.tf の文言で比べる。spark が oss.tf の文言なら通り、security_groups.tf の EMR Serverless の文…
ok sg_descriptions_in_state は SG のブロックの 4 空白の description だけを <キー>\t<文言> で出し、前後のルールと VPC のブロックの description を拾わない（descri
ok sg_descriptions_in_state は SG のブロックを行頭の } で閉じ、後ろの Outputs: の 4 空白の description を拾わない（いま: 'workflow\t\n'）
ok local.security_groups / oss_security_groups の塊の行（空行とコメント以外）は全部 <キー> = "<文言>" の形で、テストの正規表現が全部拾う（いま: 16 行 / 16 …
ok sg_descriptions_in_code は実物の security_groups.tf の local.security_groups のキーと文言を全部出し、Python の正規表現で取ったものと一致する（キーの重複なし
ok sg_descriptions_in_code は OSS 版（IaC/terraform/oss。oss.auto.tfvars が nwc-oss）では oss.tf の local.oss_security_groups で上書きし、キーを足す（spark は Spark ECS task、ka
```

3. `tests/test_oss_ops.py`（呼び出し位置と通し。抜粋。通しの check は全部 ok）

```
ok ops/oss/up.sh は check_sg_descriptions（description を変えた SG の守り。ops/up-common.sh）を tf_init base/core の後、ECR より前に 1 回呼ぶ（マネージド版と同じ。cycl
ok up.sh（通し）: 終了コード 0 で最後まで行き、偽物の知らないコマンドを打たず、未定義の変数も踏まない
```

4. telegraf の description の check（Round 1 のまま）

```
ok telegraf_dialout の SG の description に MDT と syslog が無く SNMP traps があり（012 で syslog_ng、013 で gnmic に移った）、telegraf_dialout_nlb の description に syslog-ng …
```

5. 文言（`docs/cycles/` を除く）

```
$ grep -rn 'SG_DESCRIPTION_ROOTS' ops docs tests IaC   （docs/cycles/ を除く）
（出力なし。rc=1）
$ grep -rn 'だけ先に消してもよい' ops docs   （docs/cycles/ を除く）
ops/oss/up.sh:158  （telegraf_dialin の既存の守り）
ops/up.sh:598      （telegraf の既存の守り）
ops/up.sh:604      （telegraf_dialin の既存の守り）
$ grep -n 'worker_image_tag=destroy' docs/troubleshooting.md
（出力なし。rc=1）
$ grep -c 'check_sg_descriptions' docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md docs/troubleshooting.md
docs/architecture/resources/vpc-perimeter.md:1
docs/architecture/core.md:1
docs/troubleshooting.md:1
$ grep -rln 'and MDT' IaC docs --include='*.tf' --include='*.md'
docs/cycles/QUEUE.md
docs/cycles/043-sg-description-guard/build.md
docs/cycles/043-sg-description-guard/design.md
docs/cycles/041-nlb-docs-and-workflow-sg/build.md
docs/cycles/041-nlb-docs-and-workflow-sg/review.md
$ grep -n 'NLB の SG が' docs/cml-sandbox.md
（出力なし。rc=1）
```

6. 全テスト・fmt・構文

```
$ uv run --group dev --group web python tests/test_workflow.py
通過 454 / 失敗 0
$ uv run --group dev --group web python tests/test_oss_ops.py
通過 208 / 失敗 0
$ uv run --group dev --group web python tests/test_stream.py
通過 114 / 失敗 0
$ terraform fmt -check -recursive IaC/terraform/aws-managed/base/core
rc=0（出力なし）
$ bash -n ops/up.sh ; bash -n ops/oss/up.sh ; bash -n ops/up-common.sh（1 本ずつ）
rc=0 / rc=0 / rc=0（出力なし）
```

7. AWS: 未実行（設計どおり。QUEUE の「残った修正をまとめて AWS で動作確認して直す」で見る）。docker も使っていない。

### セルフレビュー

- 自分: opus-5.5 / effort: high（サブエージェントの中で effort は切り替えられない）
- 反対弁護人: opus / effort: xhigh（文脈を渡して 1 回。読み取り専用。終了後の `git status --porcelain -uall` は 9 本の M だけで、増えたものは無し）

#### 実測で縛ったもの（ミューテーション。最後のコードの編集の後に 14 個とも取り直し、全部落ちることを確かめてから元に戻した）

```
M1 up.sh の呼び出しを消す          | test_workflow.py rc=1: up.sh は check_sg_descriptions を tf_init base/core の後 … 1 回呼ぶ
M2 oss/up.sh の呼び出しを消す      | test_oss_ops.py rc=1: ops/oss/up.sh は check_sg_descriptions（…）を tf_init base/core の後 …
M3 比較を常に等しい               | rc=1: workflow の description が古い（041 より前）なら … die し …
M4 ($1 in c) を外す              | rc=1: コードに無いキー … は古い文言でも飛ばして通す（いま: rc=1 'DIE: …
M5 show の失敗を素通り            | rc=1: terraform show が失敗したら黙って通さず die する（いま: rc=0 …）
M6 入れ子の description も読む    | rc=1: description の行が無ければ（空）、違うものとして die する
M7 OSS の上書きを読まない          | rc=1: OSS 版（…）は oss.tf の文言で比べる …
M8 コードのキーを先勝ちに          | rc=1: OSS 版（…）は oss.tf の文言で比べる …
M9 空のコードの守りを外す          | rc=1: security_groups.tf から文言が 1 つも読めない … なら … die する
M10 die 文を部分消しの案内に戻す   | rc=1: workflow の description が古い … down.sh の全消しだけを案内する …
M11 state のキーの切り出しをずらす | rc=1: workflow の description が古い … die し …
M12 } での flush を外す           | rc=1: sg_descriptions_in_state は SG のブロックを行頭の } で閉じ、後ろの Outputs: … を拾わない
M13 読めない行を飛ばす             | rc=1: security_groups.tf の 1 行だけが読めない（行末のコメントなど）なら … die する
M14 読めない行で exit しない       | rc=1: （M13 と同じ check）
```

- M12 は最初の取り直しで生き残った（後ろに Outputs: が来る形がサンプルに無かった）。Outputs: の check を足して落ちるようにした。

#### 指摘と片付け

1. **Should fix（設計側。PM へ。直していない）**
   - [die 文と docs の案内] `ops/up-common.sh` の `check_sg_descriptions` の die 文、`docs/troubleshooting.md:26`（どちらも設計方針 1・5 の文面どおり）
   - 破綻シナリオ:
     - Runtime の ENI（agentic_ai。最大 8 時間ほど残る）があるあいだ、`ops/down-common.sh:140-148` の `destroy_base_core` は `aws_security_group.workload["runtime"]` を残す。
     - runtime の description が state とコードで違うと、`up.sh` は die する。案内どおり `down.sh` を打っても runtime の SG は残り、`up.sh` はまた die する。「時間をおいて打ち直す」案内が die 文にも troubleshooting にも無い。troubleshooting の行は workflow / telegraf だけを挙げ、runtime を挙げていない。
     - die 文の理由（「ほかの SG のルールが古い SG を参照したまま」）は、runtime の SG だけが残った state には当たらない。
   - 再現:
     - 反対弁護人が、メインのチェックアウトの OSS の state を読み取りで描画し、VPC・サブネット・`workload["runtime"]` だけが残り、runtime の description が旧文言（`AgentCore Runtime ENIs (terraform/agent)`。コードは `… (IaC/terraform/aws-managed/agent)`）であることを見た。その show を守りに流すと die した。
     - 自分はメインのチェックアウトに触らない指示なので、この state は読んでいない。`destroy_base_core` が runtime の SG を残すことは `ops/down-common.sh:140-148` を読んで確かめた（読んだだけ）。
   - 守りが止めること自体は正しい（ENI が残っていれば runtime の SG の作り直しは失敗する。ENI が消えていれば down.sh 1 回で通る）。直すのは案内の文面で、文面は design.md が決めているので実装では変えていない。
   - 影響: 次の実物の `ops/oss/up.sh` は手順 0 の後でこの die になる見込み（反対弁護人の実測）。

2. **Should fix（直した）**
   - [correctness / missing tests] `ops/up-common.sh` の `sg_descriptions_in_code`
   - 破綻シナリオ: `security_groups.tf` の 1 行が `<キー> = "<文言>"` の形から外れる（行末のコメント `# 041` など）と、その行は黙って捨てられ、そのキーは「コードに無いキー」として比較から外れる。空のときの die は全滅のときしか捕まえない。テストの参照用の正規表現も同じ穴を持つので、照合の check も通っていた。
   - 再現: 反対弁護人が `security_groups.tf` のコピーの workflow の行末に `# 041` を足し、DIE から workflow が消えることを見た。自分でも同じ形の check（`code_tail_comment`）を足し、直す前の形（M13）で落ちることを確かめた。
   - 直し: 塊の中の空行・コメント以外の読めない行を stderr に出して失敗させ、`check_sg_descriptions` の「読めない」die にした（「設計からの逸脱」）。テストに、読めない行の die の check と、実物の塊の行数とキーの数が一致する check を足した。

3. **Nit（設計の文言）**
   - [検証計画] design.md 検証 5 の `SG_DESCRIPTION_ROOTS` が 0 件、`だけ先に消してもよい` が 3 行だけ、は書いたとおりに打つと 043 の design.md / build.md 自身に当たる。`docs/cycles/` を除けば満たす（検証 5 の出力）。

4. **Nit（範囲外。PM へ）**
   - [範囲] `IaC/terraform/aws-managed/base/core/security_groups.tf:189-191` の `aws_security_group.endpoints` は `workload[...]` ではないので守りが見ない。description を変えれば同じく作り直しになり、VPC エンドポイントの ENI とワークロードの SG からの 443 の参照で止まる。読んだだけ。いまは変えていないので起きない。

5. **Nit（据え置き）**
   - [state の形] `ops/up-common.sh` の `sg_descriptions_in_state`
   - deposed のオブジェクト（`# aws_security_group.workload["lab"]: (deposed object …)`）があると、同じキーが 2 行出る。どちらかがコードと違えば die する（止める側に倒れる）。tainted の見出しのキーも正しく取れる。
   - 再現（scratchpad の deposed.sh。関数を `ops/up-common.sh` から切り出して、tainted 1 つ・deposed と現行の lab を流した）:
     ```
     web	T
     lab	OLD
     lab	NEW
     ```
   - この SG は `create_before_destroy` を使わないので deposed は作られない。止める側に倒れるので据え置く。

#### 問題なしとした観点と根拠

- `terraform show` の実物の形（見出し、4 空白の description、8 空白の tags、行頭の `}`、末尾の Outputs）: 反対弁護人が実物の state のバックアップを読み取りで描画し、16 キーを正しく出すこと、DIE が telegraf_dialout / telegraf_dialout_nlb / workflow のちょうど 3 つになることを確かめた。自分は実物の state を読んでいない（テストのサンプルで確かめた）。
- ルール・VPC・Outputs の description を拾わない: 検証 2 の check（M6・M12 で縛った）。
- 呼び出しの前提（up-common.sh を読む順、TF_DIR / OPS_DIR / tf / die、直前の tf_init base/core）: 反対弁護人が `ops/up.sh` と `ops/oss/up.sh` の該当箇所を読んで確かめた。test_oss_ops の通し（`未定義の変数も踏まない`）が通る。
- OSS の判定: 検証 2 の OSS の check（M7・M8）と、実物の `IaC/terraform/oss` に対する `sg_descriptions_in_code` の check。
- bash 3.2 / BSD awk: テストは macOS の `/bin/bash`（3.2）と `/usr/bin/awk` で走る。GNU awk / mawk は未実行。

#### 結論

- 未解消の Must fix: 0。
- `/cycle-design` への差し戻し: 必須ではない。指摘 1（runtime の ENI が残るときの案内）は設計の文面の話なので PM の判断に回す。

## Round 3

実装モデル: opus-5.5 / effort: high（セルフレビューも high。サブエージェントの中では effort を切り替えられない）

入力: `design.md` Round 3（設計方針 1 の runtime の一言、設計方針 5 の troubleshooting、検証 2・5 の新項目）、`review.md` Round 1 の Should fix 1。

### 実装内容

- `ops/up-common.sh` の `check_sg_descriptions`: 違うキーに runtime があれば（`case "、$diffs" in *"、runtime（"*)`。キーで判定）、die の `まだ何も作っていない` の前に設計の一言 `runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ $OPS_DIR/down.sh も残すので、ENI が消えてから $OPS_DIR/down.sh を打つ。` を足した。
- `docs/troubleshooting.md:26`: 設計方針 5 の文に替えた（runtime のときは ENI が消えてから、2026-10-08（`48683dd`）より前の state）。
- テスト:
  - `tests/test_workflow.py`: runtime が古い（`AgentCore Runtime ENIs (terraform/agent)`）/ workflow と一緒に古い / runtime 以外のキーの文言に runtime が入る、の 3 check と、troubleshooting の行の check を足した。2 キーの check に「ENI の一言が無い」を足した。
  - `tests/test_oss_ops.py`: 本物の `ops/common.sh` の die と `ops/up-common.sh` を OSS 版の `TF_DIR` / `OPS_DIR` で動かし、OSS 版の state の実物（runtime が `(terraform/agent)`）で `ops/oss/down.sh` と ENI の一言が出る check を足した。

### 変更ファイル

- ops/up-common.sh
- docs/troubleshooting.md
- tests/test_workflow.py
- tests/test_oss_ops.py
- docs/cycles/043-sg-description-guard/build.md

### 設計からの逸脱

- troubleshooting の括弧の参照先: 設計は「（「画面に入れない」の下の Runtime の ENI の項と同じ待ち）」だが、`## 画面に入れない`（`docs/troubleshooting.md:85-98`）に Runtime の ENI の項は無い（`sed -n 85,98p docs/troubleshooting.md | grep -n ENI` が 0 件）。Runtime の ENI の待ちは `## 消すとき` の `DependencyViolation` の行（`:489`）なので、「（下の「消すとき」の `DependencyViolation` の行と同じ待ち）」にした。
- 検証 6 の `bash -n` は 3 本を 1 本ずつ打った（`bash -n a b c` は 1 本目しか構文検査しない。残りは引数）。
- `tests/test_oss_ops.py` の OSS 版の die の check は、検証 3 の外の追加（OSS 版の `OPS_DIR` で文面が `ops/oss/down.sh` になることを縛る）。

### 検証（最後の編集の後に取り直した出力）

1・2. `uv run --group dev --group web python tests/test_workflow.py`（Round 3 で足した・変えた check。行は途中で切った）

```
ok check_sg_descriptions: workflow と telegraf_dialout_nlb の 2 つが古ければ、die の文に両方を出す。runtime は違わな…
ok check_sg_descriptions: runtime の description が古い（2026-10-08 より前の OSS 版の state の実物）なら、down.sh も Runt…
ok check_sg_descriptions: runtime がほかのキーと一緒に違っても（先頭でなくても）ENI の一言を 1 回だけ足す（…
ok check_sg_descriptions: runtime 以外のキーの文言に runtime が入っていても ENI の一言は足さない（キーで判定…
ok troubleshooting.md の check_sg_descriptions の行は down.sh の全消しを案内し、runtime のときは ENI が消えてから、2…
通過 458 / 失敗 0
```

3. `uv run --group dev --group web python tests/test_oss_ops.py`（呼び出し位置と通しの check は Round 2 のまま全部 ok）

```
ok ops/oss/up.sh の check_sg_descriptions: OSS 版の state の古い runtime の SG（terraform/agent）で止め、ops/oss/down.sh の全…
通過 209 / 失敗 0
```

4. telegraf の description の check: Round 1 のまま（上の 458 に含まれ ok）。

5. 文言

```
$ grep -rn --exclude-dir=cycles SG_DESCRIPTION_ROOTS ops docs tests IaC | wc -l
       0
$ grep -rn --exclude-dir=cycles だけ先に消してもよい ops docs
ops/up.sh:598 / ops/up.sh:604 / ops/oss/up.sh:158（キーを変えた既存の守りの 3 行だけ）
$ grep -n worker_image_tag=destroy docs/troubleshooting.md | wc -l
       0
$ grep -c check_sg_descriptions docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md docs/troubleshooting.md
docs/architecture/core.md:1
docs/architecture/resources/vpc-perimeter.md:1
docs/troubleshooting.md:1
$ grep -rln 'and MDT' IaC docs --include='*.tf' --include='*.md'
docs/cycles/QUEUE.md
docs/cycles/043-sg-description-guard/design.md
docs/cycles/043-sg-description-guard/build.md
docs/cycles/041-nlb-docs-and-workflow-sg/build.md
docs/cycles/041-nlb-docs-and-workflow-sg/review.md
$ grep -n 'NLB の SG が' docs/cml-sandbox.md | wc -l
       0
$ grep -c 'ENI が消えてから' ops/up-common.sh docs/troubleshooting.md
docs/troubleshooting.md:1
ops/up-common.sh:1
$ grep -c 2026-10-08 docs/troubleshooting.md
7
```

6. 全テストと整形

```
$ uv run --group dev --group web python tests/test_workflow.py   → 通過 458 / 失敗 0
$ uv run --group dev --group web python tests/test_oss_ops.py    → 通過 209 / 失敗 0
$ uv run --group dev --group web python tests/test_stream.py     → 通過 114 / 失敗 0
$ terraform fmt -check -recursive IaC/terraform/aws-managed/base/core; echo rc=$?
rc=0
$ bash -n ops/up-common.sh && echo ok-upc      → ok-upc
$ bash -n ops/oss/up.sh && echo ok-oss-up      → ok-oss-up
$ bash -n ops/up.sh && echo ok-up              → ok-up
```

7. AWS: 未実行（設計どおり QUEUE 147）。

### セルフレビュー

- 自分: opus-5.5 / effort: high。反対弁護人: opus / effort: xhigh（文脈を渡して読み取り専用。終了後 `git status --porcelain -uall` は自分の変更 4 ファイルだけ）。
- 反対弁護人の結論: Must 0 / Should 0 / Nit 5 と書き残し 1（build.md の逸脱の記録。上に書いた）。

#### mutation（自分で注入して落ちることを確かめた）

- case を常に偽（`*"、NOPE（"*`）→ test_workflow `AssertionError: check_sg_descriptions: runtime の description が古い…`、test_oss_ops `AssertionError: ops/oss/up.sh の check_sg_descriptions: OSS 版の…` で落ちる。
- case をキー以外でも当たる形（`case "$diffs" in *"runtime"*`）→ test_workflow `AssertionError: check_sg_descriptions: runtime 以外のキーの文言に runtime が入っていても…` で落ちる。
- 反対弁護人がメモリ上の変異で追加確認: 先頭の「、」無し / 常に真 / 一言を `まだ何も作っていない` の後ろ / `ops/down.sh` の直書き（test_oss_ops が殺す）はどれも落ちる。

#### 指摘

1. **Should fix（docs の事実の誤り。設計の文面由来。直さず PM へ）** [correctness / docs] `docs/troubleshooting.md:26`（と `tests/test_workflow.py:1627-1632` の「全キーが違う」）
   - 破綻シナリオ: 「2026-10-08（`48683dd`）より前の state は … 全キーが違う」は誤り。web / lab / lambda の description はパスを含まず 48683dd の前後で同じなので、die に出ない。OSS 版の `oss_security_groups` は `oss/terraform/…` からの変化で、文中の `terraform/…` → `IaC/terraform/aws-managed/…` とも形が違う。古い state で止まった人が die の一覧と docs を比べて戸惑う。
   - 確かめたコマンド: `git show 48683dd -- IaC/terraform/aws-managed/base/core/security_groups.tf | grep -E '^[-+]    (web|lab|lambda|workflow|runtime) '` → workflow / runtime の行だけが `-`/`+` で出て、web / lab / lambda は出ない。
   - 片付け: 文言は design.md 設計方針 5 そのままなので実装で吸収しない。PM の判断（設計の文面を「パスを含む description のキーが違う」等に直すか）に回す。
2. **Nit（docs の整合）** `docs/troubleshooting.md:489`、`docs/deploy.md:415,423`、`ops/down.sh:124`、`ops/oss/down.sh:94` の「そのままでよい / 次の up.sh が使い回す」は、runtime の description が古い state では次の up.sh が守りで止まるので合わない。die の文面に ENI の待ちが出るので実害は小さい。設計の変更対象外なので直さない。
3. **Nit（テスト不足）** `ops/up-common.sh:80` の判定から `（` を抜いた変異（`*"、runtime"*`）は生き残る（反対弁護人の `mut.py nokey_paren` → `SURVIVED`）。殺すには `runtime_x` のようなキーをコードの写しにも足す必要がある。今のコードに runtime で始まる別のキーは無いので直さない。
4. **Nit（設計で承知済み）** runtime だけが古いとき、die の理由の文（ほかの SG のルールが参照）は実際の理由（ENI）と合わず、ENI が消えた後なら apply が通る場面でも down.sh を 1 回余分に打たせる。設計の未確定事項 3 の範囲。
5. **Nit（検証計画）** design.md 検証 6 の `bash -n` 3 本並べは 1 本目しか見ない（反対弁護人: `bash -n <(echo true) <(echo "if then fi fi (")` が rc 0）。1 本ずつ打ったので実害なし。設計の文面を直すとよい。

#### 問題なしとした観点と根拠

- 判定の誤検知 / 見逃し: 反対弁護人が bash 3.2 を C / en_US.UTF-8 / ja_JP.UTF-8 で走らせ、`runtime`・`workflow、runtime` は当たり、`agent_runtime`・`runtime_x`・`web_runtime` は当たらないことを確かめた。state の文言は AWS の仕様で ASCII のみ。
- 文言と設計の一致: 反対弁護人が difflib で比べ、一言は完全一致、troubleshooting は括弧の参照先 1 か所（上の逸脱）だけ違う。
- down.sh が runtime の SG だけを残すか: `ops/down-common.sh:100-158` を読んだだけ（反対弁護人）。`ops/oss/down.sh` も同じ `destroy_base_core` を使う。

#### 結論

- 未解消の Must fix: 0。
- `/cycle-design` への差し戻し: 指摘 1（troubleshooting の「全キーが違う」の誤り）と逸脱 1（「画面に入れない」の参照先）は設計の文面の話なので PM の判断に回す。
