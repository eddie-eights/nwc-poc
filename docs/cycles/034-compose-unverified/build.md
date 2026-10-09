# 手元の compose の未確認を確かめる（034）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/034-compose-unverified（origin/main 4d3d071 から）。commit は A 2c7d96f / B と C 76a4697。手元の Mac（Docker Desktop 29.4.2、aarch64、メモリ約 7.75 GiB）の docker だけで確かめた。AWS には触っていない（**AWS（AMP）での逆順サンプルの扱いは未確認**。このサイクルの範囲外）。

### 変更ファイル

- A
  - `docker/compose/prometheus.yml:2-8`: `storage.tsdb.out_of_order_time_window: 1h` と理由のコメント
  - `tests/test_local_compose.py:320-321`: check 1 つ（142 → 143）
  - `docker/compose/README.md:238-252`: `## 確かめたこと` を立て、Prometheus の結果
- B と C
  - `docker/compose/README.md:254-288`: Splunk のアプリと volume の結果、check.sh の判定と本物の応答の表
  - `docker/compose/check.sh:88-90`: 判定の上のコメント（「本物の 401 の本文は未確認」を本物の形に）。判定の式は変えていない
  - `tests/test_local_compose.py:811-817`: 本物の本文での check 1 つ（143 → 144）
  - `docker/compose/compose.yaml:259,281`: 「Apple Silicon のエミュレーションは未確認」のコメント 2 か所
  - `docs/troubleshooting.md:380`: 「コードを読んだだけで、再現はしていない」を、再現した範囲と再現していない範囲に

### 設計との差

1. **window は `compose.yaml` の起動の引数でなく `prometheus.yml` に書いた。**
   Prometheus v3.15.0 には `--storage.tsdb.out_of_order_time_window` が無い（`docker run --rm prom/prometheus:v3.15.0 --help` に出ない）。設定ファイルの `storage.tsdb` にだけ書ける。compose.yaml は prometheus.yml をもう読んでいるので、compose.yaml の command は変えていない。
2. **docs は `docs/local-compose.md` でなく `docker/compose/README.md` に書いた。**
   `docs/local-compose.md` は無く、手元の compose の docs は `docker/compose/README.md` だけ。新しいファイルは作らず、README に `## 確かめたこと` を立てた。
3. **Splunk は `compose.yaml` を別のプロジェクト名（`-p nwc034`）と上書きのファイル（scratch。repo に入れない）で上げた。**
   ほかのセッションの `nwc-local` とネットワーク・volume・イメージ・ポートがぶつからないように、ネットワーク名、イメージ名（`nwc034-splunk`）、ポート（`127.0.0.1:18089:8089` だけ）、build の context（`app/splunk` の scratch の写し）を上書きした。Dockerfile と healthcheck と volume の形は compose.yaml のまま。`docker/compose/up.sh` そのものは打っていない（**up.sh 経由の再作成は未確認**）。
4. **C は判定が本物と合ったので `check.sh` の式は変えず、コメントと check を足した。**
   設計は「合わなければ直し、期待も合わせる」。合ったので式は変えていないが、コメントの「本物の 401 の本文は未確認」が古くなったので本物の形に直し、本物の本文での check を 1 つ足した（式が本物の形から外れたら落ちるように）。
5. **設計の変更対象に無い `compose.yaml` のコメント 2 か所と `docs/troubleshooting.md:380` を直した。**
   どちらも B で確かめた事実と食い違う「未確認」「再現はしていない」の文。コメントと文だけ。
   compose.yaml の「Apple Silicon は未確認」は 034 の前から古かった（`tests/check_splunk_image.py:17` に 2026-10-04 の arm64 の Mac で healthy まで 80 秒の記録がある。034 の 120〜165 秒は compose の healthcheck での値）。
6. **README の B の直し方に、`down.sh -v` のほかに Splunk だけを消す手順を書いた。**
   設計は「`down.sh -v` を書く」。`down.sh -v` は Kafka・OpenSearch・Prometheus・Grafana・Spark の checkpoint も消すので、`splunk-etc` だけを消して上げ直す手順を確かめて（変更が写り、admin で検索が通った）併記した。
7. **A の確かめは window を `10m` でなく `1h`（入れる値そのもの）で打った。**
   設計の手順 3 は「`10m` で立て直して 204 なら `1h` を足す」。入れる値で確かめるほうが直接なので `1h` で打った。

### A: Prometheus の逆順サンプル

scratch の `ooo.py`（`app/spark/snmp_sinks.py` の `prometheus_series` / `encode_write_request` / `snappy_compress` / `make_prometheus_sender` を使う）で、同じ系列に `t=now`、`t=now-60s` の順に remote write した。Prometheus は `prom/prometheus:v3.15.0`（compose.yaml と同じ版）を単体で、`--web.enable-remote-write-receiver` を付けて立てた。

既定の設定（window 0）:

```
1st POST: (204, '')
2nd POST: (400, 'out of order sample\n')
```

Prometheus のログ: `msg="Out of order sample from remote write" err="out of order sample"`。Spark の送り手（`snmp_sinks.py:802`）は 4xx を打ち直さずに捨てる。

`prometheus.yml` に `storage.tsdb.out_of_order_time_window: 1h`:

```
1st POST: (204, '')
2nd POST: (204, '')
count_over_time(...) = 2
```

前の設定で書いた volume のまま（`nwc034-promdata`）: 前の設定で 2 回目が `400`、同じ volume で新しい設定に起こし直して 2 回とも `204`、件数 3（前の設定の 1 件目を含む）。volume を消さなくてよいことを README に書いた。

`promtool check config`（repo の `prometheus.yml`）:

```
Checking /etc/p.yml
 SUCCESS: /etc/p.yml is valid prometheus config file syntax
```

### B: Splunk のアプリと volume

- 起動: Splunk 10.4.4（`platform: linux/amd64`、エミュレーション）。`healthy` まで 121 秒 / 120 秒 / 165 秒（3 回）。Ansible の PLAY RECAP は `ok=90 failed=0`。立ったので「Mac では未確認」にはしていない。
- `app/splunk` の scratch の写しの `nwc_alerts/default/savedsearches.conf` に `# nwc034-marker-B` を 1 行足し、build し直して `up -d`（コンテナは作り直された）。
  - イメージの `/opt/splunk-etc/apps/nwc_alerts/default/savedsearches.conf` の marker: 1 件
  - volume の `/opt/splunk/etc/apps/nwc_alerts/default/savedsearches.conf` の marker: 0 件
  - 起動のログに「Update /opt/splunk/etc」の task は出たが、写していない。上流の `/sbin/updateetc.sh` はイメージと volume の `splunk.version` の sha が違うときだけ写す
- コンテナと `nwc034_splunk-etc` だけを消して上げ直すと、volume の marker 1 件、admin で `index=_internal | head 1 | stats count` が `count` 1（`splunk-var` は残した）。

### C: check.sh の Splunk の判定

check.sh と同じ curl（`/services/search/jobs/export`、`output_mode=json`、パスワードは `curl -K -` で渡し、表示していない）の本文を scratch に写し、check.sh の判定の式（`judge.py` が check.sh から抜き出して当てる）を当てた。

| 打ち方 | HTTP | 本文 | 判定 |
|---|---|---|---|
| パスワード違い | 401 | `{"messages":[{"type":"ERROR","text":"Unauthorized"}]}` | `ERROR Unauthorized` |
| check.sh の検索（まだ何も入っていない） | 200 | `{"preview":false,"offset":0,"lastrow":true,"result":{"count":"0"}}` | `0 件` |
| 無い index | 200 | 同上（`"count":"0"`） | `0 件` |
| `index=_internal \| head 1 \| stats count` | 200 | 同上で `"count":"1"` | `ok` |
| `\| nosuchcmd` | 400 | `{"messages":[{"type":"FATAL","text":"Unknown search command 'nosuchcmd'."}]}` | `FATAL Unknown search command 'nosuchcmd'.` |
| `eval` の引数の誤り、無い lookup、`rest /services/nosuch` | 200 | 空 | `読めない応答: 空` |
| 無い index への `collect` | 200 | `"count":"1"`、`messages` 無し | `ok` |

`result` と `messages` の `ERROR` が 1 つの応答に混ざる形は、試した打ち方（失敗する subsearch、無い index への `collect` など）では出なかった（**本物では未確認**。テストの偽の応答でだけ縛っている）。

### 後片付け（docker）

`docker compose -p nwc034 … down -v`（コンテナ・volume 2 つ・ネットワーク）、`docker rmi nwc034-splunk`、Prometheus のコンテナ（`docker rm -f -v`）と `nwc034-promdata` の `docker volume rm`。`docker ps -a` / `docker volume ls` / `docker network ls` を `nwc034` と `nwc-local` で grep して何も出ない（exit 1）。volume の数は 24 で、始める前と同じ（ほかのセッションのものには触っていない）。

### テスト（2026-10-10、セルフレビューの直しのあとに打ち直した）

```
$ uv run --frozen python3 tests/test_local_compose.py 2>&1 | tail -1
通過 144 / 失敗 0
```

全部の `tests/test_*.py`（1 本ずつ）:

```
tests/test_agentcore.py: 通過 162 / 失敗 0
tests/test_alerts.py: 通過 168 / 失敗 0
tests/test_analytics.py: 通過 528 / 失敗 0
tests/test_collectors.py: 通過 79 / 失敗 0
tests/test_dashboard_config.py: 通過 3 / 失敗 0
tests/test_graph.py: 通過 78 / 失敗 0
tests/test_kb_index.py: 通過 7 / 失敗 0
tests/test_lab_debug.py: 通過 110 / 失敗 0
tests/test_local_compose.py: 通過 144 / 失敗 0
tests/test_nautobot.py: 68 項目すべて通過
tests/test_oss.py: 通過 174 / 失敗 0
tests/test_oss_ops.py: 通過 203 / 失敗 0
tests/test_oss_roll.py: 通過 66 / 失敗 0
tests/test_stream.py: 通過 108 / 失敗 0
tests/test_sync.py: 通過 103 / 失敗 0
tests/test_workflow.py: 通過 333 / 失敗 0
```

`test_nautobot.py` は最初に `--group web` 無しで打つと `ModuleNotFoundError: No module named 'gradio'` だった。docstring の打ち方（`uv run --frozen --group dev --group web python3 tests/test_nautobot.py` → `68 項目すべて通過`）で一度打ってからは、上のとおり通る。034 の変更とは関係しない。

### 検証方法との突き合わせ

- A: 2 回目の POST は `400` `out of order sample`、window を足して `204`（上の A）。足したのは compose.yaml でなく prometheus.yml（設計との差 1）
- B / C: 立った。起動の時間と、写らない事実と、curl の応答の形（上の B と C）
- `tests/test_local_compose.py`: `通過 144 / 失敗 0`
- docs: `docker/compose/README.md` の `## 確かめたこと` に 3 件とも「確かめた日と版」で書いた。本物で出せなかった形（result と ERROR の混在）は「出なかった」と書いた

### セルフレビュー

- 自分: opus-5.5 / effort: high（スキルの既定の xhigh には切り替えていない。サブエージェントとして動いていて、セッションの effort を変えられないため）
- 反対弁護人: Agent（general-purpose、model opus、読み取り専用。設計の意図、方針、不安な箇所を渡した）。返ってきたあと `git status --porcelain -uall` は `?? docs/cycles/034-compose-unverified/build.md` だけで、増えたものは無い

#### 退行の注入（実測）

止めずに全部の check を打つ scratch の `nostop.py`（`assert` を表示に置き換えて実行するだけ）で、check.sh に退行を 1 つずつ入れて戻した。

- `prometheus.yml` の window を `0s`: `AssertionError: prometheus.yml は同じ系列の時刻が戻るサンプル…`（320 行の check が落ちる）
- check.sh の `int(d['result'].get('count', 0))` から `int` を外す: 新しい check（`Splunk 10.4.4 の本物の応答…`）と、既存の 10 個が FAIL
- check.sh の `('FATAL', 'ERROR')` を `('FATAL',)`: 新しい check と、既存の 2 つ（認証の失敗、理由の重複）が FAIL
- 戻したあとの `git status --porcelain -uall` は空（build.md を書く前）

#### 指摘と片付け

1. **Should fix**: [設計整合 / runtime] `docker/compose/README.md` の Splunk だけを直す手順
   - 破綻シナリオ: 「確かめた」の節にあるが、実際に打ったのは別のプロジェクト名の `compose up` で、`nwc-local` での `rm -s -f splunk` や `up.sh` は打っていない。Splunk が戻るまで `spark-splunk` が HEC に書けず止まりうるのも書いていない
   - 片付け: **直した。** 確かめた範囲と打っていない範囲、消えるもの（Web で作ったもの、`local/`）、`spark-splunk` が止まっていたら `up.sh`（引数なし）を書いた。`rm` の依存元への効き方と up.sh 経由は**未確認**のまま
2. **Nit**: [data loss / docs] 同じ手順で消えるものが書かれていない → 1 と一緒に**直した**。古い `splunk-var` と新しい `etc` での KV store の状態は**未確認**（nwc_alerts は collections.conf を持たない）
3. **Nit**: [docs] `docker/compose/check.sh:90` のコメントが「検索の書き誤りは 400 FATAL」と一般化しすぎ（eval の引数の誤りや無い lookup は 200 の空の本文） → **直した**
4. **Nit**: [docs] `docker/compose/prometheus.yml:3` と README が「Kafka の追い付きで古い分が後から届く」を事実として書いている。パイプラインの中で逆順が起きるのは再現していない（設計の「調査で分かった事実」の前提） → **直していない**（最終報告に回す）
5. **Nit**: [docs] README の「1 時間より古いサンプルは今までどおり捨てる」の基準が曖昧 → **直した**（「Prometheus が持っている最新の時刻より 1 時間以上古い」）
6. **Nit**: [runtime / 未確認] README の `restart prometheus` は、書いたとおりには打っていない → **直した**（同じ volume でコンテナを作り直した形で確かめたと書いた）。`restart` で bind mount のファイルが読み直されるか、spark-http が待ちで落ちないかは**未確認**
7. **Nit**: [docs] `docker/compose/README.md:61` の「Splunk のパスワードと HEC の token が同じかは未確認」に、034 の節への参照が無い → **直していない**（範囲外。`.env` だけ変えて volume を残したときの挙動は未確認のまま）
8. **Nit**: [docs] compose.yaml の「Apple Silicon は未確認」は 034 の前から古かった（`tests/check_splunk_image.py:17`） → 設計との差 5 に書き足した
9. **Nit**: [missing tests] 新しい check の退行を拾う力は既存の check とほぼ重なる（上の注入でも既存が一緒に落ちる） → **直していない**（本物の形の記録として残す）
10. **Nit**: [設計整合] build.md にセルフレビューが無い → この節で片付けた

反対弁護人が「コードを読んだだけ」とした空の本文の経路は実測した。check.sh の判定式に本物の空の本文が届く形（`"\n"`）とテストの形（`" \n"`）を当てて、どちらも `読めない応答: 空`。

#### 「問題なし」とした観点と根拠

- 設定の正しさ: `promtool check config`（v3.15.0）が repo の `prometheus.yml` で `SUCCESS`（`global:` が空でも可）
- 起動の引数が無いこと: 反対弁護人が v3.15.0 に `--storage.tsdb.out_of_order_time_window=1h` を渡して `unknown long flag` を確かめた
- volume の名前 `nwc-local_splunk-etc`: `compose.yaml:16` の `name: nwc-local` と `:319` の `splunk-etc`
- check.sh の構文: `bash -n docker/compose/check.sh` が通る
- security: パスワードは `curl -K -` で渡し、build.md / README / scratch の本文に出していない（401 の本文にもパスワードは入らない）
- Must fix: 0
