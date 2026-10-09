# Cycle 010 kafbat-ui-on-web-ec2 レビューの記録

## Round 1

- レビューモデル: claude-opus-5-5 / effort: xhigh（セッション。cold reviewer は Agent の `model: opus`。effort を引き継ぐかは未確認）。実装モデル（build.md）: claude-opus-5-5 / effort: xhigh
- 対象: `feat/kafbat-ui-on-web-ec2` の 895cdb0..91ab2f6
- **cold reviewer に依頼した**（初回）。渡したのは 4 つだけ: design.md のパス、変更ファイル 44 本のパス一覧（`docs/cycles/010-kafbat-ui-on-web-ec2/` は除く）、未解消の Must fix は None、書き出し先 review-r01.md
  - 返ったあとの `git status --porcelain -uall`: `?? docs/cycles/010-kafbat-ui-on-web-ec2/review-r01.md` の 1 本だけ
- セルフレビュー: build.md の Round 1 の `### セルフレビュー`（Must fix 0。Should fix 2 件は片付け済み）

### cold reviewer の結果（review-r01.md）

# レビュー r01: Kafbat UI を Web の EC2 に同居させ、lab のトポロジ図を見られるようにする（010）

対象: `git diff 895cdb0..HEAD`（branch `feat/kafbat-ui-on-web-ec2`）。正本: `docs/cycles/010-kafbat-ui-on-web-ec2/design.md`。前回の未解決 Must: None（初回）。

## サマリ

- Must 0 / Should 0 / Nit 5。設計の A（Kafbat UI を ECS から Web の EC2 の Docker へ移す）と B（`lab graph` / `graph-stop`）は、design.md の変更対象・手順・検証の期待どおりに入っている。動かしたときに壊れる経路は見つからなかった。
- Nit のうち 2 件は design.md と実装の記述の食い違い（実装の方が正しい）、1 件は docs の書き漏れ、1 件は stale な description、1 件は user_data で Docker の失敗が Gradio を巻き込む形。
- 根拠として手元で走らせたもの（AWS には触れていない。`.venv/bin/python`、`PYTHONDONTWRITEBYTECODE=1`。終わったあと `git status` は clean のまま）:
  - tests: test_stream 83 通過 / 0 失敗、test_lab_debug 91 / 0、test_analytics 489 / 0、test_oss 167 / 0、test_oss_roll 66 / 0、test_alerts 137 / 0、test_oss_ops 145 / 0、test_app 158 / 0、test_dashboard_config 3 / 0、test_graph 72 / 0、test_kb_index 7 / 0、test_local_compose 81 / 0、test_nautobot「68 項目すべて通過」、test_sync 96 / 0、test_workflow 325 / 0。どれも exit 0
  - `terraform fmt -check -recursive` を `IaC/terraform/aws-managed` と `IaC/terraform/oss` で実行し、どちらも exit 0
  - `terraform validate` を aws-managed/base/core、aws-managed/pipeline/stream、oss/base/core、oss/pipeline/stream で実行し、4 つとも `Success!`。既存の `.terraform` を使い、init はしていない
  - design.md の検証 4（0 件）、5（1 件と 0）、6（何も出ない）は期待どおり。検証 3 は Nit 2 を参照
- `bash ops/check.sh` そのものは走らせていない。init が追跡対象の `.terraform.lock.hcl` を書き換えうるため。中身の tests と fmt / validate を個別に走らせて代えた。

### 見た観点 / 見ていない観点

- 見た: design.md の A と B の各項目と、実装の一対一の突き合わせ。変更対象ファイル、SSM パラメータ 3 本、precondition、`kafka_ui_web` の付け先、Cloud Map の名前空間の OSS kafka.tf への移動（アドレスは同じ）、SG の行、hop limit、ボリューム、instance_type、費用の行、roll-nodes.sh、outputs
- 見た: user_data（`web_user_data.sh.tftpl`）の正しさ。`set -euo pipefail` の下での順序と `exit 0` との前後、テンプレートの `$$` エスケープ、ヒアドキュメントのクォート、exit 75 と `Restart=always` / `RestartSec=30` による待ち、env ファイルの `umask 077` と `/run` 置き、`ExecStopPost` での削除、`--rm` と `docker rm --force`、`127.0.0.1` への bind、コメント以外が ASCII であること
- 見た: security。ネットワーク境界の Deny（`perimeter.tf:38-51`）に `kafka-cluster` と `ecr` が入っておらず、Kafbat UI の MSK IAM と ECR の pull を止めないこと。インターフェース型エンドポイントのポリシーが自アカウント限定であること。ECR の pull 権限がリポジトリ 1 本に絞られていること。パスワードを echo しないこと。インスタンスロールの共有は design.md:126-132（リスク 8）で受容済みであることを確かめた
- 見た: lab.sh の `graph` / `graph-stop` / `down` と、`clab()` 関数を systemd-run の中で使えない点の扱い。`$PWD` が `$SRC` と同じ（:14 の cd）であること。lab と lab-debug の env に `NAME_PREFIX` / `AWS_REGION` があること
- 見た: docs の記述の一貫性。Fargate、Cloud Map、ToRemoteHost、t4g.small の残り、deploy.md の 010 の注意（:102）。テストの書き直しの範囲（test_stream の Kafbat 節、test_lab_debug の graph、test_analytics の SG と費用）
- 見ていない: AWS 上の実際の動き（AWS の API・CLI は呼んでいない）。具体的には次の 3 つ:
  - IMDSv2 の hop limit 2 でコンテナから資格情報に届くか
  - MSK IAM がインスタンスロールで通るか
  - t4g.medium のメモリで Gradio と JVM が並ぶか（design.md のリスク 2 と 4）
- 見ていない: `containerlab graph` の画面が CDN のアセットを読むか（design.md のリスク 1）。build.md の範囲で、build.md は読んでいない
- 見ていない: build.md と cycle 010 の design.md 以外の文書。Kafbat UI のイメージの中身と、そのバージョンの env 名の解釈
- 見ていない: `ops/check.sh` の通しの実行（上の理由）

## Must fix

None

## Should fix

None

## Nit

- [design整合] `app/containerlab/lab.sh:150-151`: design.md:64 は `--property=WorkingDirectory=$SRC clab graph -t "$TOPO" --srv 127.0.0.1:50080` と書き、検証 8（design.md:108）は「偽 `systemd-run` の引数に `clab graph -t splab.clab.yml --srv 127.0.0.1:50080` が含まれる」を期待する。実装とテスト（`tests/test_lab_debug.py` の graph の検査）は `WorkingDirectory="$PWD" --setenv=CLAB_VERSION_CHECK=disable containerlab graph -t splab.clab.yml --srv 127.0.0.1:50080` を見ている。
  - 動きは同じで、実装の方が正しい。systemd-run の中では関数 `clab()` を呼べないし、`$PWD` は :14 の cd で lab.sh の置き場所になる。
  - ただし `containerlab graph ...` は `clab graph ...` を部分文字列として含まない。正本の design.md を読んで検証 8 の文字列で確かめる人が不一致で迷うので Nit。
  - design.md:64 と :108 を実装の形に直すのがよい。build.md の「設計との差」に書いてあるかは、build.md を読んでいないので確かめていない。
- [design整合] `docs/cycles/010-kafbat-ui-on-web-ec2/design.md:103`: 検証 3 は「`git grep -n 'service_discovery' IaC/terraform/aws-managed` が 0 件」を期待するが、HEAD では 17 件ある。内訳は pipeline/analytics/ecs.tf 1、grafana.tf 3、splunk.tf 9、pipeline/nautobot/nautobot.tf 4 で、どれも 895cdb0 から同じ件数で、010 の変更ではない。
  - pipeline/stream は 0 件（895cdb0 では kafka_ui.tf に 4 件）なので、実装は意図どおり。
  - 検証の範囲の書き間違いで、手順どおりに打った人が失敗と誤認するので Nit。範囲は `IaC/terraform/aws-managed/pipeline/stream` にするのがよい。
- [docs] `IaC/terraform/aws-managed/base/ecr/outputs.tf:34`: `kafka_ui_repository_url` の description に "IaC/terraform/aws-managed/pipeline/stream runs it on ECS." が残っている。
  - 010 で Kafbat UI は Web の EC2 の Docker に移ったので記述が古い。`terraform output` やコードを読んだ人が、Kafbat UI の居場所を ECS と誤解するので Nit。
  - design.md:80 の docs の一覧にも変更対象にも無いファイルで、設計の側でも拾い漏れている。
- [runtime] `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:27-28`: `set -euo pipefail` の下で `dnf install -y docker` と `systemctl enable --now docker` を、Gradio の設定（:80 以降）より前に置いている。どちらかが失敗すると、副機能の Kafbat UI だけでなく主機能の Web の画面も設定されないまま user_data が止まる。
  - 前に置くのは design.md:53 のとおりで、python3.13 と同じ dnf のリポジトリ（S3 ゲートウェイ）から入れるので起きにくい。それでも Kafbat UI 側の失敗が Gradio を巻き込む形なので Nit。
  - Docker とユニットの部分は、失敗しても `echo ... >&2` で続ける形にすると切り離せる。
- [docs + runtime] `docs/deploy.md:102`: 010 より前の環境についての注意は、Web の EC2 が作り直されることだけを書いている。
  - 010 より前の stream（Fargate の `kafka_ui` サービス。895cdb0 の `kafka_ui.tf:144` で `security_groups = [local.kafka_ui_sg_id]`）が動いたまま `ops/up.sh` を打つと、`tf_apply base/core`（up.sh:742）が `tf_apply pipeline/stream`（up.sh:919）より先に走る。base/core はタスクの ENI に付いたままの `kafka_ui` の SG を消そうとして DependencyViolation で止まる。
  - design.md:116 のとおり、いまは AWS に何も立っていないので実害は無い。古い環境を残している人だけが踏むので Nit。
  - 「先に down.sh するか、stream の Kafbat UI のサービスを先に消す」を 1 行足すのがよい。

## 良かった点

- 順序の逆転（Web の EC2 が stream より先にできる）を SSM パラメータ、exit 75、`Restart=always` / `RestartSec=30` で解き、stream から EC2 に触らない形にした。design.md:29 の方針どおりで、apply の順序に新しい依存を足していない。
- パスワードを含む env を `umask 077` で `/run`（tmpfs）に書き、`ExecStopPost` で消す。値を echo せず、`set -x` も使わない。ECR のログインは `--password-stdin`。bind は `127.0.0.1:8082` だけで、SG の入口を増やしていない。
- user_data のスクリプトを、偽コマンドで実際に実行して確かめるテストにした（SASL_SSL / PLAINTEXT、パラメータが無いときの 75、ECR の失敗）。lab の graph も、偽の `systemd-run` / `systemctl` で実行して確かめている。文字列の有無だけでなく振る舞いを見ている。
- `systemctl enable --now` が `exit 0` より前にあることをテストで固定した（`tests/test_stream.py` の順序の検査）。design.md:53 の要件が後の変更で崩れない。
- Cloud Map の名前空間を OSS 版の kafka.tf へ同じアドレスで移し、OSS 版で名前空間を作り直さない。費用の行（COST_CENTS）とテストの期待も合わせて更新している。
- インスタンスロールを共有するリスクを design.md のリスク 8 に、届く先・逆向き・受容の理由まで書いた。セキュリティ上の判断が追える。
- troubleshooting に Kafbat UI の行を足し、hop limit 2 の理由を web.tf のコメントに残している。

## ユーザーへの質問

None

### 指摘の確認（エンジニア2、claude-opus-5-5 / xhigh）

- Must 0 / Should 0 / Nit 5。Nit は 5 件とも再現した。分類は cold reviewer のまま（格上げ・格下げなし）。Nit なのでこのサイクルでは直さない
- Nit 1 [design整合] design.md:64・:108 の `clab graph` / `$SRC` と lab.sh:150-151 の `containerlab graph` / `"$PWD"` のずれ
  - 再現（読んだだけ）: design.md:108 は `clab graph -t splab.clab.yml --srv 127.0.0.1:50080` を期待し、lab.sh:150-151 は `--property=WorkingDirectory="$PWD" --setenv=CLAB_VERSION_CHECK=disable \` と `containerlab graph -t "$TOPO" --srv "127.0.0.1:$GRAPH_PORT"`
  - 片付け: build.md の「設計から逸脱した点」1 行目に理由ごと記録済み。design.md の B と検証 8 を実装の形に直した
- Nit 2 [design整合] design.md:103 の検証 3 は `IaC/terraform/aws-managed` 全体で 0 件を期待するが、範囲外の 17 件が残る
  - 再現: `git grep -c 'service_discovery' IaC/terraform/aws-managed` → analytics/ecs.tf:1、grafana.tf:3、splunk.tf:9、nautobot/nautobot.tf:4。同じコマンドを 895cdb0 で打つと同じ 4 本に加えて stream/kafka_ui.tf:4。`git grep -c 'service_discovery' IaC/terraform/aws-managed/pipeline/stream` → 0 件（rc=1）
  - 片付け: build.md の「設計から逸脱した点」4 行目に記録済み。design.md の検証 3 を stream に絞った形に直した
- Nit 3 [docs] IaC/terraform/aws-managed/base/ecr/outputs.tf:34 の `kafka_ui_repository_url` の description が「pipeline/stream runs it on ECS.」のまま
  - 再現: `sed -n '34p'` で上の文言を確認。design.md の変更対象にも docs の一覧にも無い
  - 片付け: 最終報告と BACKLOG の候補に回す（BACKLOG.md は PM が書く）
- Nit 4 [runtime] web_user_data.sh.tftpl:27-28 の `dnf install -y docker` と `systemctl enable --now docker` が `set -euo pipefail` の下で Gradio の設定より前にあり、失敗すると Gradio も設定されない
  - 再現（読んだだけ）: :27 `command -v docker >/dev/null || dnf install -y docker`、:28 `systemctl enable --now docker`。前に置くのは design.md:53 のとおり
  - 片付け: build.md のセルフレビューの Nit 5 と同じ指摘（反対弁護人が偽の dnf で再現済み: rc=1、`aws s3 sync` は呼ばれない）。最終報告と BACKLOG の候補に回す
- Nit 5 [docs + runtime] docs/deploy.md:102 の 010 より前の環境の注意に、Fargate の kafka_ui が動いたままだと base/core の SG の削除が止まることが無い
  - 再現（読んだだけ）: up.sh:742 `tf_apply base/core` が up.sh:919 `tf_apply pipeline/stream` より先。895cdb0 の kafka_ui.tf:144 は `security_groups = [local.kafka_ui_sg_id]`。`git diff 895cdb0..HEAD -- base/core/security_groups.tf` で `kafka_ui` の SG と行（web → kafka_ui 8080、kafka_ui → msk 9098）が消える
  - 片付け: 最終報告と BACKLOG の候補に回す。いま AWS に立っている環境は無い（design.md:116）
- 重点観点の指定は無いので、手順 4 の 3 は無し

### 前のラウンドの指摘の取り直し（HEAD = 91ab2f6）

- Must fix: build.md のセルフレビューでも 0 件
- Should fix 1 [security] Web の EC2 のインスタンスロールの共有（PM の判断で受容）
  - `grep -n '^8\. ' design.md` → :121 `8. **Kafbat UI と Gradio が Web の EC2 のインスタンスロールを共有する（PoC では受容。2026-10…`
  - `grep -n 'cycle 010' IaC/terraform/aws-managed/workflow/proposals.tf` → :11。oss/workflow/proposals.tf は `../../../terraform/aws-managed/workflow/proposals.tf` への symlink
  - `git diff --stat 6eeed4b..HEAD -- IaC/terraform/aws-managed/pipeline/stream/` → 空（MSK の権限と kafka_ui.tf は PM の判断のあとも変えていない）
- Should fix 2 [missing tests] user_data の退行注入（build.md のセルフレビューの scratchpad の self/mut.py。HEAD を `git archive` した写しで回した）:

```
HEAD=91ab2f6
[0] chmod 0755 を消す: 検出 rc=1
[1] スクリプトの shebang を消す: 検出 rc=1
[2] スクリプトの set -euo pipefail を消す: 検出 rc=1
[3] スクリプトの pipefail だけ消す: 検出 rc=1
[4] http_tokens を optional に: 検出 rc=1
[5] Wants=network-online.target を消す: 検出 rc=1
[6] docker pull の失敗を無視: 検出 rc=1
[7] ECR のログインの失敗を無視: 検出 rc=1
[8] heredoc のクォートを外す（$ が user_data の時点で展開される）: 検出 rc=1
```

  （各行の後ろの AssertionError の本文は省いた。[8] はテストの `index` が ValueError で落ちて rc=1）
- 全テスト（`bash ops/check.sh`、HEAD = 91ab2f6、未コミットは review-r01.md だけ）:

```
HEAD=91ab2f6
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. ops スクリプトの構文
== 4. 模擬テスト
通過 137 / 失敗 0
通過 489 / 失敗 0
通過 158 / 失敗 0
通過 3 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 91 / 失敗 0
通過 81 / 失敗 0
68 項目すべて通過
通過 167 / 失敗 0
通過 145 / 失敗 0
通過 66 / 失敗 0
通過 83 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0
すべて通過
rc=0
```

### cold review の 2 回目

- 依頼しない。1 回目のあと実装ファイルは 1 バイトも変わっていない（`git diff --stat 91ab2f6..HEAD` は空。このあと変えるのは docs/cycles/010 の design.md と review.md だけ）。Must / Should が 0 なので、完了判定の直前の状態は 1 回目と同じ

### 完了判定

- Must fix 0 / Should fix 0（cold review）。セルフレビューの Should fix 2 件は上で取り直した
- Nit 5 件は最終報告に載せる。コードは直さない。Nit 1・2（design.md と実装のずれ）は、design.md を現行の設計に書き直して片付けた（PM の指示。009 と同じく、build.md の「設計から逸脱した点」を design.md に取り込んだ）
- design.md の検証 10（AWS で立てて画面を見る）は未実施。PM が 008・011・012 とまとめて AWS で 1 回で行う
- サイクルは検証 10 を除いて完了。HTML は書いた（下のパス）

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-010-kafbat-ui-on-web-ec2-review.html -->
