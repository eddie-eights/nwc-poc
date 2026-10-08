# build — 根に残ったものを app/ ops/ docs/ に片付ける（017）

## Round 1

実装モデル: opus-5.5 / effort: xhigh（high へ下げる手段が無い）

基点は `docs/cycle-006-design` の 03840c8。ブランチは `chore/tidy-root`（worktree `.claude/worktrees/tidy-root`）。

### commit

| commit | 内容 |
| :--- | :--- |
| fd9918b | rename だけ（git mv の 4 組 = 9 本。中身は 1 バイトも変えない） |
| f92399c | 参照の書き換え |
| （この commit） | build.md |

### 変更ファイル（`git diff --name-status -M 03840c8 f92399c`）

```
M	.env.example
M	.gitignore
M	IaC/terraform/aws-managed/base/core/security_groups.tf
M	IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf
M	IaC/terraform/aws-managed/pipeline/stream/telegraf.tf
M	IaC/terraform/aws-managed/workflow/gateway.tf
M	IaC/terraform/oss/pipeline/analytics/opensearch.tf
M	IaC/terraform/oss/pipeline/analytics/victoriametrics.tf
M	IaC/terraform/oss/pipeline/graph/neo4j.tf
M	IaC/terraform/oss/pipeline/stream/kafka.tf
M	README.md
M	app/agentcore/app.py
M	app/containerlab/trex/kafka_load.sh
R100	tools/handler.py	app/gateway/handler.py
R100	tools/tools.json	app/gateway/tools.json
M	app/nautobot/requirements-oss.txt
M	docker/compose/README.md
M	docker/compose/check.sh
M	docker/compose/compose.yaml
R100	GLOSSARY.md	docs/GLOSSARY.md
M	docs/architecture/README.md
M	docs/architecture/resources/grafana.md
M	docs/architecture/resources/lab-ec2.md
M	docs/architecture/resources/nautobot.md
M	docs/architecture/resources/ssm-parameter-store.md
M	docs/collection.md
M	docs/data-stores.md
M	docs/deploy.md
M	docs/development.md
M	docs/faq-fukuda-nwc-poc.md
M	docs/oss-variant.md
M	docs/pipeline.md
M	docs/troubleshooting.md
M	docs/verification/20261008-oss-aws.md
M	ops/check-grafana.sh
M	ops/check.sh
M	ops/common.sh
M	ops/deploy-env.sh
M	ops/down-common.sh
M	ops/down.sh
M	ops/grafana_rules_check.py
R086	tools/netflow_send.py	ops/netflow_send.py
R086	oss/ops/down.sh	ops/oss/down.sh
R097	oss/ops/oss-images.sh	ops/oss/oss-images.sh
R097	oss/ops/roll-nodes.sh	ops/oss/roll-nodes.sh
R099	oss/ops/roll_health.py	ops/oss/roll_health.py
R097	oss/ops/up.sh	ops/oss/up.sh
M	ops/seed_graph.py
M	ops/sync-graph.sh
M	ops/up-common.sh
M	ops/up.sh
M	tests/test_alerts.py
M	tests/test_analytics.py
M	tests/test_collectors.py
M	tests/test_lab_debug.py
M	tests/test_local_compose.py
M	tests/test_nautobot.py
M	tests/test_oss.py
M	tests/test_oss_ops.py
M	tests/test_oss_roll.py
M	tests/test_stream.py
M	tests/test_sync.py
M	tests/test_workflow.py
```

参照の書き換えは scratchpad の `replace017.py` で行った。対象は `git ls-files` のうち `docs/cycles/` 以外（symlink と UTF-8 で読めないファイルは飛ばす）。

- 全ファイル: `oss/ops`→`ops/oss`、`tools/handler.py`→`app/gateway/handler.py`、`tools/tools.json`→`app/gateway/tools.json`、`tools/netflow_send.py`→`ops/netflow_send.py`。
- `tests/` だけ: `"oss", "ops"`→`"ops", "oss"`。
- ファイルごと: `ops/oss/up.sh`・`down.sh` の `"$(dirname "$0")/../../ops/`→`"$(dirname "$0")/../`、`ops/check.sh` の find（`oss`・`tools` を外す）、`gateway.tf:16` の桁揃え、`tests/test_{collectors,workflow,oss_ops,oss,nautobot}.py` の `os.path.join` と期待値。

置換のあとに手で直したのは、旧パスを字面で残す文（`docs/oss-variant.md:66` の方針 3 の括弧書き）、`README.md`・`docs/oss-variant.md` の「`oss/`」の言い換え（`IaC/terraform/oss/` と `ops/oss/`）、下の「設計からの逸脱」2 の足し 2 つ。

### 実装ステップ

| # | 結果 |
| :--- | :--- |
| 1 | fd9918b。9 本すべて rename（R100 / R086〜R099 は 2 つ目の commit の書き換え込みの類似度） |
| 2 | f92399c。grep は 1 行残る（下の「設計からの逸脱」1） |
| 3 | 下の検証 1 |
| 4 | 何もしていない。この worktree には `oss/` が無かった（`ls -d oss tools` → `ls: oss: No such file or directory` / `ls: tools: No such file or directory`）。worktree を 03840c8 から切ったので untracked の `oss/terraform/` は最初から無い |
| 5 | この build.md |

### 検証方法（f92399c の作業ツリー。この build.md のほかに未コミットの変更なし。scratchpad の verify017.sh で、build.md を書いたあとに取り直した）

検証 1 のログ（check.sh の全出力）にある `ClientError: x` と `ERROR:agent:agent failed` のトレースバックは、`tests/test_app.py` がわざと失敗させる経路のログで、test_app は 161 件通っている。

```
### 1 bash ops/check.sh
rc=0
通過 169 / 失敗 0
通過 504 / 失敗 0
通過 161 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0
69 項目すべて通過
通過 173 / 失敗 0
通過 194 / 失敗 0
通過 66 / 失敗 0
通過 96 / 失敗 0
通過 103 / 失敗 0
通過 327 / 失敗 0
すべて通過
### 2 git ls-files oss tools GLOSSARY.md（行数）
       0
### 3 ls
CLAUDE.md
IaC
README.md
app
deploy.env.example
docker
docs
ops
pyproject.toml
tests
uv.lock
### 4 実装ステップ 2 の grep
./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータ
### 5 git log --follow
f587cb0 OSS 版（005）の oss/ops/up.sh・down.sh を作り、ops/ の共通の関数を ops/common.sh・up-common.sh・down-common.sh に切り出した
f55feb4 フェーズ 3 を実装する: Temporal on ECS Fargate のワーカー（terraform/workflow）と AgentCore Gateway（MCP）
### 6 git diff --stat -M 03840c8（設計のベース）
 {tools => app/gateway}/handler.py                  |   0
 {tools => app/gateway}/tools.json                  |   0
 GLOSSARY.md => docs/GLOSSARY.md                    |   0
 {tools => ops}/netflow_send.py                     |   8 +-
 {oss/ops => ops/oss}/down.sh                       |  30 ++--
 {oss/ops => ops/oss}/oss-images.sh                 |   4 +-
 {oss/ops => ops/oss}/roll-nodes.sh                 |  10 +-
 {oss/ops => ops/oss}/roll_health.py                |   2 +-
 {oss/ops => ops/oss}/up.sh                         |  50 +++----
 63 files changed, 278 insertions(+), 276 deletions(-)
### 7 OPS_DIR
ops/oss/up.sh:41:OPS_DIR=ops/oss        # SSM のパラメータのタグ ManagedBy=ops/oss/up.sh（ops/oss/down.sh はこのタグのものだけ消す）
ops/oss/down.sh:27:OPS_DIR=ops/oss       # 消す SSM のパラメータはタグ ManagedBy=ops/oss/up.sh のものだけ（マネージド版の ManagedBy=ops/up.sh は残る）
### 8 created by
8
### 9 terraform validate
Success! The configuration is valid.

### 10 test_workflow
通過 327 / 失敗 0
```

突き合わせ:

| # | 期待 | 結果 |
| :--- | :--- | :--- |
| 1 | `すべて通過`、16 本の件数が `docs/development.md:37` と同じ | 合う。check.sh は `tests/test_*.py` の辞書順で回すので、上から alerts 169・analytics 504・app 161・collectors 79・dashboard_config 3・graph 78・kb_index 7・lab_debug 104・local_compose 132・nautobot 69・oss 173・oss_ops 194・oss_roll 66・stream 96・sync 103・workflow 327。check.sh の前半は `terraform fmt` 差分なし、validate 18 ルート OK、`bash -n: 27 本` 構文エラーなし |
| 2 | 空 | 0 行 |
| 3 | `CLAUDE.md IaC README.md app deploy.env.example docker docs ops pyproject.toml tests uv.lock` | 同じ |
| 4 | 0 行（`docs/GLOSSARY.md` 自身を除く） | **1 行残る**（`docs/oss-variant.md:66`。下の「設計からの逸脱」1）。`docs/GLOSSARY.md` 自身は `(^|[^/])GLOSSARY\.md` に当たらなかった |
| 5 | 005 以前の最初の commit まで追える | `ops/oss/up.sh` は f587cb0（005 で作った commit）、`app/gateway/handler.py` は f55feb4（フェーズ 3） |
| 6 | 動かした 9 本が rename（`=>`）で出る | 03840c8 に対して 9 本ちょうど。字面どおり `docs/cycle-006-design` に対して打つと、基点の後に入った 2 commit（3907fac・4f10db1。012 の Nit と HTML）の差分も混ざって 68 本になる（rename は同じ 9 本）。下に生ログ |
| 7 | 両方 `OPS_DIR=ops/oss` | 合う |
| 8 | 8 | 8 |
| 9 | `Success!` | 合う |
| 10 | `通過 327 / 失敗 0` | 合う |

検証 6 を字面どおり打った分（`git diff --stat -M docs/cycle-006-design` の rename と最後の行だけ。ほかは 012 の 3907fac・4f10db1 が入れた `docs/architecture/resources/{emr-serverless,msk}.md`・`docs/cycles/012-…/{design,review}.md`・`BACKLOG.md` が逆向きに出る）:

```
 {tools => app/gateway}/handler.py                       |   0
 {tools => app/gateway}/tools.json                       |   0
 GLOSSARY.md => docs/GLOSSARY.md                         |   0
 {tools => ops}/netflow_send.py                          |   8 +++----
 {oss/ops => ops/oss}/down.sh                            |  30 +++++++++++++-------------
 {oss/ops => ops/oss}/oss-images.sh                      |   4 ++--
 {oss/ops => ops/oss}/roll-nodes.sh                      |  10 ++++-----
 {oss/ops => ops/oss}/roll_health.py                     |   2 +-
 {oss/ops => ops/oss}/up.sh                              |  50 +++++++++++++++++++++----------------------
 68 files changed, 279 insertions(+), 283 deletions(-)
```

### 設計からの逸脱

1. **検証 4 が 0 行にならない（設計どうしの食い違い。PM に報告）。** 方針 3 は `docs/oss-variant.md` に「2026-10-09 より前に `oss/ops/up.sh` で立てたものは、そのときの commit の `oss/ops/down.sh` で消す」と旧パスを書くよう指定している。その 1 文が検証 4 の grep に当たる。設計の字面どおりに書き、grep の 1 行は残した。
2. **設計に無い足し 2 つ。**
   - `docs/verification/20261008-oss-aws.md:7` に「スクリプトのパスは 017 で書き換えた。Terraform のパスは当時のまま」の注。
   - `docs/development.md:7` に 017 の 1 文（007 が「`docs/verification/` は当時のパス」と書いているのに、017 は設計の変更対象表どおり verification のスクリプトのパスを書き換えるので、食い違わないように）。

   ただしセルフレビューの S1 で、verification を書き換えたこと自体が記録を誤らせると分かった。足し 2 つは S1 の PM の判断しだいで戻す。

### セルフレビュー

- 自分: opus-5.5 / xhigh。
- 反対弁護人: Agent（general-purpose、model opus。文脈を渡し、読み取り専用で頼んだ）。返ってきたあと `git -C <worktree> status --porcelain -uall` は空。
- `/robust` の要望リストは design.md の実装ステップ 1〜5 と検証 1〜10。どれも上の表のとおり済み（検証 4 だけ設計どうしの食い違いで 1 行残る）。

#### 退行の注入（1 件ずつ入れて走らせ、`finally` で戻す。AWS には触らない）

scratchpad の inject017.py（R1〜R8）:

```
== R1 check.sh の find から ops を外す（oss に戻す）（ops/check.sh、置換 1 か所のうち先頭 1 か所）
   $ tests/test_oss.py → rc=1
     AssertionError: ops/check.sh: bash -n は git ls-files '*.sh' の全部で、そこに ops/oss の 4 つ（oss-images.sh / up.sh / down.sh / roll-nodes.sh）があり、.py の find に ops があり、モックの検査は tests/test_*.py のグロブで回す（名前を 1 つずつ並べな
     AssertionError: ops/check.sh: bash -n は git ls-files '*.sh' の全部で、そこに ops/oss の 4 つ（oss-images.sh / up.sh / down.sh / roll-nodes.sh）があり、.py の find に ops があり、モックの検査は tests/test_*.py のグロブで回す（名前を 1 つずつ並べな
== R2 gateway.tf の tools.json を旧パスに戻す（IaC/terraform/aws-managed/workflow/gateway.tf、置換 1 か所のうち先頭 1 か所）
   $ terraform -chdir=IaC/terraform/aws-managed/workflow validate → rc=1
     Error: Invalid function argument
     on gateway.tf line 10, in locals:
     this result from an attribute of that resource.
   $ tests/test_workflow.py → rc=1
     AssertionError: Gateway のターゲットは tools.json から inline schema を作る
     AssertionError: Gateway のターゲットは tools.json から inline schema を作る
== R3 gateway.tf の handler.py を旧パスに戻す（IaC/terraform/aws-managed/workflow/gateway.tf、置換 1 か所のうち先頭 1 か所）
   $ terraform -chdir=IaC/terraform/aws-managed/workflow validate → rc=1
     Error: Invalid function argument
     on gateway.tf line 37, in data "archive_file" "tools":
     this result from an attribute of that resource.
   $ tests/test_workflow.py → rc=1
     AssertionError: tools Lambda は python3.13 arm64 で、handler.py / toolkit / topology / evidence / proposals / graph / data を zip にする（anomalies は入れない）
     AssertionError: tools Lambda は python3.13 arm64 で、handler.py / toolkit / topology / evidence / proposals / graph / data を zip にする（anomalies は入れない）
== R4 ops/oss/up.sh の OPS_DIR を oss/ops に戻す（ops/oss/up.sh、置換 1 か所のうち先頭 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: ops/oss/ の 2 つは resolve_name_prefix nwc-oss で接頭辞を作り、TF_DIR=IaC/terraform/oss・OPS_DIR=ops/oss・TF_LOG_NAME=tf-oss にする
     AssertionError: ops/oss/ の 2 つは resolve_name_prefix nwc-oss で接頭辞を作り、TF_DIR=IaC/terraform/oss・OPS_DIR=ops/oss・TF_LOG_NAME=tf-oss にする
== R5 ops/oss/down.sh の OPS_DIR を oss/ops に戻す（ops/oss/down.sh、置換 1 か所のうち先頭 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: SSM: ops/oss/up.sh が作った 8 つ（Kafka の CLUSTER_ID、OpenSearch・Splunk・Neo4j・Nautobot のものを含む）を消し、手で入れたものとマネージド版のものは残す
     AssertionError: SSM: ops/oss/up.sh が作った 8 つ（Kafka の CLUSTER_ID、OpenSearch・Splunk・Neo4j・Nautobot のものを含む）を消し、手で入れたものとマネージド版のものは残す
== R6 ops/oss/up.sh の roll-nodes.sh を旧パスで読む（ops/oss/up.sh、置換 1 か所のうち先頭 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: ops/oss/up.sh は stream と analytics の apply の前に roll_nodes（ops/oss/roll-nodes.sh）を同じ -var の配列で打つ
     AssertionError: ops/oss/up.sh は stream と analytics の apply の前に roll_nodes（ops/oss/roll-nodes.sh）を同じ -var の配列で打つ
== R7 ops/oss/up.sh の SSM の description を 1 つ旧パスに戻す（ops/oss/up.sh、置換 8 か所のうち先頭 1 か所）
   $ tests/test_oss_ops.py → rc=0
     通過 194 / 失敗 0
== R8 compose.yaml の netflow_send.py を旧パスに戻す（docker/compose/compose.yaml、置換 1 か所のうち先頭 1 か所）
   $ tests/test_local_compose.py → rc=0
     通過 132 / 失敗 0
   $ tests/test_collectors.py → rc=0
     通過 79 / 失敗 0
== 元に戻した
```

tests が拾わなかった R7・R8 を、検証 4 と検証 8 が拾うか（inject017b.py）:

```
== R7 ops/oss/up.sh の SSM の description を 1 つ旧パスに戻す
   検証 4 の grep → rc=0 2 行
     ./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータのタグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より
     ./ops/oss/up.sh:331:ensure_fixed_secret "/$PREFIX/telegraf-dialin/gnmi-username" "$LAB_GNMI_USERNAME" "gNMI username of the Telegraf dial-in task (created by oss/ops/up.s
   検証 8 → ['7']
== R8 compose.yaml の netflow_send.py を旧パスに戻す
   検証 4 の grep → rc=0 2 行
     ./docker/compose/compose.yaml:112:    # 試すときは tools/netflow_send.py で 1 つ送る。/metrics は 8081/tcp（8080 は Telegraf の health）。待つアドレスは Telegraf と同じく ./up.sh の TELEGRAF_BIND。
     ./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータのタグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より
   検証 8 → ['8']
== 元に戻したあとの検証 4 → 1 行
     ./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータのタグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より
== 元に戻したあとの検証 8 → ['8']
```

`ops/oss/up.sh`・`down.sh` の頭を 016 以前の `../../ops/` の形に戻す（inject017c.py。反対弁護人の指摘で足した）:

```
== R9 ops/oss/up.sh の lab-common.sh を ../../ops/ の形に戻す（ops/oss/up.sh、置換元 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: ops/oss/up.sh は ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh を読む
== R10 ops/oss/up.sh の deploy-env.sh を ../../ops/ の形に戻す（ops/oss/up.sh、置換元 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: ops/oss/up.sh は ops/common.sh・ops/up-common.sh・ops/lab-common.sh・ops/deploy-env.sh を読む
== R11 ops/oss/down.sh の deploy-env.sh を ../../ops/ の形に戻す（ops/oss/down.sh、置換元 1 か所）
   $ tests/test_oss_ops.py → rc=1
     AssertionError: ops/oss/down.sh は ops/common.sh・ops/down-common.sh・ops/deploy-env.sh を読む
== 元に戻した
```

注入のあとの `git status --porcelain -uall` は空。

#### `ops/oss/up.sh`・`down.sh` の頭を実際に読む（prelude017.sh。`.` と `cd` だけを `$0` を変えて打つ。AWS には触らない）

```
== up.sh の頭（根から bash ops/oss/up.sh の形）
  cwd=tidy-root sourced OK
  fn ok mirror_oss_images
  fn ok roll_nodes
  fn ok dir_tag
  rc=0
== up.sh の頭（ops/oss の中から bash up.sh の形）
  cwd=tidy-root sourced OK
  fn ok mirror_oss_images
  fn ok roll_nodes
  fn ok dir_tag
  rc=0
== up.sh の頭（別の場所から絶対パスで）
  cwd=tidy-root sourced OK
  fn ok mirror_oss_images
  fn ok roll_nodes
  fn ok dir_tag
  rc=0
== down.sh の頭（根から）
  cwd=tidy-root down sourced OK
  rc=0
== 退行の形（../../ops/lab-common.sh のまま）は読めないこと
  unexpected OK
  rc=0
```

最後の段は見出しの予想が外れた。`ops/oss/../../ops/lab-common.sh` は `ops/lab-common.sh` と同じファイルなので、旧形でも読める（挙動は同じ）。旧形への退行は R9〜R11 のとおり test_oss_ops が拾う。

#### PM の判断に回したもの

**S1 Should fix ［docs の正しさ・当時の記録の改変］**

- 場所: `docs/verification/20261008-oss-aws.md:286`・`:311`。原因は `design.md` の変更対象表（最後の行）が `docs/verification/20261008-oss-aws.md` を置換の対象に入れていること。007 の方針（`docs/development.md:7`「`docs/verification/` は当時のパス」）と食い違う。
- 破綻シナリオ:
  - :311 は「この worktree から `OWNER=efukuda KEEP_ECR=1 bash ops/oss/down.sh` を打ち直せば、base/core の 3 つと cluster-id のパラメータが消えるはず」と読める。
  - 「この worktree」（verify-oss-20261008）には `oss/ops/` しか無く、`ops/oss/down.sh` は No such file で止まる。
  - 新しいチェックアウトから打っても、`IaC/terraform/oss/base/core` に state が無く、`down-common.sh:182` はタグ `ManagedBy=ops/oss/up.sh` で絞るので、どちらも消えない。
  - :286「`bash ops/oss/down.sh` を、up.sh と同じ worktree で打った」は、当時無かったパスを打ったことにしている。
- 確かめたこと: 反対弁護人が verify-oss-20261008 を `ls` して `oss/ops/` と `oss/terraform/base/core/terraform.tfstate` があり `ops/oss/` が無いことを見た。自分は `ops/oss/down.sh:26-27`（`TF_DIR=IaC/terraform/oss`、`OPS_DIR=ops/oss`）と `down-common.sh:182`（上の引用）を読んだ。
- 直し方の案: verification を 03840c8 の内容に戻し、:7 の注を「パスは当時のもの。017 の後は `ops/oss/`」に替える。`development.md:7` に足した 017 の文は要らなくなる。設計の変更対象表を変えることになるので、実装では直していない。

**S2 Should fix ［設計どうしの食い違い・Terraform の差分］**

- 場所: `IaC/terraform/aws-managed/base/core/security_groups.tf:114`（設計の変更対象表が `why` の書き換えを指定）と、方針 6「Terraform の資源はそのまま」。
- 破綻シナリオ:
  - :114 の行には `only` が無いので、egress のルール（`aws_vpc_security_group_egress_rule.flow`、lab の SG）と ingress のルール（`aws_vpc_security_group_ingress_rule.flow`、telegraf_dialout_nlb の SG）の両方の `description` になる（`:153`/`:156`、`:166`/`:169`）。
  - `IaC/terraform/oss/base/core/security_groups.tf` はこのファイルへのシンボリックリンクなので、次の `ops/up.sh` と `ops/oss/up.sh` の base/core の apply で、計 4 本のルールの description が変わる。
- 確かめたこと: provider（hashicorp/aws v6.64.0）のソース `internal/service/ec2/vpc_security_group_ingress_rule.go` を読んだだけ。description の変更は Update の `ModifySecurityGroupRules` で in-place になり、`RequiresReplace` は `security_group_id` と送り元の種類の変更だけに付く。`terraform plan` は AWS が要るので打っていない。作り直しにならないことは**未確認（読んだだけ）**なので Should fix のまま置く。
- 選べる形: (a) このまま（次の apply で description だけ in-place で変わる）、(b) `why` を旧パスのままにし、検証 4 の grep に 1 行足す。

**S3 Should fix ［docs と成果物の食い違い・検証 4 の穴］**

- 場所: `docs/architecture/README.md:7` と `docs/architecture-oss.pptx`。設計に pptx の扱いが無い。
- 破綻シナリオ: README は OSS 版の pptx の中身を「`ops/oss/up.sh`」のスライドと説明するようになったが、pptx のスライドは `oss/ops/up.sh`（slide 8）と `oss/terraform/`（slide 1）のまま。pptx は zip なので検証 4 の grep では拾えない。
- 確かめたこと: Python の zipfile で `ppt/slides/*.xml` を読んだ結果、architecture-oss.pptx の slide 8 に `oss/ops/up.sh`、slide 1 に `oss/terraform/`。architecture-managed.pptx には旧パスが無い。
- 選べる形: (a) README:7 だけ `oss/ops/up.sh` に戻す、(b) 後のサイクルで pptx を作り直す。

**S4 Should fix ［保守性・docs の読み違い］**

- 場所: 既存の「OSS 版の ops/up.sh」「OSS ops/up.sh」という言い回し。`git grep -n -o -E "OSS[^|]{0,12}ops/up\.sh" -- ":!docs/cycles"` で 15 ファイル 34 か所（`IaC/terraform/oss/pipeline/**` の 9 本、`app/{agentcore,dashboard,temporal}/requirements-oss.txt`、`docker/images/{neo4j,spark}/Dockerfile`、`tests/test_oss.py:1899`）。
- 破綻シナリオ: 017 の前は「`oss/` の中の `ops/up.sh`」と読めたが、017 の後はマネージド版の `ops/up.sh` と読める。
- 設計の置換の組に入っていない（`oss/ops` の字面を含まない）ので直していない。反対弁護人も `git grep` で 15 ファイル 34 か所を確かめた。

**D1 設計どうしの食い違い** は上の「設計からの逸脱」1（方針 3 と検証 4）。

#### 最終報告に回したもの（Nit。格下げの根拠は実行して取った）

**N1 Nit ［missing tests］ `ops/oss/up.sh` の SSM の description `(created by ops/oss/up.sh)` を縛る test が無い（R7）**

- 最初は Should fix と置いた。反対弁護人の指摘で Nit に下げた。
- 下げた根拠:
  - 検証 4 の grep と検証 8 が拾う（上の inject017b の出力。grep 2 行、検証 8 が 7）。
  - description は消す対象の選び方に使われていない。`grep -rn 'created by\|Description\|description' ops/down-common.sh ops/oss/down.sh` の当たりは ENI の 2 行（`down-common.sh:20,69`）だけで、SSM はタグで絞る（`down-common.sh:182`）。
  - `ensure_secret` はパラメータがあれば作り直さずに抜ける（`up-common.sh:117` `SecureString) echo "$name はある（作り直さない）"; return 0 ;;`。`:155` の `ensure_fixed_secret` も同じ）。description は作るときだけ付く。
  - 方針 6 が新しい test を足さないと決めている。

**N2 Nit ［missing tests・既存］ `ops/check.sh` の `.py` の find に `app` があることを見る test が無い**

- `app/gateway/handler.py` の構文検査は `app` が拾う。`find` の行を見る test は `tests/test_oss.py:1938-1941` だけで、見ているのは `ops` だけ（`grep -rn "find app\|_chk_find\|'find '" tests/` の当たりはこの 2 行）。
- 03840c8 の同じ箇所も `"oss" in _chk_find.group(1).split()` だけで、`tools` を見ていなかった（`git show 03840c8:tests/test_oss.py` の 1940-1941 行）。017 で増えた穴ではない。

**N3 Nit ［報告の事実の訂正］ verification に残る `oss/terraform`**

- 当初「3 行」と数えたが、`docs/verification/20261008-oss-aws.md` の :55、63、64、303、343、383、430、432 の 8 行。どれも当時の Terraform のパスで、:7 の注のとおり。S1 の判断しだいで扱いが変わる。

#### 反対弁護人の指摘で取り消した自分の結論

- 「tests は `../../ops/X` → `../X` の退行を拾えない」は誤り。`tests/test_oss_ops.py:1010-1013` が up.sh の `'/../lab-common.sh"'`・`'/../deploy-env.sh"'` と down.sh の `'/../deploy-env.sh"'` を見ている。R9〜R11 で 3 件とも落ちることを確かめた。
- 「verification の :311 は Nit（cluster-id は PM が消した）」は甘かった。パスそのものが当時の worktree に無い。S1 に上げた。
- 「SG は ingress の 1 本」は誤り。egress と ingress の 2 本 × マネージドと OSS の 2 つ（S2）。

#### 問題なしとした観点と根拠

- Gateway の Lambda は入れ替わらない: 動かした 2 本の blob が同じ（`git rev-parse 03840c8:tools/handler.py HEAD:app/gateway/handler.py` → どちらも `727a8b7…`、`tools.json` はどちらも `442ea77…`）。`gateway.tf:16` の zip の中の名前は `index.py` のまま。反対弁護人は、dynamic の source が map のキーの辞書順（`app/agentcore/…` < `app/gateway/handler.py`）で回るので並びも前と同じと確かめた。zip を実際に作って hash を比べてはいない。
- `OPS_DIR` を使う箇所（`common.sh:7`、`up-common.sh` の ensure 系のタグ、`down-common.sh:182` のタグと案内、`roll-nodes.sh:72,109` の `"$OPS_DIR/roll_health.py"`）: 読んだだけ（自分と反対弁護人）。R4・R5 で test_oss_ops が値を縛っていることは実行して確かめた。
- R8（`compose.yaml:112`）: コメント。実行される参照は `tests/test_collectors.py` の 1 か所で、検証 1 で通っている。
- 機械置換のやりすぎ: verification（S1）のほかは見つからなかった。MCP の `"tools/list"`・`"tools/call"`（`app/agentcore/mcp_client.py`）は置換されていない（反対弁護人が確認）。
- tests の弱化: `test_nautobot.py:257` の pathspec から `oss` を外したのは同値（`git ls-files oss` が 0 行。検証 2）。`test_oss.py:1941` の「find に `ops`」は `roll_health.py` が `ops/oss/` に移ったので意味が保たれる。R1 で落ちることを確かめた。
- CI（`.github`）、`.dockerignore`、Makefile は追跡ファイルに無い。`pyproject.toml` にパスの参照は無い（反対弁護人が確認）。

## Round 2

実装モデル: opus-5.5 / effort: xhigh（high へ下げる手段が無い）

cold review 1 回目の Should fix 5 件への PM の判断（design.md df42f51）を入れたラウンド。`docs/cycle-006-design` の df42f51 をマージしてから作業した（cd72989）。

### commit

| commit | 内容 |
| :--- | :--- |
| cd72989 | `docs/cycle-006-design`（df42f51）のマージ |
| 0ae2439 | S1・S3: `docs/verification/20261008-oss-aws.md` を 03840c8 の内容に戻す（`git checkout 03840c8 --`）。`docs/development.md:7` の 017 の 1 文を変更対象表の文にする。`docs/architecture/README.md:7` に pptx の注を足す |
| 2dc6237 | S4: 方針 7。「OSS 版の ops/up.sh」「(the) OSS ops/up.sh」（down.sh も）を `ops/oss/up.sh` / `ops/oss/down.sh` に置き換える（15 ファイル 34 か所） |
| （この commit） | build.md Round 2 と review.md の PM の判断 |

### 変更ファイル（`git diff --name-status -M cd72989 2dc6237`）

```
M	IaC/terraform/oss/pipeline/analytics/grafana.tf
M	IaC/terraform/oss/pipeline/analytics/network.tf
M	IaC/terraform/oss/pipeline/analytics/opensearch.tf
M	IaC/terraform/oss/pipeline/analytics/spark.tf
M	IaC/terraform/oss/pipeline/analytics/victoriametrics.tf
M	IaC/terraform/oss/pipeline/graph/neo4j.tf
M	IaC/terraform/oss/pipeline/graph/outputs.tf
M	IaC/terraform/oss/pipeline/graph/sync.tf
M	IaC/terraform/oss/pipeline/stream/kafka.tf
M	app/agentcore/requirements-oss.txt
M	app/dashboard/requirements-oss.txt
M	app/temporal/requirements-oss.txt
M	docker/images/neo4j/Dockerfile
M	docker/images/spark/Dockerfile
M	docs/architecture/README.md
M	docs/development.md
M	docs/verification/20261008-oss-aws.md
M	tests/test_oss.py
```

方針 7 の置換の内訳（`policy7.py` の出力。正規表現は `OSS 版の ops/(up|down)\.sh` → `ops/oss/\1.sh` と `(?:[Tt]he )?OSS ops/(up|down)\.sh` → `ops/oss/\1.sh`）:

```
analytics/grafana.tf 2 / analytics/network.tf 2 / analytics/opensearch.tf 6 / analytics/spark.tf 4 / analytics/victoriametrics.tf 2
graph/neo4j.tf 3 / graph/outputs.tf 1 / graph/sync.tf 2 / stream/kafka.tf 6
app/agentcore/requirements-oss.txt 1 / app/dashboard/requirements-oss.txt 1 / app/temporal/requirements-oss.txt 1
docker/images/neo4j/Dockerfile 1 / docker/images/spark/Dockerfile 1 / tests/test_oss.py 1
files 15 / places 34
```

置き換えたのはコメント、Terraform の variable / output の `description`、precondition の `error_message`（grafana.tf と sync.tf の 1 か所ずつ）、`tests/test_oss.py:1899` の check の見出しだけ。

### 検証（`verify017r2.sh`。HEAD 2dc6237 で取った）

```
### HEAD
2dc6237 根に残ったものを app/ ops/ docs/ に片付ける（017）: OSS 版の ops/up.sh の言い回しを ops/oss/up.sh にする
### 1 bash ops/check.sh
rc=0
通過 169 / 失敗 0
通過 504 / 失敗 0
通過 161 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0
69 項目すべて通過
通過 173 / 失敗 0
通過 194 / 失敗 0
通過 66 / 失敗 0
通過 96 / 失敗 0
通過 103 / 失敗 0
通過 327 / 失敗 0
すべて通過
### 2 git ls-files oss tools GLOSSARY.md（行数）
       0
### 3 ls
CLAUDE.md
IaC
README.md
app
deploy.env.example
docker
docs
ops
pyproject.toml
tests
uv.lock
### 4 実装ステップ 2 の grep
./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータ
./docs/architecture/README.md:7:スライドの構成図は 2 本。マネージド版が [architecture-managed.pptx](../architecture-managed.pptx)（10 枚。データの流れ、9 つの Terraform �
### 4b git grep 'OSS (版の )?ops/(up|down).sh'（行数）
       0
### 4c git diff --stat 03840c8 -- docs/verification docs/*.pptx（行数）
       0
### 5 git log --follow
f587cb0 OSS 版（005）の oss/ops/up.sh・down.sh を作り、ops/ の共通の関数を ops/common.sh・up-common.sh・down-common.sh に切り出した
f55feb4 フェーズ 3 を実装する: Temporal on ECS Fargate のワーカー（terraform/workflow）と AgentCore Gateway（MCP）
### 6 git diff --stat -M docs/cycle-006-design
 {tools => app/gateway}/handler.py                  |   0
 {tools => app/gateway}/tools.json                  |   0
 GLOSSARY.md => docs/GLOSSARY.md                    |   0
 {tools => ops}/netflow_send.py                     |   8 +-
 {oss/ops => ops/oss}/down.sh                       |  30 +-
 {oss/ops => ops/oss}/oss-images.sh                 |   4 +-
 {oss/ops => ops/oss}/roll-nodes.sh                 |  10 +-
 {oss/ops => ops/oss}/roll_health.py                |   2 +-
 {oss/ops => ops/oss}/up.sh                         |  50 +--
 76 files changed, 850 insertions(+), 292 deletions(-)
### 7 OPS_DIR
ops/oss/up.sh:41:OPS_DIR=ops/oss        # SSM のパラメータのタグ ManagedBy=ops/oss/up.sh（ops/oss/down.sh はこのタグのものだけ消す）
ops/oss/down.sh:27:OPS_DIR=ops/oss       # 消す SSM のパラメータはタグ ManagedBy=ops/oss/up.sh のものだけ（マネージド版の ManagedBy=ops/up.sh は残る）
### 8 created by
8
### 9 terraform validate
Success! The configuration is valid.

### 10 test_workflow
通過 327 / 失敗 0
```

- 1: 16 本の件数は Round 1 と同じ（173 / 194 / 327 / 79 を含む）。
- 4: 2 行。`oss-variant.md:66` は期待どおりの除外。`architecture/README.md:7` は期待の除外に無い（下の「設計からの逸脱」2）。当たっているのは変更対象表のとおりに足した注の `oss/ops/` で、`grep -n -o -E '.{0,40}oss/ops.{0,40}'` の出力は「SG、`ops/oss/up.sh`（スライドの中のパスは 017 より前の `oss/ops/`・`oss/terraform/` のまま。作り直しは BACKLOG）、A」。
- 6: 基準の `docs/cycle-006-design` は e70f59e（013 の design.md の commit。まだマージしていない）。rename は同じ 9 本。
- 9: check.sh も `terraform fmt -check` と `validate` を aws-managed と oss の両方に打つ（check.sh:3-6、25-31）。方針 7 で `description` と `error_message` を書き換えた Terraform も通っている。

#### 中身のハッシュで決まるもの（`tags017.py`。03840c8 と HEAD の比較）

```
== dir_tag のイメージ（版の部分は X）
  telegraf   03840c8 X-5e4849f9cbd4  HEAD X-5e4849f9cbd4  同じ
  syslog-ng  03840c8 X-90f5978c4447  HEAD X-90f5978c4447  同じ
  grafana    03840c8 X-b8d6d51ee31d  HEAD X-b8d6d51ee31d  同じ
  splunk     03840c8 X-071b361034b8  HEAD X-071b361034b8  同じ
  spark      03840c8 X-384311cb6a49  HEAD X-aba177ebac64  違う
  neo4j      03840c8 X-e8013fd894bf  HEAD X-6964f3a5a2a6  違う
  nautobot   03840c8 X-83eb721caa36  HEAD X-0cad931cdd12  違う
  nautobot の context の diff -r:
    diff -r <tmp>/ctx_old/requirements-oss.txt <tmp>/ctx_new/requirements-oss.txt
    2c2
    < # docker buildx build --platform linux/arm64 --build-arg REQUIREMENTS=requirements-oss.txt -f docker/images/nautobot/Dockerfile <context>（OSS 版の oss/ops/up.sh の build_nautobot）
    ---
    > # docker buildx build --platform linux/arm64 --build-arg REQUIREMENTS=requirements-oss.txt -f docker/images/nautobot/Dockerfile <context>（OSS 版の ops/oss/up.sh の build_nautobot）
== Lambda の zip の材料・wheels の requirements（ファイルの sha256 の頭 12 桁）
  graph status（index.py）: 213544924d3b / 213544924d3b  同じ
  graph status（graph.py）: 128950c1c02e / 128950c1c02e  同じ
  kb_index（index.py）: 8983e6f2a422 / 8983e6f2a422  同じ
  gateway tools（handler）: 7609a12ecad1 / 7609a12ecad1  同じ
  gateway tools（tools.json）: 81199b62871f / 81199b62871f  同じ
  wheels app/dashboard/requirements.txt: c3d165a5952f / c3d165a5952f  同じ
  wheels app/dashboard/requirements-oss.txt: 4a08a1a3a581 / b921876e2ce0  違う
  neo4j layer app/graph/requirements-oss.txt: 58a699ad13bd / 58a699ad13bd  同じ
== lab の S3（app/containerlab/ の木）
  0a0c1db518c5 / ffd2cf07ca7d  違う
```

Round 1 と比べて spark・neo4j・wheels（`app/dashboard/requirements-oss.txt`）の 3 つが新しく「違う」になった（下のセルフレビューの S5）。

### 設計からの逸脱

1. **方針 7 の対象は変更対象表ではなく 4b の grep で決めた。**
   - 表に無い `IaC/terraform/oss/pipeline/graph/outputs.tf:14` は grep が拾うので入れた。
   - 表にある `IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf:77` と `app/nautobot/requirements-oss.txt:2` は「OSS 版の ops/oss/up.sh」で、grep に当たらないので変えていない。`app/nautobot/requirements-oss.txt` を変えると Nautobot のタグがもう 1 回変わる。
2. **D2: 検証 4 の期待に `docs/architecture/README.md:7` の除外が無い。** 変更対象表（README:7 の注）が `oss/ops/` を字面で入れるので、検証 4 は 2 行になる。設計どうしの食い違いで、実装は表のとおり。PM に報告する。

### セルフレビュー

自分: opus-5.5 / effort xhigh。反対弁護人は呼んでいない（このラウンドの変更はコメントと docs だけで、Round 1 で呼んだ。cold review の 2 回目は PM が PR に対して呼ぶ）。

#### PM の判断に回したもの

**S5 Should fix ［設計どうしの食い違い・次の up.sh の動き］ 方針 7 で Spark と Neo4j のイメージのタグと wheels-oss のハッシュが変わる**

- 場所: `docker/images/spark/Dockerfile:7`、`docker/images/neo4j/Dockerfile:6`、`app/dashboard/requirements-oss.txt:2`（2dc6237 で書き換えたコメント）。設計は方針 6 の 1 つめの補足（design.md:52「ほかの 6 つのイメージ・Lambda の zip・wheels のハッシュは変わらない」）とリスク 6（Nautobot だけを織り込む）。
- 原因: Dockerfile は `dir_tag` の材料（`ops/oss/oss-images.sh:23-31` の `dir_tag "$OSS_SPARK_VERSION" app/spark docker/images/spark/Dockerfile` と neo4j の同じ形）。`fetch_wheels`（`ops/up-common.sh:86-97`）のハッシュは requirements のファイルの中身。方針 7 はコメントだけと書くが、コメントもハッシュに入る。
- 破綻シナリオ（読んだだけ。AWS では確かめていない）:
  - 次の `ops/oss/up.sh` は、`KEEP_ECR=1` で ECR を残していても `ecr_has` が外れ（`ops/oss/up.sh:176-183`）、`NEED_OSS_BUILD=1` で Spark と Neo4j のイメージを 1 回ビルドし直す。
  - 立っている環境に打ち直すと、`tf_apply pipeline/graph -var "neo4j_image_tag=$NEO4J_TAG"`（`:415`）で Neo4j のタスクが入れ替わる。Neo4j のデータは残らない（`neo4j.tf:8-10`）。トポロジは 7-3b（`:424-428`）が空を見て入れ直す。変更履歴は Nautobot の Job「Telegraf とグラフ DB に同期」を打ち直すまで戻らない。Spark も `:468` の `spark_image_tag` でタスク定義が変わる。
  - `fetch_wheels wheels-oss`（`:281`）は手元の `wheels-oss/` を消して取り直す（1 回だけ）。Web の EC2 の user_data には requirements のハッシュが無い（`web.tf:95-101`）ので EC2 は入れ替わらない。
  - down.sh のあとに立て直す普段の流れでは、タスクはどのみち新しいので、増えるのはビルドの時間だけ。
- 退行の注入: 方針 7 の言い回しを 1 か所戻しても tests は落ちない（`inject017r2.sh`。下）。言い回しを縛るのは 4b の grep だけ。
- 選べる形: (a) このまま。方針 6 の補足とリスク 6 に Spark・Neo4j・wheels-oss を足す。(b) Dockerfile 2 本と `app/dashboard/requirements-oss.txt` のコメントを戻し、4b の除外に足す。
- 片付け: 直していない（設計の文の変更になる。PM の判断）。

**D2** は上の「設計からの逸脱」2。

#### 退行の注入（`inject017r2.sh`）

`IaC/terraform/oss/pipeline/analytics/spark.tf` の 1 か所を「OSS 版の ops/up.sh」に戻して、4b と tests を見た。最後に戻した。

```
注入:  1 file changed, 1 insertion(+), 1 deletion(-)
4b: 1 行
test_oss: 通過 173 / 失敗 0
test_oss_ops: 通過 194 / 失敗 0
test_analytics: 通過 504 / 失敗 0
戻した: 0 行
```

方針 6 が新しい test を足さないと決めているので、tests が拾わないのは設計どおり（N1 と同じ形）。

#### 問題なしとした観点と根拠

- tests の件数: 検証 1 の 16 本が Round 1 と同じ。`tests/test_oss.py:1899` は check の見出しの文字列だけで、件数は変わらない。
- Terraform: 書き換えたのは variable / output の `description`、precondition の `error_message`、コメント。どれも資源の属性ではなく state に入らないので plan に差分は出ない（Terraform の決まり。AWS が要るので plan は打っていない）。fmt と validate は検証 1 の check.sh が両方の根に打って通っている。
- verification と pptx: 検証 4c が 0 行（03840c8 と同じ）。
- `docs/development.md:7`・`docs/architecture/README.md:7`: 変更対象表の文と同じ（読んで突き合わせた）。
- Round 1 から変わらないもの: Lambda の zip の材料 5 本、`app/dashboard/requirements.txt`、neo4j の layer、telegraf・syslog-ng・grafana・splunk のタグ（上の `tags017.py`）。

## Round 3

実装モデル: opus-5.5 / effort: xhigh

cold review 2 回目（PM が PR #5 の 36c2633 に対して呼んだ）の S1・N1 への PM の判断を入れたラウンド。実装ファイル（スクリプト・Terraform・tests・app）は変えない。

### commit

| commit | 内容 |
| :--- | :--- |
| 36c2633 | `docs/cycle-006-design`（e70f59e）のマージ。衝突なし |
| （この commit） | S1・N1: `docs/deploy.md:183`（007 の移行手順）と `docs/oss-variant.md:102`（2026-10-08 の検証結果の行）を 03840c8 の行に戻す（`oss/ops/down.sh`）。design.md の方針 6 の 1 文と検証 4 の除外、design-log.md Round 3、review.md Round 2、この build.md Round 3 |

戻した 2 行が 03840c8 と同じこと（`repro_r02b.sh`）:

```
== 03840c8 deploy.md:183 ==
OSS 版は `oss/ops/down.sh`
== 36c2633 deploy.md:183 ==
OSS 版は `ops/oss/down.sh`
== 作業ツリー deploy.md:183 ==
OSS 版は `oss/ops/down.sh`
== 作業ツリーと 03840c8 の行の一致 ==
docs/deploy.md:183 一致
docs/oss-variant.md:102 一致
```

### 検証（`verify017r3.sh`。36c2633 にこの commit の変更を載せた作業ツリーで取った。build.md はこのあと書いた）

```
### HEAD と未コミットの変更
36c2633 Merge branch 'docs/cycle-006-design' into chore/tidy-root
 M docs/cycles/017-tidy-root/design-log.md
 M docs/cycles/017-tidy-root/design.md
 M docs/cycles/017-tidy-root/review.md
 M docs/deploy.md
 M docs/oss-variant.md
### 1 bash ops/check.sh
rc=0
通過 169 / 失敗 0
通過 504 / 失敗 0
通過 161 / 失敗 0
通過 79 / 失敗 0
通過 3 / 失敗 0
通過 78 / 失敗 0
通過 7 / 失敗 0
通過 104 / 失敗 0
通過 132 / 失敗 0
69 項目すべて通過
通過 173 / 失敗 0
通過 194 / 失敗 0
通過 66 / 失敗 0
通過 96 / 失敗 0
通過 103 / 失敗 0
通過 327 / 失敗 0
すべて通過
### 2 git ls-files oss tools GLOSSARY.md（行数）
       0
### 3 ls
CLAUDE.md
IaC
README.md
app
deploy.env.example
docker
docs
ops
pyproject.toml
tests
uv.lock
### 4 実装ステップ 2 の grep
./docs/oss-variant.md:66:| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータ
./docs/oss-variant.md:102:| 全体 | lab でリンクを落とすと、Grafana のアラート → SNS → Lambda → Neo4j の status → Web のトポロジまでつながった。エージェント
./docs/deploy.md:183:**前の配置で立てた環境は、007 をマージする前に前の配置の `ops/down.sh`（OSS 版は `oss/ops/down.sh`）で消す。** 007 は SG の description（作り
./docs/architecture/README.md:7:スライドの構成図は 2 本。マネージド版が [architecture-managed.pptx](../architecture-managed.pptx)（10 枚。データの流れ、9 つの Terraform �
### 4b git grep 'OSS (版の )?ops/(up|down).sh'（行数）
       0
### 4c git diff --stat 03840c8 -- docs/verification docs/*.pptx（行数）
       0
### 5 git log --follow
f587cb0 OSS 版（005）の oss/ops/up.sh・down.sh を作り、ops/ の共通の関数を ops/common.sh・up-common.sh・down-common.sh に切り出した
f55feb4 フェーズ 3 を実装する: Temporal on ECS Fargate のワーカー（terraform/workflow）と AgentCore Gateway（MCP）
### 6 git diff --stat -M docs/cycle-006-design
 {tools => app/gateway}/handler.py                  |   0
 {tools => app/gateway}/tools.json                  |   0
 GLOSSARY.md => docs/GLOSSARY.md                    |   0
 {tools => ops}/netflow_send.py                     |   8 +-
 {oss/ops => ops/oss}/down.sh                       |  30 +-
 {oss/ops => ops/oss}/oss-images.sh                 |   4 +-
 {oss/ops => ops/oss}/roll-nodes.sh                 |  10 +-
 {oss/ops => ops/oss}/roll_health.py                |   2 +-
 {oss/ops => ops/oss}/up.sh                         |  50 +-
 76 files changed, 1234 insertions(+), 292 deletions(-)
### 7 OPS_DIR
ops/oss/up.sh:41:OPS_DIR=ops/oss        # SSM のパラメータのタグ ManagedBy=ops/oss/up.sh（ops/oss/down.sh はこのタグのものだけ消す）
ops/oss/down.sh:27:OPS_DIR=ops/oss       # 消す SSM のパラメータはタグ ManagedBy=ops/oss/up.sh のものだけ（マネージド版の ManagedBy=ops/up.sh は残る）
### 8 created by
8
### 9 terraform validate
Success! The configuration is valid.

### 10 test_workflow
通過 327 / 失敗 0
```

検証 4 は 4 行で、どれも design.md の除外に書いたもの（`docs/oss-variant.md:66`・`docs/oss-variant.md:102`・`docs/deploy.md:183`・`docs/architecture/README.md:7`）。

36c2633（マージの直後、Round 3 の変更の前）に同じ検証を取った出力（`verify017_36c2633.out`）との差（`diff 36c2633 の出力 この出力`）:

```
1c1
< ### HEAD
---
> ### HEAD と未コミットの変更
2a3,7
>  M docs/cycles/017-tidy-root/design-log.md
>  M docs/cycles/017-tidy-root/design.md
>  M docs/cycles/017-tidy-root/review.md
>  M docs/deploy.md
>  M docs/oss-variant.md
37a43,44
> ./docs/oss-variant.md:102:| 全体 | lab でリンクを落とすと、Grafana のアラート → SNS → Lambda → Neo4j の status → Web のトポロジまでつながった。エージェント
> ./docs/deploy.md:183:**前の配置で立てた環境は、007 をマージする前に前の配置の `ops/down.sh`（OSS 版は `oss/ops/down.sh`）で消す。** 007 は SG の description（作り
56c63
<  76 files changed, 1079 insertions(+), 294 deletions(-)
---
>  76 files changed, 1234 insertions(+), 292 deletions(-)
```

検証 6 の行数の差は、docs/cycles/017-tidy-root の design.md・design-log.md・review.md に足した行。

### セルフレビュー

- 実装ファイルが変わらないので、反対弁護人と cold review は呼ばない（PM の指示）
- 方針 6 の 1 文に当たる行がほかに残っていないか: 03840c8 との diff の `docs/` の行（verification と cycles を除く）を読んだ。過去の手順と過去の実測の記録は deploy.md:183 と oss-variant.md:102 の 2 行だけ（読んだだけ。review.md Round 2 の確認）
