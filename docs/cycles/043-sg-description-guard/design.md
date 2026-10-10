# description を変えた SG が残っていれば up.sh を止め、telegraf の SG の説明を揃える（043）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。Round 2（Round 1 のセルフレビューの Must fix 1 で差し戻し。前の設計は git の `a3e208c`）。Round 3 は `review.md` Round 1 の Should 1（runtime の SG だけが残った state で案内どおり `down.sh` を打っても抜けられない）と Nit 1・2・4 を取り込んだ文面の修正（Round 2 の設計は git の `7d2a8f4`）。Round 3 のセルフレビューで troubleshooting の「全キーが違う」（web / lab / lambda はパスを含まないので違わない）と参照先（「画面に入れない」に Runtime の ENI の項は無い）を PM が直した（`4887ff0` の後）。

## Round 2 の入力（Round 1 の `build.md` の Must fix 1。原因は設計方針 1・5）

- 古い description の state のまま、案内どおりルートだけ destroy してから `ops/up.sh` を打つと守りは通るが、base/core の apply で SG を destroy → create にするとき、**残る SG のルールが古い SG を `referenced_security_group_id` で参照したまま**なので `DeleteSecurityGroup` が `DependencyViolation` になる。
  - telegraf_dialout_nlb を参照するルール: lab の送信（162 / 5140 / 2055 / 6343）と telegraf_dialout / syslog_ng / goflow2 の受信
  - telegraf_dialout を参照するルール: msk と endpoints の受信
  - workflow を参照するルール: web の送信 8233、nautobot_db と endpoints の受信
- hashicorp/aws 6.64.0（`base/core/.terraform.lock.hcl:5`）の `aws_vpc_security_group_ingress_rule` / `egress_rule` は `referenced_security_group_id` の変更を **in-place で更新**する（RequiresReplace は `security_group_id` だけ。Update は `ModifySecurityGroupRules`）。参照側のルールは新しい SG を作った後に更新されるので、古い SG を消す時点では参照が残る。041 の `review.md:72-75` も同じ結論。
- 3 キーとも、通るのは base/core の workload の SG まで消す `ops/down.sh` の全消しだけ。**「<ルート> だけ先に消してもよい」の案内と、ルートの state を見て通す条件が誤り。**

キーを変えた既存の守り（`internal` / `telegraf` / `telegraf_dialin`）は別。古いキーの SG を参照するルールはコードから消えているので、古い SG より先に destroy され、「stream だけ先に消してもよい」はそのまま正しい。

## 背景

- QUEUE 145: `docs/cml-sandbox.md:558` の見送った案の表に 041 より前の言い方（「trap の 162 は NLB の SG が `lab` の SG から受けない」）が残っている。`base/core/security_groups.tf:27` の telegraf_dialout の description は「traps, syslog and MDT」のまま（syslog は 012 で syslog_ng、MDT は 013 で gnmic に移り、dialout が受けるのは trap だけ）、`:29` の telegraf_dialout_nlb は「in front of the Telegraf dial-out task」のまま（NLB の後ろは dialout / syslog_ng / goflow2 の 3 つ）。`:24` のコメントがそれを正当化している。
- QUEUE 146: `aws_security_group.workload`（`security_groups.tf:151-159`）は `description = each.value` で、`create_before_destroy` が無く `name` も `<prefix>-<key>` で固定。description を変えると作り直し（destroy → create）になり、上のとおり**ほかの SG のルールが古い SG を参照したままなので、付けるルートを消しても destroy は `DependencyViolation` で失敗する**（先に消されたルールだけが無い状態で止まる。041 の `review.md:70-90`）。既存の守り（`ops/up.sh:586-605`、`ops/oss/up.sh:152-160`）はキーを変えた古い SG が state にあるかを `state list` で見るだけで、description の違いは見ない。
- このサイクルで dialout / NLB の description も変えるので、守りは**キーを決め打ちせず、state にある workload の SG 全部の description をコードと比べ、1 つでも違えば止めて `ops/down.sh` の全消しだけを案内する**形にする。表（`SG_DESCRIPTION_ROOTS`）は持たない（Round 1 の Should 3: 表に無い SG を変えると素通りする）。

### 現物で確認した事実

- `terraform show -no-color`（state 全体）の形。Terraform v1.16.0 で `terraform_data` の `for_each` を apply して取った実物（scratchpad）:

  ```
  # terraform_data.workload["telegraf_dialout_nlb"]:
  resource "terraform_data" "workload" {
      id     = "98591e46-d166-5783-c82e-e39ded5fa25b"
      input  = {
          description = "Internal NLB (y)"
          name        = "p-telegraf_dialout_nlb"
      }
      output = {
          description = "Internal NLB (y)"
          name        = "p-telegraf_dialout_nlb"
      }
  }

  # terraform_data.workload["workflow"]:
  resource "terraform_data" "workload" {
      id     = "cf270c52-5af1-57ab-43c3-5230e7c724be"
      ...
  ```

  リソースごとに `# <アドレス>:` の行、`resource "<type>" "<name>" {`、属性は **4 空白**の字下げで `<名前> <幅合わせの空白>= <値>`、入れ子（map / list / block）の属性は 8 空白以上、ブロックの終わりは行頭の `}`、リソースの間は空行。`aws_security_group` の `description` は SG 本体の属性なので 4 空白の行に出る（`tests/test_oss_ops.py:357-372` の偽の `state show aws_vpc.this` も hashicorp/aws 6.x の同じ形）。state 全体には `aws_vpc_security_group_ingress_rule.flow["…"]` / `egress_rule` の `description`（通信の表の `why`）も 4 空白で出るが、別のリソースのブロック（別の `# …:` 見出し）の中なので、見出しで区切れば混ざらない。
- AWS の SG の description に使える文字は `a-zA-Z0-9 ._-:/()#,@[]+=&;{}!$*` で、`"` も改行もタブも入らない。コード側も `security_groups.tf` の HCL のリテラルでエスケープが無い。`"(.*)"` で取ってよく、`<キー>\t<文言>` の形で受け渡してよい。
- コードの description の置き場: `security_groups.tf:19-41` の `  security_groups = {` 〜 `  }`（行は `^    <キー> +"= "<文言>"$`。4 空白、`=` の前は幅合わせの空白）。OSS 版（`IaC/terraform/oss/base/core/` は全ファイルがマネージド版へのシンボリックリンクで、`oss.auto.tfvars:3` の `project = "nwc-oss"` だけが実体）は `oss.tf:11` の `oss = var.project == "nwc-oss"` で `oss.tf:17-24` の `  oss_security_groups = {` 〜 `  }`（kafka / efs / opensearch / victoriametrics / neo4j / spark）が `oss.tf:76-79` の `merge` で**上書き**され、`oss_replaced`（msk）が抜ける。つまり state の SG のコード側の文言は「OSS なら `oss_security_groups` が勝ち、無ければ `security_groups`」。
- `ops/down-common.sh:140-158` の `destroy_base_core` は Runtime の ENI が残るときも workload の SG を必ず消す。down 済みから up すると SG は新しく作られるだけで `must be replaced` は出ない（Round 1 の Should 2）。守りが実物の state で止まらないことを見るには、**立った環境で `ops/up.sh` を 2 回目に打つ**。
- `tf`（`ops/common.sh:32`）は `terraform -chdir="$TF_DIR/$root" "$@"`。`die`（`ops/common.sh:10`）は `NG: …` を stderr に出して `exit 1`。`tf_init`（`ops/up-common.sh:9`）は OSS 版では `-lockfile=readonly`。`OPS_DIR` はマネージド版 `ops`、OSS 版 `ops/oss`。`ops/oss/up.sh` は `ops/common.sh` と `ops/up-common.sh` を読む（`:37-38`）。
- `tests/test_oss_ops.py:334-403` の偽の terraform は `init` / `state list` / `state show aws_vpc.this` / `apply` / `output` / `destroy` に答え、ほかは `fake terraform: unknown` で落ちる。`up.sh（通し）` の土台は全ルートに `terraform.tfstate` を置く（`:518-523`。Round 1 で `security_groups.tf` の写しと OSS 版のシンボリックリンクも足した）ので、守りの塊は通しのテストでも走る。**`show -no-color` に答える枝を足さないと通しのテストが落ちる。**
- Round 1 のテスト（`tests/test_workflow.py` の `_csd_run`）は `ops/up-common.sh` から関数を切り出し、`tf` / `tf_init` / `die` を bash の関数で差し替えて動かす。この仕組みは Round 2 でも使う（`tf base/core show -no-color` に答える形に変える）。

## 設計方針

1. **守りは state にある workload の SG 全部の description をコードと比べる。** `ops/up-common.sh` の Round 1 の `SG_DESCRIPTION_ROOTS` / `sg_description_in_code` / `sg_description_in_state` / `check_sg_descriptions` を次の 3 つに置き換える。
   - `sg_descriptions_in_code`: `$TF_DIR/base/core/security_groups.tf` の `  security_groups = {` 〜 `  }` の各行から `<キー>\t<文言>` を出す。`$TF_DIR/base/core/oss.auto.tfvars` に `^project *= *"nwc-oss"` の行があれば（`oss.tf:11` と同じ判定）、`$TF_DIR/base/core/oss.tf` の `  oss_security_groups = {` 〜 `  }` も読み、同じキーは後者で上書きする（awk の連想配列で済む。`oss_replaced` の msk は OSS の state に無いので扱わない）。塊の中に空行とコメント以外で `<キー> = "<文言>"` の形から外れた行（行末のコメントなど）があれば、その行を stderr に出して失敗する（黙って捨てるとそのキーが「コードに無いキー」になって比較から外れる。Round 2 で足した）。
   - `sg_descriptions_in_state`: `tf base/core show -no-color` の出力（標準入力）から、`# aws_security_group.workload["<キー>"]:` の見出しで始まるブロック（次の行頭 `}` まで）の **4 空白ちょうど**の `description` の行（`^    description +"= "(.*)"$`）の最初の 1 つを `<キー>\t<文言>` で出す。ブロックに `description` の行が無ければ `<キー>\t`（空）を出す。
   - `check_sg_descriptions`: 先に `sg_descriptions_in_code` を打ち、失敗したか 1 つも読めなければ `die "$TF_DIR/base/core/security_groups.tf から SG の description が読めない（local.security_groups の形が変わったなら ops/up-common.sh の sg_descriptions_in_code を直す）。まだ何も作っていない"`（形が変わったときに全 SG を黙って飛ばさない。Round 2 で足した）。次に `tf base/core show -no-color` を **1 回だけ**打つ（失敗したら `die "$TF_DIR/base/core の state が読めない（terraform show の失敗。上のエラー）。まだ何も作っていない"`。既存の守りの `2>/dev/null` と違い、黙って通さない）。state のキーごとに、コードに同じキーがあれば文言を比べ、違うもの（空も含む）を集める。**コードに無いキーは飛ばす**（キーを変えた古い SG は既存の守りの担当）。1 つでも違えば die。ルートの state は見ない（`tf_init <root>` も `state list` も打たない）。文面:
     `$TF_DIR/base/core の state の SG の description がコードと違う: <キー>（state「<state の文言>」/ コード「<コードの文言>」）、<キー>（…）。description を変えると SG は作り直しで、ほかの SG のルールが古い SG を参照したままなので、付けるルートだけ消しても消せない（DependencyViolation）。先に $OPS_DIR/down.sh で全部消してから $OPS_DIR/up.sh。まだ何も作っていない`
     違うキーに `runtime` が含まれるときは、`まだ何も作っていない` の前に次を足す（`ops/down-common.sh:140-150` は Runtime の ENI があると runtime の SG を残すので、案内どおり `down.sh` を打っても抜けられない。既存の `internal` の守り `ops/up.sh:592` と同じ一言）: `runtime は Runtime の ENI（agentic_ai。最長 8 時間ほど残る）があるあいだ $OPS_DIR/down.sh も残すので、ENI が消えてから $OPS_DIR/down.sh を打つ。`
2. **呼ぶ場所は Round 1 のまま**: `ops/up.sh` と `ops/oss/up.sh` の `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]` の塊の末尾（telegraf_dialin の `fi` の後、`tf_init base/core` 済み、`log "1. ECR リポジトリ"` より前）。呼び出しの上のコメントは「キーはそのままで description を変えた SG（workflow は 041、telegraf_dialout / telegraf_dialout_nlb は 043）も作り直しで、ほかの SG のルールが古い SG を参照したままなので、付けるルートを消しても消せない。state の description がコードと違えば、全部消してもらう（ops/up-common.sh の check_sg_descriptions。全キーを見るので表は無い）」に直す。
3. **文言はコードから読む**（up.sh に写しを持たない）。awk 1 本で済ませ、BSD awk（macOS）で動く書き方にする（`match` + `substr`。`gensub` や `-v` 以外の GNU 拡張を使わない）。
4. **description を直す**（Round 1 のまま）。`security_groups.tf:27` → `Telegraf dial-out ECS task - SNMP traps behind the NLB (IaC/terraform/aws-managed/pipeline/stream)`、`:29` → `Internal NLB in front of the Telegraf dial-out, syslog-ng and GoFlow2 tasks (IaC/terraform/aws-managed/pipeline/stream)`。`:24` のコメントは「dialout と NLB の description は 043 で syslog_ng / goflow2 に合わせた（description を変えると作り直しで、ほかの SG のルールが古い SG を参照したままなので、付けるルートを消しても消せない。古い description の state があると ops/up.sh は check_sg_descriptions で止める。全キーを見るので表は無い）」にする。`:22-23` の「ops/up.sh は古いキーの state のまま stream があると止める」はそのまま。
5. **docs を揃える。** `docs/cml-sandbox.md:558` は Round 1 のまま（「NLB は trap（162/udp）を `lab` の SG からは受けない（`lab` の SG の 162 は送る側だけ。syslog の 5140 は 037 から受ける）」）。`core.md:102-103` と `vpc-perimeter.md:96-98` の Round 1 で足した 1 行を「state の description がコードと違う SG が 1 つでもあると、`ops/up.sh` は手順 0 の後で止まる（`ops/up-common.sh` の `check_sg_descriptions`。全キーを見る）。ほかの SG のルールが古い SG を参照したままなので、付けるルートだけ消しても消せない。`ops/down.sh` で全部消してから `ops/up.sh`」に替える。`troubleshooting.md:26`（Round 1 で足した行）は見出しを「手順 0 の後に「state の SG の description がコードと違う: …」で止まる」、対処を「description を変えた SG（workflow は 041、telegraf_dialout / telegraf_dialout_nlb は 043）が state にある（`ops/up-common.sh` の `check_sg_descriptions`）。作り直しで、ほかの SG のルールが古い SG を参照したままなので、ルートだけ消しても消せない（DependencyViolation）。まだ何も作っていない。`ops/down.sh`（OSS 版は `ops/oss/down.sh`）で全部消してから `ops/up.sh`。違うキーに runtime があるときは、Runtime の ENI（最長 8 時間ほど残る）があるあいだ `ops/down.sh` も runtime の SG を残すので、ENI が消えてから `ops/down.sh`（下の「消すとき」の `DependencyViolation` の行と同じ待ち）。2026-10-08（`48683dd`）より前の state は、description 末尾のパスが変わった（`terraform/…` → `IaC/terraform/aws-managed/…`、OSS 版の上書きは `oss/terraform/…` → `IaC/terraform/oss/…`）ので、パスを含む description（web / lab / lambda 以外の全部）が違う」に替える（**ルートだけ destroy する案内と必須変数の列挙は消す**）。
6. **QUEUE 147 に 043 の確認を足す**（PM が書く。エンジニアは QUEUE を触らない）: 「環境が立ったまま `ops/up.sh` を 2 回目に打ち、手順 0 の後の `check_sg_descriptions` が止まらずに手順 1 へ進むこと（実物の `terraform show` の形の確認。古い state は作れないので die する側は偽の `tf` のテストだけ）」。

やらないこと: SG に `create_before_destroy` を付ける（name が固定で同名の SG は作れない。名前にランダムを足すと全ルートの参照が変わる）。キーを変えた既存の守り 3 つをこの関数に統合する（state にコードに無いキーがあるかを見るもので、別物。「stream だけ先に消してもよい」もそちらでは正しい）。`terraform show -json` + Python（`PY` は `ops/up.sh:438` / `ops/oss/up.sh:131` で守りの前に決まるので使えるが、既存の守りと同じ sed / awk で足りる）。

## 変更対象ファイル

- `ops/up-common.sh` — Round 1 の表と 3 関数を、設計方針 1 の 3 関数に置き換える（`has_resources` の後）
- `ops/up.sh`、`ops/oss/up.sh` — 呼び出しは Round 1 のまま。コメントを設計方針 2 に
- `IaC/terraform/aws-managed/base/core/security_groups.tf` — `:24` のコメントを設計方針 4 に（`:27` / `:29` は Round 1 のまま）
- `docs/architecture/core.md`、`docs/architecture/resources/vpc-perimeter.md`、`docs/troubleshooting.md` — 設計方針 5
- `tests/test_workflow.py` — Round 1 の `_csd_run` と check を検証 1〜2 に合わせて書き直す
- `tests/test_oss_ops.py` — 偽の terraform に `show -no-color` の枝を足す（検証 3）
- `docs/cycles/043-sg-description-guard/build.md` — `## Round 2` を追記

## 再利用するもの

- `tf` / `tf_init` / `die`（`ops/common.sh`、`ops/up-common.sh`）、既存の守りの形と die の文面
- Round 1 の実装（commit `7843952`）のうち、description の修正、`cml-sandbox.md`、呼び出しの位置、`_csd_run` の仕組み（`tf` / `tf_init` / `die` を関数で差し替えて bash で動かす）、`test_oss_ops.py` の土台への `security_groups.tf` の写し
- `tests/test_oss_ops.py:357-372` の偽の `state show` の出力（hashicorp/aws 6.x の形の見本）

## 実装ステップ

1. `ops/up-common.sh` の Round 1 の表と 3 関数を消し、設計方針 1 の 3 関数を足す（`local` を使う。`set -u` で落ちない。awk は BSD で動く）
2. `ops/up.sh` と `ops/oss/up.sh` の呼び出しのコメントを設計方針 2 に直す
3. `security_groups.tf:24` のコメントを設計方針 4 に直す。`terraform fmt -check`（`IaC/terraform/aws-managed/base/core`）が通ること
4. docs 3 本を設計方針 5 に直す（`cml-sandbox.md` は触らない）
5. テストを書き直す（検証 1〜5）。`bash -n` を `ops/up.sh` / `ops/oss/up.sh` / `ops/up-common.sh` に 1 本ずつ（並べると 1 本目しか見ない）
6. `/cycle-build` 手順6 のセルフレビュー（`build.md` に `## Round 2`）

## 検証方法（期待出力つき）

1. `tests/test_workflow.py` の静的な check: `ops/up.sh` に `check_sg_descriptions` の呼び出しが 1 回あり、`tf_init base/core` より後、`log "1. ECR リポジトリ` より前。`ops/up-common.sh` に `SG_DESCRIPTION_ROOTS` が無く、`sg_descriptions_in_code` / `sg_descriptions_in_state` / `check_sg_descriptions` の 3 関数がある。`check_sg_descriptions` の本文に `tf_init` と `state list` が無い（ルートの state を見ない）。
2. `tests/test_workflow.py` の動かす check（`_csd_run`: 3 関数を `ops/up-common.sh` から切り出し、一時ディレクトリの `TF_DIR`（`base/core/security_groups.tf` と `oss.tf` は実物のコピー。OSS の場合は `oss.auto.tfvars` に `project = "nwc-oss"`）、`OPS_DIR`、関数 `tf` / `tf_init` / `die` を差し替えて bash で実行。偽の `tf` は `base/core show -no-color` に、設計で引用した形のサンプル ── `# aws_vpc.this:` のブロック、`# aws_vpc_security_group_ingress_rule.flow["nautobot-neo4j-tcp-7687"]:` のブロック（4 空白の `description = "Neo4j Bolt - Nautobot sync"` 入り）、`# aws_security_group.workload["<キー>"]:` のブロック（`arn` / `description` / `egress = []` / `id` / `ingress = []` / `name` / `tags = { … }` の順。`tags` の中は 8 空白）、最後にもう 1 つルールのブロック ── を返す。呼び出しは `calls` に残す）:
   - state の description が全キーともコードと同じ → 終了コード 0、stderr に `DIE:` 無し、`calls` に `show -no-color` が 1 回、`init` と `state list` が 0 回
   - workflow が `Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)` → 終了コード 1、stderr に `DIE:`、`workflow（state「Temporal dev server and worker ECS task`、`コード「Temporal server, UI and worker`、`先に ops/down.sh で全部消してから ops/up.sh`。`だけ先に消してもよい` は無い。`calls` に `init` も `state list` も無い
   - workflow と telegraf_dialout_nlb（`Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)`）の 2 つが古い → `DIE:` に `workflow（` と `telegraf_dialout_nlb（` の両方。`ENI が消えてから` は無い
   - runtime が `AgentCore Runtime ENIs (terraform/agent)`（実物の OSS 版の state の文言）で、ほかは同じ → 終了コード 1、`DIE:` に `runtime（` と `ENI が消えてから ops/down.sh を打つ`、その後ろに `まだ何も作っていない`
   - `security_groups.tf` の写しの workflow の行末に ` # 041` を足す → 終了コード 1、`DIE:` に `description が読めない`、stderr にその行
   - `security_groups.tf` の写しの塊を空にする → 終了コード 1、`DIE:` に `description が読めない`
   - state に `telegraf`（コードに無いキー）が古い文言であり、ほかは同じ → 終了コード 0（キーを変えた SG は既存の守りの担当）
   - state の workload の SG が 1 つも無い（`aws_vpc.this` とルールのブロックだけ）→ 終了コード 0
   - `tf base/core show` が失敗（終了コード 1）→ 終了コード 1、`DIE:` に `state が読めない`
   - OSS（`oss.auto.tfvars` あり）: state の spark が `oss.tf` の文言（`Spark ECS task, local mode …`）→ 終了コード 0。spark が `security_groups.tf` の文言（`EMR Serverless workers …`）→ `DIE:` に `spark（`。`oss.auto.tfvars` 無しで spark が `EMR Serverless workers …` → 終了コード 0
   - ルールのブロックの `description`（`Neo4j Bolt - Nautobot sync`）が SG の文言として拾われない（サンプルの順番を、ルール → SG → ルール にしてある）
   - `sg_descriptions_in_code` を実物に対して打つ: 出すキーの集合が、Python の `re.findall(r'^    (\w+)\s+= "([^"]*)"$', <security_groups = { … } の塊>, re.M)` と一致し（`workflow` / `telegraf_dialout` / `telegraf_dialout_nlb` / `spark` / `msk` を含む）、文言も一致する。OSS（`oss.auto.tfvars` あり）なら `spark` は `oss.tf` の文言で、`kafka` がある
3. `tests/test_oss_ops.py`: `ops/oss/up.sh` の呼び出しの位置（Round 1 のまま）。偽の terraform に `show` の枝（`rest[:1] == ["show"]`）を足し、`# aws_vpc.this:` のブロック（`state show` と同じ文面）だけを返す → `up.sh（通し）` の check が全部通る（workload の SG が無いので守りは通す）。
4. `tests/test_workflow.py:2100` の隣の telegraf の description の check は Round 1 のまま。
5. 文言（`docs/cycles/` は 043 自身の design / build / review に当たるので除く）: `grep -rn --exclude-dir=cycles 'SG_DESCRIPTION_ROOTS' ops docs tests IaC` が 0 件。`grep -rn --exclude-dir=cycles 'だけ先に消してもよい' ops docs` がキーを変えた既存の守りの 3 行（`ops/up.sh` の telegraf / telegraf_dialin、`ops/oss/up.sh` の telegraf_dialin）だけ。`grep -n 'worker_image_tag=destroy' docs/troubleshooting.md` が 0 件。`grep -c 'check_sg_descriptions' docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md docs/troubleshooting.md` が各 1 以上。`grep -rn 'and MDT' IaC docs --include='*.tf' --include='*.md'` が QUEUE と 041 / 043 のサイクルの docs 以外で 0 件。`grep -n 'NLB の SG が' docs/cml-sandbox.md` が 0 件。`grep -c 'ENI が消えてから' ops/up-common.sh docs/troubleshooting.md` が各 1 以上。`grep -c '2026-10-08' docs/troubleshooting.md` が 1 以上。
6. 全テスト: `python3 tests/test_workflow.py` と `python3 tests/test_oss_ops.py` が失敗 0（Round 2 で 454 / 208）。`python3 tests/test_stream.py` も失敗 0。`terraform fmt -check -recursive IaC/terraform/aws-managed/base/core` が出力無し。`bash -n` を 1 本ずつ 3 回打って無言。
7. AWS: このサイクルでは動かさない。QUEUE 147 でまとめて見る（設計方針 6）。

## 未確定事項とリスク

1. `terraform show -no-color` の `aws_security_group` の実物は手元に無い（`terraform_data` のサンプルと、`tests/test_oss_ops.py` の偽の `aws_vpc` の形から、4 空白の `description` の行と `# …:` の見出しを前提にした）。見出しとブロックの区切りは Terraform 本体の整形（provider に依らない）。実物は QUEUE 147 の 2 回目の `ops/up.sh` で確かめる。
2. `terraform show` は base/core の state 全体（SG のルールが 100 行超）を出す。1 回だけ打つので時間は `state list` と同じ程度。awk が全部読むので SIGPIPE は起きない。
3. `DependencyViolation` が実際に起きることは AWS で確かめていない（provider の実装と 041 の `review.md:72-75` から）。守りは「違えば必ず止める」側なので、起きない場合でも損は「down.sh を 1 回余計に打つ」だけ。
4. OSS の `oss_replaced`（msk）は扱わない。OSS の state に msk は無いので比較に出てこない。
