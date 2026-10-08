# Cycle 009 local-compose-followups 設計: 手元の compose の小粒の改善をまとめて入れる

PM(fable-5.1) / effort: high

この文書は現行の設計だけを書く。質問は無かった（ユーザーが判断を PM に委ねた 2026-10-08）ので design-log.md は作らない。実装（build.md の Round 1）とセルフレビューの修正（Round 2）で変わった点は、この文書に取り込んである（2026-10-08、cold review Round 1 の Should fix 1）。最初の案との差は build.md の「設計から逸脱した点」と git の履歴にある。

## 背景

手元の docker compose で動く構成を作る（006）のセルフレビューと cold review で、直さずに BACKLOG に回した小粒の改善が 11 件ある。どれも `docker/compose/` と `app/containerlab/lab.sh` と `app/telegraf/` と `ops/check.sh` の中で閉じていて、AWS には関係ない。1 サイクルに束ねて 1 本の branch で入れる。

Mac では Splunk のイメージが amd64 だけで起動しないので、**Splunk の応答そのものを確かめる 43 / 44 / 51 は入れない**（未確認のまま BACKLOG に残す）。

| # | BACKLOG | 内容 |
|---|---|---|
| 1 | 39 | Telegraf の health（8080/tcp）と MDT（57000/tcp）のポートを環境変数で変えられるようにする |
| 2 | 41 | 手元の Telegraf の受け口（8080 / 57000 / 1162 / 5140）を lab の管理ネット（GW `203.0.113.1`）と 127.0.0.1 からだけ受ける |
| 3 | 40 | containerlab が作る `app/containerlab/clab-*/` を `.gitignore` に入れる |
| 4 | 45 | `docker/compose/check.sh` と `docker/compose/lab.sh` の `.env` の読み方を compose に合わせる（`export `、CRLF、行末の `# メモ`） |
| 5 | 50 | `check.sh` の Splunk の理由を重複なし・長さの上限つき・1 行にする |
| 6 | 42 | `check.sh` で trap と Kafka のメッセージ数まで見る |
| 7 | 49 | `ops/check.sh` の 3 で `git ls-files '*.sh'` の全部を `bash -n` する |
| 8 | 48 | `lab.sh render` の表示をイメージ名にする |
| 9 | 47 | `lab.sh` の `fail-main` と `heal-bgp` の案内を手元でも合うようにする |
| 10 | 53 | compose の telegraf / spark の `restart: on-failure` に回数の上限を付ける |
| 11 | 54 | `lab.sh` の `hint` と `failover` の案内を実行で確かめるテストを足す |

## 設計方針

### 1 と 2. Telegraf の受け口（`app/telegraf/telegraf.conf.in`、`app/telegraf/telegraf.sh`、`docker/compose/`）

- `telegraf.conf.in` の固定値 `":57000"`（245 行目）、`"udp://:1162"`（252 行目）、`"http://:8080"`（375 行目）と syslog の `server`（265 行目の注のとおり `service_address` は 1.40 で拒まれる）を、`telegraf.sh` が sed で埋めるプレースホルダ（既存の `__KAFKA_BROKERS__` と同じ `__MDT_PORT__` / `__TRAP_PORT__` / `__LOG_PORT__` / `__HEALTH_PORT__` / `__BIND__`）にする。`telegraf.sh` は `TRAP_PORT=1162`（35 行目）、`MDT_PORT=57000`（37 行目）をいままでどおり既定にしつつ、環境変数が入っていればそれを使う（`: "${MDT_PORT:=57000}"` の形）。`HEALTH_PORT` と `LOG_PORT` も同じ
- 待つアドレスは新しい変数 `TELEGRAF_BIND`（既定は空 = 全部のインターフェース。AWS の ECS はいままでどおり）。手元の compose は `docker/compose/up.sh` が `TELEGRAF_BIND` を渡す。**Telegraf の 1 つの input は 1 つのアドレスでしか待てない**ので、「管理ネットの GW と 127.0.0.1 の両方」は input を 2 つ書くのではなく、lab の管理ネットの GW `203.0.113.1`（`app/containerlab/lab.sh` の `MGMT_GW`）**だけ**で待ち、`check.sh` もそこへ打つ。127.0.0.1 では待たない（README の「WSL の外から偽の trap を入れられる」の説明を直す）
- `MGMT_GW` は `lab.sh up` で containerlab が作る bridge に付く。Telegraf が先に上がると bind に失敗するので、`docker/compose/up.sh` が `ip -o -4 addr show` で `203.0.113.1` の有無を見て、無ければ `TELEGRAF_BIND` を空にして全部で待つ（WARNING を出し、`lab.sh up` のあとに `up.sh telegraf` で直すよう案内）。README の手順は `lab.sh up` → `up.sh` の順に並べる
- `check.sh` の判定に「Telegraf: health が 200」を足す（`outputs.health` に打つ。bind の失敗や restart の上限で止まったものは繋がらないので NG になり、running でも health が落ちているものも NG に入る。設計方針 10 のリスク）
- health の 8080 は `telegraf.sh` の表示（84 行目）にも出す
- `.env.example` に置くのは `MDT_PORT` と `HEALTH_PORT` の 2 つだけ。trap（1162）と syslog（5140）のポートは `lab.sh` の REDIRECT と SR Linux の設定に揃えるので変えられず、`TELEGRAF_BIND` は `up.sh` が毎回決める（compose の注と README に書く）

### 3. `.gitignore`

- `app/containerlab/clab-*/` を `.gitignore:14` の `app/containerlab/splab.clab.yml` の隣に足す

### 4. `.env` の読み方（`docker/compose/check.sh:6-7`、`docker/compose/lab.sh:7`）

- `.env` の読み方を自前の sed で真似るのをやめ、**compose 自身に読ませる**。`check.sh` と `lab.sh` は `docker compose --env-file F config --environment 2>/dev/null` の出力（`KEY=値` を 1 行ずつ）を 1 回だけ変数に取り、`env_get()` は `sed -n "s/^$1=//p" | tail -1` でそこから引く。クォート、`\` の逃がし、`$X` と `$$` の展開、`# メモ`、`export `、CRLF の扱いが compose と完全に同じになり、シェルに同じ名前の環境変数があればそちらが勝つのも compose と同じ（`lab.sh` の `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` も、前は `.env` が勝っていたが compose と同じ向きになる）
- compose が読めない `.env` のときのエラーは値の一部を含むことがあるので出さず、汎用の文言で `exit 1` する（sudo も curl も打たない）。値は 1 行に限る（README と `.env.example` に書く）
- 2 か所は同じ 2 行にし、`tests/test_local_compose.py` が突き合わせる。本物の compose（手元は v5.1.3）に試し用の `.env` を読ませるテストで、`pa$$word` / `ab$HOME` / `"a\"b"` / `P4 = spaced` / TAB のコメント / `${P7}z` など 9 通りと「シェルが勝つ」を確かめる

### 5 と 6. `docker/compose/check.sh`

- Splunk の理由（43-47 行目）は `sorted(set(...))` で重複を消し、改行を空白にし、200 字で切る。NG の行が 1 行になる
- trap と Kafka のメッセージ数: Kafbat UI の `GET /api/clusters/nwc/topics?perPage=100` を 1 回取り、各トピックの `messagesCount`（全パーティションの和。`kafka-get-offsets.sh` の最新オフセットの和と同じ値になることを手元で確かめた）で `metrics` と `traps`（トピックの実名。`app/telegraf/telegraf.conf.in` の `[[outputs.kafka]]` の `topic`）が `> 0` を判定に足す。`docker exec` が要らず、偽の curl でテストできる。trap は `fail-main` を打たないと来ないので、`traps` は「0 件なら `fail-main` か `trap-test` を打ってから見る」と `注意:` の理由に出し、NG にはしない（WARN 扱い。`ng` を増やさない）
- Spark の判定を 2 項目足す（「Spark: spark-splunk / spark-http が動いている」）。`docker compose ps -a --format json` を 1 回取り、`exited` / `restarting` は状態を出して `logs` と `up.sh <service>` を案内、`created` は依存（送り先）が healthy でないことを案内、コンテナが無ければ `up.sh` を案内する。判定は 6 → 11 項目

### 7. `ops/check.sh:45`

- `for f in ops/up.sh … docker/compose/*.sh` の並べたリストを `git ls-files '*.sh'` に替える。`tests/test_local_compose.py:211-212` の正規表現と 232 行目の検査を新しい形に合わせる（「1 本ずつ打つ」「2 番目以降の構文エラーで落ちる」はそのまま）

### 8 と 9. `app/containerlab/lab.sh`

- `render`（105-112 行目）の「イメージは `${REGISTRY:-?}`」を `$SRLINUX_IMAGE` と `$MULTITOOL_IMAGE`（変数名は lab.sh の実物に合わせる）に替える
- `fail-main`（154-159 行目）、`fail-bgp` / `heal-bgp`（167-170 行目）の案内は、EC2 の `lab` コマンドではなく**いま自分が呼ばれた打ち方**で出す。`lab.sh` は環境変数 `LAB_CMD` があればそれを使い、無ければ `$0` から決める（PATH にある `lab` なら `sudo lab`、それ以外は `sudo <呼ばれたパス>`）。`export` するので `failover` が中で打つ `"$SELF" fail-main` の案内も同じになる。`hint()`（92 行目）も同じ。`LAB_CMD` は表示にだけ使い、実行しない
- 手元のラッパー `docker/compose/lab.sh` は `app/containerlab/lab.sh` を絶対パスで呼ぶので、`LAB_CMD="$0"`（ラッパー自身のパス）を渡す。ラッパーが渡すのは `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` / `TELEGRAF_LOCAL=1` / `LAB_CMD` の 4 つだけ（シェルの `REGISTRY` / `AWS_REGION` が ECR や SSM へ流れない）

### 10. compose の `restart`

- `docker/compose/compose.yaml` の spark の anchor（48 行目）と telegraf（68 行目）の `restart: on-failure` を `restart: on-failure:5` にする（compose の文法。swarm の `deploy.restart_policy` は使わない）。README に「5 回で止まるので `docker compose logs telegraf` を見る」を足す。Docker は回数を戻さないので、止まったものは `check.sh`（Telegraf の health、Spark の 2 項目）が NG にする
- Spark の 2 つは**送り先が healthy になるまで起こさない**。送り先が起きる前に始めると POST の再試行（約 12 秒）のあとジョブが終わり、5 回の上限を使い切って止まる（手元の Docker で `restartCount` 0→5、80 秒で `exited` を確認）。splunk（`/sbin/checkstate.sh`。上流のイメージの HEALTHCHECK と同じ。`interval 15s / timeout 30s / retries 5 / start_period 10m`）、opensearch（`/` が 200 か 401。`start_period 5m`）、prometheus（`/-/ready`。`start_period 1m`）に healthcheck を足し、`spark-splunk` は `splunk: {condition: service_healthy}`、`spark-http` は `opensearch` と `prometheus` の同じ条件を `depends_on` に書く。`x-spark` のマージキーは `depends_on` を混ぜないので、Kafka 3 つ（`service_started`）と合わせて各 service に書く
- その代わり `up.sh`（`docker compose up -d --build`）は Splunk が healthy になるまで戻らない（WSL の x86_64 で 2〜3 分の見込み）。healthy にならなければ `dependency failed to start: container nwc-local-splunk-1 is unhealthy` で止まり、Spark は `Created` のまま残る。README に「`logs splunk` で理由を見て直してから `up.sh` を打ち直す」を書く

### 11. `hint` / `failover` のテスト

- `tests/test_local_compose.py:305-327` の正規表現だけの検査に、`fail-bgp` と `failover`（`hint` はサブコマンドではなく関数なので、`hint` を通るサブコマンドを打つ）を偽の `iptables` / `sudo` / `ip` / `docker`（`forward` の検査と同じ仕組み）で実行して標準出力に期待の文言が出る検査を足す。期待の文言は 9 で直した `LAB_CMD` 付きの形。偽の `docker` は IS-IS の経路（dc1-spine-02 だけ）を返す（`failover` の `route()` は経路が無いと `set -e` と `pipefail` で止まる。今までからの挙動で、このサイクルでは直さない。BACKLOG）

## 実物で確認した契約（設計時。変更前のコードの行番号）

- `app/telegraf/telegraf.conf.in:245` `service_address = ":57000"`、`:252` `service_address = "udp://:1162"`、`:265` の注（`inputs.syslog` は `server`。`service_address` は 1.40 で拒まれる）、`:375` `service_address = "http://:8080"`
- `app/telegraf/telegraf.sh:103` の sed: `-e "s#__KAFKA_BROKERS__#$q#" -e "s#__AWS_REGION__#$AWS_REGION#" -e "s#__SNMP_AGENTS__#$agents#" -e "s#__GNMI_TARGETS__#$gnmi#" -e "s#__SYSLOG_STANDARD__#$SYSLOG_STANDARD#"`
- `docker/compose/check.sh:6-7` `env_get() { sed -n "s/^$1=//p" .env | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }`、`docker/compose/lab.sh:7` `f=.env; [ -f "$f" ] || f=.env.example`
- `ops/check.sh:45` は並べた 19 本。`git ls-files '*.sh'` は 27 本（差は `app/grafana/start.sh`、`app/neo4j/entrypoint.sh`、`app/splunk/entrypoint.sh`、`app/telegraf/telegraf.sh`、`ops/sync-graph.sh`、`oss/compose/check-*.sh` × 3。最後の 3 本は `chore/remove-oss-compose` で消える）
- `app/containerlab/lab.sh:20-21` `TRAP_PORT=1162`、`:236` `iptables -t nat -I PREROUTING 1 -s "$MGMT" -d "$MGMT_GW" -p udp --dport 162 … -j REDIRECT --to-ports "$TRAP_PORT"`
- `docker/compose/compose.yaml:48` と `:68` `restart: on-failure`、`:119` `ports: ["127.0.0.1:18080:8080"]`
- Telegraf 1.40 の `inputs.syslog` の `server` と `inputs.snmp_trap` の `service_address` が `203.0.113.1:port` の形を受けるか → build.md の Round 1 の検証（`--test` で確かめた）
- `docker compose config --environment` は compose v5.1.3 で `KEY=値` を 1 行ずつ出す（`tests/test_local_compose.py` が本物の compose で確かめる）。使える最小の版は未確認

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `app/telegraf/telegraf.conf.in` | 4 つの受け口をプレースホルダに（1・2） |
| `app/telegraf/telegraf.sh` | 既定値と環境変数、sed、表示（1・2） |
| `docker/compose/up.sh` | `TELEGRAF_BIND` と `203.0.113.1` の有無、healthy 待ちの注（2・10） |
| `docker/compose/compose.yaml` | 注、`restart: on-failure:5`、splunk / opensearch / prometheus の healthcheck、Spark の `depends_on`（1・10） |
| `docker/compose/.env.example` | `MDT_PORT` と `HEALTH_PORT`、書式の注（1・4） |
| `docker/compose/README.md` | 受け口、順番、restart の上限、healthy 待ちと失敗の案内、値は 1 行（2・4・10） |
| `.gitignore` | `app/containerlab/clab-*/`（3） |
| `docker/compose/check.sh` | `env_get`（compose に読ませる）、Splunk の理由、Kafka のメッセージ数、Telegraf の health、Spark の 2 項目（2・4・5・6・10） |
| `docker/compose/lab.sh` | `env_get`、`LAB_CMD`（4・9） |
| `ops/check.sh` | `git ls-files '*.sh'`（7） |
| `app/containerlab/lab.sh` | render の表示、案内の `LAB_CMD`（8・9） |
| `tests/test_local_compose.py` | 7 の正規表現、4 の本物の compose での突き合わせ、11 の実行テスト、1・2・10 の文字列検査、check.sh の偽の curl / docker |
| `tests/test_lab_debug.py` ほか | `telegraf.conf.in` の文字列を見ているテストがあれば合わせる（`grep -rn '57000\|:1162\|:8080' tests/` で探す） |
| `docs/cycles/BACKLOG.md` | PM が直す。エンジニアは触らない |

## 再利用するもの

- `telegraf.sh` の `__X__` プレースホルダと sed
- `tests/test_local_compose.py` の `forward` の偽 `iptables` / `sudo` の仕組み
- `ops/check.sh` の「1 本ずつ `bash -n`」の for 文

## 実装ステップ

1. 3・7・8・10 の小さいもの（commit 1 本）
2. 4・5・6 の `check.sh` / `lab.sh`（commit 1 本）
3. 1・2 の Telegraf（commit 1 本）
4. 9・11 の案内とテスト（commit 1 本）
5. `uv sync --group dev --group web` → `bash ops/check.sh` が `すべて通過` / exit 0
6. 手元の確認（下）。できなかったものは `build.md` に「未確認」と書く

## 検証方法

- `bash ops/check.sh` の最後が `すべて通過`、`echo $?` が `0`。`ops/check.sh` の 3 が通る本数が `git ls-files '*.sh' | wc -l` と同じ
- 1・2: `MDT_PORT=57001 HEALTH_PORT=18081 TELEGRAF_BIND=127.0.0.1 bash app/telegraf/telegraf.sh`（または render 相当の関数）で出た `telegraf.conf` に `127.0.0.1:57001` と `http://127.0.0.1:18081` があり、`__` が残らないこと。`docker run --rm -v <conf>:/etc/telegraf/telegraf.conf telegraf:1.40.1 --test-wait 0 --config /etc/telegraf/telegraf.conf --test` が設定エラーで止まらないこと（`inputs.syslog` の `server` に `127.0.0.1:5140` の形を受けるか）
- 4: 試し用の `.env` に `export A="x"`、`B=y # memo`、CRLF の `C=z`、`pa$$word`、`ab$HOME`、`"a\"b"`、`P4 = spaced`、TAB のコメント、`${P7}z` を置き、本物の `docker compose --env-file … config --environment` から `env_get` した値が compose の展開と同じで、シェルの同名の変数が勝つこと（`tests/test_local_compose.py`。`docker compose` が無い環境ではその検査を飛ばす）。読めない `.env` では値を出さずに止まること
- 5: Splunk の偽の応答（同じ `ERROR Unauthorized` 30 件）で理由が 1 行かつ 200 字以内（テスト）
- 6: compose を上げられる環境なら `check.sh` の出力に `Kafka: metrics のメッセージ数 > 0` と `traps` の行が出る。Mac では偽の curl（Kafbat の JSON）でテストし、`messagesCount` と `kafka-get-offsets.sh` の和が同じことを手元の `kafka-1` で 1 回確かめる
- 8・9・11: `bash app/containerlab/lab.sh fail-bgp` / `failover` の出力に `sudo app/containerlab/lab.sh heal-bgp`（`LAB_CMD` の形）が出ること。テストが red-green（文言を元に戻すと落ちる）
- 10: `docker compose -f docker/compose/compose.yaml config | grep -c 'on-failure:5'` が `4`（telegraf + spark 2 つ + `x-spark` の anchor 自身も `config` の出力に残る）。`config` の `spark-splunk` の `depends_on` に `splunk: condition: service_healthy`、`spark-http` に `opensearch` / `prometheus` の同じ条件があること（テスト）。手元で prometheus と opensearch を別プロジェクト名で上げ、2 つとも `healthy` になること（`down -v` で消す）
- 10（WSL）: `up.sh` のあと `docker compose ps` で splunk が `healthy`、Spark 2 つが `running`。healthy までの所要時間を build.md か review.md に記録する（下のリスク 6）

## 未確定事項とリスク

1. Telegraf 1.40 の `inputs.syslog` の `server` が `udp://203.0.113.1:5140` の形を受けるか → `--test` で確かめる。受けなければ syslog だけ全部で待つことにし、README に書く
2. Telegraf が lab の bridge より先に上がると bind に失敗する → `up.sh` が bridge を見る。見落とすケース（lab を後から作り直したとき）は README に「`docker compose restart telegraf`」と書く
3. `restart: on-failure:5` は、`SNMP_AGENTS` が空で 5 回落ちたあと人が気付かないと止まったまま → `check.sh` の telegraf の判定（health が 200 か）で拾う。その判定が無ければ足す
4. `git ls-files '*.sh'` は worktree の index を見るので、追跡していない新しい `.sh` は見ない。これは今までと同じ範囲（並べたリストも追跡済みだけ）
5. 43 / 44 / 51（Splunk の応答そのもの）は入れない。WSL でユーザーが Splunk を上げたときに別のサイクルで見る
6. **Splunk の healthcheck（`/sbin/checkstate.sh`）が WSL で healthy になるかは未確認**（Mac では Splunk が amd64 のエミュレーションになる）。上流のイメージの HEALTHCHECK と `IaC/terraform/aws-managed/pipeline/analytics/splunk.tf` と同じコマンドなので受かる見込みだが、外れると `up.sh` が最長 `start_period 10m + 15s × 5` 止まったあと `dependency failed to start` で失敗し、`spark-splunk` が `Created` のまま残る（README に書いた）。WSL の初回の `up.sh` で `docker compose ps` の health と所要時間を記録し、healthy にならなければ `start_period` か healthcheck の形を直す
7. `docker compose config --environment` の無い古い compose では `check.sh` と `lab.sh` が止まる（汎用の文言で案内）。使える最小の版は未確認（v5.1.3 にはある）。改行を含む値は読み違える（値は 1 行に限る）

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-009-local-compose-followups-design.html -->
