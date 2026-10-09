# 手元の compose の未確認を確かめる（034）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | 手元の Prometheus が spark-http の追い付きで逆順のサンプルを捨てないか確かめる（out_of_order_time_window が無い） | 006 のセルフレビュー U1 | 確かめて docs に書く |
| B | 手元の Splunk のアプリを作り直したとき splunk-etc の volume に写るか確かめる | 006 のセルフレビュー U2 | 確かめて docs に書く |
| C | 手元の check.sh の Splunk の判定を本物の応答で確かめる（401 の本文、messages に ERROR が混ざる応答） | 006 の Round 2 のセルフレビュー U1 / U2 | 確かめて直す |

3 件とも「手元の docker compose（`docker/compose/`）で確かめる」もの。結果が「問題なし」なら docs に書くだけ、問題があれば直す。Mac では Splunk のイメージが amd64 だけなので B と C は `--platform linux/amd64` で起動を試し、立たなければ「未確認」のまま docs に書く。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- A: Spark の http ジョブ（`app/spark/snmp_sinks.py:699` の `prometheus_series`、`:769` の `snappy_compress`）が remote write で Prometheus に書く。Kafka の追い付き（lag が溜まった状態からの再開）では、1 つの series に古い timestamp のサンプルが新しいものの後に来ることがある。Prometheus は `out_of_order_time_window`（`storage.tsdb` の設定）が 0 のとき、同じ series の逆順サンプルを `out of order sample` として 400 で捨てる。`docker/compose/compose.yaml` の prometheus に `--storage.tsdb.out_of_order_time_window` の指定が無い（実装で確かめる）。
- B: `docker/compose/compose.yaml` の splunk は `platform: linux/amd64`（`tests/test_local_compose.py:99`）、named volume `splunk-etc` / `splunk-var`。Splunk のイメージは起動時に `/opt/splunk/etc/apps/<app>` を `SPLUNK_APPS_URL` か bind から入れる。`splunk-etc` が named volume なので、2 回目以降の起動でアプリを作り直しても volume の古い版が勝つ可能性（これが U2）。`docker/compose/down.sh -v` で volume ごと消す。
- C: `docker/compose/check.sh` の Splunk の判定（実装で行番号を確かめる）は REST の `/services/search/jobs/export` か `/services/server/info` の応答を見て、401 の本文の形と `messages` に ERROR が混ざるかで ok / ng を決めている。本物の応答で確かめていない（006 の Round 2 の U1 / U2）。`tests/test_local_compose.py:686-695` が check.sh の 15 項目の ok と docker コマンド列を縛る。
- Mac で Splunk が立つかは未確認（`splunk-volume-leak` の記憶: 手元の splunk コンテナは `docker rm -f -v` で消す。溜まって 5 GB を切ると検索が止まる）。

## 設計方針

**A. Prometheus の逆順サンプル（手元で再現）**

1. Prometheus を単体で立てる（`docker run --rm -p 9090:9090 prom/prometheus:<compose.yaml と同じ版> --web.enable-remote-write-receiver`）。
2. `prometheus_series` / `snappy_compress` を流用した小さなスクリプト（`tests/` ではなく scratch。repo には入れない）で、同じ series に `t=now`、`t=now-60s` の順に remote write する。
3. 2 回目の POST が `400` で本文に `out of order sample` を含めば「捨てる」。`storage.tsdb.out_of_order_time_window=10m` を付けて立て直して `204` になれば、`docker/compose/prometheus.yml` の `storage.tsdb.out_of_order_time_window: 1h`（追い付きの最大を 1 時間と見積もる）を足す。捨てないなら何もしない。（実装で分かった事実: Prometheus v3.15.0 にはこれを変える起動の引数が無く、設定ファイルにだけ書ける。cold review が `--help` で確かめた。レビュー後に設計を正本に合わせて書き換えた）
4. どちらでも `docs/local-compose.md`（手元の compose の docs。実物の名前は実装で確かめる）に「追い付きの逆順サンプルは…（確かめた日、Prometheus の版、結果）」を書く。

**B. Splunk のアプリと volume（Mac で立てば）**

1. `docker compose up splunk`（`--platform linux/amd64` は compose.yaml にある）。立たなければ（Rosetta / QEMU のエラー、起動が 10 分で終わらない）ここで止め、docs に「Mac では未確認（理由）」。
2. 立ったら `app/splunk/` のアプリの 1 ファイル（例: `default/savedsearches.conf` のコメント）を変え、イメージを build し直して `docker compose up -d splunk`。コンテナの中の `/opt/splunk/etc/apps/<app>/default/…` に変更が写るかを見る。
3. 写らないなら `docs/local-compose.md` に「アプリを作り直したら `down.sh -v`（volume ごと消す）」を書き、`docker/compose/README` にも 1 行。写るなら「写る」と書く。コードは変えない。

**C. check.sh の Splunk の判定（Mac で立てば）**

1. B で立った Splunk に `curl -sk -u admin:<wrong>` で 401 の本文を取り、check.sh の判定（本文の形）と合うか。`curl -sk -u admin:<right> …/services/search/jobs/export -d search='search index=_internal | head 1' -d output_mode=json` で件数が返り、`messages` に `ERROR` が混ざらないか。
2. 判定が本物と合わなければ check.sh を直し、`tests/test_local_compose.py:686-695` の期待も合わせる。
3. 結果を docs に書く。立たなければ「未確認」のまま。

**やらないこと。** Splunk の版を上げない。compose.yaml に Mac 用の分岐を足さない。A のスクリプトを repo に入れない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `docker/compose/prometheus.yml` | A | `storage.tsdb.out_of_order_time_window: 1h`（捨てる場合だけ。起動の引数は無いので設定ファイルに） |
| `docker/compose/check.sh` | C | 判定（合わない場合だけ） |
| `tests/test_local_compose.py` | A, C | 変えた分の check |
| 手元の compose の docs（`docs/local-compose.md` 相当）と `docker/compose/README` | A, B, C | 確かめた結果（日付、版、結果、未確認ならその理由） |
| `docs/cycles/BACKLOG.md` | — | PM が書く（エンジニアは触らない） |

## 再利用するもの

- `app/spark/snmp_sinks.py` の `prometheus_series` / `snappy_compress`。
- `docker/compose/check.sh` の Splunk の curl。

## 実装ステップ

1. A。結果に応じて compose.yaml と check。docs。1 commit。
2. B と C。立った / 立たないの事実、変えたものがあれば check。docs。1 commit。
3. `build.md`（頭は `実装モデル: opus-5.5 / effort: high`。A の POST の応答（status と本文）、B の起動ログの要点、C の curl の応答（パスワードは伏せる）、テストの出力、セルフレビュー）。

## 検証方法（期待出力まで）

- A: scratch のスクリプトの 2 回目の POST の status と本文を build.md に貼る。`400` + `out of order sample` なら compose.yaml に window を足し、同じスクリプトで `204`。
- B / C: `docker compose up splunk` の結果（立った / 立たない）と理由。立ったときは curl の応答の形。
- `uv run --frozen python3 tests/test_local_compose.py` が `失敗 0`（件数は実測）。
- docs に 3 件の結果が「確かめた（日付、版）」か「未確認（理由）」で書かれている。

## 未確定事項とリスク

1. Mac で Splunk が立たない可能性が高い（amd64 のみ）。その場合 B / C は「未確認」のまま docs に書き、BACKLOG の行は PM が「WSL で確かめる」に書き換えて残す。
2. A の Prometheus の挙動は版で変わる（2.39 以降で out-of-order の受け入れが入った）。compose.yaml の版で確かめ、docs に版を書く。
3. Splunk のコンテナと volume は確かめたあと `docker rm -f -v` で消す（`splunk-volume-leak`）。
