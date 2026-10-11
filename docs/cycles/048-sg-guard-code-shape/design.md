# `check_sg_descriptions` に Terraform の書き方の守りを足す（workload の `description = each.value` と endpoints の `count` / `for_each`）（048）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-11。ベースは main の `c1d4070`。

## 背景

- QUEUE 161（046 の cold review Round 2 の Nit 2 と、046 のセルフレビュー Round 2 の反対弁護人の Nit 2）。043 の守り `check_sg_descriptions`（`ops/up-common.sh:91-108`）は、state の SG の description を `sg_descriptions_in_code`（`ops/up-common.sh:29-70`）が読んだコードの文言と比べる。コード側で読むのは `local.security_groups` / `local.oss_security_groups` の表の値と、`resource "aws_security_group" "endpoints"` のブロックの 2 空白の `description` だけ
- 表の値が workload の SG の description になるのは、`resource "aws_security_group" "workload"` の `description = each.value`（`IaC/terraform/aws-managed/base/core/security_groups.tf:158`）があるから。この行を `"pre ${each.value}"` のような式に変えると、apply で workload の SG 全部の description が変わる（= 全部作り直し。ほかの SG のルールが古い SG を参照したままなので `DependencyViolation`）のに、守りは表の値と state（変える前の apply の値 = 表の値）を比べて通す。**守りは表を見ているが、表が description になる経路を見ていない**
- endpoints に `count` / `for_each` の行を足すと、state の見出しが `# aws_security_group.endpoints[0]:` になり、`sg_descriptions_in_state` の前方一致（`ops/up-common.sh:81` の `# aws_security_group.endpoints:`）に当たらない。比べる行が出ないので黙って通る（046 の `review.md:245-248`。cold reviewer が一時コピーで再現: state 側の見出しが `endpoints[0]` で description が古くても `rc=0`、出力の行が無い）
- workload も同じ穴がある: `for_each = local.workload_security_groups`（`security_groups.tf:155`）を `count` や別の表に替えると、state の見出しのキーが表のキーと合わなくなり、`check_sg_descriptions` は「コードに無いキー」として全部飛ばして通す。QUEUE の文言には無いが、同じ awk の同じブロックで 1 行で足せるので、このサイクルで一緒に見る（方針 3。PM の判断）

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

`local.workload_security_groups`（`security_groups.tf:142-145`）は `local.security_groups` から `oss_replaced` を抜き、OSS 版なら `local.oss_security_groups` を merge したもの。キーは表のキーそのまま。

state 側の見出しの読み方（`ops/up-common.sh:81-82`。前方一致なので `[0]` が付くと当たらない）:

```
      if (index($0, "# aws_security_group.workload[\"") == 1 && (e = index($0, "\"]:")) > 0) key = substr($0, 32, e - 32)
      else if (index($0, "# aws_security_group.endpoints:") == 1) key = "endpoints"
```

`sg_descriptions_in_code` の awk は、すでに endpoints の resource のブロック（`inr`）を見出し行の完全一致で入り、行頭の `}` で抜ける（`ops/up-common.sh:41-43`）。読めない行は `epbad` に溜めて END で `NG: aws_security_group.endpoints の description が読めない:` を stderr に出して `exit 1`。`check_sg_descriptions` はその `exit 1` を「`security_groups.tf` から SG の description が読めない（…）。まだ何も作っていない」の die にする（`terraform show` は打たない）。

`docs/troubleshooting.md` にこの「読めない」の die の行は無い（grep 0 件。NG の行で直す場所が分かるので、このサイクルでも足さない）。

## 設計方針

1. **守りは `sg_descriptions_in_code` の awk に足し、既存の「読めない → NG → die」の経路に乗せる。** `check_sg_descriptions` の本文と die の文言、`sg_descriptions_in_state` は変えない。`terraform show` を打つ前に止まる
2. **workload のブロック**（`resource "aws_security_group" "workload" {` の行から行頭の `}` まで。endpoints と同じ入り方）に、ちょうど `  description = each.value` の行があること。無い・式（`"pre ${each.value}"`、`lower(each.value)`）・行末のコメントは NG。`  description` で始まる行で完全一致でないものは、その行を NG に出す（endpoints の `epbad` と同じ形）。ブロックの見出し行が無い（resource の名前を変えた）のも NG
3. **同じ workload のブロックに、ちょうど `  for_each = local.workload_security_groups` の行があること**（`=` の前の空白は 1 つ以上）。`  count` で始まる行、別の `for_each` は NG に行を出す。QUEUE の文言に無い追加（背景の 4 つ目）
4. **endpoints のブロックに `  count` / `  for_each` で始まる行があれば NG。** `epbad` にその行を足し、END の NG に「count / for_each があると state の見出しが「# aws_security_group.endpoints[0]:」になり守りが外れる」の一言を付ける
5. **コメント行（`  #` で始まる）と、ほかのリソースのブロック（ルール、`endpoints_none`）は見ない。** `local.security_groups` の表の読み方も変えない
6. OSS 版は `IaC/terraform/oss/base/core/security_groups.tf` がマネージド版へのリンクなので同じ 1 本。`oss.tf` に workload / endpoints の resource は無いので見ない
7. NG の文言は 1 つにまとめる: `NG: aws_security_group.workload の書き方が読めない:` のあとに行を並べる（endpoints の NG が先にあれば両方出す）。`check_sg_descriptions` の die はそのまま（「まだ何も作っていない」で終わる）
8. AWS では動かさない（state は `tests/test_workflow.py` の `_csd_show` のサンプル。046 と同じ）

## 変更対象ファイル

- `ops/up-common.sh` — `sg_descriptions_in_code` の awk（workload のブロックの規則、endpoints の `count` / `for_each` の規則、END の NG）。ヘッダのコメント（`ops/up-common.sh:24-28`）に「コードの書き方（workload の `description = each.value` と `for_each`、endpoints に `count` / `for_each` が無いこと）も読めなければ止める（cycle 048）」の 1 行
- `tests/test_workflow.py` — 046 の塊（`# cycle 046 Round 2` の check 群）の後ろに T-1〜T-8 を足す
- `docs/architecture/core.md:103` と `docs/architecture/resources/vpc-perimeter.md:98` — 「workload の全キーと `endpoints` を見る」のあとに「コードの書き方が読めなくても止まる（048）」の一言

## 再利用するもの

- `tests/test_workflow.py` の `_csd_run(edits=[(ファイル名, 前, 後)])`（コピーの `security_groups.tf` を書き換えて `check_sg_descriptions` を bash で動かす。rc / stdout / stderr / tf の呼び出しを返す）と `_sdc_tmp(sg_tf, oss_tf)`（`sg_descriptions_in_code` だけを動かす）、`_sg_tf_core`、`_ep_head`
- awk の既存の `inr` / `rblk` / `epbad` の形（workload は `inw` / `wblk` / `wbad` で同じに）

## 実装ステップ

1. `sg_descriptions_in_code` の awk に workload のブロックの規則を足す（見出し行の完全一致で `inw = 1; wblk = 1`、行頭 `}` で抜ける、`  description = each.value` の完全一致で `wdesc = 1`、`  description` で始まるほかの行は `wbad` に、`  for_each += local\.workload_security_groups$` で `wfe = 1`、`  (count|for_each)` で始まるほかの行は `wbad` に）
2. endpoints のブロックに `  (count|for_each)` で始まる行の規則を足し、`epbad` に行と一言を入れる
3. END で `wblk` / `wdesc` / `wfe` / `wbad` を見て NG を出し `exit 1`（endpoints の NG の前後どちらでもよいが、両方あれば両方出す）
4. テスト T-1〜T-8。変異 M1〜M5 で落ちることを確かめる
5. docs 2 箇所とヘッダのコメント
6. 全テスト、`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh`、`ops/check.sh`

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

変異（それぞれ T が落ちること）: M1 workload のブロックの規則を全部消す → T-2 / T-3 / T-5。M2 `  description = each.value` の完全一致を前方一致にする → T-4。M3 `for_each` の規則を消す → T-5。M4 endpoints の `count` / `for_each` の規則を消す → T-6。M5 コメント行も規則に掛ける → T-7。

ほかに: 全テスト（`tests/test_*.py`）が失敗 0。`tests/test_oss_ops.py:1226-1241`（本物の `check_sg_descriptions` を実物の `security_groups.tf` で動かす）がそのまま通る。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0。`ops/check.sh` 通過。

## 未確定事項とリスク

1. `  for_each = local.workload_security_groups` の完全一致は、表を inline にするようなリファクタで `up.sh` を止める。NG の行がどこを直すか（`ops/up-common.sh` の `sg_descriptions_in_code`）を言うので受け入れる（043 からの「読めない行は黙って飛ばさない」と同じ倒し方）
2. `description` の行が 2 つある書き方は `terraform validate` が止めるので見ない（1 つ目で `wdesc`、2 つ目は完全一致なら通る）
3. awk は `==` / `~` / `index` だけで 046 と同じ範囲。gawk / mawk の差は見ない
4. `endpoints[0]` の見出しの形は 046 の cold reviewer が一時コピーで再現したもので、AWS の実物の state では見ていない（`count` を足した apply はしない）。`for_each` なら `endpoints["a"]` になるが、どちらも前方一致に当たらないのは同じ
