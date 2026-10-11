# `check_sg_descriptions` に Terraform の書き方の守りを足す（workload の `description = each.value` と endpoints の `count` / `for_each`）（048）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。ベースは main の `c1d4070`。Round 2（2026-10-11）: Round 1 のセルフレビューの Should fix 1（`oss.tf` の `local.workload_security_groups` の merge が守りの外）を取り込み、逸脱 1（`=` の前の空白）を正本にした。Round 2 のベースは `bd7f6e3`。

## 背景

- QUEUE 161（046 の cold review Round 2 の Nit 2 と、046 のセルフレビュー Round 2 の反対弁護人の Nit 2）。043 の守り `check_sg_descriptions`（`ops/up-common.sh:91-108`）は、state の SG の description を `sg_descriptions_in_code`（`ops/up-common.sh:29-70`）が読んだコードの文言と比べる。コード側で読むのは `local.security_groups` / `local.oss_security_groups` の表の値と、`resource "aws_security_group" "endpoints"` のブロックの 2 空白の `description` だけ
- 表の値が workload の SG の description になるのは、`resource "aws_security_group" "workload"` の `description = each.value`（`IaC/terraform/aws-managed/base/core/security_groups.tf:158`）があるから。この行を `"pre ${each.value}"` のような式に変えると、apply で workload の SG 全部の description が変わる（= 全部作り直し。ほかの SG のルールが古い SG を参照したままなので `DependencyViolation`）のに、守りは表の値と state（変える前の apply の値 = 表の値）を比べて通す。**守りは表を見ているが、表が description になる経路を見ていない**
- endpoints に `count` / `for_each` の行を足すと、state の見出しが `# aws_security_group.endpoints[0]:` になり、`sg_descriptions_in_state` の前方一致（`ops/up-common.sh:81` の `# aws_security_group.endpoints:`）に当たらない。比べる行が出ないので黙って通る（046 の `review.md:245-248`。cold reviewer が一時コピーで再現: state 側の見出しが `endpoints[0]` で description が古くても `rc=0`、出力の行が無い）
- workload も同じ穴がある: `for_each = local.workload_security_groups`（`security_groups.tf:155`）を `count` や別の表に替えると、state の見出しのキーが表のキーと合わなくなり、`check_sg_descriptions` は「コードに無いキー」として全部飛ばして通す。QUEUE の文言には無いが、同じ awk の同じブロックで 1 行で足せるので、このサイクルで一緒に見る（方針 3。PM の判断）
- 表の値が description になる経路は「`local.security_groups` の表 → `oss.tf` の `local.workload_security_groups = merge(…)` → `for_each` → `each.value`」の 3 段で、Round 1 は後ろの 2 段だけを守った。1 段目の merge（`oss.tf:76-79`）を `k => "pre ${v}"` や `"x_${k}" => v` に書き換えると、workload の SG が全部作り直しになるのに通る（Round 1 のセルフレビューの Should fix 1。PM が一時コピーで再現: マネージド版 rc=0 で 17 行、OSS 版 rc=0 で 22 行、出力は実物と同じ）。同じ守りの穴なのでこのサイクルで塞ぐ（方針 6・9。PM の判断）

## 現物で確認した事実（推測ではない）

コード側（`security_groups.tf:154-162` と `191-197`。どちらも 2 空白の属性で、`description` は 1 行）:

```
resource "aws_security_group" "workload" {
  for_each = local.workload_security_groups

  name        = "${local.name_prefix}-${replace(each.key, "_", "-")}"
  description = each.value
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name_prefix}-${replace(each.key, "_", "-")}" }
}
```

```
resource "aws_security_group" "endpoints" {
  name        = "${local.name_prefix}-endpoints"
  description = "VPC endpoints - HTTPS from the workload SGs, no outbound"
  vpc_id      = aws_vpc.this.id

  tags = { Name = "${local.name_prefix}-endpoints" }
}
```

`local.workload_security_groups` は `oss.tf:76-79`（`security_groups.tf` ではない）。`local.security_groups` から `oss_replaced` を抜き、OSS 版なら `local.oss_security_groups` を merge したもの。キーは表のキーそのまま。2 空白の属性で、中の 2 行は 4 空白:

```
  workload_security_groups = merge(
    { for k, v in local.security_groups : k => v if !contains(local.oss_replaced, k) },
    { for k, v in local.oss_security_groups : k => v if local.oss },
  )
```

`sg_descriptions_in_code` は `oss.tf` を OSS 版のときだけ awk の 2 本目の引数に渡し（`ops/up-common.sh:33-34, 90`）、awk は `file == 2` で `oss_security_groups` の表を読み、`ARGC > 2` を「OSS 版」の判定に使う（`ops/up-common.sh:72`）。マネージド版では `oss.tf` を読まない。`IaC/terraform/oss/base/core/oss.tf` はマネージド版へのリンク。

state 側の見出しの読み方（`ops/up-common.sh:81-82`。前方一致なので `[0]` が付くと当たらない）:

```
      if (index($0, "# aws_security_group.workload[\"") == 1 && (e = index($0, "\"]:")) > 0) key = substr($0, 32, e - 32)
      else if (index($0, "# aws_security_group.endpoints:") == 1) key = "endpoints"
```

`sg_descriptions_in_code` の awk は、すでに endpoints の resource のブロック（`inr`）を見出し行の完全一致で入り、行頭の `}` で抜ける（`ops/up-common.sh:41-43`）。読めない行は `epbad` に溜めて END で `NG: aws_security_group.endpoints の description が読めない:` を stderr に出して `exit 1`。`check_sg_descriptions` はその `exit 1` を「`security_groups.tf` から SG の description が読めない（…）。まだ何も作っていない」の die にする（`terraform show` は打たない）。

`docs/troubleshooting.md` にこの「読めない」の die の行は無い（grep 0 件。NG の行で直す場所が分かるので、このサイクルでも足さない）。

## 設計方針

1. **守りは `sg_descriptions_in_code` の awk に足し、既存の「読めない → NG → die」の経路に乗せる。** `check_sg_descriptions` の本文と die の文言、`sg_descriptions_in_state` は変えない。`terraform show` を打つ前に止まる
2. **workload のブロック**（`resource "aws_security_group" "workload" {` の行から行頭の `}` まで。endpoints と同じ入り方）に、`  description = each.value` の行があること（`=` の前の空白は 1 つ以上。terraform fmt が続く属性と `=` を揃えるため。Round 1 の逸脱 1 を正本にした）。無い・式（`"pre ${each.value}"`、`lower(each.value)`）・行末のコメント・タブは NG。`  description` で始まる行で完全一致でないものは、その行を NG に出す（endpoints の `epbad` と同じ形）。ブロックの見出し行が無い（resource の名前を変えた）のも NG
3. **同じ workload のブロックに、ちょうど `  for_each = local.workload_security_groups` の行があること**（`=` の前の空白は 1 つ以上）。`  count` で始まる行、別の `for_each` は NG に行を出す。QUEUE の文言に無い追加（背景の 4 つ目）
4. **endpoints のブロックに `  count` / `  for_each` で始まる行があれば NG。** `epbad` にその行を足し、END の NG に「count / for_each があると state の見出しが「# aws_security_group.endpoints[0]:」になり守りが外れる」の一言を付ける
5. **コメント行（`  #` で始まる）と、ほかのリソースのブロック（ルール、`endpoints_none`）は見ない。** `local.security_groups` の表の読み方も変えない
6. **`oss.tf` はマネージド版でも常に awk に渡す**（2 本目の引数）。OSS 版かどうかは引数の数ではなく `awk -v oss=1` で渡し、`oss_security_groups` の表を読む条件と「塊が無い」の NG（`ops/up-common.sh:58, 72`）を `oss` で判定する（マネージド版では `oss_security_groups` の塊を読まない・無くても NG にしない）。`oss.tf` が無ければ awk の前に `NG: <dir>/oss.tf が無い` を stderr に出して `return 1`（同じ die に乗る）。OSS 版は `IaC/terraform/oss/base/core/` の 2 本がマネージド版へのリンクなので同じ 1 本ずつ。`oss.tf` に workload / endpoints の resource は無い
7. NG の文言は 1 つにまとめる: `NG: aws_security_group.workload の書き方が読めない:` のあとに行を並べる（workload のブロックの行、oss.tf の merge のブロックの行の順。endpoints の NG が先にあれば両方出す）。`check_sg_descriptions` の die はそのまま（「まだ何も作っていない」で終わる）
8. AWS では動かさない（state は `tests/test_workflow.py` の `_csd_show` のサンプル。046 と同じ）
9. **`oss.tf` の `local.workload_security_groups` のブロック**（`  workload_security_groups = merge(` の行（`=` の前の空白は 1 つ以上）から `  )` の行まで）は、コメント行（`    #` で始まる）と空行を除いて、ちょうど次の 2 行がこの順で、ほかに行が無いこと: `    { for k, v in local.security_groups : k => v if !contains(local.oss_replaced, k) },` と `    { for k, v in local.oss_security_groups : k => v if local.oss },`。違う行はその行を NG に出す（`FILENAME: 行`）。見出しの行が無い（名前を変えた・`security_groups.tf` に移した・`merge(` 以外にした）のも NG（「oss.tf に「  workload_security_groups = merge(」の行が無い」）。NG の文言は方針 7 の workload の NG に続けて出す（同じ経路の守りなので 1 つにまとめる）

## 変更対象ファイル

- `ops/up-common.sh` — `sg_descriptions_in_code` の awk（workload のブロックの規則、endpoints の `count` / `for_each` の規則、`oss.tf` の merge のブロックの規則、`oss.tf` を常に渡して `-v oss=` で OSS 版を判定、END の NG）。ヘッダのコメント（`ops/up-common.sh:29`）の 048 の 1 行に「`oss.tf` の `local.workload_security_groups` の merge」を足す
- `tests/test_workflow.py` — 046 の塊の後ろの 048 の check 群（T-1〜T-8 と追加 3 件。Round 1）に T-9〜T-13 を足す。`_sdc_tmp(sg_tf, oss_tf=None)` は `oss_tf` が無いときも実物の `oss.tf`（`_oss_tf_core`）を書く（OSS 版の判定は `oss.auto.tfvars` の有無のまま）。`_csd_run` はすでに両方をコピーしている
- `docs/architecture/core.md:103` と `docs/architecture/resources/vpc-perimeter.md:98` — Round 1 の一言（「コードの書き方（workload の `description = each.value` と `for_each`）が読めないときや…」）に「`oss.tf` の `local.workload_security_groups` の merge」を足す
- `IaC/terraform/aws-managed/base/core/security_groups.tf:154` の `resource "aws_security_group" "workload"` の上と、`oss.tf:76` の `workload_security_groups = merge(` の上に、endpoints の `security_groups.tf:189-190` と同じ形のコメント 1 行（「`ops/up.sh` の `check_sg_descriptions` が `for_each` と `description = each.value` の 2 行の形を見る（048）」/「merge の 2 行の形を見る（048）」。cold review Round 1 の Nit 3。`terraform fmt -check` が通ること）

## 再利用するもの

- `tests/test_workflow.py` の `_csd_run(edits=[(ファイル名, 前, 後)])`（コピーの `security_groups.tf` を書き換えて `check_sg_descriptions` を bash で動かす。rc / stdout / stderr / tf の呼び出しを返す）と `_sdc_tmp(sg_tf, oss_tf)`（`sg_descriptions_in_code` だけを動かす）、`_sg_tf_core`、`_ep_head`
- awk の既存の `inr` / `rblk` / `epbad` の形（workload は `inw` / `wblk` / `wbad`。Round 1 で実装済み。merge のブロックは `inm` / `mblk` で同じに）

## 実装ステップ

1. （Round 1 で済み）`sg_descriptions_in_code` の awk に workload のブロックの規則（見出し行の完全一致で `inw = 1; wblk = 1`、行頭 `}` で抜ける、`^  description += each\.value$` で `wdesc = 1`、`  description` で始まるほかの行は `wbad` に、`^  for_each += local\.workload_security_groups$` で `wfe = 1`、`  (count|for_each)` で始まるほかの行は `wbad` に）
2. （Round 1 で済み）endpoints のブロックに `  (count|for_each)` で始まる行の規則を足し、`epbad` に行と一言を入れる
3. （Round 1 で済み）END で `wblk` / `wdesc` / `wfe` / `wbad` を見て NG を出し `exit 1`（両方あれば両方出す）
4. **Round 2**: `oss.tf` を常に渡す。shell 側で `[ -f "$dir/oss.tf" ]` を見て無ければ NG と `return 1`、OSS 版なら `-v oss=1`。awk の `file == 2 && $0 == "  oss_security_groups = {"` と `ARGC > 2 && !ossblk` を `oss` で判定する
5. **Round 2**: awk に merge のブロックの規則を足す（`^  workload_security_groups += merge\($` で `inm = 1; mblk = 1`、`  )` で抜ける、コメント行と空行は飛ばす、期待の 2 行は順番どおりに数え（1 行目が来る前に 2 行目が来たら NG）、それ以外の行は `wbad` に）。END で `mblk` と 2 行の有無を見て `wbad` に足す
6. **Round 2**: テスト T-9〜T-13、`_sdc_tmp` の変更。変異 M6〜M8 で落ちることを確かめる。Round 1 の T-1〜T-8 と追加 3 件、変異 M1〜M5 がそのまま通ることを取り直す
7. **Round 2**: docs 2 箇所とヘッダのコメント
8. 全テスト、`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh`（1 本ずつ）、`ops/check.sh`

## 検証方法（期待出力つき）

| # | 何を | 期待 |
| :--- | :--- | :--- |
| T-1 | 土台 | 実物の `security_groups.tf` の workload のブロックに `  description = each.value` と `  for_each = local.workload_security_groups` がちょうど 1 行ずつ、endpoints のブロックに `  count` / `  for_each` の行が無い。既存の `_sdc_real` の check（実物で出す件数と内容）がそのまま通る |
| T-2 | `  description = each.value` → `  description = "pre ${each.value}"` | `_csd_run(edits=…)` が rc=1、stderr に `aws_security_group.workload` とその行、「security_groups.tf から SG の description が読めない」「まだ何も作っていない」、「state の SG の description がコードと違う」は無い、`tf base/core show` は打たない。`_sdc_tmp` も rc=1 で stdout 空 |
| T-3 | その行を消す | rc=1、stderr に「description = each.value」の行が無い旨 |
| T-4 | `  description = each.value # memo` / `  description = lower(each.value)` | どちらも rc=1 でその行を出す |
| T-5 | `  for_each = local.workload_security_groups` → `  for_each = local.security_groups` / `  count = 3` | どちらも rc=1 でその行を出す（`show` は打たない） |
| T-6 | endpoints の見出しの直後に `  count = 1` / `  for_each = toset(["a"])` | rc=1、stderr にその行と「endpoints[0]」、`show` は打たない。`_sdc_tmp` も rc=1 で stdout 空 |
| T-7 | コメント行は見ない | endpoints に `  # count = 1`、workload に `  # description = "x"` を足しても rc=0 で、出力は実物と同じ |
| T-8 | ほかのブロックは見ない | `resource "aws_vpc_security_group_egress_rule" "endpoints_none" {` の直後に `  count = 1` を足しても rc=0 |
| T-9 | `oss.tf` の merge の 1 行目を `k => "pre ${v}"`、2 行目を `"x_${k}" => v` | マネージド版（`_csd_run(edits=[("oss.tf", …)])`）も OSS 版（`oss=True`）も rc=1、stderr に `oss.tf` とその行、「aws_security_group.workload の書き方が読めない」「security_groups.tf から SG の description が読めない」「まだ何も作っていない」、「state の SG の description がコードと違う」は無い、`tf base/core show` は打たない。`_sdc_tmp(_sg_tf_core, 書き換えた oss.tf)` も rc=1 で stdout 空 |
| T-10 | merge のブロックの見出しを `workload_security_groups = local.security_groups`（merge 無し）にする / ブロックごと消す / 2 行の順を入れ替える / 3 行目を足す | どれも rc=1。見出しが無いものは「oss.tf に「  workload_security_groups = merge(」の行が無い」、ほかはその行（順の入れ替えは 1 行目に来た 2 行目の行） |
| T-11 | `oss.tf` を消す（マネージド版） | rc=1、stderr に `oss.tf が無い`、`show` は打たない |
| T-12 | マネージド版で `oss.tf` の `  oss_security_groups = {` を `  oss_security_groups = merge({` に崩す | rc=0 で出力は実物と同じ（マネージド版は OSS の表を読まない。OSS 版で同じ崩しが rc=1 なのは 046 の既存の check のまま） |
| T-13 | merge のブロックにコメント行 `    # memo` と空行を足す | rc=0 で出力は実物と同じ |

変異（それぞれ T が落ちること）: M1 workload のブロックの規則を全部消す → T-2 / T-3 / T-5。M2 `  description = each.value` の完全一致を前方一致にする → T-4。M3 `for_each` の規則を消す → T-5。M4 endpoints の `count` / `for_each` の規則を消す → T-6。M5 コメント行も規則に掛ける → T-7。M6 merge のブロックの規則を全部消す → T-9 / T-10。M7 OSS 版の判定を `ARGC > 2` に戻す → T-12。M8 2 行の順を見ない → T-10（順の入れ替え）。

ほかに: 全テスト（`tests/test_*.py`）が失敗 0。`tests/test_oss_ops.py:1226-1241`（本物の `check_sg_descriptions` を実物の `security_groups.tf` で動かす）がそのまま通る。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0。`ops/check.sh` 通過。

## 未確定事項とリスク

1. `  for_each = local.workload_security_groups` の完全一致は、表を inline にするようなリファクタで `up.sh` を止める。NG の行がどこを直すか（`ops/up-common.sh` の `sg_descriptions_in_code`）を言うので受け入れる（043 からの「読めない行は黙って飛ばさない」と同じ倒し方）
2. `description` の行が 2 つある書き方は `terraform validate` が止めるので見ない（1 つ目で `wdesc`、2 つ目は完全一致なら通る）
3. awk は `==` / `~` / `index` だけで 046 と同じ範囲。gawk / mawk の差は見ない
4. `endpoints[0]` の見出しの形は 046 の cold reviewer が一時コピーで再現したもので、AWS の実物の state では見ていない（`count` を足した apply はしない）。`for_each` なら `endpoints["a"]` になるが、どちらも前方一致に当たらないのは同じ
5. merge のブロックの 2 行の完全一致は、`oss_replaced` の名前を変える・フィルタを足すリファクタで `up.sh` を止める。1 と同じ倒し方（NG の行が直す場所を言う）
6. `local.security_groups` の表そのもの（`security_groups.tf` の `locals`）を別の local 経由にする書き方（`security_groups = local.base_sgs`）は、表の塊が無くなるので既存の「塊が無いか、キーが 1 つも無い」で止まる（046）。経路の守りはこれで 3 段とも閉じる
