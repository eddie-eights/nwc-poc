# NLB の受信の説明を 037 に合わせる（038）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

「lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）」で、NLB の SG（`telegraf_dialout_nlb`）は `lab` の SG から 5140/udp も受けるようになった（NetFlow 2055 / sFlow 6343 は前から両側）。コードと docs の本文は 037 で直したが、コメントと docs の一部に「NLB は管理ネットワークの CIDR から受ける」「EC2 から試しに送れるのは NetFlow」だけの記述が残り、`lab` の SG から 5140 / 2055 / 6343 も受けることが無い。037 のコールドレビュー Round 1 の Nit 4 件（`docs/cycles/037-syslog-nlb-ingress/review.md` の「Nit」）のうち、design.md 自身の誤り以外を消化する。

残っている箇所（2026-10-10 に PM が実物で確かめた行番号）:

- `IaC/terraform/aws-managed/base/core/security_groups.tf:106-107`（trap・syslog・NetFlow・sFlow の行の上のコメント。「NLB は管理ネットワークの CIDR から受け、lab の EC2 は NLB の SG へ送る」）
- `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:20`（「telegraf_dialout_nlb 管理ネットワークの CIDR から udp 162 / 5140 / 2055 / 6343 を受け」）
- `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:15`（「trap / syslog lab の EC2 → NLB の SG（udp 162 / 5140）、NLB は管理ネットワークの CIDR から受ける」）
- `docs/architecture/core.md:89-90`（SG の原則の箇条。例外が無い。表の 82 行目には `lab → telegraf_dialout_nlb` の行がある）
- `docs/architecture/core.md` の「経緯」（012 と 013 の行で終わり、037 が無い）
- `docs/architecture/resources/vpc-perimeter.md` の「経緯」（2026-10-09 で終わり、2026-10-10 の 037 が無い。100 行目の原則には例外が書いてある）
- `docs/pipeline.md:106`、`docs/collection.md:32`、`docs/deploy.md:95`（「NetFlow は EC2 から `ops/netflow_send.py` で送って確かめる」とあるが、syslog を `logger` で送って確かめられることが無い）
- Nit の 5 件目（DNAT の送り元を残す `RETURN` が Docker の MASQUERADE の下に回ったときの壊れ方が、037 のあとは「落ちる」から「送り元が EC2 の IP で届き、別の機器として入る」に変わる）は、`docs/troubleshooting.md:140` の行（trap / syslog / NetFlow が Kafka に来ないときの切り分け）に一言足す

## 設計方針

1. **事実は変えない。** IaC の本文（`sg_flows` の行）と `tests/test_analytics.py` の `EXPECTED_FLOWS` は触らない。変えるのはコメント行と docs だけ。
2. **「NLB は管理ネットワークの CIDR から受ける」と書いてある 3 か所に、「`lab` の SG からも 5140 / 2055 / 6343 を受ける（EC2 自身が試しに送るぶん。162 は受けない）」を足す。** 書き方は `security_groups.tf:108-113` の既存の行の表現（「NetFlow / sFlow は両側」）と `docs/architecture/core.md:82` の表の行に揃える。
3. **`docs/architecture/core.md` の原則の箇条に例外を 1 行足す。** 文面は `docs/architecture/resources/vpc-perimeter.md:100` にある「例外は EC2 自身が試しに送る NetFlow / sFlow / syslog。両側に書く」に揃える（vpc-perimeter.md の出典は core.md なので、正本の側を厚くする）。
4. **「経緯」に 037 の行を足す。** core.md は `- 2026-10-10（037）: …` の形（012 / 013 の行と同じ）、vpc-perimeter.md は `- 2026-10-10: …` の形（既存の行と同じ）。中身は「lab の EC2 から NLB の 5140/udp の受信を足した（EC2 自身が `logger` で試せるように。機器の syslog は前から CIDR で受ける）」。
5. **「EC2 から試しに送れるのは NetFlow」の 3 か所に syslog を足す。** コマンドは 037 が `docs/pipeline.md` / `docs/troubleshooting.md` に書いた `logger` の形をそのまま指す（新しく書かない。該当の節へのリンクか 1 行の引用にする）。
6. **DNAT の壊れ方の一言は `docs/troubleshooting.md:140` の行に足す。** 「`forward-status` で `RETURN` が MASQUERADE より上に無いと、機器の syslog / NetFlow / sFlow は送り元が EC2 の IP になって届き（037 からは NLB で落ちずに通る）、Spark とエージェントは別の機器（または不明）として入れる。`sudo lab forward` で打ち直す」。根拠は `app/containerlab/lab.sh:173`（Docker が MASQUERADE を nat の先頭に入れる）と `:334`（`POSTROUTING 1 … -j RETURN`）、`:340`（`forward-status` の表示）。実装者はこの 3 行を自分で読んで、行番号が今もそこかを確かめる。
7. **AWS では確かめない。** 動作が変わらない（コメントと docs だけ）。

## 変更対象ファイル

| ファイル | 変更 |
| --- | --- |
| `IaC/terraform/aws-managed/base/core/security_groups.tf` | 106-107 行目のコメントだけ |
| `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf` | 20 行目のコメントだけ |
| `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf` | 15 行目のコメントだけ |
| `docs/architecture/core.md` | SG の原則に例外 1 行、「経緯」に 037 の行 |
| `docs/architecture/resources/vpc-perimeter.md` | 「経緯」に 2026-10-10 の行 |
| `docs/pipeline.md`、`docs/collection.md`、`docs/deploy.md` | syslog も EC2 から `logger` で試せることを足す |
| `docs/troubleshooting.md` | 140 行目の行に DNAT の `RETURN` が崩れたときの壊れ方を一言 |

OSS 版（`IaC/terraform/oss/`）の 3 ファイルはシンボリックリンクなので同時に変わる。

## 再利用するもの

- `security_groups.tf:108-113` の既存コメント（「NetFlow / sFlow は両側」）と `docs/architecture/core.md:82` の表の行（受ける / 受けないの言い回し）
- `docs/architecture/resources/vpc-perimeter.md:100` の例外の文面
- 037 が `docs/pipeline.md` / `docs/troubleshooting.md` に書いた `logger` の手順（`. /etc/*-lab.env`、`--region "$AWS_REGION"`）

## 実装ステップ

1. コメント 3 か所を直す（設計方針 2）
2. core.md の原則と経緯、vpc-perimeter.md の経緯（設計方針 3・4）
3. pipeline.md / collection.md / deploy.md の syslog（設計方針 5）
4. troubleshooting.md の一言（設計方針 6）
5. 検証を打ち、`build.md` に記録する

## 検証方法

1. `./ops/check.sh` が `すべて通過`（通過数は 037 の Round 1 と同じ `通過 333 / 失敗 0` を期待。模擬テストの数は変えない）。
2. `.tf` の差分がコメント行だけであること: `git diff -U0 main -- '*.tf' | grep '^[+-][^+-]' | grep -v '^[+-][[:space:]]*#'` の出力が空。
3. `grep -rn '管理ネットワークの CIDR から受け' IaC/terraform/aws-managed docs --include='*.tf' --include='*.md' | grep -v cycles` の各行が、同じ行か直後の行で `lab` の SG から 5140 / 2055 / 6343 を受けることに触れている（3 か所。0 件にする必要は無い。事実として機器からは CIDR で受ける）。
4. `grep -n '037' docs/architecture/core.md docs/architecture/resources/vpc-perimeter.md` で「経緯」に 1 行ずつ出る。
5. `grep -n 'logger' docs/pipeline.md docs/collection.md docs/deploy.md` で 3 ファイルとも 1 件以上（037 の時点では pipeline.md だけ）。
6. `grep -n 'RETURN' docs/troubleshooting.md` で 140 行目付近に 1 件。
7. `terraform fmt -check` は `ops/check.sh` に含まれる（コメントの変更で落ちない）。

## 未確定事項とリスク

1. **行番号は 2026-10-10 の `main`（409a6f4）のもの。** 直す前に `grep` で位置を取り直す（上の grep がそのまま使える）。
2. **言い回しの揺れ。** 「受ける / 受けない」を 3 か所で別々に書くと次の Nit になるので、`core.md:82` の表の行の言い方（「lab の SG は 162/udp も NLB へ送るが、NLB は trap を lab の SG から受けない」）に揃える。
3. **troubleshooting.md の一言が長くなる。** 表の 1 セルに入れるので 2 文まで。長くなるなら `docs/pipeline.md:346`（`forward-status` を見る行）の側に足して、表からはそこを指す。
