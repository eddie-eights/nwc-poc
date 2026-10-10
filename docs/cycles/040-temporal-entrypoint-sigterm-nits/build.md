# temporal-server の entrypoint を起動中の SIGTERM で止められるようにし、039 の Nit を消化する（040）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。ベースは 040587a）

### 変えたもの

- `docker/images/temporal-server/entrypoint.sh`
  - `log()` の直後に `trap '…; exit 143' TERM INT` を置いた（設計方針 1）。
  - `step=1` 〜 `step=4` を、手順 1 の `i=0` / `until nc` の前、手順 2 の `log "ロール …` の前、手順 3 の `for pair` の前、手順 4 の `namespace-rds.sh … &` の前に置いた。
  - `export PGHOST=…` の直後で `SQL_TLS_ENABLED` / `SQL_HOST_VERIFICATION` を小文字にした。`true` で `SQL_CA` が空なら exit 1 にした（設計方針 6）。
  - `SQL_TLS_SERVER_NAME` と `SQL_HOST_NAME` の違いを書いたコメントを 1 行足した。
  - `exec /sbin/tini -- /etc/temporal/entrypoint.sh` にした（設計方針 3）。
  - 先頭のコメントに trap の 1 行を足した。
- `docker/images/temporal-server/namespace.sh`
  - 引数ごとに `: "${N:?…の N つ目が無い}"` を置いてから代入するようにした。
  - 5 行目のコメントを design のとおりに書き換えた（設計方針 4）。
- `docker/images/temporal-server/Dockerfile`: 3 行目のコメントを書き換えた（設計方針 5）。
- `IaC/terraform/aws-managed/workflow/ecs.tf`
  - `SQL_HOST_VERIFICATION` の行の上に 2 行のコメントを置いた。
  - healthCheck の後ろに、コメント 3 行と `stopTimeout = 120` を足した（設計方針 2・6）。
- `tests/test_workflow.py`（設計方針 7）
  - initProcessEnabled の check を `not in _ecs_code` だけにした。
  - `stopTimeout` が `["120"]` の check を足した。
  - exec の期待を `/sbin/tini` にした。
  - trap の静的な check を足した。
  - trap の振る舞いの check を足した。
  - `_ts_ep_tls` の切り出しを `.find()` と guard にし、`log()` を先頭に足し、stderr を返すようにした。
  - TLS の check を 2 つ足した（大文字、CA 無し）。
  - `_ts_ns_run` に `nc_ok` / `health_ok` を足し、nc と temporal の呼び出しを記録するようにした。
  - namespace.sh の check を 3 つ足した（nc 30 回、health 30 回、引数 2 つ）。
  - 既存の check 名を「引数不足以外はどこで抜けても exit 0」に変えた。
- `docs/architecture/resources/temporal.md`（設計方針 8）
  - 「初期化は…entrypoint がやる」に 2 項目足した。
  - 制約の表の PostgreSQL 18 の行に 1 文足した。
  - 経緯に 040 を 1 行足した。

### 設計からの逸脱

- trap の文言の `$step` を `${step:-0}` にした。entrypoint は `set -eu` なので、`step=1` より前に SIGTERM が来ると、`$step` の展開で trap 自身が落ちる。
- `_ts_ep_tls` の guard は、design の `(None, {}, {})` ではなく `(None, {}, {}, "")` を返す。stderr を 4 つ目の戻り値にしたため（design の同じ項の指示）。
- ecs.tf の `SQL_HOST_VERIFICATION` のコメントは、行末ではなく上の 2 行に置いた。env の行そのものを変えないためで、検証 4 の差分はコメントと `stopTimeout` だけになる。
- trap の振る舞いの check を足した（design は静的な check だけ）。
  - log と trap の行だけを sh で動かし、`step=2; kill -TERM $$; echo after` を送る。
  - 期待するのは、rc 143、`after` が出ないこと、stderr に「手順 2 のあと」。
- namespace.sh の nc 30 回と health 30 回の check は、直す前から通った。振る舞いは 039 からあり、足したのは記録（スタブ）と check だけ。

### 検証

#### 1. テスト（直す前は新しい check で失敗 7、直したあと失敗 0。総数 370 → 378）

直す前は、check を書いてコードを直す前の状態で走らせた。素の実行は最初の失敗で止まるので、`<scratchpad>/allfail.py` で失敗を全部並べた。このスクリプトは test_workflow.py の `check()` の assert を「FAIL を出して続ける」に置き換えて実行するもので、元のファイルは変えない。

```
FAIL temporal の stopTimeout は 120（Fargate の上限。初回の setup-schema + update-schema の途中で SIGKILL しない。cycle 040。いま: []）
FAIL entrypoint はパスワードをコマンドラインに載せない（psql は \getenv、temporal-sql-tool は SQL_PASSWORD）で、最後に tini の子として公式の entrypoint へ exec …
FAIL entrypoint は log() の後に trap を 1 つ置き TERM / INT で exit 143、step=1〜4 を手順 1（nc）/ 2（master の psql）/ 3（for pair）/ 4（namespace）の直前に順に置く（…
FAIL entrypoint の trap: SIGTERM で次の行へ進まず exit 143、ログに手順の番号が出る（いま: rc=-15 '' ''）
FAIL namespace.sh: 引数が 2 つ（retention が無い）なら何も呼ばずに非 0 で抜け、retention が無いと言う（いま: rc=1 [] '…
FAIL entrypoint の TLS: SQL_TLS_ENABLED / SQL_HOST_VERIFICATION は大文字でも真（True / TRUE → verify-full、ホスト名を検証する。いま: {'SQL_TLS': 'True', 'SQL_TLS_CA_FILE': '/ca…
FAIL entrypoint の TLS: SQL_HOST_VERIFICATION=true で SQL_CA が無ければ temporal-sql-tool を呼ばずに非 0 で止まり、SQL_CA が要ると言う（いま: rc=0 {'_ARGS': '--plugin postgr…
```

直したあと（最後の編集のあと）:

```
$ uv run --group dev --group web python tests/test_workflow.py
…
ok Temporal UI（8233）は土台の通信の表の web → workflow の 1 行で、…（cycle 036）
ok 修復案の status に obsolete がある（tools.json の説明も）
通過 378 / 失敗 0
rc=0（FAIL の行は 0）
```

#### 2. `./ops/check.sh`（最後の編集のあと）

```
$ ./ops/check.sh
rc=0
（test_workflow の段）通過 378 / 失敗 0
すべて通過
```

#### 3. `sh -n`

```
sh -n entrypoint.sh: rc=0
sh -n namespace.sh: rc=0
rc=0
```

イメージの中の ash でも確かめた。

```
$ docker run --entrypoint sh nwc-temporal-server:040-test -c 'ls -l /sbin/tini; command -v tini; sh -n …'
-rwxr-xr-x    1 root     root         68536 Aug  4  2025 /sbin/tini
/sbin/tini
ash_sh_n_ok
```

#### 4. `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf`

env、secrets、healthCheck には差分が無い。

```
@@ -51,6 +51,8 @@ resource "aws_ecs_task_definition" "workflow" {
         { name = "SQL_TLS_ENABLED", value = "true" }, # RDS PostgreSQL 15 以降は rds.force_ssl=1 が既定
+        # true にするには RDS の CA をイメージに入れて SQL_CA を渡す（SQL_HOST_NAME は任意。無ければ接続先のホスト名）。
+        # false のあいだは psql（require）も temporal-server（InsecureSkipVerify）も CA を確かめない
         { name = "SQL_HOST_VERIFICATION", value = "false" },
@@ -74,6 +76,10 @@ resource "aws_ecs_task_definition" "workflow" {
         startPeriod = 180
       }
+      # 起動中（entrypoint の DB を待つ / ロールと DB / スキーマ）に停止が来ると、entrypoint の trap は走っている手順が返るまで待ってから exit 143 で抜ける。
+      # 初回の setup-schema + update-schema の途中で SIGKILL しないよう Fargate の上限の 120 秒にする。通常の停止は tini が SIGTERM を
+      # temporal-server に渡してすぐ抜けるので 120 秒は待たない（cycle 040）
+      stopTimeout = 120
       logConfiguration = {
```

`terraform fmt -check` の差分は無い。

#### 5. 手元の docker で起動中の SIGTERM（arm64、`docker build -t nwc-temporal-server:040-test docker/images/temporal-server/`）

手順は `<scratchpad>/v5.py` で動かした。

- 起動は、届かないアドレス `POSTGRES_SEEDS=10.255.255.1` で行う。
- 3 秒待ってから止める。

```
$ docker run -d --name ts-040 -e POSTGRES_SEEDS=10.255.255.1 ... nwc-temporal-server:040-test  rc=0
$ time docker stop -t 60 ts-040
ts-040
stop にかかった秒: 7.1
$ docker inspect -f '{{.State.ExitCode}}' ts-040
143
$ docker logs ts-040
entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 1 のあと。ここまでの手順はべき等なので次の起動でやり直す）
```

比較として、main のイメージ `nwc-temporal-server:040-main` も同じ手順で止めた。

- イメージの作り方: `git archive main docker/images/temporal-server` を scratchpad に出して build した。`git stash` は使っていない。

```
$ docker run -d --name ts-040-main -e POSTGRES_SEEDS=10.255.255.1 ... nwc-temporal-server:040-main  rc=0
$ time docker stop -t 60 ts-040-main
ts-040-main
stop にかかった秒: 60.1
$ docker inspect -f '{{.State.ExitCode}}' ts-040-main
137
$ docker logs ts-040-main
entrypoint-rds: 10.255.255.1:5432 を待つ（1/30）
entrypoint-rds: 10.255.255.1:5432 を待つ（2/30）
entrypoint-rds: 10.255.255.1:5432 を待つ（3/30）
entrypoint-rds: 10.255.255.1:5432 を待つ（4/30）
```

#### 6. 手元の docker で通常の道

- 動かし方:
  - `postgres:18` + `nwc-temporal-server:040-test` を、ネットワーク `t040` で動かした。
  - `--init` は付けず、`SQL_TLS_ENABLED=false` にした。
  - パスワードは使い捨ての値。
  - 手順は `<scratchpad>/v6.py`。

```
$ docker logs ts040 2>&1 | grep entrypoint-rds
entrypoint-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
entrypoint-rds: temporal: 初回なので setup-schema -v 0.0
entrypoint-rds: temporal: update-schema
entrypoint-rds: temporal_visibility: 初回なので setup-schema -v 0.0
entrypoint-rds: temporal_visibility: update-schema
entrypoint-rds: namespace: default を作った（retention 72h）
$ docker exec ts040 ps -o pid,ppid,stat,comm
PID   PPID  STAT COMMAND
    1     0 S    tini
   45     1 S    temporal-server
   88     0 R    ps
$ docker exec ts040 sh -c '/etc/temporal/namespace-rds.sh 127.0.0.1:7233 default'
/etc/temporal/namespace-rds.sh: line 13: 3: namespace-rds.sh <frontend のアドレス> <namespace> <retention> の 3 つ目が無い
rc=2
$ time docker stop -t 30 ts040
ts040
stop にかかった秒: 1.2
ExitCode=0
```

ExitCode 0 は 039 の Round 2 と同じ値（039 の build.md の検証 5 は 1 秒 / 0）。

#### 7. `_ts_ep_tls` の目印の guard

`<scratchpad>/v7.py` で確かめた。

- やったこと:
  - `entrypoint.sh` の `sql_tool() {` を一時的に `sql_tool_x() {` に変えた。
  - その状態で `allfail.py` と素の実行を走らせた。
  - 終わったら元に戻した。

```
$ （sql_tool() { を sql_tool_x() { に変えて）python allfail.py tests/test_workflow.py | grep -E 'FAIL|Error|通過'
FAIL entrypoint の temporal-sql-tool と psql の TLS はサーバー本体と同じ SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME から導く（ホスト名検証を決め打ちしない）
FAIL entrypoint の TLS の切り出しの目印（export PGHOST= / sql_tool_skip_host_verify=true; fi / sql_tool() { / \n}\n）がある
FAIL entrypoint の sql_tool: 引数をそのまま temporal-sql-tool に渡し、SQL_PASSWORD / SQL_TLS は呼んだ側のシェルに残さない（いま: {}）
FAIL entrypoint の TLS（ecs.tf と同じ SQL_HOST_VERIFICATION=false）: psql は require、temporal-sql-tool はホスト名を検証しない。CA とサーバー名は渡さない（いま: {}）
FAIL entrypoint の TLS の切り出しの目印（export PGHOST= / sql_tool_skip_host_verify=true; fi / sql_tool() { / \n}\n）がある
FAIL entrypoint の TLS: SQL_HOST_VERIFICATION が無ければ false と同じ（いま: {}）
…（目印の FAIL と各 TLS の check の FAIL が交互に続く）
FAIL entrypoint の TLS: SQL_HOST_VERIFICATION=true で SQL_CA が無ければ temporal-sql-tool を呼ばずに非 0 で止まり、SQL_CA が要ると言う（いま: rc=None {} ''）
通過 370 / 失敗 0
rc=0
```

`ValueError` は出ていない。

- `通過 370 / 失敗 0` は `allfail.py` が FAIL を数えないため（通った check だけの数）。
- 素の実行は、目印より前にある静的な check（文字列 `sql_tool() {` を探す既存の check）で止まる。

```
$ （同じ状態で）uv run --group dev --group web python tests/test_workflow.py 2>&1 | tail -4
  File ".../tests/test_workflow.py", line 14, in check
    assert cond, name
           ^^^^
AssertionError: entrypoint の temporal-sql-tool と psql の TLS はサーバー本体と同じ SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME から導く（ホスト名検証を決め打ちしない）
rc=1
$ （戻したあと）git diff --stat
 IaC/terraform/aws-managed/workflow/ecs.tf   |  6 +++
 docker/images/temporal-server/Dockerfile    |  2 +-
 docker/images/temporal-server/entrypoint.sh | 17 +++++-
 docker/images/temporal-server/namespace.sh  |  5 +-
 docs/architecture/resources/temporal.md     |  5 +-
 tests/test_workflow.py                      | 82 +++++++++++++++++++++++------
 6 files changed, 96 insertions(+), 21 deletions(-)
```

素の実行が guard の check で止まる経路も確かめた（Round 1 の作業中）。

- 終わりの目印 `sql_tool_skip_host_verify=true; fi` を `true;  fi` に変えた。
- 静的な check はこの文字列を見ないので、`_ts_ep_tls` の中の guard の `check(…, False)` が AssertionError で止めた。
- ValueError は出ていない。

#### 片付け

- 消したもの:
  - コンテナ: `docker rm -f -v ts040 pg040`（ts-040、ts-040-main、ts040b はスクリプトの中で消した）
  - ネットワーク: `docker network rm t040`
  - イメージ: `docker rmi nwc-temporal-server:040-test nwc-temporal-server:040-main postgres:18 temporalio/server:1.32.1`（4 件 Untagged）
- 確かめた結果:

```
$ docker ps -a
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
$ docker images | grep -E 'nwc-temporal-server|postgres +18|temporalio/server'; echo rc=$?
rc=1
$ docker network ls | grep t040; echo rc=$?
rc=1
```

### セルフレビュー

- 実行したモデル:
  - 自分: opus-5.5 / effort high（サブエージェントの中では effort を切り替えられない）
  - 反対弁護人: opus / effort xhigh。文脈を渡し、読み取り専用で頼んだ。
- 反対弁護人の返答のあと、`git status --porcelain -uall` を確かめた。増えたのは自分の build.md だけだった。
- 結果: Must fix 0 / Should fix 1 / Nit 8。Should fix は直していない（PM の指示）。

#### Should fix

1. [missing tests] 「前景の子の途中で切らない」をテストが縛っていない
   - 場所: `tests/test_workflow.py:665-682`、`docker/images/temporal-server/entrypoint.sh:113`
   - 破綻シナリオ:
     - update-schema（または for pair の中の psql）を `cmd & wait $!` に書き換えると、trap が `wait` を割り込む。
     - その結果、update-schema が途中で切られて exit 143 になる。
     - それでも静的な check も振る舞いの check も通る。
   - 再現: コピーした木（`<scratchpad>/inject040b.py`。worktree は変えない）で 113 行目の末尾に `& wait $!` を足して test_workflow.py を走らせた。

     ```
     [update-schema を & wait $! にする] rc=0
     ```

   - 反対弁護人は docker（alpine の ash、PID 1）でも確かめている（`sh -c 'sleep 6; echo child-done' & wait $!`、stop 0.1 秒 / 143、`child-done` が出ない）。
   - 片付け: 最終報告に回した。案は静的な check で、非コメント行で末尾が `&` なのは namespace-rds.sh の 1 行だけ、`wait` の行は無い、を確かめる。

#### Nit

1. [missing tests] trap の `${step:-0}` をテストが縛っていない
   - 場所: `entrypoint.sh:36`、`tests/test_workflow.py:675-682`
   - 破綻シナリオ: `$step` に戻すと、`step=1` より前の SIGTERM で trap 自身が `set -u` で落ちる。exit 143 にならず、停止のログも出ない。
   - 再現（`<scratchpad>/inject040.py` と `stepunset.py`）:

     ```
     [${step:-0} を $step に戻す（set -u で step=1 の前の SIGTERM）] rc=0 通過 378 / 失敗 0
     busybox ash $step: rc=2 out='' err="…sh: step: parameter not set"
     busybox ash ${step:-0}: rc=143 out='' err='entrypoint-rds: SIGTERM（手順 0 のあと）'
     ```

   - Nit にした根拠（実測）: 退行しても止まらなくなるわけではなく、rc 2 ですぐ抜ける（反対弁護人の docker: stop 3.1 秒 / ExitCode 2）。窓は trap の行から `step=1` までの組み込みと `tr` だけ。
   - 片付け: 最終報告に回した。
2. [missing tests] テストの guard が抜けている（`tests/test_workflow.py:638`、`:672`）
   - 破綻シナリオ:
     - 638 の `next()` には既定値が無い。`log() {` が改名されると StopIteration で止まり、check の名前が出ない。
     - 672 の `_ts_first(...) < _ts_trap[0]` は、log() が無いと -1 になり常に真。
   - 再現:

     ```
     [log() { を logx() { にする] rc=1
       File ".../inj040/tests/test_workflow.py", line 638, in _ts_ep_tls
         log_line = next(ln for ln in _ts_ep.splitlines() if ln.startswith("log() {"))
     StopIteration
     ```

   - Nit にした根拠: 退行は通らずに rc 1 で落ちる。読みにくいのは失敗の表示だけ。
   - 片付け: 最終報告に回した。
3. [docs] trap の文言「手順 N のあと」が、手順 N の途中で止めたときにも出る
   - 場所: `entrypoint.sh:36`
   - 破綻シナリオ: DB を待っている途中（nc の 1 回が返った区切り）で止めても「手順 1 のあと」と出る。
   - 確かめたもの: 検証 5 のログ。文言は design の設計方針 1 のとおり。
   - 片付け: 最終報告に回した。
4. [docs] 手順の番号の振り方が entrypoint の中で 2 通りある
   - 場所:
     - `entrypoint.sh:5-9` の先頭のコメント（1 = ロールと DB 〜 4 = exec）
     - 本文の `# 1.` 〜 `# 5.` と step の値（1 = DB を待つ 〜 4 = namespace）
   - 040 からログに step の番号が出るので、ずれが目に付く。temporal.md の「手順 1〜3」は本文の番号の側。
   - 確かめたもの: `grep -n '^#   [0-9]\.\|^# [0-9]\. '` で 5-9 行目と 61 / 71 / 94 / 116 / 121 行目。
   - 片付け: 最終報告に回した（先頭のコメントは 039 以前からこの形）。
5. [design] `SQL_TLS_ENABLED=false` でも、`SQL_HOST_VERIFICATION=true` で `SQL_CA` が無ければ exit 1 になる
   - 場所: `entrypoint.sh:44-46`
   - design の設計方針 6 の条件どおり。ecs.tf は TLS true / 検証 false なので、いまの設定では発火しない（`tests/test_workflow.py` が ecs.tf の値を縛っている）。
   - 確かめたもの: 読んだだけ（反対弁護人の変異では、この条件に TLS を足しても 378 通過）。
   - 片付け: 最終報告に回した。
6. [correctness] 小文字化しても `yes` / `on` / `1` は真にならない
   - 場所: `entrypoint.sh:42-43`
   - サーバー本体の YAML がこれらを真に読むかは未確認（反対弁護人の記憶。確かめていない）。ecs.tf は `"false"` 固定なので、いまは発火しない。
   - 片付け: 最終報告に回した。
7. [検証の抜け] `temporalio/server:1.32.1` の中の `tr '[:upper:]' '[:lower:]'` は動かしていない
   - TLS の振る舞いの check は手元の macOS の sh と tr で動く。反対弁護人は alpine 3.24 の busybox で `True` / `TRUE` / `tRuE` → `true` を確かめた。
   - 片付け: AWS で確かめるときに 1 行見る（最終報告に回した）。
8. [missing tests] namespace.sh の `${1:?}` / `${2:?}` を直接縛る check が無い（引数 2 つの check は `${3:?}` だけを縛る）
   - 確かめたもの: 読んだだけ。
   - 片付け: 最終報告に回した。

#### 問題なしとした観点と根拠

- **trap の退行はテストが捕まえる。**
  - コピーした木に注入して確かめた（`<scratchpad>/inject040.py`）。
  - 次の 5 つは、どれも rc=1 で落ちた:
    - trap の行を消す
    - `exit 143` を `exit 1` にする
    - INT を外す
    - `step=3` をループの中へ動かす
    - `exec /sbin/tini` を `exec tini` に戻す
  - 反対弁護人の変異でも捕まった（trap を step=1 の後へ、`trap ''`、`trap - TERM INT` を別の行に、など）。
- **前景の子を切らない。**
  - 手元の docker で確かめた。手順 3 の setup-schema の直後に止めると、0.1 秒 / 143 で「手順 3 のあと」と出る。
  - 2 回目の起動では、temporal2_vis の setup-schema を飛ばして update-schema から続け、namespace を作った。最終の版は temporal2 が 1.19、temporal2_vis が 1.14（`<scratchpad>/v6b.py`）。

    ```
    $ docker stop -t 120 ts040b（temporal2: 初回なので setup-schema のログを見た直後）
    stop にかかった秒: 0.1
    ExitCode=143
    entrypoint-rds: ロール temporal2 と DB temporal2 / temporal2_vis を確かめる
    entrypoint-rds: temporal2: 初回なので setup-schema -v 0.0
    entrypoint-rds: temporal2: update-schema
    entrypoint-rds: temporal2_vis: 初回なので setup-schema -v 0.0
    entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 3 のあと。ここまでの手順はべき等なので次の起動でやり直す）
    --- 2 回目の起動（docker start）
    entrypoint-rds: ロール temporal2 と DB temporal2 / temporal2_vis を確かめる
    entrypoint-rds: temporal2: update-schema
    entrypoint-rds: temporal2_vis: update-schema
    entrypoint-rds: namespace: default を作った（retention 72h）
    temporal2 curr_version: 1.19
    temporal2_vis curr_version: 1.14
    ```

  - コマンド置換 `has=$(...)` の最中の SIGTERM は、置換が返ってから trap が走る。反対弁護人の docker: 3.1 秒 / 143。
- **trap の行より前の SIGTERM。**
  - PID 1 は無視するので、stopTimeout まで待つだけ（反対弁護人の docker: 10.1 秒 / 137）。
  - 窓は 13〜35 行の組み込みだけ。
- **`namespace-rds.sh &` と `exec` の間の SIGTERM。**
  - PID 1 が exit 143 で抜けると、pid namespace ごと孤児も消える（読んだだけ。反対弁護人も同じ結論）。
- **set -u と tr。**
  - `SQL_TLS_ENABLED` / `SQL_HOST_VERIFICATION` の `:=` の既定値が、小文字化より前の行にある（読んだ）。
  - 空の `DEFAULT_NAMESPACE_RETENTION` は `:=72h` で埋まる（読んだ。反対弁護人も同じ結論）。
- **stopTimeout の位置とテストの範囲。**
  - temporal のコンテナの map の中にある（検証 4 の diff）。
  - 反対弁護人の変異で、30 にする、消す、worker へ動かす、がどれも捕まった。
- **security。**
  - trap が出すのは手順の番号だけ。
  - `/proc/1/environ` にパスワードが見える時間は 039 と同じか短い（止めたときは exec を待たずに抜ける）。
  - 子プロセス（nc / psql / tr）が env を受け継ぐのは 039 から同じ（読んだだけ）。
- **後片付け。**
  - セルフレビューで pull した `alpine:3.22` は `docker rmi` で消した（`Untagged: alpine:3.22`）。
  - 反対弁護人のコンテナ `adv040-*` は反対弁護人が消した。
  - コピーした木 `<scratchpad>/inj040` はスクリプトの最後で消した。

### 残っているもの

- Should fix 1 と Nit 1〜8（上）。直していない。
- AWS では確かめていない（design の設計方針 9）。
