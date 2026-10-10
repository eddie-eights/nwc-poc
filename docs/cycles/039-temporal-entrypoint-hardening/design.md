# temporal-server の entrypoint の守りを締める（039）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

「Temporal の履歴を RDS に残す（036）」のコールドレビュー Round 1（`docs/cycles/036-temporal-rds/review.md` の「Nit」）で、`docker/images/temporal-server/entrypoint.sh` と `IaC/terraform/aws-managed/workflow/ecs.tf` に 3 つの Nit が出た。どれも動作には影響しないが、1〜数行で閉じられるので 1 サイクルで消化する。

1. **[security] master のパスワードが temporal-server の env に残る。** `entrypoint.sh:16` で必須にした `NAUTOBOT_DB_PASSWORD`（Nautobot の RDS の master のパスワード）は、ロールと DB を作ったあと使わないのに、`:107` の `exec /etc/temporal/entrypoint.sh` のあとも PID 1 の env に残る。`ecs:ExecuteCommand` できる人が `/proc/1/environ` を読むと master のパスワードが見える（036 の design.md のリスク 7 が受け入れた代償。読める範囲は Nautobot のコンテナと同じ）。
2. **[整合性] `sql_tool` のホスト名検証が決め打ち。** `entrypoint.sh:67` の `sql_tool` は `SQL_TLS_DISABLE_HOST_VERIFICATION=true` を固定で渡す。サーバー本体は `ecs.tf:53-54` の `SQL_TLS_ENABLED=true` / `SQL_HOST_VERIFICATION=false` を読むので、いまは揃っている。036 のリスク 2 の逃げ道（RDS の CA を入れて `SQL_CA` を指し、ホスト名検証を有効にする）を使うと、サーバーだけが検証してスキーマの投入（temporal-sql-tool）と psql は検証しないまま残る。
3. **[runtime] namespace を作る背景のサブシェルがゾンビで残る。** `entrypoint.sh:82-104` の `( … ) &` は `exec` のあと temporal-server（PID 1）の子になり、抜けたあと誰も `wait` しない。1 起動に 1 つで増えないが、`ecs.tf` の temporal コンテナに `linuxParameters.initProcessEnabled = true` を足せば ECS の init プロセスが回収する。

現物（2026-10-10 に PM が読んだ）:

- `entrypoint.sh` の該当行は上のとおり。最後の行は `exec /etc/temporal/entrypoint.sh`（`tests/test_workflow.py:589-591` が「最後の行がこれ」を要求する）。
- `ecs.tf` の temporal コンテナ（37-80 行目）は `environment` / `secrets` / `healthCheck` / `logConfiguration` を持ち、`linuxParameters` は無い。リポジトリ全体でも `initProcessEnabled` は使っていない（`grep -rn initProcessEnabled IaC/` → 0 件）。
- `tests/test_workflow.py:538-542` は temporal の env に `SQL_TLS_ENABLED = "true"`、`SQL_HOST_VERIFICATION = "false"` があることを見る（ecs.tf は変えないので、この期待は変えない）。
- `IaC/terraform/oss/workflow/ecs.tf` は aws-managed のシンボリックリンク。OSS 版にも同じ変更が入る。

### 推測した書式（実装者が実物で確かめる）

temporal-sql-tool の TLS の env 名は、PM の記憶では `SQL_TLS`（`--tls`）、`SQL_TLS_DISABLE_HOST_VERIFICATION`（`--tls-disable-host-verification`）、`SQL_TLS_CA_FILE`（`--tls-ca-file`）、`SQL_TLS_SERVER_NAME`（`--tls-server-name`）。サーバー本体（`temporalio/server` の `config_template_embedded.yaml` の postgres12 の枝）は `SQL_TLS_ENABLED` / `SQL_HOST_VERIFICATION` / `SQL_CA` / `SQL_HOST_NAME` を読む（036 の design.md の「サーバーの設定」で確認済み）。**temporal-sql-tool 側の 4 つは実装の前に `docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools:1.32.1 --help` で確かめ、`build.md` に出力の該当行を引用する。** 名前が違えば実物に合わせる（設計の字面に合わせない）。

## 設計方針

1. **`exec` の直前で master のパスワードを env から外す。** `exec /etc/temporal/entrypoint.sh` の直前に `unset NAUTOBOT_DB_PASSWORD` を 1 行置く（`NAUTOBOT_DB_USER` / `NAUTOBOT_DB_NAME` は値が秘密でないので残してよい。消すならパスワードと同じ行で）。最後の行が `exec …` である形は変えない（test_workflow の期待）。ロールと DB の作成（手順 2）と `btree_gin` は `unset` より前なので影響しない。
2. **`sql_tool` と psql の TLS の設定を、サーバー本体と同じ env から導く。** entrypoint の先頭の既定値に `: "${SQL_HOST_VERIFICATION:=false}"` を足し、
   - temporal-sql-tool へは `SQL_TLS_DISABLE_HOST_VERIFICATION` を `SQL_HOST_VERIFICATION` の否定で渡す（`false` → `true`、`true` → `false`）。`SQL_CA` があれば `SQL_TLS_CA_FILE="$SQL_CA"`、`SQL_HOST_NAME` があれば `SQL_TLS_SERVER_NAME="$SQL_HOST_NAME"` も渡す（無ければ渡さない。空文字を渡して挙動を変えない）。
   - psql へは `SQL_HOST_VERIFICATION=true` のとき `PGSSLMODE=verify-full`、`SQL_CA` があれば `PGSSLROOTCERT="$SQL_CA"`。`false` のときは今の `require` のまま。
   - `ecs.tf` の値は変えない（`SQL_HOST_VERIFICATION=false` のまま。今回は「逃げ道を使ったときに 1 か所で揃う」ようにするだけ）。
3. **temporal コンテナに `linuxParameters = { initProcessEnabled = true }` を足す。** Fargate はプラットフォーム 1.3.0 以降で対応（`platform_version` は `LATEST`）。ui / worker のコンテナには足さない（背景のプロセスを持たない）。
4. **テストで守る。** `tests/test_workflow.py` の entrypoint の check 群に 2 つ、ecs.tf の check 群に 1 つ足す:
   - 「`exec` の直前の（コメントでない）行が `unset NAUTOBOT_DB_PASSWORD` を含む」
   - 「`sql_tool` に `SQL_TLS_DISABLE_HOST_VERIFICATION=true` の決め打ちが無く、`SQL_HOST_VERIFICATION` から導いている」
   - 「temporal コンテナに `initProcessEnabled = true` があり、ui / worker には無い」
   検証 1 のとおり、直す前にこの 3 つが落ちることを見てから直す。
5. **docs を 1 行ずつ。** `docs/architecture/resources/temporal.md` の「知見」の「初期化は temporal のコンテナの entrypoint がやる」の箇条に、「ロールと DB を作ったあと master のパスワードは env から外してから temporal-server を起こす」と「namespace を作る背景のプロセスは ECS の init（`initProcessEnabled`）が回収する」を足す。「制約と未確認」の 111 行目（TLS の `SQL_HOST_VERIFICATION=false`）に「ホスト名検証を有効にするときは `SQL_HOST_VERIFICATION=true` と `SQL_CA` を ecs.tf に足すだけで、entrypoint の psql / temporal-sql-tool も同じ値を読む」を足す。「経緯」に 2026-10-10（039）の行を 1 つ。`docs/workflow.md` は変えない（流れは同じ）。
6. **AWS では確かめない。** イメージとタスク定義が変わるので、QUEUE の「残った修正をまとめて AWS で動作確認して直す」で、タスクの中で `cat /proc/1/environ | tr '\0' '\n' | grep -c NAUTOBOT_DB_PASSWORD` が `0`、`ps` にゾンビ（`Z`）が無いことを見る（PM が QUEUE の行に足す）。

## 変更対象ファイル

| ファイル | 変更 |
| --- | --- |
| `docker/images/temporal-server/entrypoint.sh` | 既定値に `SQL_HOST_VERIFICATION`、psql の `PGSSLMODE` / `PGSSLROOTCERT` の分岐、`sql_tool` の TLS の env を導出、`exec` の直前に `unset NAUTOBOT_DB_PASSWORD`。先頭のコメント（手順 4 の説明）に 1 行 |
| `IaC/terraform/aws-managed/workflow/ecs.tf` | temporal コンテナに `linuxParameters = { initProcessEnabled = true }` とコメント 1 行 |
| `tests/test_workflow.py` | check を 3 つ足す |
| `docs/architecture/resources/temporal.md` | 知見 2 行、制約 1 文、経緯 1 行 |

`docker/images/temporal-server/` の中身が変わるので、`ops/up.sh` の `dir_tag` で ECR のタグが変わり、次の `up.sh` でビルドし直される（仕組みは 036 のまま。触らない）。

## 再利用するもの

- `entrypoint.sh` の既存の `if [ "$SQL_TLS_ENABLED" = "true" ]; then …` の分岐（33 行目）。同じ形で `SQL_HOST_VERIFICATION` の分岐を足す
- `tests/test_workflow.py:588-600` の entrypoint の check 群（`_ts_ep`）と `:531-545` の ecs.tf の check 群（`_c_temporal` / `_c_ui` / `_c_worker`）
- 036 の `build.md`（`docs/cycles/036-temporal-rds/build.md` の「検証」81 行目以降と 214 行目以降）にある、手元の docker で `postgres:18` と自前イメージを起こす手順（TLS ありの再現も含む）

## 実装ステップ

1. テストを 3 つ足し、落ちることを見る（設計方針 4）
2. `entrypoint.sh` を直す（設計方針 1・2）。先に temporal-sql-tool の `--help` で env 名を確かめる
3. `ecs.tf` に `linuxParameters` を足す（設計方針 3）
4. docs（設計方針 5）
5. 検証を打ち、`build.md` に記録する。手元の docker を使ったらイメージとコンテナとネットワークを消す（036 の build.md 264-265 行目と同じ後片付け）

## 検証方法

1. `uv run --group dev --group web python tests/test_workflow.py` が、直す前は新しい 3 つの check で落ち（失敗 3）、直したあと失敗 0。
2. `./ops/check.sh` が `すべて通過`（fmt 差分なし、validate OK。`IaC/terraform/oss/workflow` も validate の対象に入っていること）。
3. `sh -n docker/images/temporal-server/entrypoint.sh` が rc=0。`shellcheck` が入っていれば `shellcheck -s sh` で新しい警告が無い（入っていなければ build.md にそう書く）。
4. `docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools:1.32.1 --help` の出力に、設計方針 2 で使う env 名（`SQL_TLS_DISABLE_HOST_VERIFICATION` / `SQL_TLS_CA_FILE` / `SQL_TLS_SERVER_NAME`）が載っていること。載っていなければ実物の名前に直し、build.md の「設計からの逸脱」に書く。
5. 手元の docker で 036 の build.md 81 行目以降と同じ手順（`postgres:18` + 自前イメージ。TLS ありの 214 行目以降の形でよい）で起こし、
   - `docker exec <temporal> sh -c 'tr "\0" "\n" < /proc/1/environ | grep -c NAUTOBOT_DB_PASSWORD'` が `0`、同じコマンドで `POSTGRES_PWD` は `1`（サーバー本体が使う）
   - `docker exec <temporal> ps -o pid,stat,comm` で `Z` の行が無い（`initProcessEnabled` は ECS の機能なので手元では `docker run --init` で代える。`--init` 無しだと `Z` が 1 つ出ることも見て、build.md に両方の出力を書く）
   - `temporal operator namespace describe -n default` が通り、`entrypoint-rds:` のログに `update-schema` と namespace の行が出る（036 と同じ）
   - 終わったら `docker rm -f -v` / `docker network rm` / `docker rmi` で消し、`docker ps -a` と `docker images` で残っていないことを見る
6. `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf` が `linuxParameters` の追加（とコメント）だけ。`environment` / `secrets` の行は変わらない。

## 未確定事項とリスク

1. **temporal-sql-tool の env 名の記憶違い。** 検証 4 で実物を見る。違っていたら実物に合わせ、design.md の「推測した書式」は PM が review で直す。
2. **`SQL_HOST_VERIFICATION=true` の経路は手元でも AWS でも通していない。** 036 と同じで、いまは `false` で動かす。`true` にしたときに psql の `verify-full` が RDS の CA（`global-bundle.pem`）で通るかは、逃げ道を使うときに確かめる（このサイクルの範囲外）。
3. **`initProcessEnabled` でタスク定義が変わり、次の `up.sh` で workflow のサービスが入れ替わる。** 036 で min 100 / max 200 にしてあるので、履歴は RDS に残り、ワークフローは続く（AWS では未確認。QUEUE の AWS 動作確認で見る）。
4. **`unset` の位置。** `exec` の直前（namespace の背景プロセスを起こしたあと）に置く。背景のサブシェルは `unset` より前に fork するので、サブシェル側の env には残る。サブシェルは `temporal operator` しか呼ばず、短命で抜けるので受け入れる。気になるなら、サブシェルの先頭でも `unset NAUTOBOT_DB_PASSWORD` する（1 行。実装者の判断でよい。足したら build.md に書く）。
