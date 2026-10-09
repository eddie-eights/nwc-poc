# ops の小さな直しを片付ける（023）の実装記録

## Round 1

実装モデル: opus（opus-5.5）/ effort: 未確認（このセッションから設定を読めない）。ブランチ feat/023-ops-fixes（origin/main d17b80c から。worktree のブランチ worktree-agent-a3c4d4766e7606386 を push）。1 件 1 commit（A 97365cf / B 69e37b8 / C 83255e0 / D e89a2ea）。

### 変更ファイル

- A
  - `ops/up-common.sh`: `ensure_secret` / `ensure_fixed_secret` の一時ファイルをグローバルの `SECRET_INPUT` に持つ（`local input` をやめる）
  - `ops/up.sh`: `on_exit` に `SECRET_INPUT` の 1 行
  - `ops/oss/up.sh`: 1 行の trap を `on_exit()` 関数（`TF_AWS_CONFIG` / `NAUTOBOT_CTX` / `ROLL_PLAN` / `SECRET_INPUT`）にし、exec の前は `trap - EXIT` のあと `on_exit` を呼ぶ
  - `tests/test_oss_ops.py`: 偽 `aws` の `ssm put-parameter` に `FAKE_SSM_PUT_SIGNAL`、SIGINT / SIGTERM の check 2 つ、oss の `on_exit` の check 1 つ（196 → 199）
  - `tests/test_analytics.py`: 字面を `file://$SECRET_INPUT` に
- B
  - `app/spark/snmp_sinks.py`: `ensure_topics` は `createTopics(new).values()` でトピックごとに `get()` し、`TopicExistsException` のものは返さない
  - `tests/test_analytics.py`: 偽の `_Admin` に `values()`、`:674` の期待を `[]`、一部だけ TopicExists の check（513 → 514）
- C
  - `ops/check.sh`: `5.` の段と冒頭のコメント
  - `tests/test_oss.py`: `5.` の段の字面の check（173 → 174）
  - `docs/development.md`: check.sh の説明に `5.` を 1 句と、テストの件数（analytics / oss / oss_ops）
- D
  - `tests/test_stream.py`: `_s83` の 3 条件を `_s83_re.fullmatch(_s83)` の 1 本に（106 のまま）

### 設計との違い

1. **A: 「`ops/oss/up.sh` は関数を定義しない」の既存の check を、`on_exit` だけ許す形に直した。** 設計の「`ops/oss/up.sh` の trap を `on_exit()` 関数にする」と、`tests/test_oss_ops.py` の既存の check（`funcs(up)` が空）がぶつかる。`funcs(up) == {"on_exit"} and not funcs(down)` にして、名前もそう直した。あわせて、ROLL_PLAN を見ていた既存の check（`"\ntrap '"` の字面に頼っていた）は `on_exit` の本文を見る形にした。
2. **A: oss の `on_exit` の check に、設計に無い条件を 1 つ足した。** `up.count("\ntrap ") == 2`（`trap on_exit EXIT` と `trap - EXIT` の 2 つだけ。1 行の trap が残っていない）。
3. **C: 旧名を `ops/check.sh` に字面で書かない（`OLD_NAME='net''ops'`）。** 設計のスニペットのまま字面で書くと、`ops/check.sh` 自身が `git ls-files` の grep に掛かって `5.` が必ず止まる。`tests/test_oss.py` の check の名前も字面を避けて「旧名」と書いた（`tests/test_oss.py` が許す 3 ファイルに入っていないため）。`docs/development.md` も同じ。
4. **C: `docs/development.md` のテストの件数も直した。** 設計は「段の一覧があれば 1 行」。`:37` に段の一覧（文）があったので `5.` を 1 句足し、同じ文の件数（`test_analytics` 513 → 514、`test_oss` 173 → 174、`test_oss_ops` 196 → 199）も直した。
5. **C の止まり方は、`5.` の段だけを抜き出して打った。** 通しの `ops/check.sh` は 1 回 4〜9 分かかる（この PC で 9:16 と 4:02）ので、README.md と tables.tf にわざと足す 2 回は `ops/check.sh` から `log` / `die` の定義と `5.` の段をそのまま切り出した写し（scratchpad の `stage5.sh`。`set -euo pipefail` と `cd` は同じ）で打った。通しの 1 回（足さない形）は本物の `ops/check.sh`。
6. **D: 末尾は `fi\n\n*` でなく `fi\n*`。** `_s83` は `_between(…, "\n# ---- 8-5. ")` なので 8-5 の見出しの前の `\n` を含まない。`fi` の直後に空行が無い (d) では `_s83` の末尾が `fi` で切れ、設計の `fi\n\n*` では落ちる（実測は検証 4）。

### 検証 1（テスト）

```
$ uv run --group dev --group web python tests/test_oss_ops.py
通過 199 / 失敗 0
$ uv run --group dev --group web python tests/test_analytics.py
通過 514 / 失敗 0
$ uv run --group dev --group web python tests/test_oss.py
通過 174 / 失敗 0
$ uv run --group dev --group web python tests/test_stream.py
通過 106 / 失敗 0
$ uv run --group dev --group web python tests/test_local_compose.py
通過 138 / 失敗 0
```

ほかの 11 本は `ops/check.sh` の `4.` の中で打った（検証 3。全部 失敗 0）。

### 検証 2（A の割り込み）

赤: `SECRET_SH` に `ops/up.sh` の `on_exit` を組み込まずに打った（check は最初の失敗で止まるので、一時的に値を print し、INT と TERM の順を入れ替えてもう 1 回打った。どちらも戻した）:

```
RED-DEBUG INT returncode= -2 put-parameter= 1 new-id あり= False 残った= ['nwc-secret.Xt7yuS']
AssertionError: ensure_secret: put-parameter の最中に SIGINT で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
RED-DEBUG TERM returncode= -15 put-parameter= 1 new-id あり= False 残った= ['nwc-secret.MngIwV']
AssertionError: ensure_secret: put-parameter の最中に SIGTERM で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
```

緑（組み込んだ形）:

```
ok ensure_secret: put-parameter の最中に SIGINT で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
ok ensure_secret: put-parameter の最中に SIGTERM で止まっても、値を書いた一時ファイルを残さない（ops/up.sh の on_exit が消す）
ok ops/oss/up.sh と down.sh は関数を定義しない（ops/ の共通の関数を読む。up.sh の EXIT の trap の on_exit だけは別。cycle 023）
ok ops/oss/up.sh の EXIT の trap は on_exit 関数で、TF_AWS_CONFIG・NAUTOBOT_CTX・ROLL_PLAN・SECRET_INPUT の 4 つを消し、exec の前に trap を外して同じ on_exit を 1 回呼ぶ（cycle 023）
```

```
$ grep -c SECRET_INPUT ops/up-common.sh ops/up.sh ops/oss/up.sh
ops/up-common.sh:9
ops/oss/up.sh:1
ops/up.sh:1
$ grep -n 'local input' ops/up-common.sh   # 0 行（rc=1）
```

### 検証 3（C の check.sh）

通しで 2 回打った（C を入れた直後と、D まで入れた最後）。最後の回:

```
$ time bash ops/check.sh
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK
== 3. スクリプトの構文
bash -n: 28 本
構文エラーなし
== 4. 模擬テスト
  （test_agentcore）通過 161 / 失敗 0
  （test_alerts）通過 168 / 失敗 0
  （test_analytics）通過 514 / 失敗 0
  （test_collectors）通過 79 / 失敗 0
  （test_dashboard_config）通過 3 / 失敗 0
  （test_graph）通過 78 / 失敗 0
  （test_kb_index）通過 7 / 失敗 0
  （test_lab_debug）通過 97 / 失敗 0
  （test_local_compose）通過 138 / 失敗 0
  （test_nautobot）68 項目すべて通過
  （test_oss）通過 174 / 失敗 0
  （test_oss_ops）通過 199 / 失敗 0
  （test_oss_roll）通過 66 / 失敗 0
  （test_stream）通過 106 / 失敗 0
  （test_sync）通過 103 / 失敗 0
  （test_workflow）通過 327 / 失敗 0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
rc=0
（time）bash ops/check.sh … 126.08s user 57.75s system 75% cpu 4:02.56 total
```

空行と `ok` の行と、わざと例外を起こす check の Traceback（`test_agentcore` / `test_sync`）は省いた。`4.` の件数の頭の（ ）のファイル名は、読みやすいように付けた（`ops/check.sh` は出さない。並びは `tests/test_*.py` のグロブの順）。C を入れた直後の 1 回目（9:16）も同じ件数で、最後が `netops なし（許した 3 ファイル 5 行だけ）` → `すべて通過`、rc=0。

止まり方（`5.` の段の写しで。設計との違い 5）:

```
--- そのまま: rc=0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
--- README.md に 1 行足した: rc=1
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）

!! netops が残っている（許すのは tables.tf の moved と test_analytics の check だけ）: IaC/terraform/aws-managed/pipeline/analytics/tables.tf IaC/terraform/oss/pipeline/analytics/tables.tf README.md tests/test_analytics.py
--- tables.tf の moved に 1 行足した: rc=1
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）

!! tables.tf と test_analytics.py の netops が 5 行でない（6 行）。moved 3 行と check 2 行以外に増えている
--- 戻したあと: rc=0
== 5. 旧名 netops が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）
netops なし（許した 3 ファイル 5 行だけ）

すべて通過
(README.md と IaC/ に差分なし)
```

### 検証 4（D の 4 通り）

`ops/up.sh` の 8-3 を書き換えた写しを scratchpad に作り、1 つずつ `ops/up.sh` に上書きして `tests/test_stream.py` を打ち、`git checkout -- ops/up.sh` で戻した（commit していない）。(a2) は設計の (a) の趣旨（`&&` で旧い check をすり抜ける形）を確かめるために足した 1 通りで、`if` を先に `:` だけで閉じ、restart は `[ -n "$NEVER" ] &&` に続けて if の外に置く。

| 形 | 新しい check（test_stream） | 旧い check（字面だけで判定） |
|---|---|---|
| (a) `[ -z "$SKIP_STREAM" ] \|\| … && {` 〜 `}` | 失敗（AssertionError） | 落ちる |
| (a2) if を先に閉じて `&&` で restart | 失敗（AssertionError） | **通る**（すり抜け） |
| (b) `if` を 2 スペース字下げ | 失敗（AssertionError） | 落ちる |
| (c) 呼ばない `s83() {` で包む | 失敗（AssertionError） | 落ちる |
| (d) `fi` の直後の空行を消す | 通過 106 / 失敗 0 | **落ちる**（`"\nfi\n"` が 0 回） |
| 元の形 | 通過 106 / 失敗 0 | 通る |

```
--- (a): rc=1
    AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
--- (a2): rc=1
    AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: …（同じ）
--- (b): rc=1
    AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: …（同じ）
--- (c): rc=1
    AssertionError: Kafbat UI を起こす Web の restart は stream の apply より後: …（同じ）
--- (d): rc=0
    ok Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
    通過 106 / 失敗 0
--- 元の形: rc=0
    ok Kafbat UI を起こす Web の restart は stream の apply より後: …（同じ）
    通過 106 / 失敗 0
```

設計の `fi\n\n*` のままだと (d) で落ちる:

```
up_d.sh '…いる"\nfi' design fi\n\n*: False
up.sh   '…る"\nfi\n' design fi\n\n*: True
```

### 検証 5（B の挙動）

赤: `:674` の期待だけ `[]` にして（`snmp_sinks.py` は旧いまま）打った:

```
AssertionError: ensure_topics: 同時に作られて TopicExistsException になっても先へ進み、作ったとは言わない
```

緑:

```
ok ensure_topics: 同時に作られて TopicExistsException になっても先へ進み、作ったとは言わない
ok ensure_topics: 一部だけ TopicExists（logs だけほかが先に作った）なら、作れた traps だけを返す。close はする
```

`git diff d17b80c -- app/spark/snmp_sinks.py` に `log(` の行は無い（`main` のログは変えていない。変えたのは `ensure_topics` の docstring と本体だけ）。

未確定 1（py4j の `values()`）は偽物の `_Admin` で形を合わせただけ。`values()` が返す Java の `Map` を `futures.get(t)`（キーは頼んだトピック名の文字列）で引き、`KafkaFuture.get()` が `TopicExistsException` を含む例外を投げる、という形。実機（MSK / compose の Kafka）では確かめていない。

### 検証 6（構文）

```
$ bash -n ops/up.sh ops/oss/up.sh ops/up-common.sh ops/check.sh; echo "rc=$?"
rc=0
$ bash -n ops/oss/up.sh; bash -n ops/up-common.sh; bash -n ops/check.sh   # bash -n a b は a しか見ないので 1 本ずつも。どれも rc=0
```

`ops/check.sh` の `3.`（`git ls-files '*.sh'` の 28 本を 1 本ずつ）も `構文エラーなし`。

### 検証 7（AWS）

確かめていない（設計どおり）。

### セルフレビュー

観点: correctness / security（平文の一時ファイルが残る経路）/ runtime bugs（`set -u` / `set -e` の下の `on_exit`）/ design.md との差 / 足りないテスト。

- **correctness**
  - `ensure_secret` / `ensure_fixed_secret` の呼び出しは全部メインのシェルから（`ops/up.sh:922,923,929,1039`、`ops/oss/up.sh:348-353,410,473,478`、`ensure_splunk_secrets` / `ensure_nautobot_secrets` 経由も含む）。サブシェルや `&` の中で呼ぶ所は無いので、`SECRET_INPUT` の代入は EXIT の trap から見える。graph の裏の apply（`( tf_apply_only … ) &`）は secret を作らない。
  - 2 つの関数は直列にしか呼ばれないので 1 変数でよい（設計どおり）。終わりで `SECRET_INPUT=""` に戻すので、あとの `on_exit` が別のファイルを消すことは無い。
  - `ensure_topics` は `missing` の順に `made` を積むので、戻りの順は頼んだ順のまま。`TopicExists` 以外の例外は上げ、`admin.close()` は `finally` のまま。
- **security（平文の一時ファイルが残る経路）**
  - put-parameter の最中の SIGINT / SIGTERM は、ops/up.sh も ops/oss/up.sh も `on_exit` が消す（前者は検証 2 で実測、後者は同じ行の字面の check）。
  - 残る経路は 2 つ。① `mktemp` がファイルを作ってから `SECRET_INPUT=` の代入が終わるまでの間の割り込み: 値を書く前なので空のファイルが残るだけ。② SIGKILL や電源断: trap では消せない。どちらもモード 0600（`umask 077`）。
- **runtime bugs**
  - `set -u`: `SECRET_INPUT=""` は `up-common.sh` の読み込み（`ops/up.sh:152`、`ops/oss/up.sh:38`）で必ず先に通り、trap はそのあと（`ops/up.sh:166`、`ops/oss/up.sh:51`）。`ROLL_PLAN` は `ops/oss/roll-nodes.sh:23` で `""`（読み込みは trap より前。もとの 1 行の trap も同じ変数を見ていた）。
  - `set -e`: `on_exit` の各行は `if [ -n … ]; then rm -f …; fi` で、空なら 0 を返す。exec の前の `on_exit` で `rm` が失敗すると `set -e` で止まるのは、もとの手書きの 2 行と同じ。
- **design.md との差**: 上の「設計との違い」の 6 つ。
- **足りないテスト**: 下の Should fix の 2・3。

Must fix: 0（見つからなかった）。

Should fix（直していない）:

1. `ops/up.sh:1351-1352`（マネージド版）の exec の前は、まだ `trap - EXIT` のあと `TF_AWS_CONFIG` だけを手で消している。OSS 版は `on_exit` を呼ぶ形にしたので非対称。いまは `SECRET_INPUT` / `MSK_SCRAM_INPUT` が使い終わりで空になるので値は残らないが、`NAUTOBOT_CTX` などを足したときに漏れやすい（023 の範囲外。`on_exit` は `GRAPH_PID` を待つので、そこまでに待ち終わっていることも確かめる）。
2. 割り込みの check は `ensure_secret` だけ。`ensure_fixed_secret` は同じ形だが打っていない。
3. `ops/oss/up.sh` の `on_exit` は字面の check だけで、シグナルを送って実際に消えることは打っていない（`SECRET_SH` に組み込んでいるのは `ops/up.sh` の `on_exit`）。
4. `ops/check.sh` の `5.` の「3 ファイル 5 行」は固定値（設計の未確定 3）。`moved` を消すときはここも直す。WSL の GNU grep では未確認（未確定 2）。
