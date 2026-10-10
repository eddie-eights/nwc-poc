# NLB の受信の説明を 037 に合わせる（038）の実装記録

## Round 1

実装モデル: opus-5.5 / effort: high

ブランチ feat/038-nlb-ingress-docs（origin/main df18d07 から）。コメントと docs だけ。AWS には触っていない。

### 変更ファイル

- `IaC/terraform/aws-managed/base/core/security_groups.tf`（OSS 版はシンボリックリンク）: 115-117 行目のコメント。「NLB は lab の SG からも udp 5140 / 2055 / 6343 を受ける（EC2 自身が試しに送るぶん）。lab の SG は 162/udp も NLB へ送るが、NLB は trap を lab の SG から受けない」を足した
- `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf`（同）: 20-21 行目のコメント。telegraf_dialout_nlb の受信に「lab の SG からも udp 5140 / 2055 / 6343（trap の 162 は受けない）」
- `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf`（同）: 15-16 行目のコメント。同じ内容（logger / ops/netflow_send.py）
- `docs/architecture/core.md`: SG の原則の箇条に例外 1 行（NetFlow / sFlow / syslog は両側。162 は受けない）、「経緯」に `2026-10-10（037）` の行
- `docs/architecture/resources/vpc-perimeter.md`: 「経緯」に `2026-10-10` の行
- `docs/pipeline.md:107`、`docs/collection.md:33`、`docs/deploy.md:96`: syslog も lab の EC2 から `logger` で試せる、と troubleshooting.md の「syslog の試験行が logs に入らない」の「正しい送り方」への参照
- `docs/troubleshooting.md:140`: `RETURN` が MASQUERADE より上に無いときの壊れ方を 1 文

### 設計からの逸脱

- 行番号: `security_groups.tf` の該当コメントは 106-107 ではなく 115-116 だった（036 の workflow の行が上に入ったため）。中身は設計どおり。
- 設計方針 5 / 検証 5 の「037 が `docs/pipeline.md` に書いた `logger` の手順」は、実際は `docs/troubleshooting.md` の「syslog の試験行が logs に入らない」→「正しい送り方」にあり、pipeline.md には `logger` が 0 件だった。3 ファイルともそこを指した（pipeline.md だけはコマンドの形を 1 行で引用）。
- 設計方針 6 の文面「機器の syslog / NetFlow / sFlow は送り元が EC2 の IP になって届き（037 からは NLB で落ちずに通る）」は、「trap は NLB で落ち、syslog / NetFlow / sFlow は送り元が EC2 の IP になって届き（syslog は 037 から NLB で落ちずに通る）」にした。lab → NLB の 162 は `only = "egress"`（`security_groups.tf:122`、`only != "egress"` の行だけ受信を作る `:175`）なので trap は落ち、2055 / 6343 は 037 より前から両側だった。
- 検証 3 の grep は 2 件（lab/telegraf.tf:15、security_groups.tf:115）。stream/telegraf.tf:20 は「CIDR から udp 162 …を受け」の語順で pattern に当たらないので、`-A1` で別に見た（下の出力）。
- `lab.sh` の行番号（設計方針 6）は今も 173 / 334 / 340 で合っていた。

### 検証

1. `./ops/check.sh`（最後の編集のあと）:

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

6. `grep -n 'RETURN' docs/troubleshooting.md`:

```
140:| gnmic のタスクで `gn get` は通るのに trap / syslog / NetFlow（`ops/netflow_send.py` で送ったもの）が Kafka に来ない | … `forward-status` で `RETURN` が Docker の MASQUERADE より上に無いと、trap は NLB で落ち、syslog / NetFlow / sFlow は送り元が EC2 の IP になって届き（syslog は 037 から NLB で落ちずに通る）、Spark とエージェントは別の機器（または不明）として入れるので、`sudo lab forward` で打ち直す |
```

7. `terraform fmt -check`: 1. の `差分なし`。

`git diff --check`: 出力なし。
