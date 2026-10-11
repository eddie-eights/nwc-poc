# build: `aws_security_group.endpoints` の description も `check_sg_descriptions` の守りに入れる（046）

## Round 1

実装モデル: claude-opus-5-5 / effort: high（PM のサブエージェント。設計モデル fable-5-1 は上位なのでそのまま実装）

### 変更ファイル

- `ops/up-common.sh`: `sg_descriptions_in_code` の awk に endpoints の resource ブロック読み（フラグ `inr` / `rblk` / `epok`、読めない 2 空白の description の行は `epbad`）。END で、ブロックが無い・description が読めない・workload に `endpoints` キーがあれば `NG: aws_security_group.endpoints の description が読めない:` を出して exit 1。workload の後に `endpoints\t<文言>` を 1 行。description が `${` / `%{` を含む（補間・テンプレート）なら読めない扱い。`sg_descriptions_in_state` に見出しが `# aws_security_group.endpoints:` で始まる行（後ろの ` (tainted)` も）→ キー `endpoints`。workload の表が読めないときの止め（下の逸脱）。コメント
- `tests/test_workflow.py`: `_SG_EP_DESC`（実物の resource ブロックから正規表現。取れなければ assert）、`_csd_show(sgs, endpoints=…)`、`_csd_run(…, endpoints=…, code_ep_broken=False)`、check 9 本（T-1〜T-9）と、セルフレビューで足した check 4 本（`_sdc_tmp` でブロックが無い / キーの衝突、`_ep_cases` で補間・テンプレート・行末のコメント・4 空白の入れ子、OSS 版で security_groups.tf の塊だけ読めない、見出しに ` (tainted)`）。偽の state の並びを実物（アドレス順）に合わせ endpoints を workload の前に置き、アドレスに endpoints を含むルール 2 つを足した。既存 check 3 本の期待値を変えた（下の「設計からの逸脱」）
- `IaC/terraform/aws-managed/base/core/security_groups.tf`: コメントだけ（14 行目、27 行目、endpoints の resource の上）。属性は不変
- `ops/up.sh` / `ops/oss/up.sh`: コメント「workload の全キーと endpoints を見るので表は無い」
- `docs/troubleshooting.md:26`（endpoints も見ると足し、パスを含むのは workload の description で endpoints は違わないと書いた）、`docs/architecture/core.md:103`、`docs/architecture/resources/vpc-perimeter.md:98`

### 設計からの逸脱

- **`sg_descriptions_in_code` に「workload の表が読めない」止めを足した**（`if (!sgblk || n == 0)` → `NG: <security_groups.tf> に「  security_groups = {」の塊が無いか、キーが 1 つも無い`、exit 1）。特定した原因: endpoints の 1 行を必ず出すようにしたので、`local.security_groups` の塊が読めなくても出力が空でなくなり、`check_sg_descriptions` の `[ -n "$code" ]` の die が効かなくなった。既存の check「security_groups.tf から文言が 1 つも読めない…なら…die する」が `rc=0 '' ['tf base/core show -no-color']` で落ちて見つかった（workload を全部素通りする退行）。設計の方針 1（比べる側は変えない）を保つため、読む側で止めた。副作用として OSS 版で security_groups.tf の塊だけが読めない場合も止まる（それまでは oss.tf の分だけで通っていた）
- **T-3 の期待の順を設計と逆にした**（設計は `workflow（…）、endpoints（…）`、実装は `endpoints（…）、workflow（…）`）。設計の未確定事項 3 は偽の state を「workload → endpoints」の順に置く前提だったが、実物の terraform show はアドレス順で `aws_security_group.endpoints` が `aws_security_group.workload[...]` より前（`scratchpad/da/show-backup.txt` の実物の写しで確認。下の検証 3）。die の文の並びは state の並びなので、偽の state を実物に合わせると endpoints が先になる。比べる側（`check_sg_descriptions`）は変えていない
- 既存 check の期待値を 3 本変えた。元の期待値は「出力は workload のキーだけ」を前提にしていたが、設計どおり endpoints の行が加わるため:
  - 「sg_descriptions_in_state は SG のブロックの 4 空白の description だけを…」: 入力に endpoints（`E (y)`）を載せ、期待の先頭に `endpoints\tE (y)` を足した（実物の並び）
  - 「sg_descriptions_in_code は実物の security_groups.tf の…一致する」「…OSS 版…」: 期待に `endpoints` を足し、最後の 1 行が endpoints であることも見る
- T-6 は設計どおり `_csd_run(code_ep_broken=True)`。設計の state 引数は無いが、`workflow` を古くした state を渡して「読めないときに比べる前に止まる」ことを見た（state を読む前に止まるので結果は同じ）

### 検証（最後の編集のあとに取り直した出力）

1. `bash -n`（ワークツリーのガードが複数ファイルの 1 行を拒むので 1 本ずつ）

```
$ bash -n ops/up-common.sh; echo "up-common rc=$?"
up-common rc=0
$ bash -n ops/up.sh; echo "up rc=$?"
up rc=0
$ bash -n ops/oss/up.sh; echo "oss/up rc=$?"
oss/up rc=0
```

2. 全テスト（`python3` には yaml が無いので `uv run -q --group dev --group web python`。`scratchpad/rt.sh` が各ファイルの rc と末尾 1 行を出す）

```
$ bash scratchpad/rt.sh tests/test_*.py
tests/test_agentcore.py rc=0 :: 通過 168 / 失敗 0
tests/test_alerts.py rc=0 :: 通過 168 / 失敗 0
tests/test_analytics.py rc=0 :: 通過 549 / 失敗 0
tests/test_collectors.py rc=0 :: 通過 79 / 失敗 0
tests/test_dashboard_config.py rc=0 :: 通過 3 / 失敗 0
tests/test_graph.py rc=0 :: 通過 78 / 失敗 0
tests/test_kb_index.py rc=0 :: 通過 7 / 失敗 0
tests/test_lab_debug.py rc=0 :: 通過 113 / 失敗 0
tests/test_local_compose.py rc=0 :: 通過 145 / 失敗 0
tests/test_nautobot.py rc=0 :: 68 項目すべて通過
tests/test_oss.py rc=0 :: 通過 177 / 失敗 0
tests/test_oss_ops.py rc=0 :: 通過 209 / 失敗 0
tests/test_oss_roll.py rc=0 :: 通過 66 / 失敗 0
tests/test_stream.py rc=0 :: 通過 118 / 失敗 0
tests/test_sync.py rc=0 :: 通過 104 / 失敗 0
tests/test_workflow.py rc=0 :: 通過 475 / 失敗 0
```

T-1〜T-9、既存の `check_sg_descriptions:` の 12 本、`tests/test_oss_ops.py` の 043 の 2 本はこの中で通っている（check は最初の失敗で止まるので、失敗 0 は全部の通過）。

3. 実物のコードから読む、と（補足）実物の terraform show の写しから読む（`scratchpad/fv.py`。`source` は他を評価するので、テストの `_upc_fn` と同じく関数だけ切り出して打つ。`da/show-backup.txt` は反対弁護人が示した 10-11 03:32 の実物の `terraform show -no-color` の写しで、endpoints が生きていたときのもの。設計の未確定事項 1 の見出しの形と 4 空白の description はこれで確かめられた）

```
$ sg_descriptions_in_code (TF_DIR=IaC/terraform/aws-managed) -> rc=0, 17 行, stderr=''
  最後の 1 行: endpoints<TAB>VPC endpoints - HTTPS from the workload SGs, no outbound
  endpoints の行の数: 1
$ sg_descriptions_in_code (TF_DIR=IaC/terraform/oss) -> rc=0, 22 行, stderr=''
  最後の 1 行: endpoints<TAB>VPC endpoints - HTTPS from the workload SGs, no outbound
  endpoints の行の数: 1
$ sg_descriptions_in_state < da/show-backup.txt（実物の terraform show の写し）-> rc=0
  endpoints<TAB>VPC endpoints - HTTPS from the workload SGs, no outbound
  gnmic<TAB>gnmic ECS task - gNMI subscriptions to Kafka (IaC/terraform/aws-managed/pipeline/stream)
  goflow2<TAB>GoFlow2 ECS task - NetFlow and sFlow behind the NLB to Kafka (IaC/terraform/aws-managed/pipeline/stream)
  grafana<TAB>Grafana ECS task (IaC/terraform/aws-managed/pipeline/analytics)
  lab<TAB>Lab EC2 - containerlab, forwards the lab mgmt network
  lambda<TAB>Lambda in the VPC - graph status, MCP tools, knowledge base index
  msk<TAB>MSK brokers (IaC/terraform/aws-managed/pipeline/stream)
  nautobot<TAB>Nautobot ECS task - web, celery worker and redis (IaC/terraform/aws-managed/pipeline/nautobot)
  nautobot_db<TAB>Nautobot PostgreSQL on RDS (IaC/terraform/aws-managed/pipeline/nautobot)
  runtime<TAB>AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)
  spark<TAB>EMR Serverless workers (IaC/terraform/aws-managed/pipeline/analytics)
  splunk<TAB>Splunk ECS task (IaC/terraform/aws-managed/pipeline/analytics)
  syslog_ng<TAB>syslog-ng ECS task - device syslog behind the NLB to Kafka (IaC/terraform/aws-managed/pipeline/stream)
  telegraf_dialout<TAB>Telegraf dial-out ECS task - traps, syslog and MDT behind the NLB (IaC/terraform/aws-managed/pipeline/stream)
  telegraf_dialout_nlb<TAB>Internal NLB in front of the Telegraf dial-out task (IaC/terraform/aws-managed/pipeline/stream)
  web<TAB>Chat web EC2 - SSM port forwarding to Grafana, Splunk and the Temporal UI starts here
  workflow<TAB>Temporal dev server and worker ECS task (IaC/terraform/aws-managed/workflow)
```

4. 実物の state から読む（main のチェックアウトの `IaC/terraform/aws-managed/base/core` で `terraform show -no-color` だけ。出力は scratchpad に置き、確認後に消した）

```
$ bash scratchpad/sds_real.sh
show rc=0
runtime<TAB>AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)
---
11
289:# aws_security_group.workload["runtime"]:
```

期待どおり runtime の 1 行だけ（endpoints は 045 の down.sh で消えている）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort high（サブエージェントの中では effort を切り替えられないので high のまま）
- 反対弁護人: opus / effort xhigh（`Agent`、読み取り専用。設計の意図・実装の方針・不安な箇所（sgblk の止めの副作用、T-6 の state 引数、変異 M2 で T-6 が落ちない件）を渡した。終わったあと作業ツリーの状態を見て、自分の変更以外が増えていないことを確かめた）

#### 指摘と片付け

| # | 分類 | [観点] 場所 | 破綻シナリオ | 確かめたこと | 片付け |
| :-- | :-- | :-- | :-- | :-- | :-- |
| 1 | Should | [correctness] `ops/up-common.sh:41` | endpoints の description を `"${local.name_prefix} …"` のような補間にすると、コードは `${…}` のまま、state は展開後の値になり、毎回の up.sh が誤って die する（直す手段が down.sh しかない） | 変異 M13 / M14（判定を外す）で `_ep_cases` の check が落ちる。判定が無いと補間の文言のまま出る | 直した（`${` / `%{` を含めば読めない行として止める）。**workload 側（043 の `local.security_groups`）にも同じ穴があるが範囲外**。PM に報告 |
| 2 | Should | [missing tests] `tests/test_workflow.py:1482` | 偽の state にアドレスに endpoints を含むルールが無く、見出しの判定を `$0 ~ /endpoints/` にゆるめても通っていた | 変異 M9 で 9 本落ちる | 直した（`endpoints_none` と `flow["gnmic-endpoints-tcp-443"]` のブロックを足した） |
| 3 | Should | [missing tests] `tests/test_workflow.py`（OSS の塊の check） | 逸脱で足した `!sgblk` の止めを OSS 版で縛る check が無かった（`n == 0` だけに戻すと oss.tf の分で n>0 になり素通り） | 変異 M15 で落ちる | 直した（check を足した） |
| 4 | Should | [記録] build.md | 検証の生ログが無かった | — | 「検証」とこのセクションを書いた |
| 5 | Nit→直した | [correctness] `ops/up-common.sh:75` | taint された endpoints は見出しが `# aws_security_group.endpoints: (tainted)` になり、完全一致では読まずに通す | 変異 M10（完全一致に戻す）で tainted の check が落ちる | 直した（前方一致。workload と同じ） |
| 6 | Nit→直した | [設計整合] `tests/test_workflow.py:1482`、T-3 | 偽の state の並びが実物と逆 | `da/show-backup.txt` で endpoints が workload より前 | 直した（上の逸脱） |
| 7 | Nit→直した | [missing tests] `ops/up-common.sh:39-43, 56` | 行末の `"` の判定を外す・字下げを問わない・ブロック無しの文言を消す変異が生き残っていた | M11 / M12 / M16 がそれぞれ 1 本落ちる | 直した（`_ep_cases` と `_sdc_tmp` の文言の check） |
| 8 | Nit→直した | [docs] `docs/troubleshooting.md:26`、`security_groups.tf:27` | 「web / lab / lambda 以外の全部」が endpoints も含むと読める。27 行目のコメントが「全キー」のまま | 読んだだけ | 直した。043 の docs の check（「web / lab / lambda 以外の全部）が違う」を含む）が通ることは検証 2 で確認 |

自分で見つけたもの: endpoints の 1 行を必ず出すと `check_sg_descriptions` の `[ -n "$code" ]` の die が効かなくなる退行（既存 check が `rc=0 '' ['tf base/core show -no-color']` で落ちて見つけた）。「設計からの逸脱」の 1 つ目で直した。

未解消の Must fix: 0。

#### 変異（`scratchpad/mut.py` で 1 つずつ入れ、check を止めない写しの `tests/_mut_tw.py` で打って戻した。全部が 1 本以上落ちる）

```
== M1 state の endpoints の分岐を消す: rc=0 落ちた check 6
   FAILED sg_descriptions_in_state は SG のブロック（workload と endpoints。cycle 046）の 4 空白の description だけを <キー>\t<文言
   FAILED check_sg_descriptions: endpoints の description が古ければ、state とコードの文言を出して die し、down.sh の全消しを案内する。runti
   FAILED check_sg_descriptions: workload の SG と endpoints が両方違えば、die の文に state の並び（terraform show はアドレス順なので e
   FAILED check_sg_descriptions: state の endpoints のブロックに 4 空白の description の行が無ければ（12 空白の入れ子だけ）、空として die する（c
   FAILED check_sg_descriptions: OSS 版も同じ 1 本で endpoints を見る（古ければ die、同じなら通る。cycle 046。いま: rc=0 / 0 '' ''）
   FAILED sg_descriptions_in_state は見出しに「 (tainted)」が付いた endpoints も読む（作り直しの対象そのもの。cycle 046。いま: 'web\tW\n'）
== M2 code の endpoints の出力を消す: rc=0 落ちた check 7
   FAILED sg_descriptions_in_code は実物の security_groups.tf の local.security_groups のキーと文言を全部出し、Python の正規表現で取った
   FAILED sg_descriptions_in_code は OSS 版（IaC/terraform/oss。oss.auto.tfvars が nwc-oss）では oss.tf の local.oss_se
   FAILED check_sg_descriptions: endpoints の description が古ければ、state とコードの文言を出して die し、down.sh の全消しを案内する。runti
   FAILED check_sg_descriptions: workload の SG と endpoints が両方違えば、die の文に state の並び（terraform show はアドレス順なので e
   FAILED check_sg_descriptions: state の endpoints のブロックに 4 空白の description の行が無ければ（12 空白の入れ子だけ）、空として die する（c
   FAILED check_sg_descriptions: OSS 版も同じ 1 本で endpoints を見る（古ければ die、同じなら通る。cycle 046。いま: rc=0 / 0 '' ''）
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M3 code の endpoints の読みを全部消す: rc=0 落ちた check 9
   FAILED sg_descriptions_in_code は実物の security_groups.tf の local.security_groups のキーと文言を全部出し、Python の正規表現で取った
   FAILED sg_descriptions_in_code は OSS 版（IaC/terraform/oss。oss.auto.tfvars が nwc-oss）では oss.tf の local.oss_se
   FAILED check_sg_descriptions: endpoints の description が古ければ、state とコードの文言を出して die し、down.sh の全消しを案内する。runti
   FAILED check_sg_descriptions: workload の SG と endpoints が両方違えば、die の文に state の並び（terraform show はアドレス順なので e
   FAILED check_sg_descriptions: state の endpoints のブロックに 4 空白の description の行が無ければ（12 空白の入れ子だけ）、空として die する（c
   FAILED check_sg_descriptions: security_groups.tf の endpoints の description が読めない（式）なら、endpoints を黙って飛ばさず di
   FAILED check_sg_descriptions: OSS 版も同じ 1 本で endpoints を見る（古ければ die、同じなら通る。cycle 046。いま: rc=0 / 0 '' ''）
   FAILED sg_descriptions_in_code: endpoints の resource のブロックが無い（名前を変えた）か、local.security_groups に endpoints とい
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M4 workload の表が読めないときの止めを消す: rc=0 落ちた check 2
   FAILED check_sg_descriptions: security_groups.tf から文言が 1 つも読めない（local.security_groups の形が変わった）なら、全キーを飛ばして素通
   FAILED sg_descriptions_in_code: OSS 版で security_groups.tf の local.security_groups の塊だけが読めない（oss.tf は読める）なら、
== M5 読めない description の行を集める規則を消す: rc=0 落ちた check 2
   FAILED check_sg_descriptions: security_groups.tf の endpoints の description が読めない（式）なら、endpoints を黙って飛ばさず di
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M6 キーの衝突の判定を消す: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の resource のブロックが無い（名前を変えた）か、local.security_groups に endpoints とい
== M7 state のキー名を endpoint にする: rc=0 落ちた check 6
   FAILED sg_descriptions_in_state は SG のブロック（workload と endpoints。cycle 046）の 4 空白の description だけを <キー>\t<文言
   FAILED check_sg_descriptions: endpoints の description が古ければ、state とコードの文言を出して die し、down.sh の全消しを案内する。runti
   FAILED check_sg_descriptions: workload の SG と endpoints が両方違えば、die の文に state の並び（terraform show はアドレス順なので e
   FAILED check_sg_descriptions: state の endpoints のブロックに 4 空白の description の行が無ければ（12 空白の入れ子だけ）、空として die する（c
   FAILED check_sg_descriptions: OSS 版も同じ 1 本で endpoints を見る（古ければ die、同じなら通る。cycle 046。いま: rc=0 / 0 '' ''）
   FAILED sg_descriptions_in_state は見出しに「 (tainted)」が付いた endpoints も読む（作り直しの対象そのもの。cycle 046。いま: 'endpoint\tVP
== M8 endpoints を最初に出す: rc=0 落ちた check 3
   FAILED sg_descriptions_in_code は実物の security_groups.tf の local.security_groups のキーと文言を全部出し、Python の正規表現で取った
   FAILED sg_descriptions_in_code は OSS 版（IaC/terraform/oss。oss.auto.tfvars が nwc-oss）では oss.tf の local.oss_se
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M9 state の見出しをゆるめる（endpoints を含めば何でも）: rc=0 落ちた check 9
   FAILED check_sg_descriptions: state の description が全キーともコードと同じなら通り、terraform show を 1 回だけ打ち、init も state li
   FAILED check_sg_descriptions: コードに無いキー（キーを変えた古い telegraf。既存の守りの担当）は古い文言でも飛ばして通す（いま: rc=1 'DIE: /var/folders
   FAILED check_sg_descriptions: state に workload の SG が 1 つも無い（aws_vpc とルールのブロックだけ）なら通す（いま: rc=1 'DIE: /var/f
   FAILED check_sg_descriptions: OSS 版（oss.auto.tfvars が project = "nwc-oss"）は oss.tf の文言で比べる。spark が oss.tf の
   FAILED sg_descriptions_in_state は SG のブロック（workload と endpoints。cycle 046）の 4 空白の description だけを <キー>\t<文言
   FAILED check_sg_descriptions: state の endpoints の description がコードと同じなら通り、terraform show を 1 回だけ打ち、init も s
   FAILED check_sg_descriptions: state に endpoints のブロックが無い（初回、消した後）なら通す（cycle 046。いま: rc=1 'DIE: /var/folders
   FAILED check_sg_descriptions: OSS 版も同じ 1 本で endpoints を見る（古ければ die、同じなら通る。cycle 046。いま: rc=1 / 1 'DIE: /var
   FAILED sg_descriptions_in_state は見出しに「 (tainted)」が付いた endpoints も読む（作り直しの対象そのもの。cycle 046。いま: 'endpoints\tV
== M10 state の見出しを完全一致に戻す（tainted を見逃す）: rc=0 落ちた check 1
   FAILED sg_descriptions_in_state は見出しに「 (tainted)」が付いた endpoints も読む（作り直しの対象そのもの。cycle 046。いま: 'web\tW\n'）
== M11 行末が " かの判定を消す: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M12 字下げを問わず読む: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M13 補間の判定を消す: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M14 %{ の判定だけ消す: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の description が補間・テンプレートの指示・行末のコメントなら、その行を出して止める（state には展開後の値が入り
== M15 sgblk を見ず n == 0 だけ: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: OSS 版で security_groups.tf の local.security_groups の塊だけが読めない（oss.tf は読める）なら、
== M16 ブロックが無いときの文言を消す: rc=0 落ちた check 1
   FAILED sg_descriptions_in_code: endpoints の resource のブロックが無い（名前を変えた）か、local.security_groups に endpoints とい
```

設計の期待との差: 設計は「endpoints の出力を消すと T-2〜T-7 が落ちる（T-6 は NG の文言で）」。出力の printf だけを消す M2 では T-6 は落ちない（読めない NG は END の判定で出るので残る）。読みを全部消す M3 で T-6 が落ちる。M1 は設計どおり T-2 / T-3 / T-5 / T-7（と state の読みの check）。M4 は逸脱で足した止め、M9〜M16 はセルフレビューで足した守り。

#### 問題なしとした観点と根拠

- [API compatibility] 比べる側 `check_sg_descriptions` と die の文言は 043 のまま（変えたのは `sg_descriptions_in_code` / `sg_descriptions_in_state` の awk とコメントだけ）。既存の `check_sg_descriptions:` の 12 本と `test_oss_ops.py` の 2 本が通る（検証 2）
- [runtime] 実物の security_groups.tf / oss.tf で読める（検証 3。aws-managed 17 行、OSS 22 行、endpoints は最後の 1 行だけ）。実物の state の写しで endpoints が読める（検証 3 の state の部分）
- [awk の方言] 手元は `/usr/bin/awk`（BWK）だけで gawk / mawk は無い。使ったのは `index` / `match` / `RLENGTH` / `substr` / `FILENAME` / `ARGV` で POSIX の範囲。gawk / mawk では動かしていない（読んだだけ）
- [security] シークレット・`.env` は読んでいない。AWS では何も作っていない。main のチェックアウトで打ったのは `terraform show -no-color` だけ

#### PM に回すもの

- workload（`local.security_groups` / `local.oss_security_groups`）の description が補間を含むと、043 の守りが毎回誤って die する（endpoints は 046 で塞いだ）。いまの実物に補間は無いので壊れてはいない。QUEUE の候補
- 設計の未確定事項 1（endpoints の実物の terraform show の形）は `scratchpad/da/show-backup.txt`（10-11 03:32 の実物の写し）で確かめられた。QUEUE に足す予定の「次に AWS で立てたら grep で記録する」が要るかは PM の判断
