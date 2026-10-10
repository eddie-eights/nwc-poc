# temporal-server の entrypoint の守りを締める（039）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。Round 2（差し戻しからの再設計。経緯は `design-log.md`）。

## 背景

「Temporal の履歴を RDS に残す（036）」のコールドレビュー Round 1（`docs/cycles/036-temporal-rds/review.md` の「Nit」）で、`docker/images/temporal-server/entrypoint.sh` と `IaC/terraform/aws-managed/workflow/ecs.tf` に 3 つの Nit が出た。どれも動作には影響しないが、数行で閉じられるので 1 サイクルで消化する。

1. **[security] master のパスワードが temporal-server の env に残る。** `NAUTOBOT_DB_PASSWORD`（Nautobot の RDS の master のパスワード）は、ロールと DB を作ったあと使わないのに、`exec` のあとも PID 1 の env に残り、タスクの中のプロセス（uid temporal）が `/proc/1/environ` から読める。
   **守れる範囲**: entrypoint のあとに動くプロセス（tini、temporal-server、namespace を作る背景のプロセス）の `/proc/<pid>/environ` から消すこと。**守れない範囲**: ECS Exec（`ecs:ExecuteCommand`）のシェルはタスク定義の env を SSM agent から引き継ぐので、`env` でそのまま見える。これは 036 の design.md のリスク 7 が受け入れた代償（読める範囲は Nautobot のコンテナと同じ）で、根本対策（初期化を一回きりのタスクに分ける）は別サイクル。
2. **[整合性] `sql_tool` のホスト名検証が決め打ち。** Round 1 で直した（`SQL_HOST_VERIFICATION` / `SQL_CA` / `SQL_HOST_NAME` から導く）。Round 2 では変えない。
3. **[runtime] namespace を作る背景のプロセスがゾンビで残る。** `( … ) &` は `exec` のあと temporal-server（同じ PID）の子になり、抜けたあと誰も `wait` しない。Round 1 の `initProcessEnabled` では解けない（init は孤児しか回収せず、親の temporal-server は生きている。さらに PID 1 が init になると `/proc/1/environ` にパスワードが戻る。`design-log.md`）。**PID 1 を tini にし、temporal-server を tini の子にする。** 背景のプロセスは `exec tini` で tini の子になり、抜けたら tini が回収する。

現物（2026-10-10 に PM が読んだ。ブランチ `feat/039-temporal-entrypoint-hardening` の 7d4b180）:

- `entrypoint.sh`: 手順 2 の最後の `NAUTOBOT_DB_PASSWORD` の使用は 75-76 行目（`btree_gin` の psql）。手順 3（`sql_tool`、78-99 行目）はロールのパスワード（`POSTGRES_PWD`）だけを使う。手順 4 の背景のサブシェル（101-123 行目）は先頭で `unset NAUTOBOT_DB_PASSWORD` しているが、fork だけのサブシェルは `/proc/<pid>/environ` に親の最初の env を持ち続けるので、namespace ができるまで（最長で 30 回 × 5 秒 × 3 段）は uid temporal から読める。最後の行は `unset NAUTOBOT_DB_PASSWORD` → `exec /etc/temporal/entrypoint.sh`（127-128 行目）。
- `Dockerfile`: `temporalio/server:1.32.1`（Alpine 3.24）に `apk add --no-cache postgresql18-client`。`apk add --no-cache tini` で `tini-0.19.0-r3`（`/sbin/tini`。`tini --version` → `tini version 0.19.0`）が入ることを PM が確かめた。tini は無い。
- `ecs.tf:77-78`: Round 1 が足した `linuxParameters = { initProcessEnabled = true }` とコメント。**外す。**
- `tests/test_workflow.py`: 557-559 行目（initProcessEnabled）、589-590 行目（`docker/images/temporal-server/` は 3 ファイル）、592-594 行目と 607-609 行目（最後の行が `exec /etc/temporal/entrypoint.sh`、その直前が `unset`）、638-639 行目（背景のサブシェルの先頭の `unset`）は、この設計で期待が変わる。TLS の check 群（611-652 行目）は変えない。
- `docs/architecture/resources/temporal.md`: 知見 61-62 行目（unset と initProcessEnabled）、制約の表 111 行目、経緯 134 行目（Round 1 が書いた）。
- `IaC/terraform/oss/workflow/ecs.tf` は aws-managed のシンボリックリンク。

## 設計方針

1. **master のパスワードは、最後に使った直後に env から外す。** `unset NAUTOBOT_DB_PASSWORD` を手順 2 の最後の psql（`btree_gin`）の直後に 1 行置く（`exec` の直前から動かす）。それより後のコードの行に `NAUTOBOT_DB_PASSWORD` は出ない（手順 3 の `sql_tool` と psql、手順 4 の namespace、手順 5 の `exec` のどれにも渡らない）。背景のサブシェルの先頭の `unset` は要らなくなるので消す（設計方針 3 で別の実行ファイルにする）。
2. **TLS の導出は Round 1 のまま。** `SQL_HOST_VERIFICATION`（既定 `false`）の否定を temporal-sql-tool の `SQL_TLS_DISABLE_HOST_VERIFICATION` に、`SQL_CA` → `SQL_TLS_CA_FILE` / `PGSSLROOTCERT`、`SQL_HOST_NAME` → `SQL_TLS_SERVER_NAME`、psql は `true` なら `verify-full`。`ecs.tf` の値は変えない。
3. **PID 1 を tini にし、namespace の作成を別の実行ファイルにする。**
   - `Dockerfile`: `RUN apk add --no-cache ${POSTGRESQL_CLIENT_PACKAGE} tini`（同じ RUN に足す）。`COPY --chmod=755 namespace.sh /etc/temporal/namespace-rds.sh` を足す。先頭のコメントに tini と namespace-rds.sh の 1 行ずつ。
   - `docker/images/temporal-server/namespace.sh`（新規。`#!/bin/sh`、`set -u`）: いまの背景のサブシェルの中身（`nc` で 7233 を待つ → `cluster health` を待つ → `describe` が無ければ `create`。各 30 回、失敗しても `exit 0`）をそのまま移す。`unset` は書かない（起こす側が外したあとに起こす）。引数で `<address> <namespace> <retention>` を受ける（env に頼らない。`TEMPORAL_ADDRESS_LOCAL` はいま export していない）。`log()` は entrypoint と同じ `entrypoint-rds:` の接頭辞（CloudWatch の見方を変えない）。
   - `entrypoint.sh` の手順 4: `( … ) &` を `/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDRESS_LOCAL" "$DEFAULT_NAMESPACE" "$DEFAULT_NAMESPACE_RETENTION" &` の 1 行にする。fork のあと exec するので、`/proc/<pid>/environ` は `unset` 後の env になる。
   - `entrypoint.sh` の最後の行: `exec tini -- /etc/temporal/entrypoint.sh`。公式の entrypoint は `exec` で temporal-server を起こすので、temporal-server は tini の直接の子になる（検証 5 で `ppid` を見る。間に `sh` が挟まっていたら build.md に書く。tini は子にだけシグナルを送るので、挟まると `docker stop` / ECS の停止が SIGKILL 待ちになる）。背景のプロセスは `exec` で tini の子になり、抜けたら tini が回収する。
   - `ecs.tf`: Round 1 の `linuxParameters` とコメントを外す（`git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf` が空）。
4. **テストで守る。** `tests/test_workflow.py` の期待を次に変える（検証 1 のとおり、変える前に落ちることを見る。素の実行は最初の失敗で止まるので 1 つずつでよい）:
   - 「最後の（コメントでない）行が `exec tini -- /etc/temporal/entrypoint.sh`」（592-594 と 607-609 行目の `exec /etc/temporal/entrypoint.sh` の期待を置き換える）
   - 「`unset NAUTOBOT_DB_PASSWORD` の行が 1 つあり、その前のコードの行で最後に `NAUTOBOT_DB_PASSWORD` を使うのは `btree_gin` の psql、その後のコードの行に `NAUTOBOT_DB_PASSWORD` が無い」（607-609 行目の「exec の直前」を置き換える）
   - 「`docker/images/temporal-server/` は Dockerfile、dynamicconfig.yaml、entrypoint.sh、namespace.sh の 4 つ」、「Dockerfile が `tini` を apk で入れ、`namespace.sh` を `/etc/temporal/namespace-rds.sh` に COPY する」
   - 「entrypoint は namespace を `/etc/temporal/namespace-rds.sh <address> <namespace> <retention> &` で起こし、`( … ) &` のサブシェルを持たない」（638-639 行目を置き換える）。「namespace.sh に `unset` も `NAUTOBOT_DB_PASSWORD` も `POSTGRES_PWD` も無く、`describe` → `create` の順で、失敗しても `exit 0`」
   - 「どのコンテナにも `initProcessEnabled` が無い」（557-559 行目を置き換える。tini がイメージの中にあり、ECS の init では背景のプロセスを回収できない、と check 名に書く）
   - TLS の check 群と `sql_tool` の振る舞いの検査（611-652 行目）は変えない。
5. **docs。** `docs/architecture/resources/temporal.md`:
   - 知見 61-62 行目を書き換える: 「ロールと DB を作った直後に master のパスワード（`NAUTOBOT_DB_PASSWORD`）を env から外す。守れるのは tini / temporal-server / namespace の背景プロセスの `/proc/<pid>/environ`。ECS Exec のシェルはタスク定義の env を引き継ぐので、そこからは見える（036 のリスク 7 のまま）」「PID 1 はイメージの tini。temporal-server と namespace を作る背景のプロセスは tini の子で、抜けたら tini が回収する（ECS の `initProcessEnabled` では回収できない: init は孤児しか拾わず、親の temporal-server は生きている）」
   - 制約の表に 1 行: 「ECS Exec | シェルがタスク定義の env を引き継ぐので master のパスワードが見える。`ecs:ExecuteCommand` の権限は Nautobot のコンテナと同じ扱い」
   - 経緯 134 行目（039）を「tini を PID 1 にし、namespace を別の実行ファイルにし、master のパスワードを最後に使った直後に外す」に直す（`initProcessEnabled` を消す）。
   - `docs/workflow.md` は変えない。
6. **AWS では確かめない。** QUEUE の「残った修正をまとめて AWS で動作確認して直す」で、ECS Exec から: `ps -o pid,ppid,stat,comm` で PID 1 が `tini`、`temporal-server` の ppid が 1、`Z` が無い。`for p in /proc/[0-9]*; do echo "$(cat $p/comm) $(tr '\0' '\n' < $p/environ | grep -c NAUTOBOT_DB_PASSWORD)"; done` で tini / temporal-server / namespace-rds.sh が `0`（ssm agent とそのシェルは `1` でよい）。イメージが変わってタスクが入れ替わっても承認待ちが残ること（PM が QUEUE の行を直す）。

## 変更対象ファイル

| ファイル | 変更 |
| --- | --- |
| `docker/images/temporal-server/Dockerfile` | `tini` を apk に足す、`namespace.sh` の COPY、先頭のコメント 2 行 |
| `docker/images/temporal-server/namespace.sh` | 新規（背景のサブシェルの中身を移す。引数 3 つ） |
| `docker/images/temporal-server/entrypoint.sh` | `unset` を `btree_gin` の直後へ、手順 4 を `namespace-rds.sh … &` の 1 行に、最後の行を `exec tini -- …`、先頭のコメント（手順 3・4・5 の説明） |
| `IaC/terraform/aws-managed/workflow/ecs.tf` | Round 1 の `linuxParameters` とコメントを外す（main と同じに戻す） |
| `tests/test_workflow.py` | 設計方針 4 |
| `docs/architecture/resources/temporal.md` | 設計方針 5 |

`docker/images/temporal-server/` の中身が変わるので、`ops/up.sh` の `dir_tag` で ECR のタグが変わり、次の `up.sh` でビルドし直される（036 のまま）。

## 再利用するもの

- `entrypoint.sh:101-123` のサブシェルの中身（そのまま `namespace.sh` へ）と `log()`（31 行目）
- `tests/test_workflow.py` の `_ts_ep` / `_ts_ep_code` と `_c_temporal` / `_c_ui` / `_c_worker`、Round 1 の `_ts_ep_tls`
- 036 の `build.md`（81 行目以降と 214 行目以降）の手元の docker の手順。Round 1 の `build.md` の検証 5（`--init` あり / なしの出力）

## 実装ステップ

1. テストの期待を変え、落ちることを見る（設計方針 4）
2. `namespace.sh` を作り、`entrypoint.sh` と `Dockerfile` を直す（設計方針 1・3）
3. `ecs.tf` の `linuxParameters` を外す（設計方針 3）
4. docs（設計方針 5）
5. 検証を打ち、`build.md` に `## Round 2` で追記する。手元の docker を使ったらイメージとコンテナとネットワークを消す

## 検証方法

1. `uv run --group dev --group web python tests/test_workflow.py` が、変える前は新しい期待で落ち、直したあと失敗 0。
2. `./ops/check.sh` が `すべて通過`。
3. `sh -n` が `entrypoint.sh` と `namespace.sh` で rc=0。
4. `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf` が空。
5. 手元の docker で 036 の build.md と同じ手順（`postgres:18` + 自前イメージ。**`--init` は付けない**）で起こし、namespace のログ（`namespace: default を作った` か `がある`）が出たあとに:
   - `docker exec <temporal> ps -o pid,ppid,stat,comm`: PID 1 が `tini`、`temporal-server` の ppid が 1（間に `sh` が無い）、`Z` の行が無い
   - `docker exec <temporal> sh -c 'for p in /proc/[0-9]*; do echo "$(cat $p/comm) $(tr "\0" "\n" < $p/environ | grep -c NAUTOBOT_DB_PASSWORD)"; done'`: `docker exec` 自身の `sh` 以外は全部 `0`（`docker exec` はコンテナの Env を引き継ぐので `1`。ECS Exec と同じ）。同じコマンドで `POSTGRES_PWD` は `temporal-server` が `1`
   - `temporal operator namespace describe -n default` が通る
   - `docker stop -t 30 <temporal>` が 30 秒待たずに返る（tini が SIGTERM を temporal-server へ渡す）。`docker inspect -f '{{.State.ExitCode}}'` を build.md に書く
   - 終わったら `docker rm -f -v` / `docker network rm` / `docker rmi` で消し、`docker ps -a` と `docker images` で残っていないことを見る
6. `docker run --rm --entrypoint sh <自前イメージ> -c 'tini --version; ls -l /etc/temporal/namespace-rds.sh'` が `tini version 0.19.0` と `-rwxr-xr-x` を出す（検証 5 の中で打ってよい）。

## 未確定事項とリスク

1. **公式 entrypoint が `exec` で temporal-server を起こすこと。** 検証 5 の `ppid` で見る。間に `sh` が挟まるなら、`exec tini -s -- …` ではなく、`entrypoint.sh` 側で公式 entrypoint の中身（`TEMPORAL_BROADCAST_ADDRESS` の補完と `temporal-server start`）を読んで判断し、build.md に書いて PM に回す（設計の変更になる）。
2. **`SQL_TLS_SERVER_NAME` を temporal の postgres プラグインが読まないかもしれない**（Round 1 の反対弁護人。未確認）。`SQL_HOST_NAME` はいま渡していないので動作に影響しない。design は渡す形のまま残し、QUEUE に未確認として足す（PM）。
3. **`SQL_HOST_VERIFICATION=true` の経路は手元でも AWS でも通していない。** 036 と同じ。
4. **イメージとタスク定義が変わり、次の `up.sh` で workflow のサービスが入れ替わる。** min 100 / max 200 で履歴は RDS に残る（AWS では未確認。QUEUE の AWS 動作確認で見る）。
5. **ECS Exec からは master のパスワードが見える**（背景 1）。このサイクルでは守らない。
