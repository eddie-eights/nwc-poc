# NLB の受信の説明を 037 に合わせる（038）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/038-nlb-ingress-docs（origin/main df18d07 から）。コメントと docs だけ。AWS には触っていない。

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/security_groups.tf`（OSS 版はシンボリックリンク）: 115-117 行目のコメント。「NLB は lab の SG からも udp 5140 / 2055 / 6343 を受ける（EC2 自身が試しに送るぶん）。lab の SG は 162/udp も NLB へ送るが、NLB は trap を lab の SG から受けない」を足した
- `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf`（同）: 20-21 行目のコメント。telegraf_dialout_nlb の受信に「lab の SG からも udp 5140 / 2055 / 6343（trap の 162 は受けない）」
- `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf`（同）: 15-17 行目のコメント。NetFlow / sFlow も同じ形（udp 2055 / 6343）と、NLB が lab の SG からも 5140 / 2055 / 6343 を受けること（logger / ops/netflow_send.py。162 は受けない）
- `docs/architecture/core.md`: SG の原則の箇条に例外 1 行（NetFlow / sFlow / syslog は両側。162 は受けない）、「経緯」に `2026-10-10（037）` の行
- `docs/architecture/resources/vpc-perimeter.md`: 「経緯」に `2026-10-10` の行
- `docs/pipeline.md:107`、`docs/collection.md:33`、`docs/deploy.md:96`: syslog も lab の EC2 から `logger` で試せる、と troubleshooting.md の「syslog の試験行が logs に入らない」の「正しい送り方」への参照
- `docs/troubleshooting.md:140`: `RETURN` が MASQUERADE より上に無いと trap は NLB で落ち、syslog は送り元が EC2 の IP で届く、の 1 文と pipeline.md への参照
- `docs/pipeline.md:348-350`: `forward-status` の行の下に、`RETURN` が崩れたときの壊れ方（trap / syslog の `tags.source` / RFC3164 の受け口なら `sysName` も）

### 設計からの逸脱

- 行番号: `security_groups.tf` の該当コメントは 106-107 ではなく 115-116 だった（036 の workflow の行が上に入ったため）。中身は設計どおり。
- 設計方針 5 / 検証 5 の「037 が `docs/pipeline.md` に書いた `logger` の手順」は、実際は `docs/troubleshooting.md` の「syslog の試験行が logs に入らない」→「正しい送り方」にあり、pipeline.md には `logger` が 0 件だった。3 ファイルともそこを指した（pipeline.md だけはコマンドの形を 1 行で引用）。
- 設計方針 6 の文面（「syslog / NetFlow / sFlow は送り元が EC2 の IP で届き、Spark とエージェントは別の機器として入れる」）は実装と合わないので変えた（セルフレビューの M1）。trap は NLB で落ちる（lab → NLB の 162 は `only = "egress"`、`security_groups.tf:122` / `:175`）。syslog の機器名は送り元ではなく HOST（`app/syslog-ng/syslog-ng.conf.in:14` `keep-hostname(yes)`、`:56` `tags.sysName=$HOST`）で、送り元が変わるのは `tags.source`。既定の RFC3164 で SR Linux（RFC5424）を受けるときだけ HOST が送り元の IP で埋まる（`docs/troubleshooting.md:408`）。lab の機器は NetFlow / sFlow を出さないので書かない。
- 設計のリスク 3 に従い、詳しい壊れ方は `docs/pipeline.md` の `forward-status` の行（348-350）に置き、表のセルは 1 文とそこへの参照にした。
- 検証 3 の grep は 2 件（lab/telegraf.tf:15、security_groups.tf:115）。stream/telegraf.tf:20 は「CIDR から udp 162 …を受け」の語順で pattern に当たらないので、`-A1` で別に見た（下の出力）。
- `lab.sh` の行番号（設計方針 6）は今も 173 / 334 / 340 で合っていた。

### 検証

1. `./ops/check.sh`（セルフレビューの直しのあと、最後の編集のあとに取り直した。直しの前も同じ件数）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし
== 2. 10 のルートの validate … 20 ルートとも OK
== 3. スクリプトの構文
構文エラーなし
== 4. 模擬テスト
通過 168 / 失敗 0 通過 168 / 失敗 0 通過 549 / 失敗 0 通過 79 / 失敗 0 通過 3 / 失敗 0 通過 78 / 失敗 0 通過 7 / 失敗 0 通過 110 / 失敗 0 通過 145 / 失敗 0 68 項目すべて通過 通過 177 / 失敗 0 通過 207 / 失敗 0 通過 66 / 失敗 0 通過 114 / 失敗 0 通過 104 / 失敗 0 通過 355 / 失敗 0
== 5. 旧名 netops が戻っていない
netops なし（許した 3 ファイル 5 行だけ）
すべて通過
```

設計の期待「通過 333」は 037 の時点の最後の組。036 の取り込みで 355 / 207 に増えており、036 の build.md:269 の件数と全部一致（このサイクルで模擬テストの数は変わっていない）。

2. `.tf` の差分がコメント行だけ:

```
$ git diff -U0 main -- '*.tf' | grep '^[+-][^+-]' | grep -v '^[+-][[:space:]]*#' | wc -l
       0
```

コマンドが効くことの確認: lab/telegraf.tf の末尾に `locals { injected = 1 }` を足すと `+locals { injected = 1 }` が出た。元に戻して 0。

3. `管理ネットワークの CIDR から受け`:

```
IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:15:#   trap / syslog  lab の EC2 → NLB の SG（udp 162 / 5140）、NLB は管理ネットワークの CIDR から受ける（送り元が機器の管理 IP のままなので）。
IaC/terraform/aws-managed/base/core/security_groups.tf:115:      # trap・syslog・NetFlow・sFlow: 機器 → lab の EC2（lab.sh forward の DNAT）→ NLB。送り元は機器の管理 IP のままなので、NLB は管理ネットワークの CIDR から受け、
IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:20:#   telegraf_dialout_nlb  管理ネットワークの CIDR から udp 162 / 5140 / 2055 / 6343 を受け（送り元が機器の管理 IP のまま）、lab の SG からも udp 5140 / 2055 / 6343 を
IaC/terraform/aws-managed/pipeline/stream/telegraf.tf-21-#                         受け（lab の EC2 自身が試しに送るぶん。trap の 162 は lab の SG から受けない）、telegraf_dialout へ udp 1162 と
```

lab/telegraf.tf:16 と security_groups.tf:116 が直後の行で「lab の SG からも udp 5140 / 2055 / 6343 を受ける」。

4. `grep -n '037' docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md`:

```
docs/architecture/core.md:111:- 2026-10-10（037）: lab の EC2 から NLB（telegraf_dialout_nlb）の 5140/udp の受信を足した（EC2 自身が `logger` で試せるように。機器の syslog は前から CIDR で受ける）。
docs/architecture/resources/vpc-perimeter.md:100:  例外は EC2 自身が試しに送る NetFlow / sFlow / syslog。両側に書く（syslog は「lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）」から）。
docs/architecture/resources/vpc-perimeter.md:130:- 2026-10-10: lab の EC2 から NLB（telegraf_dialout_nlb）の 5140/udp の受信を足した（037。EC2 自身が `logger` で試せるように。機器の syslog は前から CIDR で受ける）。
```

5. `grep -n 'logger' docs/pipeline.md docs/collection.md docs/deploy.md`:

```
docs/collection.md:33:- `syslog`: 機器の syslog のほかに、lab の EC2 から `logger` で NLB の 5140 へ送って試せる（手順は [troubleshooting.md](troubleshooting.md) の「syslog の試験行が logs に入らない」の「正しい送り方」）。
docs/deploy.md:96:  syslog は lab の EC2 から `logger` で NLB へ送っても試せる（[troubleshooting.md](troubleshooting.md) の「syslog の試験行が logs に入らない」の「正しい送り方」）。
docs/pipeline.md:107:  syslog も lab の EC2 から `logger -n <NLB の IP> -P 5140 -d --rfc3164 -t acl-probe "…"` で試せる（手順は [troubleshooting.md](troubleshooting.md) の「syslog の試験行が logs に入らない」の「正しい送り方」）。
```

6. `grep -n 'RETURN' docs/troubleshooting.md docs/pipeline.md`（セルフレビューの直しのあと）:

```
docs/troubleshooting.md:140:| gnmic のタスクで `gn get` は通るのに trap / syslog / NetFlow（`ops/netflow_send.py` で送ったもの）が Kafka に来ない | … `forward-status` で `RETURN` が Docker の MASQUERADE より上に無いと、trap は NLB で落ち、syslog は送り元が EC2 の IP になって届く（[pipeline.md](pipeline.md) の `forward-status` の行。`sudo lab forward` で打ち直す） |
docs/pipeline.md:348:  - `RETURN`（送り元を残す行。`app/containerlab/lab.sh` の `forward`）が Docker の MASQUERADE より上に無いと、…
```

7. `terraform fmt -check`: 1. の `差分なし`。

`git diff --check`: 出力なし。

`.tf` の差分（検証 2）と検証 3〜5 の grep は、セルフレビューの直しのあとに打ち直して同じ結果（検証 2 は `0`、検証 3 は同じ 2 件 + stream の 1 件、検証 5 は 3 ファイル 1 件ずつ）。

### セルフレビュー

自分: opus-5.5 / effort: high（サブエージェントの中なので切り替えられない）。反対弁護人: opus / effort: xhigh（文脈を渡し、読み取り専用。返ったあと作業ツリーの未追跡・変更は無し）。

反対弁護人の指摘と片付け:

- M1 Must fix [correctness] `docs/troubleshooting.md:140`: 足した文「syslog / NetFlow / sFlow は送り元が EC2 の IP で届き、Spark とエージェントは別の機器として入れる」が実装と合わない。再現: `app/syslog-ng/syslog-ng.conf.in:14`（`keep-hostname(yes)`）・`:56`（`tags.sysName=$HOST`）・`:60`（`tags.source=$SOURCEIP`）、`app/spark/snmp_sinks.py:461-469`（`with_sysname` は sysName があればそのまま返す）を読んで確かめた。RFC5424 の受け口なら sysName は機器名のまま。lab の機器は NetFlow / sFlow を出さない（`docs/collection.md:32`）。sFlow の `sampler_address` がデータグラムの AgentIP という点は自分では確かめていない（反対弁護人の WebFetch のみ。docs には書かなかった）。→ 直した（表は 1 文、詳しくは pipeline.md:348-350）。
- S1 Should fix [設計整合] 同じセルが 3 文で設計のリスク 3（2 文まで）を超え、見出し列「Kafka に来ない」とずれる。→ M1 の直しで 1 文 + 参照にした。
- S2 Should fix [docs] `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:15-16`: 見出し「trap / syslog」の下に 2055 / 6343 の受信を書いたのに、lab の送信 2055 / 6343 が無い。→ 自分で足した行の中なので直した（「NetFlow / sFlow も同じ形（udp 2055 / 6343）」）。
- S3 Should fix [docs] `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:16`: 「試すときは … ops/netflow_send.py で NLB へ送る」が NetFlow だけ（syslog の logger が無い）。NetFlow の行の括弧書きで誤りではない。設計の変更対象の行の外。→ 直さず最終報告に回す。
- S4 Should fix [docs] `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:3`: 「次の 3 つで届ける」が gNMI・trap・syslog だけで、NetFlow / sFlow の DNAT（`app/containerlab/lab.sh:328-332`）が無い。012 からの古さで設計の範囲外。→ 最終報告に回す。
- S5 Should fix [docs] `docs/architecture/resources/vpc-perimeter.md:62`: 表の行が「lab の管理ネットワーク、lab → telegraf_dialout_nlb：162/udp、5140、2055、6343」で、lab の SG から 162 を受けると読める（括弧で「NetFlow / sFlow / syslog は EC2 から試しに送る分も」とあり、誤りとまでは言えない）。設計はこのファイルの経緯だけ。→ 最終報告に回す。
- S6 Should fix [docs] `docs/troubleshooting.md:452`（037 で入った行。038 の差分の外）: 「Spark の DEVICE_MAP は 203.0.113.101 を dc1-trex-01 に引く」は、`logger --rfc3164` が HOST にホスト名を入れて sysName があるので `with_sysname` が引かない（`snmp_sinks.py:461-469`）。038 はこの節へのリンクを 3 か所足した。→ 最終報告に回す（QUEUE 候補）。
- N1 Nit `stream/telegraf.tf:18`・`lab/telegraf.tf:8`・`lab.sh:333` の「送り元の IP で機器を引く」は trap と gNMI だけ当てはまる。→ 残す。
- N2 Nit `ops/netflow_send.py:8` は「<NLB の DNS 名>」、docs は「<NLB の IP>」。範囲外。→ 残す。
- N3 Nit `docs/pipeline.md:107` の logger の行に受け口の形式の但し書きが無い。`--rfc3164` はどちらの受け口でも入る（`docs/troubleshooting.md:381` / `:385`）ので実害なし。→ 残す。

問題なしとした観点と根拠:

- .tf の本文を変えていない: 検証 2 の grep が 0、注入すると拾うことを確かめた。`terraform fmt` / validate は check.sh で通過。
- trap が NLB で落ちる: `security_groups.tf:122`（`only = "egress"`）と `:175`（`only != "egress"` だけ受信を作る）を読んだ。
- リンク先の見出し: `docs/troubleshooting.md` に「### syslog の試験行が logs に入らない」（370）と「#### 正しい送り方」（439）があることを grep で確かめた。
- `lab.sh` の行番号 173 / 334 / 340: 読んで確かめた。
- MASQUERADE された送り元が NLB の SG の lab 参照で通る、は読んだだけ（AWS では確かめていない。037 の「正しい送り方」の 2 つ目と同じ前提で、troubleshooting.md:454 も「AWS では未確認」）。

ジンテーゼ: 直す前の結論「Must fix 0」は誤りだった（M1）。直したあとも、AWS の上での NLB の SG と client IP preservation の組み合わせは未確認のまま（docs は 037 と同じ前提に立つ）。未解消の Must fix は 0。
