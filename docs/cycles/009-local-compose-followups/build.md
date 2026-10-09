# Cycle 009 local-compose-followups 実装の記録

## Round 1

実装モデル: claude-opus-5-5

エンジニア3（PM の指示）。ブランチ `fix/local-compose-followups`（`docs/cycle-006-design` の a773fc4 をマージしてから）。AWS には何も立てていない・触っていない。`docs/cycles/BACKLOG.md` は触っていない（PM が直す）。

### commit

| commit | design の実装ステップ（項目 / BACKLOG） | 変えたファイル |
|---|---|---|
| d163911 | 1: 3（40）・7（49）・8（48）・10（53） | `.gitignore`、`ops/check.sh`、`app/containerlab/lab.sh`、`docker/compose/compose.yaml`、`docker/compose/README.md`、`docs/development.md`、`tests/test_local_compose.py`、`tests/test_oss.py`（8 files） |
| e4fcf4d | 2: 4（45）・5（50）・6（42） | `docker/compose/check.sh`、`docker/compose/lab.sh`、`docker/compose/README.md`、`tests/test_local_compose.py`（4 files） |
| 1b093f3 | 3: 1（39）・2（41） | `app/telegraf/telegraf.conf.in`、`app/telegraf/telegraf.sh`、`docker/compose/{up.sh,check.sh,compose.yaml,.env.example,README.md}`、`tests/{test_local_compose,test_lab_debug,test_stream}.py`（10 files） |
| 59766af | 4: 9（47）・11（54） | `app/containerlab/lab.sh`、`docker/compose/lab.sh`、`docker/compose/README.md`、`docs/development.md`、`tests/test_local_compose.py`（5 files） |
| （この commit） | この build.md | 1 file |

43 / 44 / 51（Splunk の応答そのもの）は design どおり入れていない。

### 入れたもの

- 1（39）
  - `telegraf.conf.in` の 4 つの受け口（MDT / trap / syslog / health）を `__BIND__:__X_PORT__` にし、`telegraf.sh` が sed で埋める。
  - 既定は今までの値（`LOG_PORT` 5140、`TRAP_PORT` 1162、`MDT_PORT` 57000、`HEALTH_PORT` 8080、`TELEGRAF_BIND` 空 = 全部のインターフェース）。ECS（`telegraf.tf` は何も渡さない）とデバッグ用の EC2 は今までと同じ設定になる。
  - render は、ポートが 1〜65535 の数字でない（先頭の 0 も拒む）か `TELEGRAF_BIND` が空でも IPv4 でもなければ、変数の名前を出して止まる。
  - 起動の 1 行に health のポートと bind を出す。
  - compose は `MDT_PORT` / `HEALTH_PORT` / `TELEGRAF_BIND` を渡す。
- 2（41）
  - `docker/compose/up.sh` が `ip -o -4 addr show` で lab の管理ネットの GW（203.0.113.1）を探し、あれば `TELEGRAF_BIND` にする。
  - 無ければ空（全部のインターフェース）にして WARNING を出し、`lab.sh up` のあとに `up.sh telegraf` で直すよう案内する。
  - `docker/compose/check.sh` に「Telegraf: health が 200」の判定を足した。
  - README の手順は cp .env → `lab.sh up` → `up.sh` → `lab.sh check` → `check.sh` の順。
- 3（40）
  - `.gitignore` に `app/containerlab/clab-*/` を足し、README の「消す」の注を直した。
- 4（45）
  - `check.sh` と `lab.sh` の `env_get` を同じ定義にした。
  - 行頭の `export `、CRLF、クォート無しの値の後ろの ` # メモ` を落とす。`"…"` / `'…'` の中の `#` と `x#y` は残し、後の定義が勝つ。
- 5（50）
  - Splunk の理由は `sorted(set(...))` で重複を消す。
  - judge の理由は全部 1 行（改行と続く空白は空白 1 つ）、200 字まで。
- 6（42）
  - Kafka の件数は Kafbat UI のトピック一覧（1 回だけ取る）の `messagesCount` で見る。
  - `metrics` が 0 件なら NG。`traps` が 0 件なら「注意」で `fail-main` か `trap-test` を案内し、`ng` は増やさない。
- 7（49）
  - `ops/check.sh` の 3 は、`git ls-files '*.sh'` の全部を 1 本ずつ `bash -n` する。
  - 取れなければ die し、本数を `bash -n: N 本` で出す。
- 8（48）
  - `lab.sh render` の表示を「イメージは `$SRLINUX_IMAGE` と `$MULTITOOL_IMAGE`」にした。
- 9（47）
  - `lab.sh` の案内（fail-main / fail-bgp / heal-bgp / trap-test / failover / up の forward 失敗 / telegraf run / 転送なしの hint）を `LAB_CMD` で出す（下の逸脱 9）。
  - `hint()` に 3 つ目の引数（戻すサブコマンド）を足した。fail-bgp は手元でも「戻すのは '… heal-bgp'」を出す。
  - fail-main に戻し方（heal-main）を足した。
- 10（53）
  - compose の telegraf と spark の anchor を `restart: on-failure:5` にし、README に止まったときの見方を書いた。
- 11（54）
  - fail-bgp / heal-bgp / fail-main / failover を、偽の docker（sr_cli の BGP の admin-state と IS-IS の経路を返す）/ iptables / sudo / sleep / snmpwalk で実行し、標準出力の文言を見る検査を 7 つ足した。
  - 呼び方は 3 通り（ラッパー / PATH の lab / 直のパス）、分岐は 4 つ（手元 / デバッグ用の EC2 / stream / 転送なし）を見る。

### 設計から逸脱した点

| # | 項目 | design | 実装 | 理由 |
|---|---|---|---|---|
| 1 | 6 | `kafka-1` の `kafka-get-offsets.sh` で `snmp` / `snmp_trap` を数える | Kafbat UI の `messagesCount` で `metrics` / `traps` を数える | トピックの実名は `metrics` / `traps`（`telegraf.conf.in` の `topic`）。`docker exec` が要らず、偽の curl でテストできる。手元で上げた `kafka-1` で、`kafka-get-offsets.sh --topic 'traps\|logs'` の和（traps 7 = `traps:2:7`、logs 0）と Kafbat の `messagesCount` が同じだった（コンテナは消した） |
| 2 | 10 | `config \| grep -c 'on-failure:5'` が 3 | 4 | x-spark の anchor 自身も `config` の出力に残る（telegraf / spark-splunk / spark-http と合わせて 4） |
| 3 | 1 | `: "${MDT_PORT:=57000}"` の形 | `MDT_PORT=${MDT_PORT:-57000}` | 既存の定数と同じ書き方にした。意味は同じ |
| 4 | 1・2 | `.env.example` に 4 つのポートと `TELEGRAF_BIND` | `MDT_PORT` と `HEALTH_PORT` だけ | trap / syslog のポートは `lab.sh` の REDIRECT と SR Linux の設定に合わせて固定。`TELEGRAF_BIND` は `up.sh` が毎回決める（compose の注と README に書いた） |
| 5 | 2 | 管理ネットの GW と 127.0.0.1 | GW だけ。GW が無ければ全部のインターフェース（WARNING） | design のとおり 1 つの input は 1 つのアドレスでしか待てない。lab より先に上げたときは bind に失敗して止まるので、全部で待って案内する |
| 6 | 2（リスク 3） | `check.sh` の telegraf の判定が無ければ足す | 足した（判定は 6 → 8 → 9 項目） | restart の上限で止まった・bind に失敗したも「繋がらない」で拾う |
| 7 | 2 | README の順番の前提 | `lab.sh up` → `up.sh` の順に並べ替えた | bridge が先に要る |
| 8 | ― | ― | 足した行の `$VAR` を `${VAR}` にした | macOS の bash 3.2 は `$VAR` の直後に全角文字が続くと 1 バイト食う |
| 9 | 9 | 案内は `$0` で出す | ラッパーが `LAB_CMD="$0"` を渡し、`lab.sh` は `LAB_CMD` が無いときだけ `$0` から決める（PATH の `lab` なら `sudo lab`、それ以外は `sudo <呼ばれたパス>`）。export するので failover が中で打つ `"$SELF" fail-main` も同じ | 手元のラッパーは `../../app/containerlab/lab.sh` を絶対パスで呼ぶので、`$0` のままだとラッパーを通らない打ち方（`TELEGRAF_LOCAL` も `.env` のイメージも渡らない）を案内してしまう。直に打てば design どおり `sudo app/containerlab/lab.sh …` |
| 10 | 11 | `bash app/containerlab/lab.sh hint` を打つ | fail-bgp / failover を直のパスで打つ | `hint` はサブコマンドではない（関数） |
| 11 | 11 | ― | テストの偽の docker が IS-IS の経路（dc1-spine-02 だけ）を返す | failover の `route()` は経路が無いと `set -e` と `pipefail` で止まり、「(IS-IS の経路が無い)」の分岐に来ない（今までからの挙動。直していない。下の「PM への候補」） |

### 検証

#### 1. `uv sync --group dev --group web` → `bash ops/check.sh`（59766af、未コミットの変更が無い状態）

```
$ uv sync --group dev --group web
Resolved 86 packages in 2ms
Audited 82 packages in 1ms
$ bash ops/check.sh
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
（18 ルートとも OK）
== 3. スクリプトの構文
bash -n: 27 本
構文エラーなし
== 4. 模擬テスト
通過 137 / 失敗 0
通過 489 / 失敗 0
通過 158 / 失敗 0
通過 3 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 84 / 失敗 0
通過 107 / 失敗 0
68 項目すべて通過
通過 167 / 失敗 0
通過 144 / 失敗 0
通過 66 / 失敗 0
通過 75 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0

すべて通過
exit=0
$ git ls-files '*.sh' | wc -l
      27
```

最後の 2 行は空行と `すべて通過`。3 の本数（27）は `git ls-files '*.sh'` と同じで、`oss/compose/check-*.sh` の 3 本を含む（エンジニア2 の `chore/remove-oss-compose` で消える予定。いまの木の分で通っている）。

#### 2. red-green（変更を 1 つずつ元に戻すとテストが落ちる）

scratchpad の `redgreen.py`（`[ファイル, 新, 旧, テスト]` の組を 1 つずつ当てて走らせ、戻す）で打った。

| commit | 件数 | 結果 |
|---|---|---|
| d163911 | commit メッセージに件数を残していない。台本が残っているのは 1 件（`ops/check.sh` の「`.sh` が取れなければ die」を外す） | 落ちる |
| e4fcf4d | 8 | 8 件とも落ちる |
| 1b093f3 | 22 | 22 件とも落ちる |
| 59766af | 13 | 13 件とも落ちる（最初の 11 件のうち 2 件は落ちなかった。EC2 の分岐ではどちらでも `sudo lab` になるため。直のパスで `TELEGRAF_IMAGE` の分岐を打つ検査を足して落ちるようにした） |

#### 3. テスト（各 commit の時点）

| commit | 結果 |
|---|---|
| d163911 | test_local_compose 82 / 0、test_oss 167 / 0 |
| e4fcf4d | test_local_compose 88 / 0 |
| 1b093f3 | test_local_compose 100 / 0、test_stream 75 / 0、test_lab_debug 84 / 0 |
| 59766af | test_local_compose 107 / 0、test_stream 75 / 0、test_lab_debug 84 / 0、test_alerts 137 / 0 |

`docs/development.md` の `test_local_compose` の項目数は 107 に直した。

#### 4. shellcheck（`uv tool run --from shellcheck-py shellcheck`）

- `docker/compose/check.sh`・`docker/compose/lab.sh`・`docker/compose/up.sh`・`app/telegraf/telegraf.sh`: 指摘なし
- `app/containerlab/lab.sh`: 変更前（a773fc4）と同じ 6 件（SC2209 / SC2012 / SC1090 / SC2178 / SC2128 × 2）。増えていない

#### 5. 1・2 の render と Telegraf 1.40.1（design の未確定事項 1）

`MDT_PORT=57001 HEALTH_PORT=18081 TELEGRAF_BIND=127.0.0.1` で `telegraf.sh` の render を打った:

```
service_address = "127.0.0.1:57001"
service_address = "udp://127.0.0.1:1162"
server = "udp://127.0.0.1:5140"
service_address = "http://127.0.0.1:18081"
（__ は残らない）
```

この conf と `.star` を載せて `telegraf:1.40.1 --test-wait 0 --test` を打つと、設定エラーで止まらず `Listening on udp://127.0.0.1:1162` と `Listening on udp://127.0.0.1:5140` を出した。`inputs.syslog` の `server` もアドレス付きを受けるので、未確定事項 1 は解消（syslog も GW で待てる）。

ホストに無いアドレスで待たせたときの挙動（`up.sh` が GW を見る理由）:

```
$ （snmp_trap を udp://203.0.113.1:1162 にして telegraf:1.40.1 を起こす）
E! [telegraf] Error running agent: starting input inputs.snmp_trap: listening failed: listen udp 203.0.113.1:1162: bind: cannot assign requested address
（エージェントは止まる）
```

#### 6. 4 の `.env` の読み方

11 通りの入力（`export`、CRLF、`# メモ`、クォートの中の `#`、`x#y`、重複など）で、`env_get` の値が `docker compose config`（v5.1.3）と同じだった。Mac の BSD sed と GNU sed 4.9（`python:3.13-slim`）の両方で確かめた。同じ入力は `test_local_compose` にも入れた（2 つの定義の一致と、lab.sh / check.sh の実行）。

#### 7. 10 の restart

```
$ docker compose -f docker/compose/compose.yaml --env-file docker/compose/.env.example config | grep -n on-failure
（253・305・382・435 行の 4 か所が on-failure:5。telegraf、spark-splunk、spark-http、x-spark の anchor）
```

### 未確認

Mac では確かめられなかったもの:

- WSL の実機
  - clab の bridge の 203.0.113.1 に Telegraf が bind できること。
  - lab を作り直したあとの受け口。README には `up.sh telegraf` で直すと書いた。
  - 全部のインターフェースで待つときに、WSL の外から届くこと。
  - ラッパー（`docker/compose/lab.sh`）が出す案内の文言。
- 本物の Splunk（Mac では amd64 のイメージが起動しない。43 / 44 / 51 と同じ理由）。
- 本物の compose を全部上げた状態での `docker/compose/check.sh` の 9 項目。テストは偽の curl / docker で見た。
- EC2 の実機で `sudo lab …` の `$0` が `/usr/local/bin/lab` になり、案内が `sudo lab` と出ること。sudo の `secure_path` 次第なので、テストは PATH に置いた `lab` へのリンクで見た。

### PM への候補（BACKLOG は触っていない）

- 残りの `$VAR` を `${VAR}` にする
  - 場所は `app/containerlab/lab.sh` と `app/telegraf/telegraf.sh` で、直後に全角文字が続くもの。
  - 影響が出るのは Mac の bash 3.2 だけ。EC2・WSL・コンテナの bash 5 では出ない。
- failover の `route()` が IS-IS の経路が無いと止まる
  - `nhg=$(srl … | grep -oE … | head -1 | awk …)` で grep が何も当たらないと、`set -e` と `pipefail` で lab.sh ごと終わる。
  - そのため「(IS-IS の経路が無い)」の分岐に来ない。

### セルフレビュー

PM（fable-5.1）が 2026-10-08 に実施。`/robust` の手順で diff（a5097ab のマージ、17 ファイル）を読み、反対弁護人（opus、文脈なし）に反証させた 9 件を自分で再現して分類した。実装は変えていない（直すものはエンジニア2 の `fix/009-spark-restart-env-get` に回した）。

| # | 指摘 | 分類 | 再現 | 扱い |
|---|---|---|---|---|
| 1 | `x-spark` の `restart: on-failure:5`（compose.yaml:50）が Splunk の起動（2〜3 分）を待たない。`http_post`（snmp_sinks.py:341-358、`HTTP_RETRIES=3`）は約 12 秒で `RuntimeError` → クエリが止まり `main` が 1 で終わる。5 回使い切ると Exited のまま。check.sh に Spark のコンテナを見る項目が無い | **Should fix**（runtime） | 実測: `docker run --restart on-failure:5 alpine sh -c 'sleep 12; exit 1'` → 10 秒ごとに restartCount 0,1,2,3,4,4,5 と増え、80 秒で `status=exited exitCode=1 restartCount=5`。12 秒以上走っても回数は戻らない。compose は `depends_on: [kafka-1, kafka-2, kafka-3]` だけで Splunk / OpenSearch の healthy を待たない（compose.yaml:51、grep `service_healthy` は 0 件） | 直す。splunk / opensearch の healthcheck と `depends_on … condition: service_healthy`（または待ちループ）＋ check.sh に spark の running 判定＋テスト |
| 2 | `env_get`（check.sh:11、lab.sh:10）の読み方が compose と違う | **Should fix**（correctness） | 合成の .env を `docker compose config` と並べた（scratchpad `envt/`）: `pa$$word` → compose `pa$word` / env_get `pa$$word`、`ab$HOME` → 展開 / そのまま、`"a\"b"` → `a"b` / `a\`、`P4 = spaced` → `spaced` / 空、`val<TAB># memo` → `val\t# memo` / `val`、`${P7}z` → `az` / `${P7}z`。`.env.example` の既定値では一致する | 直す。`$` `\` `=` の周りの空白・TAB の後の `#` がある行に注意を出し `.env.example` に制約を書く、または `docker compose config` で compose の解釈を読む（エンジニアが選ぶ）＋テスト |
| 3 | failover の `route()`（lab.sh:199/215）は IS-IS の経路が無いと `grep … \| head -1` の rc=1 を `set -e` + `pipefail` が拾い、「(IS-IS の経路が無い)」の分岐に来ずに終わる。テストは経路を偽装しているので検出しない | Should fix（runtime）だが **009 の範囲外** | `/bin/bash -c 'set -euo pipefail; f(){ local x; x=$(echo a\|grep b\|head -1); echo after; }; f'` → 何も出ず rc=1 | 009 より前からの不具合で、build.md の「PM への候補」と BACKLOG に既にある。lab.sh は 011 が大きく組み直すので、そちらのあとで別に直す |
| 4 | check.sh の Kafka の判定は `messagesCount` が累積なので、volume が残っていると Telegraf が死んでも ok | Nit | 読んだだけ（Kafbat の `messagesCount` はトピックの総数）。design 6 は「件数 > 0」を要件にしている | 直さない。最終報告に載せる |
| 5 | up.sh:16 / check.sh:79 の `ip … \| grep -q` は SIGPIPE で偽になり得る（fail-open）。`.` が未エスケープ。Mac には `ip` が無い | Nit | 読んだだけ。`grep -q` は最初の一致で閉じるので `ip` 側が SIGPIPE を受けるが、`grep -q` 自身の rc は 0。pipefail の無い up.sh では `$?` は grep のもの | 直さない |
| 6 | `app/containerlab` で `sudo bash lab.sh` と打つと案内が `sudo lab.sh heal-bgp` になる | Nit | 読んだだけ（`LAB_CMD` は `$0` から決める。README の打ち方は `sudo app/containerlab/lab.sh …` か `sudo lab`） | 直さない。build.md の「未確認」に EC2 の `$0` が既にある |
| 7 | ops/check.sh:44-48 の `git ls-files` は非 ASCII のファイル名を引用符付きで返し `bash -n` が die する | Nit | `git ls-files '*.sh' \| grep -c '"'` → 0（該当ファイルなし） | 直さない |
| 8 | テストが文字列の照合だけ（test_local_compose.py:420 / :636、test_stream、test_lab_debug） | 据え置き | design.md「検証方法」がテストの手段を文字列の検査と偽の curl / docker で定めている。2 の修正で合成の .env を使う検査が入る | 2 の修正に合成 .env の検査を含める。残りは直さない |
| 9 | telegraf.sh の BIND の正規表現が `999.999.999.999` を通す | Nit | 読んだだけ（bind に失敗して Telegraf が止まり、check.sh の health で拾う） | 直さない |

反対弁護人が不成立とした 6 件（`$VAR` + 全角、bash 3.2 の `${!p}`、sed インジェクション、Kafbat の 8080、on-failure の 4 か所、`restart telegraf` の BIND）は PM も同意。

テスト: 反対弁護人が `uv run python tests/test_local_compose.py` 111 / 0、`test_stream` 75 / 0、`test_lab_debug` 84 / 0 を実行。PM は a5097ab のマージ後に `bash ops/check.sh` → 最終行「すべて通過」（scratchpad `check-008.log`、cde390f 時点）。

未解消: 1 と 2（エンジニア2 が直す）。解消したら cold reviewer #1 を呼ぶ（`/cycle-review`）。

## Round 2（セルフレビューの Should fix 2 件の修正）

- 実装モデル: opus-5.5（エンジニア2）。commit: 468fd46（`fix/009-spark-restart-env-get`、61d475c から 1 commit）。PM が docs/cycle-006-design にマージ
- エンジニア2 の報告（SendMessage）をそのまま写す。PM は `bash ops/check.sh` をマージ後に打ち直した（結果は review.md の Round 1）

### 1. Spark が送り先より先に起きて落ちる件（Should fix 1）

- splunk / opensearch / prometheus に healthcheck を足した（splunk: `/sbin/checkstate.sh`、opensearch: `/` が 200 か 401、prometheus: `/-/ready`）
- `depends_on` に `service_healthy` を足した。spark-splunk は splunk を、spark-http は opensearch と prometheus を待つ。`x-spark` のマージキーは `depends_on` を混ぜないので、Kafka 3 つと合わせて各 service に書いた
- check.sh に「Spark: spark-splunk / spark-http が動いている」の 2 項目を足した（`docker compose ps -a --format json` を 1 回）。exited / restarting は状態を出して logs と `up.sh <service>` を案内、created は依存が healthy でないことを案内、コンテナが無ければ up.sh を案内
- 手元で prometheus と opensearch を別プロジェクト名で上げ、2 つとも healthy になるのを見てから `down -v` で消した。opensearch の `/` は 401 だった

### 2. env_get の件（Should fix 2）は案 (b)

- check.sh と lab.sh は `docker compose --env-file F config --environment` の出力から値を取る
- 本物の compose（v5.1.3）に試し用の .env を読ませるテストを足した。P1〜P9 のすべてと、シェルが勝つことを確かめる
- compose が読めない .env では値のかけらが stderr に出るので、出さずに汎用の文言で止まり、sudo も curl も打たない（テストあり）
- `tests/test_local_compose.py` は 121 通過（111 から 10 件増）

### 検証

- `ops/check.sh`: 325 通過・失敗 0、最後の行「すべて通過」（エンジニア2）

### 未確認・挙動の変化

- `config --environment` が使える compose の最小の版は未確認（v5.1.3 にはある）
- 改行を含む値は読み違える（README と .env.example に「値は 1 行」と書いた）
- Splunk を含む全体の healthy の待ち時間は未確認（WSL でも Apple Silicon でも）。splunk の healthcheck は手元で走らせていない
- lab.sh でもシェルの `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` が .env より勝つようになった（compose と同じ。前は .env が勝っていた）

### セルフレビュー

Round 1 の `### セルフレビュー` の Should fix 1・2 がこの修正で解消。未解消の Must fix / Should fix: 無し。Nit 4/5/6/7/9 と据え置きの 8、範囲外の 3 は Round 1 のまま最終報告へ。
