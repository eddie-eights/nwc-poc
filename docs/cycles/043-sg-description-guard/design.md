# description を変えた SG が残っていれば up.sh を止め、telegraf の SG の説明を揃える（043）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。

## 背景

- QUEUE 145: 041 で「NLB は trap を lab の SG から受けない」の言い方を揃えたが、`docs/cml-sandbox.md:558` の見送った案の表（「trap の 162 は NLB の SG が `lab` の SG から受けない（syslog の 5140 は 037 から受ける）」）が残った。`base/core/security_groups.tf:27` の telegraf_dialout の description は「traps, syslog and MDT」のまま（syslog は 012 で syslog_ng、MDT は 013 で gnmic に移り、dialout が受けるのは trap だけ）、`:29` の telegraf_dialout_nlb は「in front of the Telegraf dial-out task」のまま（NLB の後ろは dialout / syslog_ng / goflow2 の 3 つ）。`:24` のコメント「description は cycle 012 で syslog と MDT が抜けても変えない（変えると作り直し）」がそれを正当化している。
- QUEUE 146: `aws_security_group.workload`（`security_groups.tf:151-159`）は `description = each.value` で、`create_before_destroy` が無く `name` も `<prefix>-<key>` で固定。description を変えると作り直し（destroy → create）になり、その SG を ENI（ECS のタスク、NLB）が付けたままだと destroy が `DependencyViolation` で失敗し、先に消されたルールだけが無い状態で止まる（041 の `review.md:70-90`）。041 で workflow の description を変えたので、041 より前の state に workflow のルートが残ったまま `ops/up.sh` を打つとこうなる。既存の守り（`ops/up.sh:586-605`、`ops/oss/up.sh:152-160`）は**キーを変えた**古い SG（`internal` / `telegraf` / `telegraf_dialin`）が state にあるかを `state list` で見るだけで、description の違いは見ない。
- このサイクルで dialout / NLB の description も変えるので、守りはキーを決め打ちせず「state の description がコードと違う SG を、付けるルートが残ったまま apply しない」の形にする（workflow は 041、telegraf_dialout / telegraf_dialout_nlb は 043、以後 description を変えるたびに表へ 1 行足す）。

### 現物で確認した事実

- `terraform state show` の属性行は `    description = "…"` の形（`=` の前後は幅合わせの空白）。`aws_security_group.workload` はインラインの `ingress {` / `egress {` を持たない（`grep -c 'ingress {' security_groups.tf` → 0。ルールは別リソース）ので、`description` の行は SG 本体の 1 行だけ。念のため `head -n 1` で最初の行を取る（属性は名前順で、`description` は `egress` / `ingress` より前）。
- コードの description は `security_groups.tf` の `local.security_groups` の `    <キー> = "<文言>"`（4 空白、`=` の前は幅合わせの空白）。OSS 版の `IaC/terraform/oss/base/core/security_groups.tf` はこのファイルへのシンボリックリンクで、`oss.tf:14-23` が差し替えるのは `msk` を抜いて `kafka` / `efs` / `opensearch` / `victoriametrics` / `spark` を足すだけ。workflow / telegraf_dialout / telegraf_dialout_nlb は両版で同じ文言。
- SG を付けるルート: workflow は `workflow/locals.tf:105`（`security_group_ids["workflow"]`）、telegraf_dialout と telegraf_dialout_nlb は `pipeline/stream/locals.tf:53,55`（NLB は `pipeline/stream/telegraf.tf:69`、タスクは ECS のサービス）。
- `tf`（`ops/common.sh:32`）は `terraform -chdir="$TF_DIR/$root" "$@"`。`tf_init`（`ops/up-common.sh:9`）は OSS 版では `-lockfile=readonly`。`OPS_DIR` はマネージド版 `ops`（`ops/common.sh:7`）、OSS 版 `ops/oss`（`ops/oss/up.sh:41`）。`ops/oss/up.sh` は `ops/common.sh` と `ops/up-common.sh` を読む（`:37-38`）ので、共通の関数は `ops/up-common.sh` に置けば両方から呼べる。
- 既存の守りのテスト: `tests/test_workflow.py:1457-1465`（`up` の文字列で `grep -qxF 'aws_security_group.workload["<key>"]'` が `log "1. ECR リポジトリ` より前にある）、`tests/test_oss_ops.py:1203-1206`（`pos()` で順序）。`tests/test_workflow.py:1268` は `ops/up-common.sh` から `run_temporal_init() {` 〜 `\n}\n` を切り出して bash で動かす（`:1288-1296`。PATH に偽の `aws` を置く）。同じ切り出しで新しい関数も bash で動かせる。
- `tests/test_workflow.py:2009-2011` は workflow の description を見る（`_wf_sg_desc`）。telegraf の description を見るテストは無い。
- docs で守りを説明している所: `docs/architecture/core.md:102-103`、`docs/architecture/resources/vpc-perimeter.md:96-98`（「description を変えると作り直しになり、ENI が付いていると消えない。変えるときは先に `ops/down.sh`」）、`docs/troubleshooting.md:25`（「2026-09-29 より前の SG（internal）が残っている」で止まる行）。telegraf のキーの守り（2026-10-04 / 10-09）は docs に行が無い。

## 設計方針

1. **守りは description の比較にする。** `ops/up-common.sh` に `SG_DESCRIPTION_ROOTS="workflow:workflow telegraf_dialout:pipeline/stream telegraf_dialout_nlb:pipeline/stream"` と関数 `check_sg_descriptions` を置く。キーごとに `tf base/core state show -no-color 'aws_security_group.workload["<key>"]'` の `description` を sed で取り（state に無ければ飛ばす）、`$TF_DIR/base/core/security_groups.tf` の同じキーの文言と比べ、違っていて、かつ付けるルートの state にリソースがあれば `die`。文面は既存の守りに合わせる: `<TF_DIR>/base/core の state の SG <key> の description（<state の文言>）がコード（<コードの文言>）と違い、<root> がその SG を付けている。description を変えると作り直しで、付けたままでは消せない（DependencyViolation）。先に <OPS_DIR>/down.sh で消す（<root> だけ先に消してもよい）。まだ何も作っていない`。コードに文言が無い（表のキーの綴り違い）なら、それも `die`（黙って素通りさせない）。
2. **呼ぶ場所は既存の守りの塊の末尾**（`ops/up.sh` の `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]` の中、telegraf_dialin の `fi` の後。`ops/oss/up.sh` も同じ塊の中）。`tf_init base/core` 済みなので `state show` が動く。`log "1. ECR リポジトリ"` より前。
3. **文言はコードから読む**（up.sh に写しを持たない）。sed は `s/^    <key> *= "\(.*\)"$/\1/p`。テストで「表の全キーについて sed の結果が空でなく、Python の正規表現で取った文言と一致する」を押さえて、書式のずれを検出する。
4. **description を直す。** `security_groups.tf:27` → `Telegraf dial-out ECS task - SNMP traps behind the NLB (IaC/terraform/aws-managed/pipeline/stream)`、`:29` → `Internal NLB in front of the Telegraf dial-out, syslog-ng and GoFlow2 tasks (IaC/terraform/aws-managed/pipeline/stream)`。`:24` のコメントは「dialout と NLB の description は 043 で syslog_ng / goflow2 に合わせた（変えると作り直し。古い description の state のまま stream があると ops/up.sh は check_sg_descriptions で止める。description を変えたら ops/up-common.sh の SG_DESCRIPTION_ROOTS に足す）」に替える。`:22-23` の「ops/up.sh は古いキーの state のまま stream があると止める」はそのまま。
5. **docs を揃える。** `docs/cml-sandbox.md:558` の括弧を「NLB は trap（162/udp）を lab の SG からは受けない（lab の SG の 162 は送る側だけ。syslog の 5140 は 037 から受ける）」にする（041 の `core.md:84` / `pipeline.md:349` / `security_groups.tf:117` と同じ言い方）。`core.md:102-103` と `vpc-perimeter.md:96-98` の「変えるときは先に ops/down.sh」の後ろに「state の description がコードと違う SG（workflow、telegraf_dialout、telegraf_dialout_nlb）を付けるルートが残っていると、`ops/up.sh` は手順 0 の後で止まる（`ops/up-common.sh` の `check_sg_descriptions`。description を変えたら `SG_DESCRIPTION_ROOTS` に足す）」を足す。`troubleshooting.md:25` の次に、この守りで止まる行を 1 行足す（原因: description を変えた SG を付けるルートが残っている。対処: `ops/down.sh`、またはそのルートだけ `terraform -chdir=… destroy`）。
6. **QUEUE 147 に 043 の確認を 1 行足す**（PM が書く。エンジニアは QUEUE を触らない）: 「base/core の apply で telegraf_dialout / telegraf_dialout_nlb の 2 つが作り直される（down 済みから up するので DependencyViolation は出ない）。apply のログに `must be replaced` が 2 つ、`ops/up.sh` が通ること」。

やらないこと: SG に `create_before_destroy` を付ける（name が固定で同名の SG は作れない。名前にランダムを足すと全ルートの参照が変わる）。`state list` だけの既存の守りをこの関数に統合する（キーを変えた守りは state に古いキーがあるかを見るもので、description の比較では拾えない）。

## 変更対象ファイル

- `ops/up-common.sh` — `SG_DESCRIPTION_ROOTS`、`sg_description_in_code`、`sg_description_in_state`、`check_sg_descriptions` を足す（`tf_init` / `has_resources` の近く）
- `ops/up.sh` — 守りの塊（`:586-605`）の末尾で `check_sg_descriptions` を呼ぶ。コメントに「description を変えた SG」の守りを足す
- `ops/oss/up.sh` — 守りの塊（`:152-160`）の末尾で `check_sg_descriptions` を呼ぶ
- `IaC/terraform/aws-managed/base/core/security_groups.tf` — `:24` のコメント、`:27` と `:29` の description
- `docs/cml-sandbox.md` — `:558`
- `docs/architecture/core.md` — `:102-103`
- `docs/architecture/resources/vpc-perimeter.md` — `:96-98`
- `docs/troubleshooting.md` — `:25` の次に 1 行
- `tests/test_workflow.py` — 守りの静的な check（`:1457-1465` の隣）、関数を bash で動かす check（`:1268-1300` の切り出しと同じやり方）、`:2009-2011` の隣に telegraf の description の check
- `tests/test_oss_ops.py` — `:1203-1206` の隣に `check_sg_descriptions` の呼び出しの位置の check

## 再利用するもの

- `tf` / `tf_init` / `die` / `has_resources`（`ops/common.sh`、`ops/up-common.sh`）
- 既存の守りの形（`ops/up.sh:596-605`）と die の文面
- `tests/test_workflow.py:1268-1300` の「`ops/up-common.sh` から関数を切り出して bash で動かす」やり方（偽のコマンドは PATH の先頭の一時ディレクトリに置く。ここでは `tf` と `tf_init` と `die` を関数で上書きすればよく、偽の `terraform` は要らない）

## 実装ステップ

1. `ops/up-common.sh` に表と 3 つの関数を足す（設計方針 1・3。`local` を使う。`set -u` で落ちない）
2. `ops/up.sh` と `ops/oss/up.sh` の守りの塊の末尾で `check_sg_descriptions` を呼ぶ（設計方針 2）
3. `security_groups.tf` の `:24` / `:27` / `:29` を直す（設計方針 4）。`terraform fmt -check`（`IaC/terraform/aws-managed/base/core`）が通ること
4. docs 4 本を直す（設計方針 5）
5. テストを足す（検証 1〜4）。`bash -n ops/up.sh ops/oss/up.sh ops/up-common.sh`
6. `/cycle-build` 手順6 のセルフレビュー

## 検証方法（期待出力つき）

1. `tests/test_workflow.py` に足す静的な check: `ops/up.sh` に `check_sg_descriptions` の呼び出しがあり、その位置が `tf_init base/core` より後、`log "1. ECR リポジトリ` より前。`ops/up-common.sh` の `SG_DESCRIPTION_ROOTS` に `workflow:workflow`、`telegraf_dialout:pipeline/stream`、`telegraf_dialout_nlb:pipeline/stream` の 3 つがあり、各キーが `security_groups.tf` の `local.security_groups` にあり、各ルートの `locals.tf` が `security_group_ids["<key>"]` を読んでいる。
2. `tests/test_workflow.py` に足す動かす check（`check_sg_descriptions` を `ops/up-common.sh` から切り出し、一時ディレクトリの `TF_DIR`（`base/core/security_groups.tf` は実物のコピー）、`OPS_DIR=ops`、関数 `tf` / `tf_init` / `die` を上書きして bash で実行）:
   - state の description がコードと同じ → 終了コード 0、`DIE:` 無し
   - workflow の description が `Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)` で `workflow/terraform.tfstate` があり `tf workflow state list` が非空 → 終了コード 1、stderr に `DIE:` と `SG workflow の description（Temporal dev server and worker ECS task` と `workflow がその SG を付けている` と `先に ops/down.sh で消す（workflow だけ先に消してもよい）`
   - 同じく違うが `workflow/terraform.tfstate` が無い、または `state list` が空 → 終了コード 0
   - `tf base/core state show` が失敗（state にその SG が無い）→ 終了コード 0
   - `telegraf_dialout_nlb` が `Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)` で `pipeline/stream` が非空 → `DIE:` に `telegraf_dialout_nlb` と `pipeline/stream だけ先に消してもよい`
   - 表のキーの綴りを `workflowx:workflow` に替えると `DIE:` に `security_groups.tf に SG workflowx の description が無い`
   - `sg_description_in_code` が実物の `security_groups.tf` から 3 キーとも空でない文言を返し、Python の `re.search(r'^    <key>\s+= "([^"]*)"$', …, re.M)` と一致する
3. `tests/test_oss_ops.py`: `ops/oss/up.sh` の `check_sg_descriptions` の呼び出しが `tf_init base/core` より後、`log "1. ECR リポジトリ` より前。
4. `tests/test_workflow.py:2009` の隣: `telegraf_dialout` の description に `MDT` と `syslog` が無く `SNMP traps` がある。`telegraf_dialout_nlb` の description に `syslog-ng` と `GoFlow2` がある。
5. 文言: `grep -rn 'and MDT' IaC docs --include='*.tf' --include='*.md'` が QUEUE と 041 / 043 のサイクルの docs 以外で 0 件。`grep -n 'NLB の SG が' docs/cml-sandbox.md` が 0 件。`grep -c 'check_sg_descriptions' docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md docs/troubleshooting.md` が各 1 以上。
6. 全テスト: `python3 tests/test_workflow.py` と `python3 tests/test_oss_ops.py` が失敗 0（042 の時点で 436 / 207。足した分だけ増える）。`python3 tests/test_stream.py` も失敗 0（`:293` が security_groups.tf を読む）。`terraform fmt -check -recursive IaC/terraform/aws-managed/base/core` が出力無し。`bash -n` 3 本が無言。
7. AWS: このサイクルでは動かさない。QUEUE 147 でまとめて見る（設計方針 6）。

## 未確定事項とリスク

1. `terraform state show` の出力は provider の schema に依る。`description` の行の形（`description = "…"`）は hashicorp/aws の 5 系で変わっていないが、テストは偽の `tf` で動かすので実物の出力は 147 の AWS の確認で 1 度見る（止まらないことの確認。古い state は作れないので `die` の方は偽の `tf` のテストだけ）。
2. 守りは表に載せたキーだけを見る。以後 description を変えるときに表へ足し忘れると素通りする。`security_groups.tf:24` のコメントと `core.md` の 1 行で「変えたら `SG_DESCRIPTION_ROOTS` に足す」と書いて残す。
3. `tf <root> state list` はそのルートの init が要る。既存の守りと同じく `[ -s …/terraform.tfstate ] && { tf_init <root>; … }` の形にして、state が無いルートで init を走らせない。
