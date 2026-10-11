# `aws_security_group.endpoints` の description も `check_sg_descriptions` の守りに入れる（046）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。ベースは main の `087fa23`。Round 2（cold review Round 1 の Should fix 1 と Nit 5 を取り込み）で更新。

## 背景

- QUEUE 159（043 の cold review Round 1 の Nit 3 と Round 2 の Nit 4）。043 の守り `check_sg_descriptions`（`ops/up-common.sh:28-82`）は、state にある `aws_security_group.workload["<キー>"]` 全部の description をコードの `local.security_groups`（OSS 版は `local.oss_security_groups` で上書き）と比べ、違えば `ops/down.sh` の全消しを案内して止める。
- 同じ `security_groups.tf` にはもう 1 つ SG がある。`aws_security_group.endpoints`（`IaC/terraform/aws-managed/base/core/security_groups.tf:189-195`。インターフェース型と OpenSearch Serverless の VPC エンドポイントに付ける。for_each 無しの単独リソース）。`local.sg_keys = concat(keys(local.workload_security_groups), ["endpoints"])` でルールの表に入り、workload の SG のルール（`aws_vpc_security_group_*_rule.flow`）が `referenced_security_group_id` で参照する。
- だから endpoints の description を変えたときも workload と同じ: name 固定で作り直し、ほかの SG のルールが古い SG を参照したままなので、付けるルートだけ消しても `DependencyViolation`。いまの守りは見出しが `# aws_security_group.workload["…"]:` の形だけを読むので、endpoints は素通りし、`terraform apply` の途中で止まる（043 の「まだ何も作っていない」の保証が効かない）。
- OSS 版の `IaC/terraform/oss/base/core/security_groups.tf` はマネージド版へのシンボリックリンク（`tests/test_oss_ops.py:536`）で、`oss.tf` は endpoints を上書きしない（`grep -n endpoints IaC/terraform/aws-managed/base/core/oss.tf` は flow の表の 1 行だけ）。守りは両版で同じ 1 本（`ops/up.sh:609` と `ops/oss/up.sh:163` が呼ぶ）。

## 現物で確認した事実（推測ではない）

コード側（`security_groups.tf:189-195`。description は 2 空白の属性、値はプレーンな文字列 1 行）:

```
resource "aws_security_group" "endpoints" {
  name        = "${local.name_prefix}-endpoints"
  description = "VPC endpoints - HTTPS from the workload SGs, no outbound"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name_prefix}-endpoints" }
}
```

state 側（main のチェックアウトの `base/core` で 2026-10-11 に `terraform show -no-color` を打った実物。endpoints は 045 の `down.sh` で消えているので、同じ「for_each 無し」の `aws_vpc.this` と、残っている workload の形を引く）:

```
# aws_security_group.workload["runtime"]:
resource "aws_security_group" "workload" {
    arn                    = "arn:aws:ec2:ap-northeast-1:493116771193:security-group/sg-0924fddfd9a541885"
    description            = "AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)"
…
# aws_vpc.this:
resource "aws_vpc" "this" {
    arn                                  = "arn:aws:ec2:ap-northeast-1:493116771193:vpc/vpc-05374043e4fbcdddb"
```

for_each 無しのリソースの見出しは `# <アドレス>:`（インデックス無し）。endpoints は `# aws_security_group.endpoints:` になり、本体の属性は workload と同じ 4 空白ちょうど（`    description            = "…"`）。見出しの中の属性の並びは英字順なので description は arn の次に来る（endpoints の実物の行は 046 では見られない。「未確定事項とリスク」1）。

## 設計方針

1. **守りの 3 関数のうち、読む側 2 つに endpoints を足す。比べる側 `check_sg_descriptions` は変えない。**
   - `sg_descriptions_in_code`: `security_groups.tf`（file 1）の `resource "aws_security_group" "endpoints" {` から行頭 `}` までのブロックの、2 空白ちょうどの `  description = "…"`（`match($0, /^  description += "/)` と行末が `"`）を読み、`endpoints\t<文言>` を**workload の全キーの後に 1 行**出す。ブロックが無い・description が読めない（式になった、行末にコメント、複数行、値の中にエスケープされていない `"`（行末のコメントの中の `"` も）、`${` / `%{` の補間）なら、既存の読めない行と同じく stderr に `NG: aws_security_group.endpoints の description が読めない` を出して `exit 1`（`check_sg_descriptions` の 1 つ目の die「…から SG の description が読めない」で止まる）。`oss.tf`（file 2）では resource ブロックを読まない（endpoints の上書きは無い）。workload の表に `endpoints` というキーがあれば同じく NG（キーの衝突。いまは無い）
   - `sg_descriptions_in_state`: 見出し `# aws_security_group.endpoints:` で始まる行（前方一致。後ろに ` (tainted)` が付いても読む。workload と同じ）をキー `endpoints` として扱う。ブロックの読み方（行頭 `}` まで、4 空白ちょうどの description の最初の 1 つ、無ければ空）は workload と同じコードを通す
   - workload の表（`local.security_groups` / `oss_security_groups`）の行も同じ 2 つ（エスケープされていない `"`、`${` / `%{`）を読めない行扱いにする（cold review Round 1 の Should fix 1。043 からの同じ穴で、行末のコメントの中の `"` が値に混ざって誤った die（全部の作り直しを勧める）になる）。OSS 版で `oss.tf` に `  oss_security_groups = {` の塊が無ければ、file 1 の `security_groups = {` と同じく NG で止める（黙って OSS のキーを飛ばさない。cold review Round 1 の Nit 5）
   - endpoints の 1 行を必ず出すので、`local.security_groups` の塊が無い・キーが 0 ならコードを読む側で NG にする（`check_sg_descriptions` の `[ -n "$code" ]` は効かなくなる。Round 1 の逸脱）
   - `check_sg_descriptions`: そのまま。state に endpoints のブロックが無ければ（初回、または消した後）比べる行が無いので通る。違えば die の文言に `endpoints（state「…」/ コード「…」）` が workload と同じ「、」区切りで並ぶ（並びは state の `terraform show` の順）。ENI の一言は `runtime` のときだけ（endpoints には付かない）
2. **コードは直さない。** endpoints の description はいまの文言のままにする（変えると、立てたままの環境で作り直しになる。守りを足すサイクルで守りの対象を同時に変えない）
3. **AWS では動かさない。** 変更は bash の awk と Python のテストと docs だけで、手元のテスト（`tests/test_workflow.py` の `_csd_run`、`tests/test_oss_ops.py` の実物の `check_sg_descriptions`）で閉じる。次に AWS で立てるときの 2 回目の `up.sh` が、endpoints を含めて通ることの確認になる（045 の A-1 と同じ経路）
4. **docs は「workload だけ」と読める箇所を直す。** 全体設計 HTML は触らない（構成は変わらない）

## 変更対象ファイル

- `ops/up-common.sh`: `sg_descriptions_in_code`（endpoints の resource ブロックを読む）、`sg_descriptions_in_state`（`# aws_security_group.endpoints:` の見出し）、24〜27 行と各関数のコメント（「workload の SG 全部」→「workload の SG 全部と endpoints」）
- `tests/test_workflow.py`: `_csd_show` / `_csd_run` の土台に endpoints を足し、check を足す（下の「検証方法」の T-1〜T-8）
- `IaC/terraform/aws-managed/base/core/security_groups.tf`: コメントだけ（14 行目の「SG の description は変えると作り直しになる」の行と、188 行目の endpoints の説明に「description を変えると workload と同じ作り直し。`ops/up.sh` の `check_sg_descriptions` が見る（046）」）。リソースの属性は 1 文字も変えない
- `ops/up.sh:606-608` と `ops/oss/up.sh:160-162` のコメント: 「全キーを見るので表は無い」→「workload の全キーと endpoints を見るので表は無い」
- `docs/troubleshooting.md:26`: 行の「description を変えた SG（…）が state にある」の括弧に `endpoints`（VPC エンドポイントの SG。046 から）を足す
- `docs/architecture/core.md:103` と `docs/architecture/resources/vpc-perimeter.md:98`: 「全キーを見る」→「workload の全キーと `endpoints` を見る」
- `docs/cycles/QUEUE.md`: 159 に着手印（この設計で付けた）

## 再利用するもの

- `ops/up-common.sh:28-82` の 3 関数と、その awk の形（`match` と `substr` の読み方、`flush()`、`bad` の集め方）。`check_sg_descriptions` の比較と die の文言はそのまま
- `tests/test_workflow.py:1462-1600` の土台: `_upc_fn`（関数の切り出し）、`_SG_CODE_DESC`（workload の表の正規表現）、`_csd_show`（偽の `terraform show`）、`_csd_run`（bash で打つ）、`_csd_no_root`、既存の check 12 本
- `tests/test_oss_ops.py:1226-1241`（実物の `check_sg_descriptions` を OSS の TF_DIR で打つ）と `:376`（偽の `show`。workload を載せない）。endpoints のブロックが無いまま通るので、変えない
- 043 の `design.md` / `review.md`（守りの理由、cold review の Nit 3 / Nit 4 の文面）

## 実装ステップ

1. `ops/up-common.sh` の `sg_descriptions_in_code`: awk に file 1 の `resource "aws_security_group" "endpoints" {` 〜 `}` のブロック読みを足す（`inb` とは別のフラグ `inr`。ブロックの中は description の行だけ読み、ほかの行（name / vpc_id / tags / 空行 / コメント）は読めない行扱いにしない）。END で、見つからなければ `bad` に足して exit 1、workload に `endpoints` キーがあれば同じく exit 1、見つかれば workload の後に `endpoints\t…` を出す。コメントを直す
2. `sg_descriptions_in_state`: `/^# /` の分岐に `$0 == "# aws_security_group.endpoints:"` → `key = "endpoints"` を足す。コメントを直す
3. `tests/test_workflow.py`: `_SG_EP_DESC` を実物の `security_groups.tf` の resource ブロックから正規表現で取る（`^resource "aws_security_group" "endpoints" \{\n(?:.*\n)*?  description += "([^"]*)"\n`。取れなければ `assert` で土台の崩れを知らせる）。`_csd_show(sgs, endpoints=…)` に endpoints のブロック（見出し `# aws_security_group.endpoints:`、`resource "aws_security_group" "endpoints" {`、4 空白の arn / description / id / name / tags、行頭 `}`）を workload の後に足す。値の意味は workload と同じ（文字列 = その description、`None` = description の行が無く 12 空白の入れ子だけ、`False` = ブロックごと無し）。既定は `_SG_EP_DESC`。`_csd_run(..., endpoints=…, code_ep_broken=False)` を通し、`code_ep_broken` はコピーの `security_groups.tf` の endpoints の description の行を `  description = local.endpoints_description` に置き換える（式。1 行だけ読めない）
4. check を足す（T-1〜T-8）。既存の 12 本は、既定の偽 state に endpoints のブロックが入った状態でそのまま通ること
5. コメントと docs（変更対象ファイルの 3〜6 番目）。`docs/troubleshooting.md` の check を 1 本足す（T-9）
6. `/cycle-build` 手順6 のセルフレビュー（`/robust` + 反対弁護人）。cold reviewer は PM が呼ぶ

## 検証方法（期待出力つき）

- `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` が exit 0
- `cd <worktree> && python3 tests/test_workflow.py` と `python3 tests/test_oss_ops.py` の末尾が「失敗 0」（ほかのテスト `tests/test_*.py` も全部「失敗 0」。`for t in tests/test_*.py; do python3 "$t" | tail -1; done`）
- 実物のコードから読む: `TF_DIR=IaC/terraform/aws-managed bash -c 'source ops/up-common.sh 2>/dev/null; sg_descriptions_in_code' | tail -1` が `endpoints<TAB>VPC endpoints - HTTPS from the workload SGs, no outbound`（`source` が他を評価して失敗するなら、テストの `_upc_fn` と同じく関数だけ切り出して打つ）
- 実物の state から読む: main のチェックアウトの `IaC/terraform/aws-managed/base/core` で `terraform show -no-color | sg_descriptions_in_state` が `runtime<TAB>AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)` の 1 行だけ（endpoints は消えているので出ない。state を書かない読み取りだけ。`terraform` の他のサブコマンドは打たない）
- `tests/test_workflow.py` の check（名前は意味が分かる日本語で。番号は設計の参照用）:
  - T-1: `_csd_run()`（既定。state に endpoints がコードと同じ description で載る）が rc=0、`DIE:` 無し、`tf base/core show -no-color` が 1 回、init も state list も無し
  - T-2: `_csd_run(endpoints="VPC endpoints SG")`（古い文言）が rc≠0、stderr に `endpoints（state「VPC endpoints SG」/ コード「VPC endpoints - HTTPS from the workload SGs, no outbound」）` と `ops/down.sh で全部消してから ops/up.sh` と `まだ何も作っていない` を含み、`Runtime の ENI` を含まない
  - T-3: `_csd_run(state={**_SG_CODE_DESC, "workflow": _WF_OLD}, endpoints="VPC endpoints SG")` が rc≠0、stderr に `endpoints（state「VPC endpoints SG」/ コード「…」）、workflow（state「…」/ コード「…」）` の順（「、」区切り。state の並び。実物の `terraform show` はアドレス順で endpoints が workload より前）で両方
  - T-4: `_csd_run(endpoints=False)`（state にブロックが無い。初回）が rc=0、`DIE:` 無し
  - T-5: `_csd_run(endpoints=None)`（ブロックはあるが description の行が無く、12 空白の入れ子だけ）が rc≠0、stderr に `endpoints（state「」/ コード「VPC endpoints - …」）`
  - T-6: `_csd_run(code_ep_broken=True)` が rc≠0、stderr に `aws_security_group.endpoints の description が読めない` と `security_groups.tf から SG の description が読めない` と `まだ何も作っていない`、calls に `tf base/core show -no-color` が無い（コードを読んだ時点で止まる）
  - T-7: `_csd_run(oss=True, endpoints="VPC endpoints SG")` が rc≠0、stderr に `endpoints（state「VPC endpoints SG」/ コード「…」）`（OSS 版も同じ 1 本で守る）。`_csd_run(oss=True)` は rc=0
  - T-8: `_SG_EP_DESC == "VPC endpoints - HTTPS from the workload SGs, no outbound"`（実物の文言。変えたら守りの対象が変わったとレビューで気付く）
  - T-10（Round 2）: コピーの `security_groups.tf` の endpoints の description の行末に ` # was "VPC endpoints SG"` を足すと rc≠0、stderr に `aws_security_group.endpoints の description が読めない`、calls に `tf base/core show -no-color` が無い（いまは rc=0 で値が `…no outbound" # was "VPC endpoints SG` になり、state と違うと誤って die する）
  - T-11（Round 2）: workload の表の 1 行（例 `workflow`）の行末に ` # 041 "old"` を足すと rc≠0、stderr に `local.security_groups / oss_security_groups の読めない行`。同じ行の値を `"Temporal ${var.x}"` にしても同じ NG
  - T-12（Round 2）: `_csd_run(oss=True)` でコピーの `oss.tf` の `  oss_security_groups = {` を `  oss_security_groups = merge({` にすると rc≠0、stderr に NG（いまは rc=0 で OSS のキーを黙って飛ばす）
  - T-9: `docs/troubleshooting.md` の 26 行目の行（`手順 0 の後に「state の SG の description がコードと違う`）に `endpoints` を含む
- 既存の check（`check_sg_descriptions:` で始まる 12 本と `tests/test_oss_ops.py:1222` と `:1241`）が全部通ったまま
- 変異テスト（セルフレビューで 1 回ずつ。直したら戻す）: `sg_descriptions_in_state` の endpoints の分岐を消すと T-2 / T-3 / T-5 / T-7 が落ちる。`sg_descriptions_in_code` の endpoints の出力を消すと T-2〜T-7 が落ちる（T-6 は NG の文言で）

## 未確定事項とリスク

1. ~~endpoints の実物の `terraform show` の行は 046 の中では見られない~~ → Round 1 でエンジニアが 10-11 03:32 の実物の写し（045 の環境。endpoints が生きていた state）で確かめた。見出しは `# aws_security_group.endpoints:`、4 空白の description はコードと同じ。QUEUE には足さない。元の記述:（045 の `down.sh` で消えている。立て直すのは費用に見合わない）。見出しの形 `# aws_security_group.endpoints:` は同じ for_each 無しの `# aws_vpc.this:` の実物から、本体の 4 空白の description は workload の実物から取った。外れていたら守りが endpoints を読まずに通る（いまと同じで、悪化はしない）。次に AWS で立てるサイクルの 2 回目の `up.sh` のログの後に、`terraform show -no-color | grep -n -A3 '^# aws_security_group.endpoints:'` を 1 回打って記録に残す ── QUEUE に `- [ ]` で足す
2. `sg_descriptions_in_code` の resource ブロック読みは `security_groups.tf` の書き方（resource の名前と `{` が同じ行、description が 2 空白ちょうどでプレーンな 1 行）に依る。形が変わると「読めない」で `up.sh` が止まる（黙って素通りはしない）。それが 043 と同じ設計（読めない行は止める）
3. 既定の偽 state に endpoints のブロックを足すので、既存の check 12 本の前提（`terraform show` の中身）が変わる。`_csd_show` の順は実物のアドレス順（ルール → endpoints → vpc → workload → ルール）に合わせる。die の文言の順は endpoints が先になるので、既存の check の期待で順に依るものは直す（Round 1 で 3 本直した）
4. `docs/troubleshooting.md:26` は 1 行が長い表の行。文言を足すだけで、表の列（`|` の数）を崩さない
