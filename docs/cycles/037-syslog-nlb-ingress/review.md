## Round 1

レビューモデル: opus-5.5 / effort: xhigh（cold reviewer に依頼。初回ビルド直後）。確認は PM(fable-5-1)。2026-10-10。

# 「lab の EC2 から NLB の syslog 5140 への受信ルールを足す（037）」コールドレビュー Round 1

対象: ブランチ `feat/037-syslog-nlb-ingress`、`bbfe6ea..HEAD`（8659abc feat、a45792b docs）。8 ファイル、+159 / -22。

## サマリ

- IaC の変更は `security_groups.tf` の 5140/udp の 1 行だけです。`only = "egress"` を外して `why` を変え、すぐ上のコメントの位置を移しました。
  - `sg_rules` の鍵は `"${from}-${to}-${protocol}-${port}"` で、`only` を含みません。そのため送信ルールは作り直されず、NLB の SG に `lab` の SG を参照する 5140/udp の受信ルールが 1 本増えるだけです。CIDR の行は増えません。
- 設計方針 1〜4 はどれも満たしています。設計から外れた点はすべて build.md の「設計からの逸脱」に書いてあり、どれも中身として正しい方へ直しています。
  - コメント行の順番
  - 検証 5 のコマンドに region と接頭辞を足した
  - 期待を Kafbat UI のトピック `logs` に変えた
  - troubleshooting.md の表の行と VPC フローログの記述を直した
- Must fix はありません。
  - Should fix の 1 件は、範囲外の `docs/cml-sandbox.md` に「NLB の SG は `lab_mgmt` の CIDR からしか受けない」が残っていることです。037 のあとは syslog について誤りになります。
  - Nit は 5 件で、古くなったコメントや経緯の書き漏れ、design.md 自身の誤りなどです。
- マージしてよい状態と判断します。Should fix は 1 行の書き換えで直せます。

### 見た観点

- [design.md整合性]
  - 設計方針 1〜4 と変更対象ファイルの表を、`git diff bbfe6ea..HEAD` の全行と突き合わせました。
  - build.md の逸脱の記録も 1 件ずつ確かめました。
- [correctness]（IaC）
  - `security_groups.tf` の表の仕組みを読みました（`aws_vpc_security_group_ingress_rule.flow` / `egress_rule.flow` の `for_each` の絞り込みと、`sg_rules` の鍵）。
  - `only` を外すと受信が 1 本増えるだけで、既存のルールは作り直されないことを確かめました。
- [security]（開けすぎ）
  - 増える受信は NLB の SG の 5140/udp だけで、送り元は `lab` の SG の参照です。CIDR や 162/udp の受信は増えません。
- [missing tests]
  - `tests/test_analytics.py` の `EXPECTED_FLOWS` は、通信の表と集合が一致するかを検査しています（138-139 行目）。`only` を戻すと落ちることも確かめました（build.md の検証 1 に再現の出力があり、検査の仕組みは自分で読みました）。
- [runtime]（docs に書いたコマンド）
  - `. /etc/*-lab.env` で読む `AWS_REGION` / `PARAM_PREFIX` を確かめました。`pipeline/lab/templates/lab_user_data.sh.tftpl` が書く値で、`app/containerlab/lab.sh:309-310` の読み方と同じです。
  - SSM `/<prefix>/telegraf-address` は NLB のサブネット a の ENI のプライベート IP です（`pipeline/stream/telegraf.tf` 129-133 行目）。
  - lab の EC2 のロールにはこの Parameter の GetParameter が付いています（`pipeline/lab/iam.tf:62`）。
- [docs]
  - 「送信だけ」と書いた箇所を repo 全体で grep し、残りを確かめました。
  - Athena に表 `logs` と列 `appname` が無いことは、`syslog-ng.conf.in` の format-json の `tags.appname` と `snmp_sinks.py` の `raw_telemetry` を読んで確かめました。
  - VPC フローログが条件なしで作られていることは、`base/core/flow_logs.tf:51` の `aws_flow_log.vpc` で確かめました。
- [API compatibility]（OSS 版）
  - `IaC/terraform/oss/base/core/security_groups.tf` はシンボリックリンクです。`oss.tf` の `active_sg_flows` は msk の行だけを外すので、OSS 版にも同じ受信が入ります。
- 自分で打ったコマンドと結果
  - 模擬テスト 16 本（`uv run --group dev --group web python tests/<t>.py`）は、すべて失敗 0 でした。
    - agentcore 168、alerts 168、analytics 549、collectors 79、dashboard_config 3、graph 78、kb_index 7、lab_debug 110、local_compose 145
    - nautobot は「68 項目すべて通過」
    - oss 177、oss_ops 206、oss_roll 66、stream 114、sync 104、workflow 333
  - `terraform fmt -check IaC/terraform/aws-managed/base/core`: 差分なし
  - `terraform -chdir=IaC/terraform/aws-managed/base/core validate`: `Success! The configuration is valid.`
  - `git diff --check bbfe6ea..HEAD`: 空白の誤りなし
  - `git status`: 未コミットの変更なし

### 見ていない観点

- AWS での動作は見ていません。設計方針 4 どおりで、QUEUE の「残った修正をまとめて AWS で動作確認して直す」に回っています。
  - `logger` の行が NLB を越えて syslog-ng とトピック `logs` に届くか
  - フローログに ACCEPT で出るか
- `terraform plan` は打っていません（AWS の認証と state が要るため）。
  - 送信ルールの description（`why`）の変更が in-place になるという判断は、build.md の記録（provider のソースを読んだだけ）と、`for_each` の鍵が変わらないことから推したものです。plan では確かめていません。
- lab の EC2 で `. /etc/*-lab.env` が通るか（ファイルの権限、`ssm-user` のシェル）は、user_data のテンプレートを読んだだけです。
- 036（Temporal の履歴を RDS に残す）と同じファイル（`security_groups.tf`）の別の行を並行して変えています。マージのときに衝突するかどうかは見ていません。
- `ops/check.sh` は自分では打っていません（同じ中身の模擬テスト・fmt・validate を個別に打ちました）。

## Must fix

None

## Should fix

- [docs / design.md整合性] `docs/cml-sandbox.md:558` に、037 のあと syslog について誤りになる理由が残っています。
  - 根拠: 見送った案の表の「UDP の中継（PC → トンネル → lab の EC2 の socat → NLB の 5140 / 162）」の行に、「EC2 で送り直すと送り元が EC2 の IP になり、NLB の SG が `lab_mgmt` の CIDR からしか受けない（2026-10-09 の不具合 3 と同じ所…）」とあります。
    - 037 で NLB の SG は `lab` の SG から 5140/udp を受けるようになりました（`security_groups.tf:114`）。
    - この行は `bbfe6ea` の時点からあり、今回の差分には入っていません。`grep -rn 'lab_mgmt. の CIDR からしか' docs/` で残っているのはこの 1 か所です。
  - Should の理由: 設計方針 3 の「送信だけだから届かないと書いてある箇所を直す」に当たる記述で、読み手が「EC2 から送り直しても NLB で落ちる」と誤って理解します。ただ、見送った結論は「SSM のトンネルは TCP だけ」という理由で今も成り立ち、動作にも影響しないので Must にはしません。
  - 直し方の案: 「NLB の SG が…受けない」を「162/udp の trap は NLB の SG が `lab` の SG から受けない（5140 は 037 から受ける）。送り元が EC2 の IP になり、機器の対応が送り元の IP で引けない」のように書き換えます。

## Nit

- [保守性] NLB の受信を「管理ネットワークの CIDR から受ける」とだけ書いたコメントが 3 か所に残っています。
  - 根拠:
    - `IaC/terraform/aws-managed/base/core/security_groups.tf:106-107`（「NLB は管理ネットワークの CIDR から受け、lab の EC2 は NLB の SG へ送る」）
    - `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:20`（「管理ネットワークの CIDR から udp 162 / 5140 / 2055 / 6343 を受け」）
    - `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:15`（「trap / syslog lab の EC2 → NLB の SG（udp 162 / 5140）、NLB は管理ネットワークの CIDR から受ける」）
    - 037 から 5140 は `lab` の SG からも受けます。2055 / 6343 は 037 より前から同じ状態です。
  - Nit の理由: 書いてあることは誤りではなく足りないだけで、動作には影響しません。エンジニアも build.md のセルフレビューで「最終報告に回す」としています。
- [docs] `docs/architecture/core.md:89-90` の原則の箇条に、例外が書いてありません。
  - 根拠: 原則は「lab の EC2 が転送する流れは…反対側は管理ネットワークの CIDR で書く」だけです。`docs/architecture/resources/vpc-perimeter.md:100` には「例外は EC2 自身が試しに送る NetFlow / sFlow / syslog。両側に書く」と足してあります。core.md 側にあるのは表の 82 行目の行だけです。
  - Nit の理由: 表を読めば分かり、vpc-perimeter.md の出典は core.md なので、正本の側が薄いという整合の問題にとどまります。設計方針 3 の「例外が 3 つだと分かるようにする」は表と vpc-perimeter.md で満たしています。
- [docs] SG の表を変えたのに、`docs/architecture/core.md` と `docs/architecture/resources/vpc-perimeter.md` の「経緯」に 037 の行がありません。
  - 根拠: core.md の「経緯」には SG の行を変えた 012（2026-10-08）と 013（2026-10-09）が、vpc-perimeter.md:124 以降の「経緯」には 2026-10-08 / 2026-10-09 の SG の変更があります。2026-10-10 の 037 だけが無い状態です。
  - Nit の理由: 現在の構成の記述は正しく、記録の書き漏れにとどまります。設計の変更対象にも経緯の追記は入っていません。
- [design.md整合性] design.md 自身に誤りが 3 か所残っています（実装と docs は正しい方に直してあります）。
  - 根拠:
    - `design.md:59`: 検証 2 の期待「`grep -c 'only = "egress"'` が `1`」は、`security_groups.tf:56` の原則のコメントにも同じ文字列があるので、実際は `2` です。
    - `design.md:69`: 検証 5 の期待「Athena で `logs` を `appname = 'acl-probe'` で引く」は打てません。Athena の表は `raw_telemetry` で、`appname` は `tags_json` の中にあります。
    - `design.md:73`: 未確定事項 1 の「VPC Flow Logs（作っていない）」は誤りです。`base/core/flow_logs.tf:51` に条件なしの `aws_flow_log.vpc` があります。
    - 3 つとも build.md の「設計からの逸脱」とセルフレビューに記録があり、エンジニアは PM の文書なので触っていません。
  - Nit の理由: 実装と docs に誤りは持ち込まれていません。ただ、QUEUE の AWS の動作確認で design.md の検証 5 をそのまま打つと迷うので、PM が design.md か review.md で正すのが望ましいです。
- [runtime] 037 のあと、DNAT の送り元を残す規則が崩れたときの壊れ方が「落ちる」から「別の送り元で入る」に変わります。
  - 根拠:
    - `app/containerlab/lab.sh:173` に「Docker は管理ネットワークを作るたびに自分の MASQUERADE を nat の先頭に入れる」とあり、`:334` の `POSTROUTING 1 … -j RETURN` は `forward` を打ち直すまで MASQUERADE の下に回りえます。
    - 037 より前は、そのとき機器の syslog は送り元が EC2 の IP になり、NLB の SG で落ちていました。037 のあとは受かり、送り元の IP で機器を引く Spark とエージェントでは、別の機器（または不明）として入ります。
  - Nit の理由: `lab.sh up` は deploy のあとに毎回 `forward` を打ち直し、`forward-status` にも RETURN が上にあるかの表示があります（`:340`）。NetFlow / sFlow は 037 より前から同じ性質なので、syslog だけの新しい危険ではありません。起きる条件が Docker のネットワークを手で作り直したときに限られるので、Nit にしました。docs の「正しい送り方」の切り分けに一言あると親切です。

## 良かった点

- IaC の変更が 1 行で、`sg_rules` の鍵が `only` を含まないことを根拠に、作り直しが無いことまで設計と build.md で詰めてありました。
- `EXPECTED_FLOWS` は集合の一致で検査しているので、`only = "egress"` に戻すとテストが落ちます。build.md に、戻したときの失敗の出力も残してあります。
- docs に書いた EC2 からの送信コマンドが、設計の素の `--name "/<prefix>/…"` ではなく `. /etc/*-lab.env` と `--region "$AWS_REGION"` になっています。`lab.sh forward` と同じ読み方で、既定のリージョンが無いシェルでも通る形です。
- 設計の範囲外でも、037 のあと誤りになる記述を直していました（`troubleshooting.md:133` の表の行と、VPC フローログを「作っていない」とした行）。どちらも根拠のファイルと行を build.md に書いています。
- 期待の確かめ方を、実在するもの（Kafbat UI のトピック `logs`、`raw_telemetry` の `topic = 'logs'` の `tags_json`）に直していました。
- 過去の事実（2026-10-09 に落ちた理由）は過去形のまま残し、直した後の形を別に書いています。履歴が消えていません。

## ユーザーへの質問

None

### PM の確認（2026-10-10、fable-5-1）

- Should fix（`docs/cml-sandbox.md:558`）: 再現した。`grep -rn 'の CIDR からしか' docs/ | grep -v cycles` → 1 行（558 行目）。docs の誤りなので PM が直した（162 は `lab` の SG から受けない、5140 は 037 から受ける、送り元の IP で機器を引けない）。直した後の同じ grep → `0`
- Nit の design.md の誤り 3 か所（検証 2 の `grep -c` は `2`、検証 5 の Athena の表と列、未確定事項 1 の Flow Logs）: 再現した（`grep -c 'only = "egress"'` → `2`、`flow_logs.tf:51` に `aws_flow_log.vpc`）。design.md を直した
- 残りの Nit 4 件（コメント 3 か所、core.md の原則、経緯の行、DNAT の送り元が崩れたときの壊れ方）は直さず QUEUE に 1 行で足した
- 全テスト: `./ops/check.sh` → `通過 333 / 失敗 0`、`すべて通過`（fmt 差分なし、20 ルートの validate OK）
- 2 回目の cold review は呼ばない。Round 1 のあと実装ファイル（`security_groups.tf`、`tests/test_analytics.py`）は 1 バイトも変えていない（直したのは docs と design.md だけ）
- 見た観点: design 整合性 / correctness / missing tests（cold reviewer と同じ）。見ていない観点: AWS での動作（QUEUE の「残った修正をまとめて AWS で動作確認して直す」で見る）

結論: Must fix 0 / Should fix 0（1 件を直した）/ Nit 4（QUEUE）。サイクル完了。
