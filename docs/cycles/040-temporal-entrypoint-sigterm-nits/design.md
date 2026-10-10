# temporal-server の entrypoint を起動中の SIGTERM で止められるようにし、039 の Nit を消化する（040）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

- `docker/images/temporal-server/entrypoint.sh` は手順 1〜3（DB を待つ → ロールと DB → スキーマ）を PID 1 の `sh` として走らせ、最後に `exec tini -- /etc/temporal/entrypoint.sh` で tini に替わる。**PID 1 の sh はハンドラの無いシグナルを無視する**ので、`exec tini` より前に ECS の停止（SIGTERM）が来ても止まらず、stopTimeout（既定 30 秒）後の SIGKILL で切られる。temporal-sql-tool の update-schema の途中で切れると、スキーマの版と表の中身がずれる可能性がある（039 のセルフレビュー Round 2 の Should fix 4。読んだだけで未再現）。
- 手元の docker で原理を確かめた（2026-10-10。`temporalio/server:1.32.1` の `sh -c`）。`trap` 無し: `docker stop -t 20` が 20 秒待って exit 137。`trap "…; exit 143" TERM` 有り: 前景の `sleep 8` が終わった直後に trap が走り、6 秒で exit 143。**ash は前景の子が返ってから trap を走らせる**ので、SIGTERM は「いま走っている手順の区切り」で効く（途中の psql / temporal-sql-tool を切らない）。
- 最初から tini を PID 1 にする案（手順 1〜3 も tini の下で動かす）は取らない。PID 1 の `/proc/1/environ` にタスク定義の env（master のパスワード入り）が起動から終わりまで残り、039 が `initProcessEnabled` を外した理由がそのまま戻る。
- 039 の Round 1 / Round 2 の Nit（QUEUE の 2 行）をここでまとめて消化する。TLS の名前は現物で確かめた（下の「現物で確認した契約」）。

### 現物で確認した契約（推測ではない）

- temporal-sql-tool（`temporal` v1.32.1 の `tools/sql/main.go`）が読む env: `SQL_HOST` `SQL_PORT` `SQL_USER` `SQL_PASSWORD` `SQL_DATABASE` `SQL_PLUGIN` `SQL_TLS` `SQL_TLS_CERT_FILE` `SQL_TLS_KEY_FILE` `SQL_TLS_CA_FILE` `SQL_TLS_SERVER_NAME` `SQL_TLS_DISABLE_HOST_VERIFICATION`。**`SQL_TLS_SERVER_NAME` は temporal-sql-tool の名前で合っている。**
- temporal-server の埋め込み設定テンプレート（同じ版の `docker/config_template.yaml`）の tls 節: `enabled: SQL_TLS_ENABLED` / `caFile: SQL_CA` / `certFile: SQL_CERT` / `keyFile: SQL_CERT_KEY` / `enableHostVerification: SQL_HOST_VERIFICATION` / `serverName: SQL_HOST_NAME`。**`SQL_HOST_NAME` はサーバー本体の名前**で、ecs.tf はいま渡していない（渡さなければ接続先ホスト名が使われるので、検証を有効にするときも必須ではない）。
- サーバー本体の TLS（`common/auth/tls_config_helper.go` の `NewTLSConfig`）: `InsecureSkipVerify: !EnableHostVerification`。CA は `RootCAs` に読むが、`SQL_HOST_VERIFICATION=false` なら検証自体を飛ばすので **CA を渡しても確かめない**。psql の `sslmode=require` も同じ（CA を見ない）。`true` で `caFile` が空だと OS の信頼ストアになり、RDS の CA は入っていないので繋がらない。

## 設計方針

1. **起動中の SIGTERM を trap で受け、手順の区切りで exit 143 にする。** `entrypoint.sh` の `log()` の直後に `trap '…' TERM INT` を置く。trap の中身は `log "SIGTERM を受けたので初期化を止める（手順 $step のあと。ここまでの手順はべき等なので次の起動でやり直す）"; exit 143`。各手順の先頭で `step=1` 〜 `step=4` を代入する（手順 1 の nc の前、2 の psql の前、3 の `for pair` の前、4 の namespace の前）。`exec tini` で trap は既定に戻る（exec はハンドラを持つシグナルを既定に戻す）ので外す行は要らない。
   - 止まるのは前景の子（nc / sleep / psql / temporal-sql-tool）が返った直後。update-schema の途中では切れない。SIGTERM が届いてから抜けるまでの最長は `nc -w 10` か `sleep 5` か psql か update-schema 1 回ぶん。
   - `exit 143` にする理由: 128+15 で「SIGTERM で終わった」と同じ値。CloudWatch の stopped reason と ExitCode で停止中に止めたと分かる。
2. **ECS の stopTimeout を 120 秒にする。** `IaC/terraform/aws-managed/workflow/ecs.tf` の temporal コンテナに `stopTimeout = 120`（Fargate の上限）を足し、コメントで理由を書く（初回の setup-schema + update-schema を途中で SIGKILL しないため。通常の停止は tini が SIGTERM を temporal-server に渡してすぐ抜けるので 120 秒は待たない）。`linuxParameters` は足さない。
3. **tini は `/sbin/tini` で呼ぶ。** `exec /sbin/tini -- /etc/temporal/entrypoint.sh`（PATH で引かない。`apk add tini` が置く場所は `/sbin/tini`。2026-10-10 に `temporalio/server:1.32.1` で確認）。
4. **namespace.sh の引数を明示して確かめ、コメントを直す。** `address=$1` 〜 `retention=$3` を `: "${1:?namespace-rds.sh <frontend のアドレス> <namespace> <retention> の 1 つ目が無い}"`（2 つ目、3 つ目も同様）に続けて代入する形にする。先頭コメント 5 行目を「引数が足りなければ非 0 で抜ける（起こし方の誤り）。動作中の失敗（frontend が開かない、cluster health が通らない、作れない）は log を出して exit 0 で抜けるだけ。本体の temporal-server は別プロセスで、この終了コードを誰も待たない。健全性は ECS の healthCheck の describe が見る」に書き換える（「道連れにしない」「落とさない」の言い方をやめる）。
5. **Dockerfile:3 のコメントを tini 後の書き方にする。** 「入口を entrypoint-rds.sh（初期化してから /sbin/tini の子として公式の /etc/temporal/entrypoint.sh へ exec）に替える」。
6. **TLS の入力を揃え、設定ミスは早く止める。** `entrypoint.sh` の `export PGHOST=…` の直後に:
   - `SQL_TLS_ENABLED` と `SQL_HOST_VERIFICATION` を `tr '[:upper:]' '[:lower:]'` で小文字に揃える（サーバー本体の YAML は `True` / `TRUE` も真に読むが、sh の `=` は区別する。コメントで理由を書く）
   - `SQL_HOST_VERIFICATION=true` で `SQL_CA` が空なら `log "SQL_HOST_VERIFICATION=true なのに SQL_CA（CA のパス）が無い。psql の verify-full も temporal-server も RDS の証明書を確かめられない"; exit 1`
   - 47 行目のコメントに「`SQL_TLS_SERVER_NAME` は temporal-sql-tool の env（tools/sql/main.go）、`SQL_HOST_NAME` はサーバー本体の env（config_template.yaml の serverName）」を 1 行足す。
   - `ecs.tf` の `SQL_HOST_VERIFICATION` の行のコメントを「`true` にするには RDS の CA をイメージに入れて `SQL_CA` を渡す（`SQL_HOST_NAME` は任意。無ければ接続先のホスト名）。`false` のあいだは psql（require）も temporal-server（InsecureSkipVerify）も CA を確かめない」にする。
7. **テストで守る**（`tests/test_workflow.py`。変える前に落ちることを見てから直す）:
   - 557-558 行目の check を `"initProcessEnabled" not in _ecs_code` だけにする（`linuxParameters` 丸ごとの禁止をやめる。check 名もそのまま）。temporal コンテナ（`_c_temporal` 相当の切り出し。無ければ `name      = "temporal"` から次の `{` までで作る）に `stopTimeout = 120` がある check を足す。
   - 597 行目の期待を `exec /sbin/tini -- /etc/temporal/entrypoint.sh` にする。
   - trap の check: 「`trap` の行が 1 つあり `TERM` を受けて `exit 143`、`step=1` 〜 `step=4` が順に出て、trap の行が `step=1` より前にある」。
   - `_ts_ep_tls`: 切り出しの `.index()` 4 つを `.find()` にし、どれかが `-1` なら `check("entrypoint の TLS の切り出しの目印（export PGHOST= / sql_tool_skip_host_verify=true; fi / sql_tool() { / \\n}\\n）がある", False)` を出して `(None, {}, {})` を返す（呼ぶ側の check は `_rc == 0` で落ちる）。script の先頭に `_ts_ep` の `log() {` で始まる行を足す（fail fast が `log` を呼ぶ）。stderr も返す（戻り値を 4 つにし、呼ぶ側を直す）。
   - 新しい TLS の check: `SQL_TLS_ENABLED="True", SQL_HOST_VERIFICATION="TRUE", SQL_CA="/ca.pem"` で `verify-full` / `SQL_TLS_DISABLE_HOST_VERIFICATION=false`。`SQL_HOST_VERIFICATION="true"` で `SQL_CA` 無しは rc≠0 で stderr に `SQL_CA` を含む。
   - `_ts_ns_run`: `nc` と cluster health の成否も引数にし（`nc_ok`, `health_ok`）、nc のスタブも `nc $*` を `calls` に記録する。足す check: nc が通らない → rc 0、`nc` が 30 回、`temporal` の呼び出し 0、stderr の末尾が「frontend の 7233 が開かない（30 回）。作らずに抜ける」。health が通らない → `cluster health` が 30 回、describe 0、stderr の末尾が「cluster health が通らない（30 回）。作らずに抜ける」。引数が 2 つ → rc≠0、`calls` 無し、stderr に `retention` を含む。既存の check 名「どこで抜けても exit 0」を「引数不足以外はどこで抜けても exit 0」にする。
8. **docs。** `docs/architecture/resources/temporal.md`:
   - 「初期化は temporal のコンテナの entrypoint がやる」の箇条書きに 2 つ足す: 「`exec tini` までの手順 1〜3 は PID 1 の sh で走る。その間は `/proc/1/environ` にタスク定義の env（master のパスワード入り）がある（tini に替わった時点で消える）」「起動中（手順 1〜3）に ECS の停止が来たら、走っている手順の区切りで exit 143 で抜ける（update-schema の途中で切らない）。stopTimeout は 120 秒（cycle 040）」。
   - 制約の表の「RDS の PostgreSQL 18」の行の末尾に「`SQL_HOST_VERIFICATION=false` のあいだは psql（require）も temporal-server（InsecureSkipVerify）も CA を確かめない。`true` で `SQL_CA` が無いと entrypoint が起動時に止める」を足す。
   - 経緯に 040 を 1 行。
   - `docs/workflow.md` と `docs/deploy.md` は変えない。
9. **AWS では確かめない。** QUEUE の「残った修正をまとめて AWS で動作確認して直す」で、タスク定義に `stopTimeout: 120` があること、起動直後（手順 1 のログの直後）に `aws ecs stop-task` を打つと ExitCode 143 と「SIGTERM を受けたので初期化を止める」のログが出ることを見る（PM が QUEUE の行に足す）。

## 変更対象ファイル

- `docker/images/temporal-server/entrypoint.sh`（trap と step、小文字化と fail fast、コメント、`/sbin/tini`）
- `docker/images/temporal-server/namespace.sh`（`${N:?}`、コメント 5 行目）
- `docker/images/temporal-server/Dockerfile`（3 行目のコメント）
- `IaC/terraform/aws-managed/workflow/ecs.tf`（`stopTimeout = 120`、`SQL_HOST_VERIFICATION` のコメント）
- `tests/test_workflow.py`（設計方針 7）
- `docs/architecture/resources/temporal.md`（設計方針 8）
- `docs/cycles/040-temporal-entrypoint-sigterm-nits/build.md`（新規）

## 再利用するもの

- `tests/test_workflow.py` の `_ts_ep_tls`（sh で切り出しを動かす）と `_ts_ns_run`（スタブで namespace.sh を動かす）。新しい check は同じ仕組みに足す。
- 036 の `build.md` の手元の docker の手順（`postgres:18` + 自前イメージ。`--init` は付けない）。
- `/sbin/tini` は 039 で `apk add` 済み。

## 実装ステップ

1. `tests/test_workflow.py` に設計方針 7 の check を書き、`uv run --group dev --group web python tests/test_workflow.py` で新しい check が落ちることを見る（素の実行は最初の失敗で止まるので 1 つずつでよい）。
2. `entrypoint.sh`（設計方針 1・3・6）、`namespace.sh`（4）、`Dockerfile`（5）、`ecs.tf`（2・6）を直す。
3. `temporal.md`（8）。
4. 検証 1〜6 を通し、`build.md` に出力を貼る。
5. `/cycle-build` 手順6 のセルフレビュー。

## 検証方法（期待出力つき）

1. `uv run --group dev --group web python tests/test_workflow.py`: 変える前は新しい check で落ち、直したあと失敗 0（check の総数は build.md に書く。いまは 370）。
2. `./ops/check.sh` が `すべて通過`。
3. `sh -n docker/images/temporal-server/entrypoint.sh && sh -n docker/images/temporal-server/namespace.sh` が rc=0。
4. `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf` が `stopTimeout = 120` とコメントの追加だけ（env と secrets と healthCheck に差分が無い）。
5. 手元の docker で、起動中の SIGTERM（arm64 でそのまま build できる。`docker build -t nwc-temporal-server:040-test docker/images/temporal-server/`）:
   - `docker run -d --name ts-040 -e POSTGRES_SEEDS=10.255.255.1 -e POSTGRES_USER=temporal -e POSTGRES_PWD=x -e NAUTOBOT_DB_USER=nautobot -e NAUTOBOT_DB_PASSWORD=x nwc-temporal-server:040-test`（届かないアドレスなので手順 1 の nc で回る）
   - 3 秒待って `time docker stop -t 60 ts-040`: **60 秒待たずに返る**（`nc -w 10` の残りか `sleep 5` の後。15 秒以内）。`docker inspect -f '{{.State.ExitCode}}' ts-040` が **143**。`docker logs ts-040` に `entrypoint-rds: SIGTERM を受けたので初期化を止める（手順 1 のあと` がある。
   - 比べるために main のイメージ（`git stash` は使わず、`git show main:docker/images/temporal-server/entrypoint.sh` を別ディレクトリに出して build するか、039 の build.md の値を引く）で同じことをすると 60 秒待って 137 になる。やらないなら build.md に「比較は 040 の design.md の原理の確認（sh -c。trap 無し 20 秒 / 137、有り 6 秒 / 143）に依る」と書く。
6. 手元の docker で通常の道（036 の build.md の手順。`postgres:18` + 同じイメージ。`--init` 無し）:
   - namespace のログ（`namespace: default を作った` か `がある`）が出る。`docker exec ts ps -o pid,ppid,stat,comm` で PID 1 が `tini`、`temporal-server` の ppid が 1。
   - `docker stop -t 30` が 30 秒待たずに返り、ExitCode を build.md に書く（039 と同じ値のはず）。
   - `docker exec ts sh -c '/etc/temporal/namespace-rds.sh 127.0.0.1:7233 default'`（引数 2 つ）が非 0 で `retention` を含むメッセージを出す。
   - 終わったら `docker rm -f -v` / `docker network rm` / `docker rmi nwc-temporal-server:040-test` で消し、`docker ps -a` と `docker images` に残っていない。
7. `_ts_ep_tls` の目印の guard: 一時的に `entrypoint.sh` の `sql_tool() {` を別名に変えて test_workflow.py を走らせ、ValueError ではなく「切り出しの目印」の check の失敗で止まることを見る（確認したら戻し、`git diff` が意図した差分だけ）。

## 未確定事項とリスク

1. **trap は手順の区切りまで待つ。** 手順 3 の update-schema が長いと（初回の RDS で数十秒）、SIGTERM から exit まで同じだけ掛かる。stopTimeout 120 秒で収まる見込み（手元の `postgres:18` では初回が 10 秒前後。036 の build.md）。超えたら SIGKILL で、これは変える前と同じ。
2. **ECS Exec と healthCheck のプロセスは引き続き master のパスワードを持つ**（039 のまま。根本対策は QUEUE の一回きりの初期化タスク）。この cycle は手順 1〜2 の間だけ PID 1 の sh が持つことを docs に書くだけで、無くさない。
3. **`SQL_HOST_VERIFICATION=true` の道は AWS で動かしたことが無い**（CA をイメージに入れていない）。fail fast と小文字化は `_ts_ep_tls` のスタブで確かめるだけ。
4. 検証 5 で `docker stop` の所要が 15 秒を超えたら、trap が前景の `nc -w 10` の後まで待っている以外の理由（trap が効いていない）なので、build.md に `docker logs` を貼って止める。
