# Cycle 009 local-compose-followups 設計: 手元の compose の小粒の改善をまとめて入れる

PM(fable-5.1) / effort: high

この文書は現行の設計だけを書く。質問は無かった（ユーザーが判断を PM に委ねた 2026-10-08）ので design-log.md は作らない。

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
- 待つアドレスは新しい変数 `TELEGRAF_BIND`（既定は空 = 全部のインターフェース。AWS の ECS はいままでどおり）。手元の compose は `docker/compose/up.sh` が `TELEGRAF_BIND` を渡す。**Telegraf の 1 つの input は 1 つのアドレスでしか待てない**ので、「管理ネットの GW と 127.0.0.1 の両方」は input を 2 つ書くのではなく、lab の管理ネットの GW `203.0.113.1`（`app/containerlab/lab.sh` の `MGMT_GW`）で待ち、`check.sh` もそこへ打つ。127.0.0.1 では待たない（README の「WSL の外から偽の trap を入れられる」の説明を直す）
- `MGMT_GW` は `lab.sh up` で containerlab が作る bridge に付く。Telegraf が先に上がると bind に失敗するので、`docker/compose/up.sh` が lab の bridge の有無を見て、無ければ `TELEGRAF_BIND` を渡さず全部で待つ（WARNING を出す）。順番の前提は README に書く
- health の 8080 は `telegraf.sh` の表示（84 行目）にも出す
- compose の注（`docker/compose/compose.yaml:58-59`）と `docker/compose/.env.example` に 4 つの変数を足す

### 3. `.gitignore`

- `app/containerlab/clab-*/` を `.gitignore:14` の `app/containerlab/splab.clab.yml` の隣に足す

### 4. `.env` の読み方（`docker/compose/check.sh:6-7`、`docker/compose/lab.sh:7`）

- `env_get()` を compose の読み方に寄せる。行頭の `export ` を落とし、CRLF の `\r` を落とし、クォート無しの値の行末 `# メモ` を落とす（クォートの中の `#` は残す）。実装は sed の 1 本で済ませ、`lab.sh` も同じ関数を使う（2 か所に同じ関数を置くなら、両方を `tests/test_local_compose.py` で同じ入力で突き合わせる）
- 本物の compose の読み方は `docker compose config` で確かめる（検証方法）

### 5 と 6. `docker/compose/check.sh`

- Splunk の理由（43-47 行目）は `sorted(set(...))` で重複を消し、改行を空白にし、200 字で切る。NG の行が 1 行になる
- trap と Kafka のメッセージ数: Kafka は `kafka-1` の `/opt/kafka/bin/kafka-get-offsets.sh --bootstrap-server kafka-1:9092 --topic <トピック>` で全パーティションの最新オフセットを足し、`snmp` と `snmp_trap`（トピック名は `app/telegraf/telegraf.conf.in` の `[[outputs.kafka]]` の `topic` を読む）が `> 0` を判定に足す。trap は `fail-main` を打たないと来ないので、trap のトピックは「0 件なら `fail-main` か `trap-test` を打ってから見る」と理由に出し、NG にはしない（WARN 扱い。`ng` を増やさない）

### 7. `ops/check.sh:45`

- `for f in ops/up.sh … docker/compose/*.sh` の並べたリストを `git ls-files '*.sh'` に替える。`tests/test_local_compose.py:211-212` の正規表現と 232 行目の検査を新しい形に合わせる（「1 本ずつ打つ」「2 番目以降の構文エラーで落ちる」はそのまま）

### 8 と 9. `app/containerlab/lab.sh`

- `render`（105-112 行目）の「イメージは `${REGISTRY:-?}`」を `$SRLINUX_IMAGE` と `$MULTITOOL_IMAGE`（変数名は lab.sh の実物に合わせる）に替える
- `fail-main`（154-159 行目）、`fail-bgp` / `heal-bgp`（167-170 行目）の案内は、EC2 の `lab` コマンドではなく**いま自分が呼ばれたパス**（`$0`）で出す。EC2 では `lab` が `lab.sh` へのリンクなので `$0` が `lab` になり、手元では `sudo app/containerlab/lab.sh` になる。`hint()`（92 行目）も同じ

### 10. compose の `restart`

- `docker/compose/compose.yaml` の spark の anchor（48 行目）と telegraf（68 行目）の `restart: on-failure` を `restart: on-failure:5` にする（compose の文法。swarm の `deploy.restart_policy` は使わない）。README に「5 回で止まるので `docker compose logs telegraf` を見る」を足す

### 11. `hint` / `failover` のテスト

- `tests/test_local_compose.py:305-327` の正規表現だけの検査に、`bash app/containerlab/lab.sh hint` と `failover` を偽の `iptables` / `sudo` / `ip`（`forward` の検査と同じ仕組み）で実行して標準出力に期待の文言が出る検査を足す。期待の文言は 9 で直した `$0` 付きの形

## 実物で確認した契約

- `app/telegraf/telegraf.conf.in:245` `service_address = ":57000"`、`:252` `service_address = "udp://:1162"`、`:265` の注（`inputs.syslog` は `server`。`service_address` は 1.40 で拒まれる）、`:375` `service_address = "http://:8080"`
- `app/telegraf/telegraf.sh:103` の sed: `-e "s#__KAFKA_BROKERS__#$q#" -e "s#__AWS_REGION__#$AWS_REGION#" -e "s#__SNMP_AGENTS__#$agents#" -e "s#__GNMI_TARGETS__#$gnmi#" -e "s#__SYSLOG_STANDARD__#$SYSLOG_STANDARD#"`
- `docker/compose/check.sh:6-7` `env_get() { sed -n "s/^$1=//p" .env | tail -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"; }`、`docker/compose/lab.sh:7` `f=.env; [ -f "$f" ] || f=.env.example`
- `ops/check.sh:45` は並べた 19 本。`git ls-files '*.sh'` は 27 本（差は `app/grafana/start.sh`、`app/neo4j/entrypoint.sh`、`app/splunk/entrypoint.sh`、`app/telegraf/telegraf.sh`、`ops/sync-graph.sh`、`oss/compose/check-*.sh` × 3。最後の 3 本は `chore/remove-oss-compose` で消える）
- `app/containerlab/lab.sh:20-21` `TRAP_PORT=1162`、`:236` `iptables -t nat -I PREROUTING 1 -s "$MGMT" -d "$MGMT_GW" -p udp --dport 162 … -j REDIRECT --to-ports "$TRAP_PORT"`
- `docker/compose/compose.yaml:48` と `:68` `restart: on-failure`、`:119` `ports: ["127.0.0.1:18080:8080"]`
- **推測のまま残っているもの**: Telegraf 1.40 の `inputs.syslog` の `server` と `inputs.snmp_trap` の `service_address` が `203.0.113.1:port` の形を受けるか（docs では受ける。実行で確かめる）

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `app/telegraf/telegraf.conf.in` | 4 つの受け口をプレースホルダに（1・2） |
| `app/telegraf/telegraf.sh` | 既定値と環境変数、sed、表示（1・2） |
| `docker/compose/up.sh` | `TELEGRAF_BIND` と bridge の有無（2） |
| `docker/compose/compose.yaml` | 注、`restart: on-failure:5`（1・10） |
| `docker/compose/.env.example` | 4 つのポートと `TELEGRAF_BIND`（1・2） |
| `docker/compose/README.md` | 受け口、順番、restart の上限（2・10） |
| `.gitignore` | `app/containerlab/clab-*/`（3） |
| `docker/compose/check.sh` | `env_get`、Splunk の理由、メッセージ数（4・5・6） |
| `docker/compose/lab.sh` | `env_get`（4） |
| `ops/check.sh` | `git ls-files '*.sh'`（7） |
| `app/containerlab/lab.sh` | render の表示、案内の `$0`（8・9） |
| `tests/test_local_compose.py` | 7 の正規表現、4 の突き合わせ、11 の実行テスト、1・2・10 の文字列検査 |
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
- 4: `.env` に `export A="x"`、`B=y # memo`、CRLF の `C=z` を置き、`env_get A/B/C` が `x` / `y` / `z`、`docker compose config` の同じ変数と一致すること（テストにも入れる）
- 5: Splunk の偽の応答（同じ `ERROR Unauthorized` 30 件）で理由が 1 行かつ 200 字以内（テスト）
- 6: compose を上げられる環境なら `check.sh` の出力に Kafka の件数の行が出る。Mac で上げられなければ、`kafka-get-offsets.sh` の呼び方だけテストで文字列検査にして「未確認」と書く
- 8・9・11: `bash app/containerlab/lab.sh hint` の出力に `sudo app/containerlab/lab.sh fail-main`（`$0` の形）が出ること。テストが red-green（文言を元に戻すと落ちる）
- 10: `docker compose -f docker/compose/compose.yaml config | grep -c 'on-failure:5'` が telegraf + spark 2 つ分の `3`

## 未確定事項とリスク

1. Telegraf 1.40 の `inputs.syslog` の `server` が `udp://203.0.113.1:5140` の形を受けるか → `--test` で確かめる。受けなければ syslog だけ全部で待つことにし、README に書く
2. Telegraf が lab の bridge より先に上がると bind に失敗する → `up.sh` が bridge を見る。見落とすケース（lab を後から作り直したとき）は README に「`docker compose restart telegraf`」と書く
3. `restart: on-failure:5` は、`SNMP_AGENTS` が空で 5 回落ちたあと人が気付かないと止まったまま → `check.sh` の telegraf の判定（コンテナが running か）で拾う。その判定が無ければ足す
4. `git ls-files '*.sh'` は worktree の index を見るので、追跡していない新しい `.sh` は見ない。これは今までと同じ範囲（並べたリストも追跡済みだけ）
5. 43 / 44 / 51（Splunk の応答そのもの）は入れない。WSL でユーザーが Splunk を上げたときに別のサイクルで見る
