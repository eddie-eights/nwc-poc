# temporal-server の entrypoint の守りを締める（039）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high（PM のサブエージェント。ベースは df18d07）

> [!warning] 未解消の Must fix が 1 件ある（設計側。`/cycle-design` へ差し戻す）
> 設計方針 3（`initProcessEnabled`）は、背景のサブシェルのゾンビを回収しない。そのうえ init の `/proc/1/environ` に `NAUTOBOT_DB_PASSWORD` が戻り、設計方針 1 を打ち消す。手元の docker の `--init` で再現した（下の「検証 5」）。コードは design.md のとおりに入れたまま（ecs.tf のコメント、test の check 名、temporal.md の 1 項目は誤った理由を書いている）。

### 変えたもの

- `docker/images/temporal-server/entrypoint.sh`
  - 最後の行 `exec /etc/temporal/entrypoint.sh` の直前に `unset NAUTOBOT_DB_PASSWORD` を足した。
  - `: "${SQL_HOST_VERIFICATION:=false}"` を足した。
  - psql の TLS: 検証ありなら `PGSSLMODE=verify-full`（`SQL_CA` があれば `PGSSLROOTCERT`）、TLS だけなら `require`、TLS 無しなら `prefer`。
  - `sql_tool` をサブシェルにした。`SQL_TLS_DISABLE_HOST_VERIFICATION` は `SQL_HOST_VERIFICATION` の否定。`SQL_TLS_CA_FILE` / `SQL_TLS_SERVER_NAME` は値があるときだけ渡す。
  - namespace を作る背景のサブシェルの先頭にも `unset NAUTOBOT_DB_PASSWORD` を足した（設計のリスク 4 で実装者に任された分）。
- `IaC/terraform/aws-managed/workflow/ecs.tf`: temporal コンテナに `linuxParameters = { initProcessEnabled = true }` とコメント。
- `tests/test_workflow.py`
  - design の 3 つの check: temporal だけが initProcessEnabled、exec の直前の unset、TLS の静的な検査。
  - 足した check:
    - TLS の導出部分と `sql_tool` を抜き出して `sh` で動かす振る舞いの検査 4 つ。差し替えの temporal-sql-tool が env を出す。
    - `sql_tool` が引数をそのまま渡し、`SQL_PASSWORD` / `SQL_TLS` を呼んだ側に残さない検査 1 つ。
    - 背景のサブシェルの unset の検査 1 つ。
- `docs/architecture/resources/temporal.md`
  - 「初期化は…entrypoint がやる」に 2 項目足した。
  - 制約の表の PostgreSQL 18 の行に、ホスト名検証を有効にする手順を書いた。
  - 経緯に 1 行足した。

### 設計からの逸脱

- env 名は design のとおりだった（検証 4）。逸脱なし。
- テストは design の 3 つに 6 つ足した（上）。静的な検査だけでは否定の反転・`"$@"` の脱落・サブシェルを `{ }` にした漏れを縛れなかったため。
- 背景のサブシェルの unset を足した（design のリスク 4 が「足したら build.md に書く」と求めている）。
- 制約表の「ecs.tf に足すだけ」をセルフレビューで直した。イメージに RDS の CA が無く、`tests/test_workflow.py:539` が `SQL_HOST_VERIFICATION == "false"` を縛っている。

### 検証

#### 1. テスト（直す前は失敗 3、直したあと失敗 0）

`check` は assert で最初の失敗で止まるので、失敗を全部数えるために scratchpad のラッパー `nonfatal.py` を使った（assert を記録に変えて `通過 N / 失敗 M` を出す。リポジトリには入れていない）。

直す前。`ecs.tf` と `entrypoint.sh` を `git show main:` に戻し、テストだけ新しい状態で打った:

```
$ uv run --group dev --group web python <scratchpad>/nonfatal.py tests/test_workflow.py | grep -E "^FAIL|通過"
FAIL temporal だけが initProcessEnabled = true（namespace を作る背景のプロセスを ECS の init が回収する。ui / worker は背景のプロセスを持たない）
FAIL entrypoint は exec の直前で master のパスワード（NAUTOBOT_DB_PASSWORD）を env から外す（temporal-server の /proc/1/environ に残さない）
FAIL entrypoint の temporal-sql-tool と psql の TLS はサーバー本体と同じ SQL_HOST_VERIFICATION / SQL_CA / SQL_HOST_NAME から導く（ホスト名検証を決め打ちしない）
```

design の 3 つは 3 つとも落ちた。その直後に、振る舞いの検査の抜き出しが旧コードで例外になり、`通過/失敗` の行までは届かない:

```
  File "tests/test_workflow.py", line 621, in _ts_ep_tls
    b = _ts_ep.index("sql_tool() {"); b_end = _ts_ep.index("\n}\n", b) + 3
ValueError: substring not found
```

最後の編集のあと:

```
$ uv run --group dev --group web python <scratchpad>/nonfatal.py tests/test_workflow.py | grep -E "initProcessEnabled|NAUTOBOT_DB_PASSWORD|entrypoint の TLS|sql_tool:|背景のサブシェル|通過"
ok temporal だけが initProcessEnabled = true（…）
ok entrypoint は exec の直前で master のパスワード（NAUTOBOT_DB_PASSWORD）を env から外す（…）
ok entrypoint の TLS（ecs.tf と同じ SQL_HOST_VERIFICATION=false）: …（いま: {'SQL_TLS': 'true', 'SQL_TLS_DISABLE_HOST_VERIFICATION': 'true', 'PGSSLMODE': 'require', 'PGSSLROOTCERT': ''}）
ok entrypoint の TLS: SQL_HOST_VERIFICATION が無ければ false と同じ（…）
ok entrypoint の TLS: SQL_HOST_VERIFICATION=true なら …（いま: {'SQL_TLS': 'true', 'SQL_TLS_CA_FILE': '/ca.pem', 'SQL_TLS_DISABLE_HOST_VERIFICATION': 'false', 'SQL_TLS_SERVER_NAME': 'db.example', 'PGSSLMODE': 'verify-full', 'PGSSLROOTCERT': '/ca.pem'}）
ok entrypoint の TLS: SQL_TLS_ENABLED=false なら psql は prefer、temporal-sql-tool は TLS 無し（…）
ok entrypoint の sql_tool: 引数をそのまま temporal-sql-tool に渡し、SQL_PASSWORD / SQL_TLS は呼んだ側のシェルに残さない（いま: {'_ARGS': '--plugin postgres12 --ep db -p 5432 -u temporal --db temporal update-schema -d /x', '_INNER_SQL_PASSWORD': 'pw', '_OUTER_SQL_PASSWORD': 'unset', '_OUTER_SQL_TLS': 'unset'}）
ok entrypoint の namespace を作る背景のサブシェルも、最初に NAUTOBOT_DB_PASSWORD を外す（nc / temporal に渡さない）
通過 364 / 失敗 0
$ uv run --group dev --group web python tests/test_workflow.py > /dev/null 2>&1; echo "test_workflow rc=$?"
test_workflow rc=0
```

#### 2. `bash ops/check.sh`（最後の編集のあと）

```
check rc=0
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/workflow  OK
…
すべて通過
```

#### 3. `sh -n` と shellcheck

```
$ sh -n docker/images/temporal-server/entrypoint.sh; echo "sh -n rc=$?"
sh -n rc=0
$ which shellcheck; echo "which rc=$?"
shellcheck not found
which rc=1
```

shellcheck は入っていないので未実行。

#### 4. temporal-sql-tool の env 名

```
$ docker run --rm --entrypoint temporal-sql-tool temporalio/admin-tools:1.32.1 --help 2>&1 | grep -nE "TLS|tls|EnvVars|SQL_" | head -40
30:   --tls                                   enable TLS over sql connection [$SQL_TLS]
33:   --tls-ca-file value                     sql tls client ca file (tls must be enabled) [$SQL_TLS_CA_FILE]
34:   --tls-server-name value                 override for target server name [$SQL_TLS_SERVER_NAME]
35:   --tls-disable-host-verification         disable tls host name verification (tls must be enabled) [$SQL_TLS_DISABLE_HOST_VERIFICATION]
```

design のとおり。

#### 5. 手元の docker（`postgres:18` + 自前イメージ `nwc-temporal-server:t039`、TLS あり）

準備:
- `postgres:18`（`pg039`、ネットワーク `t039`）を `ssl=on`（snakeoil の証明書）で起こした。
- pg_hba は `local all all trust / hostnossl all all all reject / hostssl all all all scram-sha-256`。

`PGSSLMODE=disable` の psql は拒まれる:

```
psql: error: connection to server at "pg039" (172.20.0.2), port 5432 failed: FATAL:  pg_hba.conf rejects connection for host "172.20.0.3", user "nautobot", database "nautobot", no encryption
```

`--init` 無し（`tmp039`。`SQL_TLS_ENABLED=true` / `SQL_HOST_VERIFICATION=false`、パスワードは使い捨ての値）:

```
$ docker logs tmp039 2>&1 | grep -n "entrypoint-rds"
1:entrypoint-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
2:entrypoint-rds: temporal: 初回なので setup-schema -v 0.0
8:entrypoint-rds: temporal: update-schema
166:entrypoint-rds: temporal_visibility: 初回なので setup-schema -v 0.0
172:entrypoint-rds: temporal_visibility: update-schema
358:entrypoint-rds: namespace: default を作った（retention 72h）
$ docker exec tmp039 sh -c 'tr "\0" "\n" < /proc/1/environ | grep -c NAUTOBOT_DB_PASSWORD'
0
$ docker exec tmp039 sh -c 'tr "\0" "\n" < /proc/1/environ | grep -c POSTGRES_PWD'
1
$ docker exec tmp039 ps -o pid,stat,comm
PID   STAT COMMAND
    1 S    temporal-server
   35 Z    entrypoint-rds.
   95 R    ps
$ docker exec tmp039 temporal operator namespace describe -n default --address 127.0.0.1:7233 | head -4
  NamespaceInfo.Name                    default
  NamespaceInfo.Id                      e188d047-1398-4aab-8d94-d3fa49d42f7f
…
$ docker exec pg039 psql -U nautobot -tAc "SELECT usename, datname, ssl FROM pg_stat_ssl JOIN pg_stat_activity USING (pid) WHERE usename='temporal' GROUP BY 1,2,3"
temporal|temporal|t
temporal|temporal_visibility|t
```

`--init` あり（`tmpi039`。ECS の `initProcessEnabled` の代わり。同じ DB で 2 回目の起動）:

```
$ docker logs tmpi039 2>&1 | grep "entrypoint-rds"
entrypoint-rds: ロール temporal と DB temporal / temporal_visibility を確かめる
entrypoint-rds: temporal: update-schema
entrypoint-rds: temporal_visibility: update-schema
entrypoint-rds: namespace: default がある
$ docker exec tmpi039 ps -o pid,stat,comm,args
PID   STAT COMMAND          COMMAND
    1 S    docker-init      /sbin/docker-init -- /etc/temporal/entrypoint-rds.sh
    7 S    temporal-server  temporal-server start
   25 Z    entrypoint-rds.  [entrypoint-rds.]
   60 R    ps               ps -o pid,stat,comm,args
$ sleep 15; docker exec tmpi039 ps -o pid,ppid,stat,comm
PID   PPID  STAT COMMAND
    1     0 S    docker-init
    7     1 S    temporal-server
   25     7 Z    entrypoint-rds.
   66     0 R    ps
$ docker exec tmpi039 sh -c 'tr "\0" "\n" < /proc/1/environ | grep -c NAUTOBOT_DB_PASSWORD'
1
$ docker exec tmpi039 sh -c 'tr "\0" "\n" < /proc/7/environ | grep -c NAUTOBOT_DB_PASSWORD'
0
$ docker exec tmpi039 id
uid=1000(temporal) gid=1000(temporal) groups=1000(temporal)
```

**期待（`--init` で `Z` が無い）を満たさない。** 原因は次のとおり（systematic-debugging の Phase 1〜3 で特定）。

- ゾンビの親は init ではなく、生きている temporal-server（PID 7）。init が回収するのは親が死んだ孤児だけ。
- init は entrypoint より前に、タスク定義の env 全部を持って起きる。`/proc/1/environ` に master のパスワードが 1 件残り、検証 5 の 1 つ目（0 件）と設計方針 1 を崩す。uid temporal から読める。
- 実装では吸収できない。design.md の設計方針 3・検証 5 の前提が誤っているので、`/cycle-design` へ差し戻す。

代案の確かめ（scratchpad だけ。リポジトリには入れていない）。Dockerfile `FROM nwc-temporal-server:t039` + `apk add --no-cache tini`、entrypoint の最後を `exec tini -- /etc/temporal/entrypoint.sh` にし、`--init` 無しで起こした（`tmpt039`）:

```
entrypoint-rds: namespace: default がある
PID   PPID  STAT COMMAND
    1     0 S    tini
   27     1 S    temporal-server
   62     0 R    ps
$ （/proc/1、/proc/27 の NAUTOBOT_DB_PASSWORD の件数、/proc/27 の POSTGRES_PWD の件数、docker stop の終了コード）
0
0
1
137
```

`Z` が無く、どちらの environ にもパスワードが無い。docker stop の 137 は docker-init でも同じだったので、tini とは関係ない。この形にするなら、design の「最後の行は `exec /etc/temporal/entrypoint.sh`」とテストを変え、`initProcessEnabled` を外す。

`SQL_HOST_VERIFICATION=true` の経路（`tmp039` と同じ形で env だけ変えた）:
- `SQL_CA` 無し:
  `psql: error: connection to server at "pg039" (172.20.0.2), port 5432 failed: root certificate file "/home/temporal/.postgresql/root.crt" does not exist`
- `SQL_CA` = snakeoil の証明書:
  `psql: error: connection to server at "pg039" (172.20.0.2), port 5432 failed: server certificate for "localhost" does not match host name "pg039"`
- どちらも verify-full が効いて落ちる。証明書もホスト名も通る組み合わせは、手元では作っていない。

片付け:

```
$ docker rm -f -v tmpi039 tmpt039 pg039; docker network rm t039; docker rmi nwc-temporal-server:t039tini nwc-temporal-server:t039 postgres:18 temporalio/admin-tools:1.32.1 temporalio/server:1.32.1 2>&1 | grep -c Untagged
tmpi039
tmpt039
pg039
t039
4
$ docker ps -a
CONTAINER ID   IMAGE     COMMAND   CREATED   STATUS    PORTS     NAMES
```

- `docker network ls` に `t039` は無い。
- `docker images` に `nwc-temporal-server`・`postgres:18`・`temporalio/*:1.32.1` は無い。
- 作業の前に取った一覧（scratchpad の `images-before.txt`）にあったイメージは消していない（`postgres:16-alpine` / `17-alpine`、`temporalio/temporal:1.9.1`、ECR のタグなど）。

#### 6. `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf`

```
@@ -74,6 +74,8 @@ resource "aws_ecs_task_definition" "workflow" {
         retries     = 6
         startPeriod = 180
       }
+      # entrypoint が namespace を作る背景のプロセスは exec のあと temporal-server の子になり、誰も wait しない。ECS の init（PID 1）に回収させる（cycle 039）
+      linuxParameters = { initProcessEnabled = true }
       logConfiguration = {
```

`linuxParameters` とコメントの追加だけ。`environment` / `secrets` は変わらない。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中では effort を切り替えられない）。
- 反対弁護人: `Agent` general-purpose / opus / effort xhigh、読み取り専用。返ってきたあとの `git status --porcelain -uall` は変更 4 ファイルの ` M` だけで、増えたものは無い。

変異注入（直したあと `<scratchpad>/ep.orig` で元に戻した）:

| 注入 | 落ちた check | 結果 |
|---|---|---|
| `sql_tool_skip_host_verify` を両方 `true` にする | `entrypoint の TLS: SQL_HOST_VERIFICATION=true なら …`（`'SQL_TLS_DISABLE_HOST_VERIFICATION': 'true'`） | 落ちる |
| 最後の `unset NAUTOBOT_DB_PASSWORD` を消す | `entrypoint は exec の直前で …` | 落ちる |
| `sql_tool` の `( )` を `{ }` にする（中の `exec` も外す） | `entrypoint の sql_tool: …`（`'_OUTER_SQL_PASSWORD': 'pw', '_OUTER_SQL_TLS': 'true'`） | `通過 363 / 失敗 1` |
| `sql_tool` の `"$@"` を消す | `entrypoint の sql_tool: …`（`'_ARGS': '--plugin postgres12 --ep db -p 5432 -u temporal --db temporal'`） | `通過 363 / 失敗 1` |
| 背景のサブシェルの unset を消す | `entrypoint の namespace を作る背景のサブシェルも …` | `通過 363 / 失敗 1` |

下 3 つは、反対弁護人の指摘 10 を受けて check を足す前は通っていた（反対弁護人の probe）。

指摘と片付け:

1. **Must fix**
   - 観点・場所: [runtime / security / 設計] `IaC/terraform/aws-managed/workflow/ecs.tf:77-78`、`docs/architecture/resources/temporal.md:62`、`tests/test_workflow.py:557-559`、design.md の設計方針 3・検証 5。
   - 破綻シナリオ: initProcessEnabled はゾンビを回収しない（親は生きている temporal-server）。init の `/proc/1/environ` に master のパスワードが残る。
   - 確かめた結果: 自分と反対弁護人の両方で一致した。検証 5 の `--init` の出力のとおり。
   - 片付け: **差し戻し**。
   - 選択肢（反対弁護人の案）:
     - A: tini を入れて `exec tini --`、initProcessEnabled を外す（推奨。上の代案の確かめ）。ECS Exec の SSM agent の孤児も tini が拾う。
     - B: 設計方針 3 を取り下げ、ゾンビ 1 つを受け入れる。
     - C: 二重 fork。パスワードの問題が残るので勧めない。
2. **Should fix**
   - 観点・場所: [security / 設計の根拠] `docs/architecture/resources/temporal.md:61`、design.md の背景 1。
   - 破綻シナリオ: ECS Exec のシェルはコンテナの設定の env を引き継ぐので、unset しても `env` で master のパスワードが見える。unset で塞がるのは temporal-server とその子の env だけ。
   - 確かめた結果: docker を片付けたあとなので**未再現**（`docker exec` がコンテナの Env を引き継ぐのは既知の挙動）。
   - 片付け: 設計の根拠の話なので最終報告に回し、差し戻しの際に PM が design と docs の書き方を決める。根本対策は初期化を一回きりのタスクに分けることで、別サイクルの規模。
3. **Should fix**
   - 観点・場所: [docs] `docs/architecture/resources/temporal.md:113`。
   - 破綻シナリオ: 「ecs.tf に足すだけ」は誤り。イメージに CA が無く、テストが `false` を縛っている。
   - 確かめた結果: `grep -niE "pem|ca-cert|bundle|SQL_CA" docker/images/temporal-server/Dockerfile` → 0 件（rc=1）。`tests/test_workflow.py:539` が `SQL_HOST_VERIFICATION == '"false"'`。
   - 片付け: **直した**（CA をイメージに入れる、ecs.tf、テストの期待、の 3 つを書いた）。
4. **Should fix → 直した**
   - 観点・場所: [missing-tests] `tests/test_workflow.py`。
   - 破綻シナリオ: 背景のサブシェルの unset、`"$@"` の脱落、サブシェルを `{ }` にして `SQL_PASSWORD` が本体に漏れる（exec で temporal-server に乗る）、の 3 つをテストが縛っていなかった。
   - 片付け: check を 2 つ足し、上の表で 3 つとも落ちることを確かめた。
5. **Nit**
   - 観点・場所: [整合性] `entrypoint.sh:10`・`:84`。
   - 内容: 反対弁護人によると、Temporal v1.32.1 の postgres プラグイン（`common/persistence/sql/sqlplugin/postgresql/session/session.go`）は `TLS.ServerName` を読まない。なら `SQL_TLS_SERVER_NAME` は効かず、3 つとも `POSTGRES_SEEDS` を相手に検証する。
   - 確かめた結果: ソースは読んでいない、**未再現**。design の設計方針 2 が渡すと決めているので残した。
   - 片付け: 最終報告に回す。
   - 補足: 自分が先に書いた「psql だけサーバー名を上書きできず落ちうる」は、これが正しければ当たらない。
6. **Nit**
   - 観点・場所: [整合性] `entrypoint.sh:36-45`。
   - 内容: `SQL_CA` があって検証が false のとき、サーバーと sql-tool は `sslrootcert` 付きの require（CA は確かめる）になるが、psql は CA を確かめない。いまは `SQL_CA` を渡していないので起きない。
   - 片付け: 最終報告。
7. **Nit**
   - 観点・場所: [runtime] `entrypoint.sh:37-39`。
   - 内容: 検証 true で `SQL_CA` が無いと、psql が `root.crt` 無しの分かりにくいメッセージで落ちる。検証 5 で同じメッセージを見た。
   - 片付け: 最終報告。
8. **Nit**
   - 観点・場所: [整合性] `entrypoint.sh:37`・`:47`。
   - 内容: `True` / `TRUE` を偽として扱う。ecs.tf は `"false"` 固定でテストが縛っている。
   - 片付け: 最終報告。
9. **Nit**
   - 観点・場所: [security] `entrypoint.sh:75-127`。
   - 内容: unset を最後の利用（`:76`）の直後へ移せば、`:90` の psql、temporal-sql-tool、背景のサブシェルにも渡らない。design は「exec の直前」を指定し、テストも最後から 2 行目を縛っているので、design に従った。
   - 片付け: 最終報告。
10. **Nit**
    - 観点・場所: [security / 検証] `entrypoint.sh:101`。
    - 内容: サブシェルの中の unset は、そのサブシェル自身の `/proc/<pid>/environ` を書き換えない。namespace ができるまで uid temporal から読める。AWS で見るなら `/proc/[0-9]*/environ` の全部を見る。
    - 片付け: 最終報告。
11. **Nit**
    - 観点・場所: [missing-tests] `tests/test_workflow.py:616-625`。
    - 内容: 抜き出しは `.index()` で、entrypoint の書き換えで ValueError になる。check の失敗としては出ない（検証 1 の「直す前」で実際に出た）。
    - 片付け: 最終報告。
12. **Nit**
    - 観点・場所: [設計との食い違い] design.md の検証 1・設計方針 3。
    - 内容:
      - 検証 1 の「失敗 3」は、素の `tests/test_workflow.py` では最初の 1 つで止まるので観測できない（ラッパーで数えた）。
      - 「Fargate 1.3.0 以降で対応」は API リファレンスに裏付けが無い（反対弁護人。未確認）。
    - 片付け: 最終報告。差し戻しで PM が直す。

「問題なし」とした観点と根拠:

- `set -eu` とサブシェル化の終了コード: `sql_tool` の rc は `( … )` の rc（exec したコマンドの rc）。振る舞いの検査で、`_rc == 0` と出力が揃うことを確かめた。失敗時の伝搬は手元の docker でしか見ていない（setup-schema と update-schema は通った）。
- TLS の 4 通り: 振る舞いの検査の出力のとおり。反対弁護人の probe とも一致した。
- `unset` の効き目（temporal-server 本体）: 検証 5 の `--init` 無しで、`/proc/1/environ` の NAUTOBOT_DB_PASSWORD が 0、POSTGRES_PWD が 1。

### 残っているもの

- 未解消の Must fix 1（設計方針 3。`/cycle-design` へ差し戻し）。
- Should fix 1（ECS Exec からは env が見える。design と docs の根拠の書き方）。
- Nit 8（上の 5〜12）。
- AWS では何も確かめていない。
