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
