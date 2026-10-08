# Splunk と Grafana のアラートを比べる

← [README](../README.md)

サイクル「Splunk と Grafana のアラートを比べる（002）」の結果を書く場所。設計は [cycles/002-alert-parity-splunk-grafana/design.md](cycles/002-alert-parity-splunk-grafana/design.md)。

2026-10-09（サイクル「gNMI を gnmic に移し、SNMP のポーリングと telegraf-dialin を外す（013）」）から、どちらの `link_down` も gNMI の IF の状態から出す（Grafana は `snmp_interface_oper_up`、Splunk は保存済みサーチ `netops_gnmi`）。保存済みサーチ `netops_poll` と `SNMP_POLL` は無くなった。下の結果は SNMP のポーリングで測った当時のもの。

## 結論

- 4 種類のアラート（`link_down` / `bgp_down` / `isis_down` / `trap`）を、Grafana と Splunk の両方が出すようにした。
- ルールはどれも書けた。ただし Grafana の 3 つは「条件つき」で、Spark の側でデータの形を整える必要があった。
- **検知の遅れと取りこぼしの比較は、1 回分だけ結果がある**（2026-10-05、`fail-main`）。両方が `link_down` と `isis_down` を出し、取りこぼしは無かった。`link_down` は Splunk のほうが 1〜3 分早かった。手順どおりの 3 回と、`bgp_down`・`trap` はまだ。それまでこのサイクルは完了にしない。

理由と背景:

- これまでは `link_down` を Grafana が、ほかの 3 つを Splunk が出していた。同じ障害を両方で見ないと、遅れも取りこぼしも比べられない。
- 既定の設定で両方が動く。`STORES` の既定は `s3,grafana,splunk`、`SNMP_POLL` の既定は `1`（[deploy.md](deploy.md)）（測った当時。2026-10-09 から `SNMP_POLL` は無い）。

メリットとデメリット:

| | 中身 |
|---|---|
| メリット | 同じ障害で 2 つの送り手を比べられる。片方が止まっても、もう片方がアラートを出す |
| デメリット | Splunk の分の費用が既定で乗る。同じ異常の通知が 2〜3 通届く（受け手は同じ異常の id にまとめる） |

## 1. できること・できないこと

「書けた」は取り込んだ形のままルールが書けたもの。「条件つき」は取り込みの前に整形が要る、または制約が残るもの。「書けない」はルールにしていないもの。

| `kind` | 見るデータ | Grafana | Splunk |
|---|---|---|---|
| `link_down` | SNMP のポーリングの `ifOperStatus` | 書けた（ルール `link_down`） | 書けた（`netops_poll`） |
| `link_down` | linkDown / linkUp の trap | 書けない | 書けた（`netops_trap`） |
| `bgp_down` | gNMI の BGP の `session_state` | 条件つき（ルール `bgp_down`） | 書けた（`netops_gnmi`） |
| `isis_down` | gNMI の IS-IS の IF の `oper_state` | 条件つき（ルール `isis_down`） | 書けた（`netops_gnmi`） |
| `trap` | linkDown / linkUp 以外の trap | 条件つき（ルール `trap`） | 書けた（`netops_trap` と `netops_trap_clear`） |

理由と、取り込みで要る整形:

| 行 | Grafana | Splunk |
|---|---|---|
| `link_down`（ポーリング） | 整形は要らない。値が数（2 = down）で、Telegraf が `sysName` と `ifName` を付けている。admin down の IF は PromQL の `unless` で外す | 整形は要らない。ただし Splunk は「いまの状態」を持たないので、サーチが前の値と比べて変わった IF だけを出す。そのために 11 分ぶんを読む |
| `link_down`（trap） | linkDown の trap は IF 名を入れた項目の名前が IF ごとに変わる。OpenSearch のクエリ 1 本でまとめられないので、ルールにしていない | 整形は要らない。SPL が項目の名前を前方一致で拾う |
| `bgp_down` / `isis_down` | 状態が文字列（`established` / `up` など）で、Prometheus に入らない。Spark が 1（正常）/ 0（それ以外）の系列に直す。機器名も無いので、Spark が device map で `sysName` を足す | 整形は要らない。文字列のまま読む。送り元の IP は、アラートアクションが `DEVICE_MAP` で機器名に直す |
| `trap` | 機器名が無いので、Spark が OpenSearch の文書に `tags.sysName` を足す。まとめる数に上限がある（機器 50 × OID 20） | 整形は要らない。解消を出すためのサーチ（`netops_trap_clear`）がもう 1 本要る |

Spark の整形の中身（`app/spark/snmp_sinks.py`）:

| 整形 | 中身 | 行き先 |
|---|---|---|
| 状態を 1 / 0 にする | `bgp_neighbor` の `session_state` から `snmp_bgp_neighbor_session_up`（`established` なら 1）、`isis_interface` の `oper_state` から `snmp_isis_interface_oper_up`（`up` なら 1）を作る | Prometheus |
| `sysName` を足す | レコードに `sysName` が無ければ、送り元の IP を device map（`--device-map`）で引いて足す | Prometheus と OpenSearch |

Splunk へ送るイベントは変えていない。

## 2. 手順

AWS に `PIPELINE=1`（`STORES` と `SNMP_POLL` は既定のまま）で立てた環境で、lab の EC2 に入って打つ（入り方は [pipeline.md](pipeline.md) の「lab に入る」）。

1 回の流れは「障害を入れる → 2 分待つ → 戻す → 2 分待つ」。これを種類ごとに 3 回くり返す。**コマンドを打つ直前の時刻（UTC）を控える。**

```bash
date -u +%FT%TZ
```

| 種類 | 入れる | 戻す | 出るはずの異常の id |
|---|---|---|---|
| `link_down` と `isis_down` | `sudo lab fail-main` | `sudo lab heal-main` | `dc1-a-leaf-01#link_down#ethernet-1/1` と、`dc1-a-leaf-01` の `isis_down` |
| `bgp_down` | `sudo lab fail-bgp` | `sudo lab heal-bgp` | `dc1-a-leaf-01#bgp_down#10.255.0.1` と、`dc1-spine-01` の `bgp_down`（相手は `dc1-a-leaf-01` のループバック） |
| `trap` | `sudo lab trap-test` | 無い（下） | `dc1-trex-01#trap#.1.3.6.1.4.1.8072.2.3.0.1` |

- `fail-main`

  `dc1-a-leaf-01` の `ethernet-1/1`（`dc1-spine-01` との fabric）を落とす。admin-state は enable のままなので、機器からは回線断に見える。IS-IS の隣接も落ちる。iBGP はループバック同士なので落ちない。
- `fail-bgp`

  `dc1-a-leaf-01` から `dc1-spine-01`（`10.255.0.1`）への iBGP の隣接 1 本を、admin-state を disable にして止める。回線は落とさない。両側のセッションが `established` でなくなる。TRex のポートのあいだの mac-vrf は `dc1-spine-02` 経由で通ったまま。
- `heal-bgp`

  `established` に戻るまで数十秒かかる。`sudo lab check` で見る。
- `trap-test`

  link でも起動の知らせでもない trap（`netSnmpExampleHeartbeatNotification`）を 1 通、`dc1-trex-01` の管理 IP から送る。戻すコマンドは無い。次の trap が来なければ、およそ 10 分後に両方が解消を出す。**次の回は解消が出てから打つ**（13 分ほど空ける）。10 分以内に打つと、解消が先へ延びる。

待つ時間を 2 分にした根拠（[pipeline.md](pipeline.md) の「アラート」の表）:

- Grafana は、Spark のマイクロバッチ 60 秒とルールの評価 1 分。
- Splunk は、Spark のマイクロバッチ 60 秒と保存済みサーチの最大 70 秒ほど。

## 3. Athena のクエリ

「アラートの履歴を残す（001）」で作った履歴を読む。**下のクエリそのものは、まだ実行していない**（2026-10-05 の動作確認では、異常の id・`status`・`source` ごとに最初の `received_at` を取るだけの集計を打った）。

読むのは S3 Tables の `alert_events`。中身は、Lambda `<prefix>-graph-status` が受けたアラートの通知 1 件ごとの行（送り手 `source`、異常の id `anomaly_id`、`status`、説明 `detail`、送り手の `starts_at`、Lambda が受けた時刻 `received_at`）。Athena のワークグループは `<prefix>-history`、カタログは `s3tablescatalog/<テーブルバケットの名前>`（`IaC/terraform/aws-managed/pipeline/analytics/history.tf`）。

1 回の試行ごとに、控えた時刻で範囲を絞って打つ。

```sql
WITH e AS (
  -- Lambda のやり直しで二重に入った行を event_id で落とす
  SELECT * FROM (
    SELECT *, row_number() OVER (PARTITION BY event_id ORDER BY received_at) AS rn
    FROM "s3tablescatalog/<テーブルバケットの名前>"."netops"."alert_events"
    WHERE received_at BETWEEN TIMESTAMP '2026-10-05 01:00:00 UTC' AND TIMESTAMP '2026-10-05 01:05:00 UTC'
  ) WHERE rn = 1
),
f AS (
  SELECT anomaly_id, status,
         min(received_at) FILTER (WHERE source = 'grafana') AS grafana_first,
         min(received_at) FILTER (WHERE source = 'splunk') AS splunk_first,
         min(received_at) FILTER (WHERE source = 'splunk' AND detail LIKE '%(splunk: poll)') AS splunk_poll_first,
         min(received_at) FILTER (WHERE source = 'splunk' AND detail LIKE '%trap)') AS splunk_trap_first
  FROM e
  GROUP BY anomaly_id, status
)
SELECT anomaly_id, status, grafana_first, splunk_first, splunk_poll_first, splunk_trap_first,
       date_diff('second', grafana_first, splunk_first) AS splunk_minus_grafana_sec,
       CASE WHEN grafana_first IS NULL THEN 'grafana が取りこぼし'
            WHEN splunk_first IS NULL THEN 'splunk が取りこぼし'
            ELSE '' END AS missed
FROM f
ORDER BY anomaly_id, status
```

読み方:

| 列 | 意味 |
|---|---|
| `grafana_first` / `splunk_first` | その異常・その `status` の通知を、送り手ごとに最初に受けた時刻 |
| `splunk_minus_grafana_sec` | 差（秒）。正なら Splunk のほうが遅い |
| `missed` | 片方にしか行が無い。取りこぼし |
| `splunk_poll_first` / `splunk_trap_first` | `link_down` を、Splunk のポーリング（`netops_poll`）と trap（`netops_trap`）に分けた時刻 |

- 入れた時刻からの遅れは、控えた時刻と `grafana_first` / `splunk_first` の差で出す。
- ポーリングと trap は `detail` の末尾で分かれる。Grafana は `(grafana: poll)` / `(grafana: gnmi)` / `(grafana: trap)`。Splunk は `(splunk: poll)` / `(splunk: linkDown trap)` / `(splunk: linkUp trap)` / `(splunk: gnmi)` / `(splunk: trap)`。
- Grafana は直らないあいだ 4 時間ごとに同じ通知を送り直す。最初の時刻だけを取るので、結果には効かない。

## 4. 結果

**1 回分だけある。手順どおりの 3 回はまだ。**

### 2026-10-05 の 1 回（AWS の全体の動作確認）

機器の名前は 2026-10-08 に lab を組み直す前のもの（`dc1-leaf-01` は今の `dc1-a-leaf-01`、`dc1-spine-01` は同じ）。

`sudo lab fail-main` を 02:36:59（UTC）に打った。遅れは、打ってから Lambda が最初の `firing` を受けるまでの秒。

| 異常の id | Grafana firing | Splunk firing | 差（Splunk − Grafana） | 取りこぼし |
|---|---|---|---|---|
| `dc1-leaf-01#link_down#ethernet-1/1` | 297 秒（02:41:56） | 121 秒（02:39:00） | −176 秒 | 無い |
| `dc1-spine-01#link_down#ethernet-1/3` | 297 秒（02:41:56） | 242 秒（02:41:01） | −55 秒 | 無い |
| `dc1-leaf-01#isis_down#ethernet-1/1.0` | 234 秒（02:40:53） | 242 秒（02:41:01） | +8 秒 | 無い |
| `dc1-spine-01#isis_down#ethernet-1/3.0` | 237 秒（02:40:56） | 242 秒（02:41:01） | +5 秒 | 無い |

分かったこと:

- どちらも、手順に書いた「2 分待つ」では足りなかった

  いちばん早い Splunk の `link_down` で 121 秒、Grafana の `link_down` は 297 秒かかった。3 回の計測をするときは、待つ時間を 5 分以上にする。
- Splunk だけが、サブインターフェースの `link_down` を出した

  `dc1-leaf-01#link_down#ethernet-1/1.0`（02:40:00）と `dc1-spine-01#link_down#ethernet-1/3.0`（02:42:01）。Grafana には対応する行が無い。異常の id が別なので、ワークフローも別に起きた（1 本の回線断で修復案が 4 件）。
- Splunk は起動の直後に `resolved` を大量に送った

  `alert_events` の `resolved` は Splunk が 83 行、Grafana が 4 行。Splunk の内訳は `bgp_down` 31、`isis_down` 46、`link_down` 6 で、`bgp_down` と `isis_down` は障害を入れていない相手の分。

この回で取れていないもの:

- `resolved` の遅れ。戻したのは承認からの `heal-main`（02:45:16）で、行はあるが時刻を控えていない。
- `link_down` の Splunk の内訳（ポーリングと trap）。
- `bgp_down`（`fail-bgp`）と `trap`（`trap-test`）。打っていない。

### 手順どおりの 3 回（未実施）

遅れは、コマンドを打ってから Lambda が通知を受けるまでの秒。

| 種類 | 回 | 入れた時刻（UTC） | Grafana firing | Splunk firing | Grafana resolved | Splunk resolved | 取りこぼし |
|---|---|---|---|---|---|---|---|
| `link_down` | 1 | | | | | | |
| `link_down` | 2 | | | | | | |
| `link_down` | 3 | | | | | | |
| `isis_down` | 1 | | | | | | |
| `isis_down` | 2 | | | | | | |
| `isis_down` | 3 | | | | | | |
| `bgp_down` | 1 | | | | | | |
| `bgp_down` | 2 | | | | | | |
| `bgp_down` | 3 | | | | | | |
| `trap` | 1 | | | | | | |
| `trap` | 2 | | | | | | |
| `trap` | 3 | | | | | | |

`link_down` の Splunk の内訳:

| 回 | ポーリング firing | trap firing | ポーリング resolved | trap resolved |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

## 既知の差と前提

結果を読むときに知っておくこと。どれも実装のファイルで確かめた。

### 送り手の数と時刻

- `link_down` は送り手が 3 つある

  Grafana のポーリング、Splunk の trap、Splunk のポーリング（`netops_poll`）。異常の id は同じだが、`starts_at` は 3 つとも違う。ワークフローは異常の id ごとに 1 つなので、ふつうは 1 つにまとまる。ただし、最初のワークフローが閉じたあとに遅れた 1 通が届くと、2 つ目が起きうる。実測はしていない。
- Splunk の `resolved` の `starts_at` は、直った時刻

  Grafana の `resolved` は、発火したときの `starts_at` を持ったまま来る。

### Grafana の側

- `resolved` の `detail` も、障害のときの文のまま

  通知のテンプレートがルールの注釈をそのまま出すので、解消の通知でも `ethernet-1/1 is down (grafana: poll)` と書かれる。解消かどうかは `status` で見る。
- `bgp_down` / `isis_down` は、最後の値を 24 時間持つ

  gNMI の on_change は変わったときにしか値が来ないので、ルールは `last_over_time(...[24h])` で最後の値を見る。設定から消した相手や IF は、最後の値が 0 のままなら最大 24 時間 firing で残る。逆に、落ちたまま 24 時間たつと系列が消えて解消が出る。
- `bgp_down` / `isis_down` の `detail` に、元の状態の文字列は出ない

  Prometheus にあるのは 1 / 0 だけ。`idle` や `active` の区別は Splunk の `detail`（`bgp session to 10.255.0.1 is idle (splunk: gnmi)` の形）で見る。
- `trap` のルール

  データが無いときは正常として扱う（`noDataState: OK`）。`KeepLast` だと、trap が 10 分の窓から出たあとも発火したままになる。まとめるのは `tags.sysName.keyword` と `tags.oid.keyword` で、数は機器 50 × OID 20 まで。プラグインが「組み合わせ × 時間の区切り」を 65535 までしか受けないため。解消は OID ごとで、その OID の trap が 10 分来なければ出る。Splunk の `netops_trap_clear` は機器ごとで、その機器から link 以外の trap が 10 分来なければ全部を閉じる。

### Spark の整形の副作用

- gNMI の全部の系列に `sysName` のラベルが付く

  対象は `sysName` を持たないレコード全部で、アラートに使う 2 つだけではない。ラベルが増えると Prometheus では別の系列になるので、切り替えの前後をまたぐ期間は、既存のダッシュボードで同じものが 2 本に見える。
- device map に無い送り元は、行き先で扱いが違う

  OpenSearch の文書は、送り元の IP をそのまま `tags.sysName` に入れる（Grafana の `trap` のルールが機器ごとにまとめる鍵が要るため）。Prometheus には足さない。

### Splunk の `netops_poll`（2026-10-09 にやめた）

- 毎分、索引に入った時刻で 11 分ぶんを読む

  判定するのは、いまの 1 分に値が届いた IF だけ。前の値は、その前の 10 分の最後の値。
- 10 分を超えて値が途切れると、前の値が無くなる

  Telegraf か Splunk が 10 分を超えて止まり、IF が落ちたままだと、戻ったときに新しい `starts_at` で `firing` をもう 1 回出す。
- どの回の「いまの 1 分」にも入らなかった変化は出ない

  Splunk が止まっているあいだの変化は、次に状態が変わるまで出ない。
- admin down は down に数えない

  落ちている IF を disable にすると、`resolved`（`detail` は `is admin down`）が出る。Grafana のルールも admin down を外す。
- ループバック、管理ポート、サブインタフェースは見ない

  Grafana のルールと同じ。

### AWS で未確認のこと

模擬テストと手元のコンテナ（Grafana 13.2.2、Splunk 10.4.3、Telegraf 1.40）で確かめたところまで。下は AWS で試していない。

| 項目 | 状態 |
|---|---|
| Grafana が OpenSearch Serverless を SigV4 でルールの評価に使えるか | 未確認。ダッシュボードで読めることは 2026-09-28 に確認済み |
| SR Linux が出す trap の OID の形（`.1.3.6.1.…` で入るか） | 未確認。手元では net-snmp の `snmptrap` で確かめた |
| `lab fail-bgp` / `lab heal-bgp` / `lab trap-test` の実際の動き | 未確認。lab の EC2 で打っていない |
| `lab trap-test` で `dc1-trex-01` が `ALARM` になるか | 未確認。送り元の IP が device map で `dc1-trex-01` に直る前提 |
| SR Linux の linkDown の trap に IF 名が載るか | 未確認。載らないと Splunk の trap の `target` が IF 名にならず、ポーリングの `link_down` と別の異常の id になる |
| `link_down` で 2 つ目のワークフローが起きるか | 2026-10-05 に確かめた。1 本の回線断でワークフローが 4 本起きた（「4. 結果」）。trap ではなく、Splunk がサブインターフェースの `link_down` も出すことと、回線の両端が別の異常になることによる |
| 上の Athena のクエリ | 未実行。`alert_events` を Athena で読めることは 2026-10-05 に確かめた |
