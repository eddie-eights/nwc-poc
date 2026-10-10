# NLB の受信と試し方の説明を揃え、workflow の SG の description を直す（041）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

- 037（lab の EC2 から NLB の syslog 5140 への受信ルール）と 038（NLB の受信の docs）のセルフレビューと cold review で残った Nit が QUEUE に 2 行ある（`troubleshooting.md:452` の `DEVICE_MAP` の誤り、NLB の受信と試し方の説明の食い違い 7 件）。どれも docs と Terraform のコメントだけで、動きは変わらない。
- 036 の cold review の Nit: `base/core/security_groups.tf:39` の workflow の SG の description が「Temporal dev server」のまま（036 で CLI の開発用サーバーから temporalio/server + RDS に替えた）。description を変えると SG が作り直しになるので、環境が無いときにやる。いま AWS に環境は無い（2026-10-10 に ECS クラスター 0 / RDS 0 を確認）ので、この cycle で直す。次の `ops/up.sh` で SG が新しく作られるだけで、動作は変わらない。
- 正本の決め方: NLB が「誰から何を受けるか」の正本は `base/core/security_groups.tf` の通信の表（`lab_mgmt → telegraf_dialout_nlb` が udp 162 / 5140 / 2055 / 6343、`lab → telegraf_dialout_nlb` は 162 が `only = "egress"`、5140 / 2055 / 6343 が両側）。docs はそれを写す（`docs/architecture/core.md:83-84` が 2 行で正しく写している）。

### 現物で確認した事実

- `app/spark/snmp_sinks.py:461-470` の `with_sysname(tags, devmap, fallback_source=False)` は `sysName` が入っていれば tags をそのまま返す。`logger --rfc3164` は HOST に送り手のホスト名を入れ、syslog-ng の `keep-hostname(yes)` でそれが `tags.sysName` になるので、`DEVICE_MAP` は引かれない。`troubleshooting.md:452` の「Spark の `DEVICE_MAP` は 203.0.113.101 を `dc1-trex-01` に引く」は誤り。
- `docs/pipeline.md` に `forward-status` は 3 か所（251 行目の表、347 行目の箇条書き、387 行目の表）。`troubleshooting.md:140` が指しているのは 347 行目の箇条書き（`RETURN` と MASQUERADE の説明がそこにしか無い）。
- `docs/deploy.md:91-98` の `#### SKIP_LAB` の節は「lab を作らない」ときの説明なのに、96 行目が「lab の EC2 から `logger` で送っても試せる」と案内している（lab が無いので EC2 も無い）。
- 「NLB は trap を lab の SG から受けない」の言い方が 5 か所で 4 通り: `core.md:84`、`stream/telegraf.tf:21`、`lab/telegraf.tf:17`、`security_groups.tf:117`、`pipeline.md:349`。
- docs の表と Terraform の表の一致を見るテストは無い（`tests/` に `vpc-perimeter.md` / `architecture/core.md` への参照が無い）。

## 設計方針

1. **`docs/troubleshooting.md`**:
   - 452 行目を「`logs` には `sysName` が EC2 のホスト名（`logger --rfc3164` が HOST に入れ、syslog-ng の `keep-hostname(yes)` でそのまま残る）、`source` が 203.0.113.101 の行として入る。`sysName` があるので Spark の `with_sysname` は `DEVICE_MAP` を引かず、機器の名前には置き換わらない（Splunk の `coalesce('tags.sysName', …)` も同じ）」にする。
   - 140 行目の「[pipeline.md](pipeline.md) の `forward-status` の行」を「[pipeline.md](pipeline.md) の「gnmic と Telegraf に入る」の `sudo lab forward-status` の箇条書き」にする（見出しの文言は `pipeline.md` の 347 行目を含む節の見出しを読んで合わせる）。
2. **`docs/architecture/resources/vpc-perimeter.md:62`** の 1 行を `core.md:83-84` と同じ 2 行に分ける: 「lab の管理ネットワーク（203.0.113.0/24） | telegraf_dialout_nlb | 162/udp、5140/udp、2055/udp、6343/udp | 機器の trap・syslog・NetFlow・sFlow（lab の EC2 が DNAT するので送り元は機器の IP のまま）」「lab | telegraf_dialout_nlb | 5140/udp、2055/udp、6343/udp | lab の EC2 のホストが自分の IP から試しに送る syslog・NetFlow・sFlow（`logger`、`ops/netflow_send.py`）。NLB は trap（162/udp）を lab の SG からは受けない」。
3. **「162 は受けない」の言い方を 1 つにする。** 5 か所を「NLB は trap（162/udp）を lab の SG からは受けない（lab の SG の 162 は送る側だけ。送り元が機器の管理 IP のままの DNAT だけを受ける）」の趣旨で揃える。Terraform のコメント（`security_groups.tf:117`、`stream/telegraf.tf:21`、`lab/telegraf.tf:17`）は行の文脈に合わせて短くしてよいが、「lab の SG からは受けない」「送る側だけ」の 2 語は 5 か所全部に入れる。`core.md:84` と `pipeline.md:349` も同じ。
4. **試し方の説明**:
   - `pipeline/stream/telegraf.tf:16` の括弧を「試すときは lab の EC2 のホストから ops/netflow_send.py（NetFlow）か logger（syslog。docs/troubleshooting.md の「正しい送り方」）で NLB へ送る」にする。
   - `pipeline/lab/telegraf.tf:3` の「次の 3 つで届ける」を「次の 4 つで届ける」にし、syslog の行の後に「NetFlow / sFlow  機器 → 203.0.113.1:2055 / 6343（udp）。trap と同じ仕組みで NLB の同じ番号 → GoFlow2 のタスクの同じ番号（lab の SR Linux は送れないので、届くのは ops/netflow_send.py で試しに送ったぶん）」を足す。
   - `docs/deploy.md:96` を「lab を作らないので、lab の EC2 から `logger` で送る試し方（[troubleshooting.md](troubleshooting.md) の「正しい送り方」）も使えない。syslog を試すなら lab を作る」にする（`SKIP_LAB` の節の中で lab の EC2 を使う案内をしない）。
   - `docs/pipeline.md:350` の「機器の名前ではなく EC2 の IP として入る」を「`tags.sysName` も送り元の IP で埋まるので、`sysName` が機器の管理 IP（正常時）ではなく EC2 の IP になる（RFC5424 の受け口なら `sysName` は機器の名前のまま）」にする（RFC3164 の受け口では正常時も機器の名前ではなく管理 IP なので、「機器の名前ではなく」を直す）。
5. **`base/core/security_groups.tf:39`** を `workflow = "Temporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)"` にする。`terraform fmt` で `=` の位置が崩れないことを見る（`ops/check.sh` の fmt）。
6. **テストで守る（1 つだけ足す）。** `tests/test_core.py`（無ければ `security_groups.tf` を読んでいるテストファイル。`grep -ln security_groups.tf tests/*.py` で探す）に check を 1 つ: 「workflow の SG の description に `dev server` が無く `Temporal server, UI and worker` がある（036 で temporalio/server + RDS にした。cycle 041）」。docs の表と Terraform の表の一致のテストは足さない（docs の表は人が読む写しで、正本は Terraform。QUEUE にも足さない）。
7. **AWS では確かめない。** description の変更で SG が作り直しになることは QUEUE の「残った修正をまとめて AWS で動作確認して直す」の行に PM が足す（`up.sh` の plan に workflow の SG の replace が 1 つ出る）。

## 変更対象ファイル

- `docs/troubleshooting.md`（140、452 行目）
- `docs/architecture/resources/vpc-perimeter.md`（62 行目を 2 行に）
- `docs/architecture/core.md`（84 行目の言い回し）
- `docs/pipeline.md`（349〜350 行目）
- `docs/deploy.md`（96 行目）
- `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf`（16、21 行目のコメント）
- `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf`（3 行目と syslog の行の後、17 行目のコメント）
- `IaC/terraform/aws-managed/base/core/security_groups.tf`（39 行目、117 行目のコメント）
- `tests/test_core.py` か同等（設計方針 6）
- `docs/cycles/041-nlb-docs-and-workflow-sg/build.md`（新規）

## 再利用するもの

- `docs/architecture/core.md:83-84` の 2 行（vpc-perimeter.md の写し元）。
- `docs/troubleshooting.md` の「正しい送り方」の節（リンク先として使う。内容は 452 行目以外変えない）。
- `security_groups.tf` を読んでいる既存の check の読み込み（`read("IaC", "terraform", "aws-managed", "base", "core", "security_groups.tf")` の形）。

## 実装ステップ

1. 設計方針 6 の check を書き、落ちることを見る。
2. 設計方針 1〜5 を直す（行番号は目安。文言で探す）。
3. 検証 1〜5。`build.md` に貼る。
4. `/cycle-build` 手順6 のセルフレビュー。

## 検証方法（期待出力つき）

1. 設計方針 6 の check が、直す前は落ち、直したあと通る（テストファイルの総数を build.md に書く）。
2. `./ops/check.sh` が `すべて通過`（terraform fmt の差分が無いこと含む）。
3. `grep -rn "dev server" IaC/ docs/ --exclude-dir=cycles` が 0 件。`grep -rn "受けない" docs/architecture/core.md docs/pipeline.md docs/architecture/resources/vpc-perimeter.md IaC/terraform/aws-managed/pipeline/stream/telegraf.tf IaC/terraform/aws-managed/pipeline/lab/telegraf.tf IaC/terraform/aws-managed/base/core/security_groups.tf` が 5 件（vpc-perimeter.md を足して 6 件）で、どの行にも「lab の SG からは受けない」と「送る側だけ」がある。
4. `grep -n "DEVICE_MAP" docs/troubleshooting.md` の 452 行目相当が「引かず」を含む。`grep -n "SKIP_LAB" -A6 docs/deploy.md` に「lab の EC2 から」で始まる試し方の案内が無い。`grep -c "forward-status" docs/troubleshooting.md` の該当行が `pipeline.md` の見出し名を含む。
5. `git diff --stat main` が設計の変更対象ファイルだけ（`app/` と `ops/` に差分が無い）。`terraform validate` は check.sh が打つ。

## 未確定事項とリスク

1. **SG の作り直し。** `description` は SG の作り直しを伴う属性なので、次の `ops/up.sh` で workflow の SG が replace になる。いま環境が無いので影響は無い。041 より前に立てた環境が残っている PC で `up.sh` を打つと、`aws_security_group.workload["workflow"]` に `create_before_destroy` が無く名前も固定なので、先に古い SG のルールが消え、SG 本体は ECS のタスクの ENI とほかの SG のルールの参照が残って `DependencyViolation` で消せず、apply が失敗する（`security_groups.tf:14` と `ops/up.sh:587-588` の書き方のとおり。走っている Temporal のタスクは送信のルールを失う）。環境が残っていれば先に `ops/down.sh`（PM が QUEUE 145 に書く。Round 1 の cold review の Should fix 1 で「一度止まる」から直した）。
2. **行番号のずれ。** 038 以降に docs が動いているので、行番号は目安。文言（引用した文）で探し、見つからなければ build.md に書いて止める（別の文に置き換えない）。
3. docs の表と Terraform の表の一致はテストしない（設計方針 6）。次にずれたときは人が気づくしかない。
