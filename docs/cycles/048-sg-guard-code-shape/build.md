# build: `check_sg_descriptions` に Terraform の書き方の守りを足す（048）

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。設計モデル fable-5-1 は上位なので design.md はそのまま実装）。ベース `2bef299`。

### 実装内容

- `ops/up-common.sh` の `sg_descriptions_in_code` の awk
  - workload のブロック（`resource "aws_security_group" "workload" {` から行頭 `}`。`inw` / `wblk`）: `^  description += each\.value$` で `wdesc`、ほかの `^  description[ =]` は `wbad`（`wdbad`）。`^  for_each += local\.workload_security_groups$` で `wfe`、ほかの `^  (count|for_each)[ =]` は `wbad`（`wfbad`）
  - endpoints のブロック: `^  (count|for_each)[ =]` を `epbad` に足し `epcnt`。END で「count / for_each があると state の見出しが「# aws_security_group.endpoints[0]:」になり守りが外れる」を足す。「ブロックに 2 空白の description の行が無い」の条件を `epbad == ""` から `!epdbad` に（count の行があっても description 欠落を出す）
  - END: 見出しが無ければ「resource "aws_security_group" "workload" { の行が無い」、`wdesc` / `wfe` が無く対応する読めない行も無ければ「…の行が無い」。endpoints の NG と `NG: aws_security_group.workload の書き方が読めない:` を両方出してから `exit 1`
  - ヘッダのコメントに 048 の 1 行。`check_sg_descriptions` の本文と die、`sg_descriptions_in_state` は変えていない
- `tests/test_workflow.py`: 046 の塊の後ろ（`_sg_roots` の前）に T-1〜T-8 と追加 3 件（`=` の揃え、両方の NG、endpoints の count と description 欠落の同時）
- `docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`: 「コードの書き方（workload の `description = each.value` と `for_each`）が読めないときや、endpoints に `count` / `for_each` があるときも止まる（048）」

### 変更ファイル

- `ops/up-common.sh`
- `tests/test_workflow.py`
- `docs/architecture/core.md`
- `docs/architecture/resources/vpc-perimeter.md`
- `docs/cycles/048-sg-guard-code-shape/build.md`

### 設計からの逸脱

1. workload の description は設計の「ちょうど `  description = each.value`（完全一致）」ではなく `=` の前の空白を 1 つ以上許した（`^  description += each\.value$`）。terraform fmt は続く属性の `=` を揃えるので、ブロックに長い名前の属性を足すと `description            = each.value` になり、完全一致では正しいコードで up.sh が止まる。広がるのは空白の数だけで、式・行末のコメント・タブは NG のまま（T-4、追加の check「= の前の空白が揃えで増えても通る」）。design.md の方針 2・実装ステップ 1 の文言の直しが要る
2. 設計の検証表に無い check を 3 件足した（上の「追加 3 件」。セルフレビューの Nit 1 で T-5 にもケースを足した）

### 検証（最後の編集のあとに取り直した出力）

| # | 結果 |
| :--- | :--- |
| T-1〜T-8 | 通過（下の生ログ） |
| 変異 M1〜M5 | それぞれ設計の T が落ちる（下の生ログ） |
| 全テスト `tests/test_*.py` | `ops/check.sh` の 4 で全部失敗 0（下の生ログ）。`tests/test_oss_ops.py` の本物の `check_sg_descriptions` の check も含む |
| `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` | 1 本ずつ打って 3 本とも rc=0（`bash -n a b` は a しか見ないため） |
| `ops/check.sh` | 「すべて通過」（下の生ログ） |

`.venv/bin/python tests/test_workflow.py` の 048 の行（「いま: …」の後ろは切った）:

```
ok T-1 実物の security_groups.tf の workload のブロックに「  description = each.value」と「  for_each = local.workload_security_groups」がちょうど 1 行ずつ、endpoints のブロックに count / for_each の行が無く、check_sg_descriptions は通る（cycle 048）
ok T-2 check_sg_descriptions: workload の「  description = each.value」が式（"pre ${each.value}"）なら、表と state を比べて通さず、その行を出して terraform show の前に止める（cycle 048）
ok T-3 check_sg_descriptions: workload のブロックに description の行が無い・resource の名前が変わったなら止める（cycle 048）
ok T-4 check_sg_descriptions: 「  description = each.value」に行末のコメントや関数が付くなら、前方一致で通さずその行を出して止める（cycle 048）
ok T-5 check_sg_descriptions: workload の for_each が別の表・count・名前の続く表・行末のコメントなら（state のキーが表と合わず全部飛ばして通る）、その行を出して止め、for_each の行が無ければその旨を出して止める（cycle 048）
ok T-6 check_sg_descriptions: endpoints のブロックに count / for_each があれば（state の見出しが endpoints[0] になり守りが外れる）、その行と「endpoints[0]」を出してterraform show の前に止める（cycle 048）
ok T-7 check_sg_descriptions: workload / endpoints のブロックのコメント行（「  # description = …」「  # count = …」）は見ず、通って出力は実物と同じ（cycle 048）
ok T-8 check_sg_descriptions: ほかのリソースのブロック（endpoints_none）の count は見ない（cycle 048）
ok check_sg_descriptions: workload の description / for_each の = の前の空白が揃えで増えても通る（cycle 048）
ok sg_descriptions_in_code: endpoints と workload の両方が読めなければ、NG を両方出す（cycle 048）
ok sg_descriptions_in_code: endpoints に count があり description の行も無ければ、count の行と「description の行が無い」を両方出す（cycle 048）
通過 490 / 失敗 0
```

変異（`ops/up-common.sh` に当て、check で止まらない形にした test_workflow.py のコピーで落ちる check を数える。scratchpad の `mut048.py`。最後に元に戻ったことを確かめた）:

```
M0 変異なし -> 0 件落ちる: []
M1 workload のブロックの規則を全部消す -> 5 件落ちる: ['T-2 check_sg_descriptions: workload の「  ', 'T-3 check_sg_descriptions: workload のブロッ', 'T-4 check_sg_descriptions: 「  descriptio', 'T-5 check_sg_descriptions: workload の fo', 'sg_descriptions_in_code: endpoints と wor']
M2 description = each.value を前方一致に -> 1 件落ちる: ['T-4 check_sg_descriptions: 「  descriptio']
M3 for_each の規則を消す -> 1 件落ちる: ['T-5 check_sg_descriptions: workload の fo']
M4 endpoints の count / for_each の規則を消す -> 3 件落ちる: ['T-6 check_sg_descriptions: endpoints のブロ', 'sg_descriptions_in_code: endpoints と wor', 'sg_descriptions_in_code: endpoints に cou']
M5 コメント行も規則に掛ける -> 1 件落ちる: ['T-7 check_sg_descriptions: workload / en']
MA for_each の「行が無い」を消す -> 1 件落ちる: ['T-5 check_sg_descriptions: workload の fo']
MB !epdbad を epbad == "" に戻す -> 1 件落ちる: ['sg_descriptions_in_code: endpoints に cou']
MC for_each の正規表現を前方一致に -> 1 件落ちる: ['T-5 check_sg_descriptions: workload の fo']
MD wfbad = 1 を消す -> 1 件落ちる: ['T-5 check_sg_descriptions: workload の fo']
元に戻した: True
```

`ops/check.sh`:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/logs  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/logs  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK
== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし
== 4. 模擬テスト
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 121 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 209 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 490 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

### セルフレビュー

- 実行モデル: 自分 opus-5.5 / effort high（サブエージェントの中なので xhigh に切り替えられない）。反対弁護人 opus / effort xhigh（文脈を渡して読み取り専用。終了後 `git status --porcelain -uall` は変更 4 ファイルのまま、増えたファイルなし）
- 反対弁護人の指摘と再現（scratchpad の `repro048.py`。一時コピーで `sg_descriptions_in_code` を打つ）

| 分類 | [観点] 箇所 | 破綻シナリオ | 再現 | 片付け |
| :--- | :--- | :--- | :--- | :--- |
| Should fix | [設計の穴] `IaC/terraform/aws-managed/base/core/oss.tf:76-79`（`local.workload_security_groups = merge(...)`） | 表の値が description になる経路は「表 → oss.tf の `workload_security_groups` → for_each → each.value」の 3 段で、守ったのは最後の 2 つだけ。`k => "pre ${v}"` や `"x_${k}" => v` に書き換えると、workload の SG が全部作り直しになるのに通る | `k => "pre ${v}" if …` に書き換え（マネージド版）→ rc 0、stdout は実物と同じ | **差し戻し**。設計の方針 6 が oss.tf を見ない範囲にしているので実装で吸収しない（PM の判断: このサイクルに足すか QUEUE か）。docs の文言は oss.tf まで守るとは読めない形に直した |
| Nit 1 | [missing tests] `ops/up-common.sh:48,49,74,79` | for_each の正規表現の `$`、`wfbad`、for_each の「行が無い」、`!epdbad` を戻してもテストが落ちなかった | 反対弁護人の変異 MA〜MD で 489/0 | **直した**。T-5 に `local.workload_security_groups_v2`・行末の `# memo`・for_each の行の削除・「行が無い」を出さないことを足し、endpoints の count と description 欠落の同時の check を足した。MA〜MD がそれぞれ落ちる（上の変異の生ログ） |
| Nit 2 | [fmt 前提] `ops/up-common.sh:58` | endpoints にタブや 4 空白で `count = 1` を書くと通る | タブの count / 4 空白の count → rc 0、stdout 同じ | 残す。`ops/check.sh` の `terraform fmt -check` で捕まる。字下げを緩めると入れ子の `dynamic` の for_each を誤検知する |
| Nit 3 | [文言] `ops/up-common.sh:84` | workload の NG に理由（each.value 以外にすると全部作り直し）が無く、die の案内が守りを緩める方向に読める | 読んだだけ | 残す（NG の文言は設計の方針 7 のまま） |
| Nit 4 | [コメント] `ops/up-common.sh:56` | 「ほかの属性の行は見ない」が count / for_each を見るようになって事実と違う | 読んだだけ | 直した（「description と count / for_each のほかの属性の行は見ない」） |
| Nit 5 | [閉じ括弧] `ops/up-common.sh:45` | `} # workload` だと `inw` が閉じず、後ろのルールの `  for_each` / `  description` が NG に出る（安全側だが直す場所が分かりにくい） | `} # workload` → rc 1、NG に flow の for_each の行 | 残す（endpoints の既存の `inr` も同じ形。terraform fmt は `}` の後ろのコメントを残すが、このリポジトリに例は無い） |
| Nit 6 | [記録・docs] | 逸脱 1 が design.md に未反映。docs の「count / for_each が無いこと…が読めなくても止まる」がねじれている | 読んだだけ | docs は直した。逸脱は上の「設計からの逸脱」に書いた |

- 「問題なし」とした観点と根拠
  - 規則の順序・閉じないブロック・file==1 の判定: 反対弁護人が一時コピーで打って、どれも NG（安全側）か意図どおり。workload を oss.tf に移すと「行が無い」で NG
  - 既存の 043 / 046 の check: test_workflow.py の全 check と test_oss_ops.py が通過（`ops/check.sh` の生ログ）
  - 逸脱 1 で通る危険な書き方: 広がるのは空白の数だけ。反対弁護人が完全一致に戻す変異（MF）で「揃え」の check が落ちることを確かめた
- 未解消の Must fix: 0

## Round 2

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。設計モデル fable-5-1 は上位なので design.md はそのまま実装）。ベース `06a4cc7`。対象は実装ステップ 4〜8。

### 実装内容

- `ops/up-common.sh` の `sg_descriptions_in_code`
  - shell: `oss.tf` を常に awk の 2 本目に渡す。無ければ `NG: <dir>/oss.tf が無い` を stderr に出して `return 1`（既存の die に乗る）。OSS 版の判定は `awk -v oss=0/1`
  - awk: `oss_security_groups` の表を読む条件と「塊が無い」の NG を `ARGC > 2` から `oss` に変えた
  - awk: merge のブロックの規則を足した
    - 入口は `^  workload_security_groups += merge\($`（`inm` / `mblk`）、出口は `$0 == "  )"`
    - `^(    #| *$)` のコメント行と空行は飛ばす
    - 1 行目・2 行目は完全一致を順番どおりに数える（`m1` / `m2`）。ほかの行は `mbad` に「`oss.tf: <行>`」で積む
    - END: 見出しが無ければ「oss.tf に「  workload_security_groups = merge(」の行が無い」を出す。`mbad` が空で 2 行が揃っていなければ「…のブロックに local.security_groups と local.oss_security_groups の 2 行が無い」を出す。どちらも workload の `wbad` の後ろに足して 1 つの NG にする
  - ヘッダのコメントの 048 の行に「oss.tf の local.workload_security_groups の merge」を足した
- `tests/test_workflow.py`
  - `_csd_run` に `remove=` を足した
  - `_sdc_tmp` は `oss.tf` を常に書く（無ければ実物を書く）。`oss=` を足した（既定は `oss_tf` を渡したかどうか）
  - 048 の塊の後ろに次を足した。実物の merge のブロックの check、T-9〜T-13、workload と merge が両方壊れたときの順の check
- `tests/test_oss_ops.py:536-540`: 偽の REPO の土台に `oss.tf` のコピーとリンクを足した（下の逸脱 1）
- `IaC/terraform/aws-managed/base/core/security_groups.tf` の workload の resource の上と、`oss.tf` の `workload_security_groups = merge(` の上に、コメントを 1 行ずつ足した（cold review Round 1 の Nit 3）
- `docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`: 「`oss.tf` の `local.workload_security_groups` の merge」を足した

### 変更ファイル

- `ops/up-common.sh`
- `tests/test_workflow.py`
- `tests/test_oss_ops.py`
- `IaC/terraform/aws-managed/base/core/security_groups.tf`
- `IaC/terraform/aws-managed/base/core/oss.tf`
- `docs/architecture/core.md`
- `docs/architecture/resources/vpc-perimeter.md`
- `docs/cycles/048-sg-guard-code-shape/build.md`

### 設計からの逸脱

1. `tests/test_oss_ops.py` は設計の変更対象に無い。`oss.tf` を必須にしたので、偽の REPO（`security_groups.tf` だけをコピー・リンクしていた）で「up.sh（通し）」の check が `oss.tf が無い` で落ちた。それを見てから土台に `oss.tf` を足した。check の意味は変えていない（`ops/` で base/core の .tf を読むのは `check_sg_descriptions` だけ。反対弁護人も確認済み）
2. 設計の検証表に無い check とケースを足した
   - T-10 の「2 行目を消す」
   - セルフレビューで足した T-10 の 5 件（下）
   - 実物の merge のブロックが 2 行だけであることの check
   - workload と merge が両方壊れたときの順の check
3. テストの土台に引数を足した。`_csd_run(remove=…)` と `_sdc_tmp(oss=…)`

### 検証（最後の編集のあとに取り直した出力）

| # | 結果 |
| :--- | :--- |
| T-1〜T-13 と追加の check | 通過（`tests/test_workflow.py` 497 / 0。Round 2 の行は下の生ログ） |
| 変異 M1〜M8 | それぞれ設計の T が落ちる。ME / MF / MG / MH と反対弁護人の X1〜X5 も落ちる（下の生ログ） |
| 全テスト `tests/test_*.py` | `ops/check.sh` の 4 で全部失敗 0。`tests/test_oss_ops.py` の 209 / 0 には、本物の `check_sg_descriptions` の check も入っている |
| `bash -n ops/up-common.sh` / `ops/up.sh` / `ops/oss/up.sh` | 1 本ずつ打って、3 本とも rc=0 |
| `ops/check.sh` | rc=0、「すべて通過」（下の生ログ） |

`.venv/bin/python tests/test_workflow.py` の Round 2 の行（「いま: …」の後ろは切った）:

```
ok oss.tf の「  workload_security_groups = merge(」のブロックは、コメント行と空行のほかは local.security_groups と local.oss_security_groups の 2 行だけ（cycle 048 Round 2）
ok T-9 check_sg_descriptions: oss.tf の merge の値やキーを書き換えたら（workload の SG が全部作り直し）、マネージド版も OSS 版も表と state を比べて通さず、その行を出して terraform show の前に止める（cycle 048 Round 2）
ok T-10 check_sg_descriptions: merge の見出しが無い（merge 無し・ブロックごと消す）なら「oss.tf に「  workload_security_groups = merge(」の行が無い」、2 行の順の入れ替え・3 行目・出口や 1 行目・2 行目の行末に式を続けたものはその行を出し、2 行目が無ければその旨を出して止める（cycle 048 Round 2）
ok T-11 check_sg_descriptions: マネージド版でも oss.tf が無ければ「oss.tf が無い」で terraform show の前に止める（cycle 048 Round 2）
ok T-12 sg_descriptions_in_code: マネージド版は oss.tf の oss_security_groups の表を読まない（崩しても通り、出力は実物と同じ。OSS 版で止まるのは 046 の check）（cycle 048 Round 2）
ok T-13 check_sg_descriptions: merge のブロックのコメント行と空行は見ず、通って出力は実物と同じ（cycle 048 Round 2）
ok sg_descriptions_in_code: workload のブロックと merge の両方が読めなければ、NG の 1 つにブロックの行、merge の行の順で出す（cycle 048 Round 2）
通過 497 / 失敗 0
```

変異（scratchpad の `mut048r2.py`。`ops/up-common.sh` に当てて、check を落ちても止まらない形にした test_workflow.py のコピーで打つ。最後に元へ戻した）:

```
M0 変異なし -> 0 件落ちる: []
M1 workload のブロックの規則を全部消す -> 6 件落ちる: ['T-2 …', 'T-3 …', 'T-4 …', 'T-5 …', 'sg_descriptions_in_code: endpoints と wor', 'sg_descriptions_in_code: workload のブロックと']
M2 description = each.value を前方一致に -> 1 件落ちる: ['T-4 …']
M3 for_each の規則を消す -> 1 件落ちる: ['T-5 …']
M4 endpoints の count / for_each の規則を消す -> 3 件落ちる: ['T-6 …', 'sg_descriptions_in_code: endpoints と wor', 'sg_descriptions_in_code: endpoints に cou']
M5 コメント行も規則に掛ける -> 1 件落ちる: ['T-7 …']
M6 merge のブロックの規則を全部消す -> 3 件落ちる: ['T-9 …', 'T-10 …', 'sg_descriptions_in_code: workload のブロックと']
M7 OSS 版の判定を ARGC > 2 に戻す -> 13 件落ちる: [… 'T-1 …', 'T-7 …', 'T-8 …', 'T-12 …', 'T-13 …' ほか 046 の check 8 件]
M8 2 行の順を見ない -> 1 件落ちる: ['T-10 …']
ME merge のコメント行と空行を飛ばさない -> 1 件落ちる: ['T-13 …']
MF merge の 2 行の欠けの NG を消す -> 1 件落ちる: ['T-10 …']
MG shell の oss.tf の有無を見ない -> 1 件落ちる: ['T-11 …']
MH merge の 1 行目を前方一致に -> 3 件落ちる: ['T-9 …', 'T-10 …', 'sg_descriptions_in_code: workload のブロックと']
X1 merge の出口を前方一致に -> 1 件落ちる: ['T-10 …']
X2 merge の見出しの $ を外す -> 1 件落ちる: ['T-10 …']
X3 2 行目を前方一致に -> 1 件落ちる: ['T-10 …']
X4 1 行目を前方一致に -> 1 件落ちる: ['T-10 …']
X5 # を含む行を飛ばす -> 1 件落ちる: ['T-10 …']
元に戻した: True
```

`ops/check.sh`（rc=0。テストの INFO / Traceback の出力は grep で落とした）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（20 ルートすべて OK）
== 3. スクリプトの構文
bash -n: 33 本
構文エラーなし
== 4. 模擬テスト
通過 168 / 失敗 0
通過 168 / 失敗 0
通過 549 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 121 / 失敗 0
通過 145 / 失敗 0
68 項目すべて通過
通過 177 / 失敗 0
通過 209 / 失敗 0
通過 66 / 失敗 0
通過 119 / 失敗 0
通過 104 / 失敗 0
通過 497 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

### セルフレビュー

- 実行したモデルと effort
  - 自分: opus-5.5 / effort high（サブエージェントの中なので xhigh に切り替えられない）
  - 反対弁護人: opus / effort xhigh
    - 文脈を渡し、読み取り専用で頼んだ。一時ファイルは scratchpad の `advocate/` に置かせた
    - 終わったあとの `git status --porcelain -uall` は自分の 7 件だけで、増えていない
- 反対弁護人の指摘と再現。再現には `advocate/cases.py` を本物の `ops/up-common.sh` に当てて使った

| 分類 | [観点] 箇所 | 破綻シナリオ | 再現 | 片付け |
| :--- | :--- | :--- | :--- | :--- |
| Should fix | [missing tests] `tests/test_workflow.py` の T-10 | awk の完全一致を緩める変異 X1〜X5 ではテストが 1 件も落ちず、緩めた awk では次の 5 つが黙って通った: 出口の後ろに式（`  ) == null ? {} : …`）、見出しの後ろに式、1 行目・2 行目の行末に map を続ける、`#` を含む 3 行目 | 実装した awk で 5 件ともマネージド版・OSS 版で rc=1（`cases.py … "A " D E` の出力）。変異 X1〜X5 をテストに当てると、反対弁護人の手元では 497 / 0 で全部通った | **直した。** T-10 の `_mg_bcases` に 5 件を足した。いまは X1〜X5 がどれも T-10 を落とす（上の変異ログ） |
| Nit 1 | [設計の範囲] `oss.tf:11`（`  oss = var.project == "nwc-oss"`） | merge の 2 行目の `if local.oss` は見ているが、`local.oss` の定義は見ていない。マネージド版で `  oss = true` にすると守りは通る。その場合 spark の description が変わって作り直しになり、msk も merge から抜ける（Terraform 側の影響は推測。plan は打っていない） | `cases.py … G` → マネージド版・OSS 版とも rc=0、出力は基準と同じ | 残す。設計（`security_groups.tf` と `oss.tf` の merge・表）の範囲外。design.md:125 の「経路の守りはこれで 3 段とも閉じる」は言い過ぎなので、PM に報告する |
| Nit 2 | [正しいコードを止める] `ops/up-common.sh:66-67` | 出口の行末にコメントを付ける（`  ) # end`）、`    // memo` と書く、のどちらも止まる。`terraform fmt` はどちらも直さない。止まる側に倒れるだけで、`) # end` のときは次の `  )` までの関係ない 3 行も NG に並ぶ | `cases.py … A2 C` → 両方の版で rc=1 | 残す。止まる側に倒れ、NG の行が直す場所を示す（design.md の未確定事項 5 と同じ倒し方） |
| Nit 3 | [文言] `ops/up-common.sh:128` の die | 原因が oss.tf にあるときも、die は「security_groups.tf から…」と案内する | 読んだだけ（T-11 がこの文言を固定している） | 残す。die の文言は設計の方針 1・7 で変えない（Round 1 の Nit 3 と同じ） |

- 「問題なし」とした観点と根拠。どれも反対弁護人が一時コピーで打った
  - 出口を `  )` の完全一致で判定する件: 次の行に `  == null ? …` を続ける書き方は HCL の構文エラーになる（`terraform fmt` が rc=2）。出口の行末に式を続けると止まる
  - 見出しが 2 回ある oss.tf、CRLF の oss.tf、空の oss.tf: どれも rc=1
  - oss.tf が壊れたリンクかディレクトリのとき: `-f` で止まる。読めない oss.tf では awk がエラーで止まる
  - `-v oss=0`: BWK awk（macOS の `/usr/bin/awk`）で偽として扱われた。マネージド版は 17 行で、T-12 も通る。gawk / mawk は手元に無いので見ていない（design.md の未確定事項 3）
  - OSS 版の経路（`IaC/terraform/oss/base/core/` のリンク）: OSS 版で 22 行、`tests/test_oss_ops.py` 209 / 0
  - 「2 行が無い」の NG が mbad が空のときしか出ない件: mbad が空でないときは崩れた行が NG に並ぶので、黙っては通らない（MF が T-10 で落ちる）
- 範囲外として残したもの（反対弁護人も「穴ではない」とした）
  - `/* */` の中に偽物のブロックを置き、本物を別の書き方にする: 通るが、わざとの細工
  - `oss_replaced` にキーを足す: キーを抜く書き換えで、description の守りの範囲外
- 未解消の Must fix: 0
- 保守性・可読性だけで直していないもの: 上の Nit 1〜3
