# build — Grafana のルール検査の残りを直す（015）

## Round 1

実装モデル: opus-5.5 / effort: xhigh（high へ下げる手段が無い。`set_session_effort` は自分のセッションを変えられない。`get_session self` の effort は xhigh）

### commit

| commit | 内容 |
| :--- | :--- |
| ac53b6d | 設計（design.md と design-log.md の Round 0） |
| 062a334 | A: ページ分け（groupNextToken）を追う（79、N2） |
| db1574e | B・C: 終了コードを 0 / 1 / 2 / 3 に分ける（80、N4） |
| 7f9fac6 | D: ssm_run の締め切り SSM_RUN_WAIT（82、N6） |
| 8e9a1da | docs: 終了コードの表と SSM_RUN_WAIT |
| 615f497 | E・F: 9-2 の tf_output と、analytics を作らない回の Grafana（81・83。012 より後に置くので最後） |
| 12e2e5a | docs/cycle-006-design（fa6234f。011 が入った）をマージ。衝突なし |
| 863fdee | セルフレビュー S1: ページの歯止めを seen と別に数える |
| 9c4dbb5 | セルフレビュー D1・D3: 応答の status と data.groups を確かめる。歯止めを最初の 1 ページを含めて 100 ページにする（design.md に書き足した） |
| 2ec500b | docs/cycle-006-design（f766351。014 と 011 Round 2 が入った）をマージ。衝突なし（deploy.md・pipeline.md・troubleshooting.md は自動マージ） |
| （この commit） | build.md と、development.md のテストの本数（マージ後の check.sh の実測） |

012（MDT の行を消す）は、f766351 の時点でも docs/cycle-006-design に入っていない。入ったらもう一度マージして衝突を解く。

### 変更ファイル（`git diff --stat fa6234f 9c4dbb5`。このサイクルの分。2ec500b のマージで入ったほかのサイクルの分は含めない）

```
 docs/architecture/resources/grafana.md             |   4 +-
 .../015-grafana-check-followups/design-log.md      |  28 ++++
 docs/cycles/015-grafana-check-followups/design.md  | 167 +++++++++++++++++++++
 docs/deploy.md                                     |   1 +
 docs/pipeline.md                                   |   2 +-
 docs/troubleshooting.md                            |  13 +-
 ops/check-grafana.sh                               |  18 ++-
 ops/grafana_rules_check.py                         |  47 +++++-
 ops/up-common.sh                                   |  57 +++++--
 ops/up.sh                                          |  14 +-
 oss/ops/up.sh                                      |   7 +-
 tests/test_alerts.py                               | 159 ++++++++++++++++++--
 tests/test_oss_ops.py                              | 111 ++++++++++++--
 13 files changed, 569 insertions(+), 59 deletions(-)
```

ほかに docs/development.md のテストの本数（この build.md と同じ commit）。

### 検証方法

#### 1. test_alerts（2ec500b の作業ツリー。下の 3 の check.sh の出力から）

```
ok ルールの確かめ: 待つ秒数（GRAFANA_WAIT）までに評価がそろわなければ未確認（2）。どのルールを待っていたかを出す
ok ルールの確かめ: Grafana が起動中（つながらない）・途中で切れた応答・JSON でない応答は待って読み直し、ずっと読めなければ理由を付けて未確認（2）
ok ルールの確かめ: 401 / 403 は待たずに未確認（2。待っても直らない）
ok ルールの確かめ: make_fetch は admin の Basic 認証でルールの API を読み（環境変数のプロキシは通さない）、401 は Unauthorized にする。パスワードは例外の文にも出力にも出さない
ok ルールの確かめ: make_fetch はリダイレクト先に Authorization（パスワード）を送らない。JSON がオブジェクトでなければ ValueError（check が待って読み直す）
ok ルールの確かめ（ページ分け）: data.groupNextToken があれば group_next_token（URL エンコードする）で次のページを読み、グループをつなぐ。どのページにも Authorization を付け、group_limit は送らない（13.2.3 の既定 -1 は全部を 1 ページで返す）
ok ルールの確かめ（ページ分け）: 判定は全部のページのルールで出す（2 ページ目のルールだけがエラーでも NG。1 ページ目だけなら OK になる形）
ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
ok ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
ok ルールの確かめ（ページ分け）: 2 ページ目が 200 で status: error を返し続けたら、1 ページ目のルールだけで OK にせず、待ち切れたら未確認（2）
ok ルールの確かめ（main）: NAME_PREFIX が無ければ止まる。Grafana は Cloud Map の grafana.<接頭辞>.internal:3000、パスワードは SSM の値、待つのは既定 300 秒。SSM が読めなければ例外の型だけを出して未確認（2）
ok ルールの確かめ: 読むのは SSM の /<接頭辞>/grafana/admin-password（Web の EC2 のロールが読める範囲）と Web と同じ環境変数・boto3。標準ライブラリのほかは boto3 だけ
ok 9-2（grafana_rules_step）: Grafana のサービスが安定してから、Web の EC2 に ops/grafana_rules_check.py そのものを NAME_PREFIX 付きで 1 回送る。OK なら判定を出し、警告は空
ok 9-2: 送るコマンドは JSON の文字列に埋めるので、ダブルクォートもバックスラッシュも含まない（ssm_run の約束）
ok 9-2: NG なら止めずに（0）、判定の行（タブの手前まで）とログの見方（/ecs/<接頭辞>-grafana の Failed to evaluate rule）と確かめ直すコマンドを警告に入れ、黄色で出す
ok 9-2: Grafana のサービスが安定しなければ確かめを送らず（0）、安定しない旨とタスクの見方を警告に入れる
ok 9-2: SSM Run Command を送れなければ、待ち続けずに（set -e の効かない $( ) の中でも ssm_run が 1 を返す）未確認の警告（判定の行が無い。ログの案内なし）を出す
ok 9-2: 判定が未確認なら、確かめられなかった旨（判定の行。タブの手前まで）と確かめ直すコマンドを警告に入れる。評価のエラーとは限らないので Grafana のログは案内しない
ok grafana_rules_check の終了コード: OK の判定で SSM も成功なら 0、NG の判定なら 1、未確認・判定の行が無い・送れないは 2。最後の判定の行を GRAFANA_VERDICT に置く
ok ssm_run: InProgress のまま SSM_RUN_WAIT 秒を過ぎたら、待つのをやめて結果の見方（get-command-invocation）を出し、9-2 は未確認の警告にする
ok ssm_run: get-command-invocation が読めないまま SSM_RUN_WAIT 秒を過ぎても同じく返す（最後の状態: 読めない）
ok ssm_run: 送った直後に get-command-invocation が読めなくても、読めるまで読み直す（締め切りの中なら OK になる）
ok SSM_RUN_WAIT が 1 以上の整数でなければ、ops/up-common.sh を読んだところで理由を言って止まる（aws には触らない）
ok トピックは土台（base/core）に 1 つ（<接頭辞>-alerts）、保存時の暗号化は AWS 管理の鍵
ok トピックのポリシー: 許すのはこのアカウントだけ（Principal に * の Allow は無い）
ok 閉域（network_perimeter）のとき、VPC の外からの sns:Publish を拒む（拒むのは Publish だけ）
ok 土台は alerts_topic_arn を出し、閉域の IAM 側の Deny に sns:* がある（events:* はもう無い）
ok 送り手（analytics）と受け手（workflow / graph）は土台の state からトピックを読む（古い土台なら apply の前に理由を言って止まる）
ok 受け手は 2 つ: SQS（raw message delivery。ワークフロー）と Lambda（status）
ok EventBridge のルールと Spark からの put_events はどこにも無い
ok check.sh は tests/ の test_*.py を全部走らせる（このテストも）
ok check.sh の構文検査は .py のあるディレクトリを全部見る（app/splunk/ のアラートアクションと app/graph/ の Lambda も。どちらも app/ の下）
ok lab.sh の fail-bgp / heal-bgp は BGP_NODE の設定にある iBGP の neighbor（BGP_PEER）の admin-state を disable / enable にする。使い方の表示に 3 つが載る（graph の行が 6 行目、011 で trex の行が 8 行目に増えた）
ok lab.sh の fail-bgp / heal-bgp は commit のあと state の admin-state を読み直し、変わっていなければ 1 で止まる（sr_cli の終了コードに頼らない。grep -q は pipe に繋がない）
ok lab.sh の trap-test の OID は Splunk の netops_trap も Grafana の trap ルールも除かない（どちらも kind = trap）。管理ネットワークの中（TREX の netns）から機器の trap と同じ $MGMT_GW:162 へ送り、送り元の管理 IP は device map で TREX（dc1-trex-01）になる
ok trap のルールの機器の terms（上位 size 件）は lab の trap の送り元（trap を lab の EC2 へ送る SR Linux（全台）+ trap-test の TREX）を全部返せる。yaml のコメントの送り元の数も同じ
通過 169 / 失敗 0
```

#### 2. test_oss_ops（同じく 3 の check.sh の出力から）

```
ok ops/up.sh（マネージド版）も Runtime のロググループのあと（9-2）に同じ確かめを打ち、OK でなければ警告（GRAFANA_WARN）を最後にもう一度出す。state の一覧は grep -q で見ない（先に抜けると terraform が SIGPIPE になり、pipefail で偽になる）
ok ops/up.sh の 9-2: Grafana を今回作る（GRAFANA）なら、state を見ずに analytics の出力のクラスターとサービスで grafana_rules_step を 1 回打つ
ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
ok ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
ok ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言って止まる（1）。grafana_rules_step は打たない
ok ops/up.sh の 9-2（81）: analytics の出力が読めない（terraform output が rc≠0）なら、terraform のエラーを残したまま止まる（1）
ok oss/ops/up.sh（81）: 9-2 で analytics の出力 grafana_service_name が空なら、どれのことかを言って止まる（1）。ecs wait も確かめの送信も打たない
ok oss/ops/up.sh（81）: 7-4b の analytics のクラスターの名前も tf_output で読み、読めない・空なら止まる（9-2 もこのクラスター）
ok up.sh → down.sh: up.sh が作った SSM のパラメータ 14 個を全部消し、up.sh が apply した 9 つのルートを全部 destroy する
ok oss/ops/up.sh と down.sh の terraform init は、どのルートも -lockfile=readonly（lock はマネージド版へのシンボリックリンクなので書き換えない）
ok IaC/terraform/oss/ の .terraform.lock.hcl は、どのルートもマネージド版の lock へのシンボリックリンク（実ファイルにしない）
ok up.sh（通し）が 9 つのルートに渡した -var の名前は、どれも IaC/terraform/oss/<ルート>/*.tf の variable で宣言されている（owner を含めて全部）
ok down.sh が destroy に渡した -var（workflow の worker_image_tag、stream の snmp_agents / gnmi_targets、owner）も、そのルートの variable で宣言されている
ok up.sh: readonly の init が止まったら apply せずに止まり、先にマネージド版のルートを init する案内（terraform -chdir=IaC/terraform/aws-managed/base/ecr init）を出す
ok sync-graph.sh --oss: Web の EC2 の id を IaC/terraform/oss/base/core の出力から取り、接頭辞 x-nwc-oss と lab のトポロジで ops/seed_graph.py を送る（GRAPH_REPLACE=0）
ok sync-graph.sh --oss --replace: 同じ送り先で GRAPH_REPLACE=1（入っていても lab の定義で入れ直す）
ok sync-graph.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed/base/core と接頭辞 x-nwc-poc のまま（OSS 版の追加で変わらない）
ok sync-graph.sh --oss --dry-run: terraform にも aws にも触らず、lab から作ったトポロジ JSON を出すだけ
ok sync-graph.sh: 知らない引数は使い方を出して 2 で止まる（terraform と aws には触らない）
ok check-grafana.sh --oss: Web の EC2 と Grafana のサービスを IaC/terraform/oss の出力から取り、接頭辞 x-nwc-oss で ops/grafana_rules_check.py を 1 回送る。OK なら 0
ok check-grafana.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed と接頭辞 x-nwc-poc
ok check-grafana.sh: NG なら 1 で終わり、判定の行と、理由を見る Grafana のログのコマンド（/ecs/x-nwc-oss-grafana の Failed to evaluate rule）を出す
ok check-grafana.sh: 未確認（判定: 未確認。届かない・401・待ち切れ）なら 2 で終わり、判定の行を出す。評価のエラーとは限らないので Grafana のログは案内しない
ok check-grafana.sh: Grafana が無い（grafana_service_name が空）なら理由を言って 3 で止まり、確かめを送らない
ok check-grafana.sh: deploy.env の誤り（load_deploy_env の die）も 3 で止まる（ops/common.sh の die の 1 は NG と紛れる）
ok check-grafana.sh: SSM_RUN_WAIT の値の誤りも 3 で止まる（確かめを送らない）
ok check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）
通過 167 / 失敗 0
```

#### 3. check.sh（2ec500b の作業ツリー）

rc 0。見出しと各テストの最後の行（標準出力。テストの並びは check.sh の順）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. スクリプトの構文
== 4. 模擬テスト
通過 169 / 失敗 0            test_alerts
通過 489 / 失敗 0            test_analytics
通過 161 / 失敗 0            test_app
通過 3 / 失敗 0              test_dashboard_config
通過 78 / 失敗 0             test_graph
通過 7 / 失敗 0              test_kb_index
通過 104 / 失敗 0            test_lab_debug
通過 122 / 失敗 0            test_local_compose
69 項目すべて通過               test_nautobot
通過 171 / 失敗 0            test_oss
通過 167 / 失敗 0            test_oss_ops
通過 66 / 失敗 0             test_oss_roll
通過 88 / 失敗 0             test_stream
通過 103 / 失敗 0            test_sync
通過 327 / 失敗 0            test_workflow
すべて通過
```

#### 4. 退行の注入（scratchpad の inject015.py。9c4dbb5 の作業ツリーで、1 件ずつ入れて走らせ、`finally` で戻す。戻したあとの `git status --porcelain` は docs/development.md と build.md だけ）

```
== M1 ページを追わない / test_alerts: rc=1 4s
   最後に通った: ok ルールの確かめ: make_fetch はリダイレクト先に Authorization（パスワード）を送らない。JSON がオブジェクトでなければ ValueError（check が待って読み直す）
   落ちた: AssertionError: ルールの確かめ（ページ分け）: data.groupNextToken があれば group_next_token（URL エンコードする）で次のページを読み、グループをつなぐ。どのページにも Authorization を付け、group_limit は送らない（13.2.3 の既定 -1 は全部を 1 ページで返す）
== M2 seen を消す / test_alerts: rc=1 4s
   最後に通った: ok ルールの確かめ（ページ分け）: 判定は全部のページのルールで出す（2 ページ目のルールだけがエラーでも NG。1 ページ目だけなら OK になる形）
   落ちた: AssertionError: ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
== M3 未確認の警告を NG と同じにする / test_alerts: rc=1 5s
   最後に通った: ok 9-2: Grafana のサービスが安定しなければ確かめを送らず（0）、安定しない旨とタスクの見方を警告に入れる
   落ちた: AssertionError: 9-2: SSM Run Command を送れなければ、待ち続けずに（set -e の効かない $( ) の中でも ssm_run が 1 を返す）未確認の警告（判定の行が無い。ログの案内なし）を出す
== M3 未確認の警告を NG と同じにする / test_oss_ops: rc=0 66s
   最後に通った: ok check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）
   落ちた: 通過 167 / 失敗 0
== M4 ssm_run の締め切りを消す / test_alerts: rc=1 25s
   最後に通った: ok 9-2: 判定が未確認なら、確かめられなかった旨（判定の行。タブの手前まで）と確かめ直すコマンドを警告に入れる。評価のエラーとは限らないので Grafana のログは案内しない
   落ちた: AssertionError: grafana_rules_check の終了コード: OK の判定で SSM も成功なら 0、NG の判定なら 1、未確認・判定の行が無い・送れないは 2。最後の判定の行を GRAFANA_VERDICT に置く
== M5 grep -q に戻す（静的検査は外して振る舞いのテストまで進める） / test_oss_ops: rc=1 56s
   最後に通った: ok ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
   落ちた: AssertionError: ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
== M6 9-2 の || exit 1 を消す / test_oss_ops: rc=0 63s
   最後に通った: ok check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）
   落ちた: 通過 167 / 失敗 0
== M7 tf_output の空の確かめを消す / test_oss_ops: rc=1 54s
   最後に通った: ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
   落ちた: AssertionError: ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言って止まる（1）。grafana_rules_step は打たない
== M8 GRAFANA_LEFT の ANALYTICS_LEFT の条件を消す / test_oss_ops: rc=1 55s
   最後に通った: ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
   落ちた: AssertionError: ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
== M9 ページの歯止めを 1 つずらす（前の形に戻す） / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
== M10 status の確かめを消す / test_alerts: rc=1 4s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
== M12 status の確かめを消す（1 ページ目の {} の検査は外して、2 ページ目の status: error のテストまで進める） / test_alerts: rc=1 4s
   最後に通った: ok ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups 
   落ちた: AssertionError: ルールの確かめ（ページ分け）: 2 ページ目が 200 で status: error を返し続けたら、1 ページ目のルールだけで OK にせず、待ち切れたら未確認（2）
== M11 groups の確かめを消し、空のページとしてつなぐ / test_alerts: rc=1 4s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
戻した
```

863fdee の前（062a334 の形）は `== M2 seen を消す / test_alerts: rc=143 757s`（終わらず手で止めた。セルフレビューの S1）。

M3 は test_alerts が受ける（ops/check-grafana.sh は grafana_rules_step を使わず、自分で終了コードを見て分けるので、test_oss_ops は M3 で落ちない）。M6 は 9-2 のトップレベルの set -e が受ける（下の N4）。

#### 5. 手元の Grafana 13.2.2（使い捨てのコンテナ）

ページ分け（scratchpad の gf_page.py）:

```
$ docker run da6b95e71d8d
health: {'database': 'ok', 'version': '13.2.2', 'commit': '1bea008f7e4e858b6824c9e364d608bd4d10b13a'}
folder: 200
rule r1 g1 201
rule r2 g2 201
{"path": "/api/prometheus/grafana/api/v1/rules", "status": 200, "groups": ["g1", "g2"], "groupNextToken": null}
{"path": "/api/prometheus/grafana/api/v1/rules?group_limit=1", "status": 200, "groups": ["g1"], "groupNextToken": "eyJuIjoiZjAxNSIsImciOiJnMSJ9"}
{"path": "/api/prometheus/grafana/api/v1/rules?group_next_token=eyJuIjoiZjAxNSIsImciOiJnMSJ9", "status": 200, "groups": ["g2"], "groupNextToken": null}
{"path": "/api/prometheus/grafana/api/v1/rules?group_limit=1&group_next_token=eyJuIjoiZjAxNSIsImciOiJnMSJ9", "status": 200, "groups": ["g2"], "groupNextToken": null}
$ docker rm -f -v gf015-pagecheck
残り: なし
```

応答の形（scratchpad の gf_status.py）:

```
$ docker run d6766ab7f143
health: {'database': 'ok', 'version': '13.2.2', 'commit': '1bea008f7e4e858b6824c9e364d608bd4d10b13a'}
{"path": "/api/prometheus/grafana/api/v1/rules", "http": 200, "top_keys": ["data", "status"], "status": "success", "data_keys": ["groups"], "groups_type": "list"}
folder: 200
rule r1 g1 201
rule r2 g2 201
{"path": "/api/prometheus/grafana/api/v1/rules", "http": 200, "top_keys": ["data", "status"], "status": "success", "data_keys": ["groups", "totals"], "groups_type": "list"}
{"path": "/api/prometheus/grafana/api/v1/rules?group_limit=1", "http": 200, "top_keys": ["data", "status"], "status": "success", "data_keys": ["groupNextToken", "groups"], "groups_type": "list"}
{"path": "/api/prometheus/grafana/api/v1/rules?group_next_token=not-a-token", "http": 200, "top_keys": ["data", "status"], "status": "success", "data_keys": ["groups", "totals"], "groups_type": "list"}
$ docker rm -f -v gf015-statuscheck
残り: なし
```

### 設計からの逸脱

- ページの歯止めの数え方: 最初の実装（062a334）は `len(seen)`（別々のトークンの数）で数えていて、`seen` を消すと終わらなかった（S1）。863fdee で pages を別に数えたが、`pages > MAX_PAGES` で 101 回読んでいた（D3）。9c4dbb5 で `>=` にし、設計の「100 ページ」に合わせた。テストの期待値も 101 から 100 に変えた。元の期待値は実装の回数から書いていて、設計から書いていなかった
- 応答の `status` と `data.groups` の確かめ（D1）: 設計に無かった。2 ページ目の `status: error` を空のページとしてつなぎ、偽の OK になるため、9c4dbb5 で足して design.md の設計方針と検証方法 1 に書き足した（docs/troubleshooting.md の 2 の理由にも）
- 9-2 の検査（検証方法 2 の 3 つ目）: 設計は静的な検査（tf_output・GRAFANA_LEFT の条件・grep -q を使わない）だけ。加えて、ops/up.sh の 9-2 のブロックを偽の terraform で bash に動かすテストを足した（64 KB を超える state、出力が空・読めない、残った analytics の有無）。grep -q の静的検査はコメントの行を除く（コメントに理由として grep -q と書いてある）
- test_alerts のページ分けのテストは、`check()` に偽の時計（`clock` と `sleep`）を渡して `wait=30` で読み直しまで通す（実時間は待たない）

### セルフレビュー

- 自分: opus-5.5 / effort xhigh（`/robust`。入力は design.md と 862fdee〜9c4dbb5 のコード）
- 反対弁護人: opus（`Agent` general-purpose。design.md・build.md のパス、変更ファイルの一覧、実装で選んだ方針、不安な箇所、自分の結論と取り消した判断を渡した。読み取り専用。返ってきたあと `git status --porcelain -uall` は渡す前と同じ）

#### 直したもの

**S1（自分）Should fix [runtime bugs / missing tests]**

- `ops/grafana_rules_check.py` の `fetch`（062a334）。歯止めを `len(seen)` で数えていた
- 破綻: 同じトークンの確かめ（`seen`）が外れると、同じトークンが続いても `len(seen)` が 1 のままで終わらない。M2 を入れた test_alerts は 757 秒たっても終わらなかった（rc=143。上の検証方法 4）
- 直した: 863fdee。ページの数を `seen` と別に数える。M2 は 4 秒で落ちる

**D1（反対弁護人）Should fix [correctness]**

- `ops/grafana_rules_check.py:77-89`。863fdee までは `data = body.get("data") or {}` と `data.get("groups") or []` だった
- 破綻: 2 ページ目が 200 で `status: error`（`data` なし）を返すと、空のページとしてつなぐ。1 ページ目のルールだけで OK になる
- 届く条件: `groupNextToken` が来るとき。13.2.2 の既定では来ない（検証方法 5 の gf_page）
- 再現: scratchpad の p2err_probe.py（nits_probe.py にも同じもの）。偽サーバーの 1 ページ目はルール 1 本とトークン `e`、2 ページ目は `{"status":"error","error":"boom"}`

  ```
  863fdee rc 0 判定: OK（1 本とも評価のエラーなし）
  9c4dbb5 rc 2 判定: 未確認（30 秒待った。Grafana のルールの API が読めない（ValueError: status が success でない（status='error', error=boom）））
  ```

- 直した: 9c4dbb5。どのページでも `status` が `success` で `data.groups` が配列であることを確かめ、違えば `ValueError`
  - 13.2.2 は 200 の応答に必ず両方を付ける。ルールが 0 本でも同じ（検証方法 5 の gf_status）
  - テスト: `/nostatus` と `/grpdict` を `_odd` に足し、2 ページ目の `status: error` のテストも足した
  - M10・M11・M12 は落ちる

**D3（反対弁護人）Should fix [設計整合性]**

- `ops/grafana_rules_check.py:100`。863fdee は `pages > MAX_PAGES` だった
- 破綻: 設計の「100 ページ」に対し 101 回読む
- 直した: 9c4dbb5。`pages >= MAX_PAGES` にし、テストの期待値を 101 から 100 に変えた
  - 元の期待値が誤りだった理由: テストを実装（`>`）の回数から書いていて、設計の「100 ページ」から書いていなかった
  - M9（`>` に戻す）で落ちる

#### PM の判断に回したもの

**D2（反対弁護人）Should fix [設計整合性 / 運用]**

- 場所: `ops/up.sh:1277-1278` と `oss/ops/up.sh:573`（と 7-4b の 464）
- 破綻: 9-2 で analytics の出力が読めないか空だと `exit 1` で止まる
  - 10 の利用者に配るコマンドが出ない
  - 警告の出し直しが出ない（`LAB_WARN` / `NAUTOBOT_WARN` / `WF_WARN` / `COST_NOTE`）
  - ポートフォワーディングに届かない
- 食い違う方針: `ops/up.sh:1235` の「set -e で up.sh ごと止まると、Web の再起動と最後の案内まで届かない」
- 止まること自体は確かめた: test_oss_ops の「ops/up.sh の 9-2（81）」2 件。止まった先が届かないことは読んだだけ（`exit 1` のあとの行）
- 実装で吸収しない理由: 81 の「tf output が失敗したら止める」は PM の決定で、design.md もそう書いている
- 片付け: 「止めずに GRAFANA_WARN に入れて先へ進む形にするか」を PM に聞く

**D8（反対弁護人）Should fix [運用]**

- 場所: `ops/up.sh:1274`
- 破綻: analytics を作らない回で、残った analytics の state の一覧が読めない（state lock など）と、9-2 を黙って飛ばす。警告も出ないので、確かめていないことが利用者に分からない
- 再現: scratchpad の d8_probe.py。9c4dbb5 の 9-2 のブロックを、`state list` が rc=1 の偽の terraform で動かした（`ANALYTICS_LEFT=1`、`GRAFANA=""`）

  ```
  rc 0
  stdout: DONE GRAFANA_LEFT=[] GRAFANA_WARN=[]
  stderr: （なし）
  terraform の呼び出し: -chdir=IaC/terraform/aws-managed/pipeline/analytics state list
  015 の前（fa6234f）の 9-2 の条件: ['if [ -n "$GRAFANA" ]; then']
  ```

- 格下げしない理由: 黙って飛ぶことを再現した。015 の前もこの回は確かめていなかった（偽の OK は出ない）が、それは格下げの根拠にならない
- 片付け: D2 と同じ問い（9-2 で terraform が読めないとき、止めるか、警告にして先へ進むか）なので、D2 と一緒に PM に聞く。止めるなら `exit 1`、進めるなら GRAFANA_WARN に「state が読めず確かめていない。確かめ直すのは ops/check-grafana.sh」を入れる

#### 最終報告に回したもの（Nit。格下げの根拠は実行して取った）

**D4 Nit [runtime bugs]**

- 場所: `ops/grafana_rules_check.py:126-127`
- 破綻: `alerts[].state` が文字列でないと `TypeError` が `check()` の外へ出る。traceback になり、判定の行が出ない
- 格下げの根拠
  - nits_probe.py: `check の外へ出た: TypeError argument of type 'int' is not a container or iterable`
  - 受け側は判定の行が無ければ 2（未確認）。偽の OK にはならない。test_alerts の「grafana_rules_check の終了コード」の `noverdict`（`G_STATUS=Failed`、出力 `Traceback …`）で確かめた

**D5 Nit [performance]**

- 破綻: 1 回の `fetch` は最大 100 ページで、1 ページに 10 秒の timeout がある。`GRAFANA_WAIT`（300）は `fetch` の途中では切らない
- 格下げの根拠
  - 13.2.2 の既定ではトークンが来ない（gf_page。`group_limit` なしで `groupNextToken: null`）
  - 来ても、上限は `SSM_RUN_WAIT` で 2 になる（test_alerts の「ssm_run: InProgress のまま SSM_RUN_WAIT 秒を過ぎたら…」）

**D6 Nit [runtime bugs]**

- 破綻: SSM の `StandardOutputContent` は 24000 字で切れる。切れると判定の行が落ちる
- 格下げの根拠: nits_probe.py で最悪の形を測った
  - 形: ルール 4 本が全部エラー、lastError 5000 字（300 字に切る）、状態 8 種 × 50
  - 結果: `rc 1 行数 9 文字数 2717 UTF-8 のバイト数 2943`。最後の行は `判定: NG（4 本のうち 4 本の評価がエラー: nwc-poc-alerts/link_down、nwc-poc-alerts/bgp_down、nwc-poc-trap/isis_down、nwc-poc-trap/trap）`
  - 切れても判定の行が無いので 2 になる（D4 と同じ受け側）

**D7 Nit [運用]**

- 破綻: `SSM_RUN_WAIT` は 9-2 だけでなく ssm_run の全部の呼び出しに効く（ops/up.sh 7 か所、oss/ops/up.sh 7 か所）。既定 1800 は、前の実質の上限（executionTimeout 3600）より短い
- 格下げの根拠
  - 設計で決めたこと（design.md:91。cloud-init は Web 3〜5 分、lab 約 10 分）
  - リスクとしても書いてある（design.md:163）

**D9 Nit [docs]**

- `docs/deploy.md:73` の例 `SSM_RUN_WAIT=3600` は、executionTimeout と同じ値
- `ops/check-grafana.sh:14` の 3 の説明に「`SSM_RUN_WAIT` の値の誤り」が無い（`docs/troubleshooting.md` の表と test_oss_ops にはある）
- `docs/troubleshooting.md:108` の 2 の理由に 100 ページと `status` が無かった（9c4dbb5 で直した）

**D10 Nit [runtime bugs]**

- 破綻: `SSM_RUN_WAIT` が 64 ビットを超えると、bash の算術が折り返す
- 格下げの根拠: nit10.py。bash 3.2.57 で測った

  ```
  SSM_RUN_WAIT=99999999999999999999: 検査は通る / deadline=7766279631452241919 / 締め切りはまだ
  SSM_RUN_WAIT=9223372036854775808: 検査は通る / deadline=-9223372036854775808 / すぐ締め切り
  ```

  - 前者は締め切りが無いのと同じで、015 の前の振る舞いになる
  - 後者は 1 回目の読みで 2 を返す。get-command-invocation の案内を出す
  - どちらも偽の OK にはならない

**N1（自分）Nit [runtime bugs]**

- 場所: `ops/up.sh:709` と `733` の `tf … state list | grep -q`
- 破綻: 9-2 で避けた SIGPIPE の形と同じ
- 格下げの根拠: 015 の前からある行で、9-2 の外（手順 3）。設計の範囲外

**N2（自分）Nit [運用]**

- 場所: `ops/sync-graph.sh:54-70`
- 破綻: ssm_run の写しを持っていて、締め切りが無い（`SSM_RUN_WAIT` が効かない）
- 格下げの根拠: design.md の変更対象に入っていない

**N3（自分）Nit [運用]**

- 破綻: `oss/ops/up.sh` は、9-2 と 7-4b のほかでも `tf … output -raw` をそのまま読んでいる（空でも止まらない）
- 格下げの根拠: 015 の前からある。81 の範囲は 9-2 だけ

**N4（自分）Nit [missing tests]**

- 場所: 9-2 の `|| exit 1`（`ops/up.sh:1277-1278`）
- 破綻: 消してもテストが落ちない（M6 rc=0）
- 格下げの根拠: M6 を入れても、test_oss_ops の「ops/up.sh の 9-2（81）: … 読めない … 止まる（1）」は通る。トップレベルの `set -e` が同じ所で止めるので、振る舞いは変わらない

#### 不成立とした反対弁護人の論点（反対弁護人が自分で実行して取り消したもの）

- M6 と `set -e`
- M3 のテストの重なり（gstep は 1 つの関数）
- `SECONDS` の使い方
- 呼ぶ側の全部に `die` があるか
- パスワードが漏れる道
- 判定の行の偽装
- F（81）に届くか
- 空の出力で成功したら 2 か
- `DeliveryTimedOut` / `Cancelled` / `TimedOut` で 2 か
- grep のロケール
- oss の 7-4b で止まるか
- `check-grafana.sh` の `set -u`

#### 問題なしとした観点と根拠

- 設計整合性: design.md の検証方法 1〜5 を全部打った（上）。逸脱は「設計からの逸脱」の 4 件
- 退行の縛り: 注入 M1〜M12。M3・M6 のほかは、狙ったテストで落ちる。M3 は test_alerts が受ける。M6 は N4
- 実物との突き合わせ: 手元の Grafana 13.2.2 で、ページ分けと `status` / `groups` の形を確かめた（検証方法 5）
- security: パスワードは Authorization ヘッダーにだけ入る。例外の文と出力には出ない。test_alerts の make_fetch の 2 件（実行）

## Round 2

実装モデル: opus-5.5 / effort: xhigh（high へ下げる手段が無い。`set_session_effort` は自分のセッションを変えられない。`get_session self` の effort は xhigh）

入力: Round 1 のセルフレビューの D2・D8 への PM の判断（2026-10-09）。9-2 で terraform が読めない（tf output の失敗・空、state list の失敗）ときは `exit 1` にせず、`GRAFANA_WARN` に「確かめていない」を入れ、`aws ecs wait` とルールの確かめを飛ばして最後の案内まで進む。design.md の E・F・検証方法 2 と 4・リスク 8 を上書きし、design-log.md に Round 1 を書いた

### commit

| commit | 内容 |
| :--- | :--- |
| 91e1740 | design.md・design-log.md（Round 1）、`grafana_skip_warn`、`ops/up.sh` と `oss/ops/up.sh` の 9-2、test_oss_ops、troubleshooting.md と grafana.md の理由 |
| 05c1cec | セルフレビュー #2: 9-2 を打つ回と打たない回のテストで `GRAFANA_WARN` が空のままかも見る（先に design.md の検証方法 2 と 4 に書き足した） |
| 8da0db0 | docs/cycle-006-design（5be0288。012 が入った）をマージ。衝突は docs/development.md のテストの本数だけ（docs/cycle-006-design 側を採り、この commit で下の check.sh の実測に合わせた） |
| （この commit） | build.md の Round 2、design-log.md の Round 2、development.md のテストの本数 |

### 変更ファイル（`git diff --stat 6ed639b 05c1cec`。このラウンドの分。8da0db0 のマージで入った 012 の分は含めない）

```
 docs/architecture/resources/grafana.md             |  2 +-
 .../015-grafana-check-followups/design-log.md      | 22 ++++++++
 docs/cycles/015-grafana-check-followups/design.md  | 50 ++++++++++-------
 docs/troubleshooting.md                            |  2 +-
 ops/up-common.sh                                   |  9 ++-
 ops/up.sh                                          | 29 ++++++----
 oss/ops/up.sh                                      | 10 +++-
 tests/test_oss_ops.py                              | 64 +++++++++++++++-------
 8 files changed, 133 insertions(+), 55 deletions(-)
```

ほかに docs/development.md のテストの本数と design-log.md の Round 2（この build.md と同じ commit）。

### 012 との噛み合わせ（8da0db0）

012 が変えた行（`git diff -U0 05c1cec 8da0db0 -- ops/up.sh ops/up-common.sh oss/ops/up.sh` の hunk の見出し）:

```
+++ b/ops/up-common.sh
@@ -2,2 +2,2 @@
@@ -252,0 +253,4 @@
@@ -267,0 +272,3 @@
@@ -329,0 +337,67 @@
+++ b/ops/up.sh
@@ -47 +47 @@
@@ -58 +58 @@
@@ -60 +60 @@
@@ -74,2 +73,0 @@
@@ -165,0 +164 @@
@@ -222 +221 @@
@@ -273 +272 @@
@@ -296,5 +295,4 @@
@@ -449 +447 @@
@@ -474,0 +473 @@
@@ -506,2 +505,3 @@
@@ -541,3 +541,4 @@
@@ -569 +570 @@
@@ -615,2 +616,3 @@
@@ -632,0 +635,4 @@
@@ -648 +654 @@
@@ -652,2 +658,2 @@
@@ -693,0 +700,7 @@
@@ -700 +712,0 @@
@@ -905 +917 @@
@@ -919,0 +932,3 @@
@@ -921,0 +937 @@
@@ -941 +957 @@
@@ -954,0 +971,7 @@
+++ b/oss/ops/up.sh
@@ -12 +12 @@
@@ -20 +20 @@
@@ -74,0 +75 @@
@@ -162 +163 @@
@@ -168,0 +170,4 @@
@@ -197 +202 @@
@@ -202 +207 @@
@@ -217,0 +223,6 @@
@@ -250 +260,0 @@
@@ -316 +326 @@
@@ -333 +343 @@
@@ -349 +359 @@
@@ -371,0 +382,7 @@
```

- 9-2（`ops/up.sh:1290-1311`、`oss/ops/up.sh:589-595`）、`tf_output`（`ops/up-common.sh:24-30`）、`grafana_skip_warn`（`ops/up-common.sh:310-315`）には触れていない
- 9-2 の前提（GRAFANA=1 の回は analytics のルートが丸ごと読めなければ 7-4 で止まる）は変わっていない。7-4 の `ops/up.sh:1112` の行を 8da0db0 から切り出し、同じ偽の terraform で打った（scratchpad の sete_probe.py）

```
切り出した行: APP_ID=$(tf pipeline/analytics output -raw application_id); echo "APP_ID=$APP_ID"
rc 1
stdout: ['bash 3.2.57(1)-release']
stderr: ['Error: Failed to load state: unsupported checkable object kind']
```

- 9-2 の振る舞いは bash の版で変わらない。8da0db0 の 9-2 を 8 つの回で bash 3.2.57 と 5.1.16 に打った（scratchpad の m92_bash5.py。各回の 1 行目。全文は rc・`WARN=[…]`・terraform の呼び出しが両方の版で同じ、trap は `TRAP 0` だけ）

```
== made bash3.2 rc=0 calls=['analytics_cluster_name', 'grafana_service_name']
== made bash5.1 rc=0 calls=['analytics_cluster_name', 'grafana_service_name']
== left bash3.2 rc=0 calls=['list', 'analytics_cluster_name', 'grafana_service_name']
== left bash5.1 rc=0 calls=['list', 'analytics_cluster_name', 'grafana_service_name']
== left_nogf bash3.2 rc=0 calls=['list']
== left_nogf bash5.1 rc=0 calls=['list']
== none bash3.2 rc=0 calls=[]
== none bash5.1 rc=0 calls=[]
== empty_svc bash3.2 rc=0 calls=['analytics_cluster_name', 'grafana_service_name']
== empty_svc bash5.1 rc=0 calls=['analytics_cluster_name', 'grafana_service_name']
== left_fail_svc bash3.2 rc=0 calls=['list', 'analytics_cluster_name', 'grafana_service_name']
== left_fail_svc bash5.1 rc=0 calls=['list', 'analytics_cluster_name', 'grafana_service_name']
== fail_cl bash3.2 rc=0 calls=['analytics_cluster_name']
== fail_cl bash5.1 rc=0 calls=['analytics_cluster_name']
== left_unread bash3.2 rc=0 calls=['list']
== left_unread bash5.1 rc=0 calls=['list']
```

### 検証方法（8da0db0 の作業ツリー）

#### 1. test_alerts（3 の check.sh の出力から、Grafana の確かめの分）

```
ok ルールの確かめ: Normal・NoData（KeepLast も）・Alerting・アラート無しはエラーではない。打ったあとの評価が全部そろえば OK（0）
ok ルールの確かめ: 「Normal (Error, KeepLast)」が打ったあとの 2 回の評価で続けば NG（1）。どのルールか、状態ごとの数も出す
ok ルールの確かめ: health が error、lastError が空でない、state が Error で始まる（execErrState: Error のとき）、系列のあるルールのエラー（Alerting (Error, KeepLast)）も NG
ok ルールの確かめ: 打ったときに見える評価（データソースを直す前・壊れる前のものかもしれない）では判定せず、lastEvaluation が変わる（次の評価）まで待つ
ok ルールの確かめ: エラーは、そのルールがもう 1 回評価されてもエラーのときだけ NG。直した直後に 1 回分残るエラーでは NG にしない
ok ルールの確かめ: まだ評価されていない（lastEvaluation が 0001- か空）ルールがあれば 10 秒おきに読み直し、全部そろってから判定する（立てた直後は最初の評価で判定する）
ok ルールの確かめ: 待つ秒数（GRAFANA_WAIT）までに評価がそろわなければ未確認（2）。どのルールを待っていたかを出す
ok ルールの確かめ: Grafana が起動中（つながらない）・途中で切れた応答・JSON でない応答は待って読み直し、ずっと読めなければ理由を付けて未確認（2）
ok ルールの確かめ: 401 / 403 は待たずに未確認（2。待っても直らない）
ok ルールの確かめ: make_fetch は admin の Basic 認証でルールの API を読み（環境変数のプロキシは通さない）、401 は Unauthorized にする。パスワードは例外の文にも出力にも出さない
ok ルールの確かめ: make_fetch はリダイレクト先に Authorization（パスワード）を送らない。JSON がオブジェクトでなければ ValueError（check が待って読み直す）
ok ルールの確かめ（ページ分け）: data.groupNextToken があれば group_next_token（URL エンコードする）で次のページを読み、グループをつなぐ。どのページにも Authorization を付け、group_limit は送らない（13.2.3 の既定 -1 は全部を 1 ページで返す）
ok ルールの確かめ（ページ分け）: 判定は全部のページのルールで出す（2 ページ目のルールだけがエラーでも NG。1 ページ目だけなら OK になる形）
ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
ok ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
ok ルールの確かめ（ページ分け）: 2 ページ目が 200 で status: error を返し続けたら、1 ページ目のルールだけで OK にせず、待ち切れたら未確認（2）
ok ルールの確かめ（main）: NAME_PREFIX が無ければ止まる。Grafana は Cloud Map の grafana.<接頭辞>.internal:3000、パスワードは SSM の値、待つのは既定 300 秒。SSM が読めなければ例外の型だけを出して未確認（2）
ok ルールの確かめ: 読むのは SSM の /<接頭辞>/grafana/admin-password（Web の EC2 のロールが読める範囲）と Web と同じ環境変数・boto3。標準ライブラリのほかは boto3 だけ
ok 9-2（grafana_rules_step）: Grafana のサービスが安定してから、Web の EC2 に ops/grafana_rules_check.py そのものを NAME_PREFIX 付きで 1 回送る。OK なら判定を出し、警告は空
ok 9-2: 送るコマンドは JSON の文字列に埋めるので、ダブルクォートもバックスラッシュも含まない（ssm_run の約束）
ok 9-2: NG なら止めずに（0）、判定の行（タブの手前まで）とログの見方（/ecs/<接頭辞>-grafana の Failed to evaluate rule）と確かめ直すコマンドを警告に入れ、黄色で出す
ok 9-2: Grafana のサービスが安定しなければ確かめを送らず（0）、安定しない旨とタスクの見方を警告に入れる
ok 9-2: SSM Run Command を送れなければ、待ち続けずに（set -e の効かない $( ) の中でも ssm_run が 1 を返す）未確認の警告（判定の行が無い。ログの案内なし）を出す
ok 9-2: 判定が未確認なら、確かめられなかった旨（判定の行。タブの手前まで）と確かめ直すコマンドを警告に入れる。評価のエラーとは限らないので Grafana のログは案内しない
ok grafana_rules_check の終了コード: OK の判定で SSM も成功なら 0、NG の判定なら 1、未確認・判定の行が無い・送れないは 2。最後の判定の行を GRAFANA_VERDICT に置く
ok ssm_run: InProgress のまま SSM_RUN_WAIT 秒を過ぎたら、待つのをやめて結果の見方（get-command-invocation）を出し、9-2 は未確認の警告にする
ok ssm_run: get-command-invocation が読めないまま SSM_RUN_WAIT 秒を過ぎても同じく返す（最後の状態: 読めない）
ok ssm_run: 送った直後に get-command-invocation が読めなくても、読めるまで読み直す（締め切りの中なら OK になる）
ok SSM_RUN_WAIT が 1 以上の整数でなければ、ops/up-common.sh を読んだところで理由を言って止まる（aws には触らない）
通過 169 / 失敗 0
```

#### 2. test_oss_ops（3 の check.sh の出力から、9-2 と check-grafana.sh の分）

```
ok up.sh（通し）: 最後（9-2）に Grafana のサービスが安定してから、Web の EC2 に ops/grafana_rules_check.py そのものを接頭辞 x-nwc-oss で 1 回送る。OK なら警告は出ない
ok up.sh（Grafana のルールの評価がエラー）: 止めずに（0）警告を出し、最後にもう一度出す。警告には判定の行（タブの手前まで）とログの見方（/ecs/x-nwc-oss-grafana）と確かめ直すコマンド（ops/check-grafana.sh --oss）
ok ops/up.sh（マネージド版）の workflow の待ちも 2 回までで、安定しなければ警告（WF_WARN）を出して先へ進み、最後にもう一度出す
ok ops/up.sh（マネージド版）も Runtime のロググループのあと（9-2）に同じ確かめを打ち、OK でなければ警告（GRAFANA_WARN）を最後にもう一度出す。state の一覧は grep -q で見ない（先に抜けると terraform が SIGPIPE になり、pipefail で偽になる）
ok ops/up.sh の 9-2: Grafana を今回作る（GRAFANA）なら、state を見ずに analytics の出力のクラスターとサービスで grafana_rules_step を 1 回打つ
ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
ok ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
ok ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して先へ進む。grafana_rules_step（aws ecs wait と確かめ）は打たない
ok ops/up.sh の 9-2（81）: analytics の出力が読めない（terraform output が rc≠0）なら、terraform のエラーを残し、止めずに（0）確かめていないと警告して先へ進む
ok ops/up.sh の 9-2（83・D8）: 今回は analytics を作らない回で、残った analytics の state の一覧が読めない（state list が rc≠0）なら、黙って飛ばさず、見出しにそう書いて、止めずに（0）確かめていないと警告する。出力は読まず、grafana_rules_step も打たない
ok ops/up.sh の 9-2（81）: terraform が読めなくても up.sh を止めない（|| exit 1 を書かない。止めると配るコマンドと警告の再掲まで届かない。8-5 と同じ）
ok oss/ops/up.sh（81）: 9-2 で analytics の出力 grafana_service_name が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して配るコマンドまで進み、警告は最後にもう一度出す。ecs wait も確かめの送信も打たない
ok oss/ops/up.sh（81）: 7-4b の analytics のクラスターの名前も tf_output で読み、読めない・空なら 7-4b で止まる（9-2 もこのクラスター）。9-2 のサービスの名前は読めなくても止めず、grafana_skip_warn で警告する
ok up.sh → down.sh: up.sh が作った SSM のパラメータ 14 個を全部消し、up.sh が apply した 9 つのルートを全部 destroy する
ok oss/ops/up.sh と down.sh の terraform init は、どのルートも -lockfile=readonly（lock はマネージド版へのシンボリックリンクなので書き換えない）
ok IaC/terraform/oss/ の .terraform.lock.hcl は、どのルートもマネージド版の lock へのシンボリックリンク（実ファイルにしない）
ok up.sh（通し）が 9 つのルートに渡した -var の名前は、どれも IaC/terraform/oss/<ルート>/*.tf の variable で宣言されている（owner を含めて全部）
ok down.sh が destroy に渡した -var（workflow の worker_image_tag、stream の snmp_agents / gnmi_targets、owner）も、そのルートの variable で宣言されている
ok up.sh: readonly の init が止まったら apply せずに止まり、先にマネージド版のルートを init する案内（terraform -chdir=IaC/terraform/aws-managed/base/ecr init）を出す
ok sync-graph.sh --oss: Web の EC2 の id を IaC/terraform/oss/base/core の出力から取り、接頭辞 x-nwc-oss と lab のトポロジで ops/seed_graph.py を送る（GRAPH_REPLACE=0）
ok sync-graph.sh --oss --replace: 同じ送り先で GRAPH_REPLACE=1（入っていても lab の定義で入れ直す）
ok sync-graph.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed/base/core と接頭辞 x-nwc-poc のまま（OSS 版の追加で変わらない）
ok sync-graph.sh --oss --dry-run: terraform にも aws にも触らず、lab から作ったトポロジ JSON を出すだけ
ok sync-graph.sh: 知らない引数は使い方を出して 2 で止まる（terraform と aws には触らない）
ok check-grafana.sh --oss: Web の EC2 と Grafana のサービスを IaC/terraform/oss の出力から取り、接頭辞 x-nwc-oss で ops/grafana_rules_check.py を 1 回送る。OK なら 0
ok check-grafana.sh（--oss 無し）: マネージド版の IaC/terraform/aws-managed と接頭辞 x-nwc-poc
ok check-grafana.sh: NG なら 1 で終わり、判定の行と、理由を見る Grafana のログのコマンド（/ecs/x-nwc-oss-grafana の Failed to evaluate rule）を出す
ok check-grafana.sh: 未確認（判定: 未確認。届かない・401・待ち切れ）なら 2 で終わり、判定の行を出す。評価のエラーとは限らないので Grafana のログは案内しない
ok check-grafana.sh: Grafana が無い（grafana_service_name が空）なら理由を言って 3 で止まり、確かめを送らない
ok check-grafana.sh: deploy.env の誤り（load_deploy_env の die）も 3 で止まる（ops/common.sh の die の 1 は NG と紛れる）
ok check-grafana.sh: SSM_RUN_WAIT の値の誤りも 3 で止まる（確かめを送らない）
ok check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）
通過 194 / 失敗 0
```

#### 3. check.sh（8da0db0。未コミットの変更なし。scratchpad の run015.py で打った）

rc 0。見出しと各テストの最後の行（標準出力。テストの並びは check.sh の順）:

```
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. スクリプトの構文
== 4. 模擬テスト
通過 169 / 失敗 0            test_alerts
通過 495 / 失敗 0            test_analytics
通過 161 / 失敗 0            test_app
通過 78 / 失敗 0             test_collectors
通過 3 / 失敗 0              test_dashboard_config
通過 78 / 失敗 0             test_graph
通過 7 / 失敗 0              test_kb_index
通過 104 / 失敗 0            test_lab_debug
通過 132 / 失敗 0            test_local_compose
69 項目すべて通過            test_nautobot
通過 172 / 失敗 0            test_oss
通過 194 / 失敗 0            test_oss_ops
通過 66 / 失敗 0             test_oss_roll
通過 95 / 失敗 0             test_stream
通過 103 / 失敗 0            test_sync
通過 327 / 失敗 0            test_workflow
すべて通過
```

#### 4. 退行の注入（8da0db0 の作業ツリーで、1 件ずつ入れて走らせ、`finally` で戻す）

このラウンドの分（scratchpad の inject015_r2.py。test_oss_ops）:

```
== R1 ops/up.sh の grafana_skip_warn を exit 1 にする（止める形に戻す） / test_oss_ops: rc=1 77s
   最後に通った: ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
   落ちた: AssertionError: ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して先へ進む。grafana_rules_step（aws ecs wait と確かめ）は打たない
== R2 oss/ops/up.sh の grafana_skip_warn を exit 1 にする / test_oss_ops: rc=1 83s
   最後に通った: ok ops/up.sh の 9-2（81）: terraform が読めなくても up.sh を止めない（|| exit 1 を書かない。止めると配るコマンドと警告の再掲まで届かない。8-5 と同じ）
   落ちた: AssertionError: oss/ops/up.sh（81）: 9-2 で analytics の出力 grafana_service_name が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して配るコマンドまで進み、警告は最後にもう一度出す。ecs wait も確かめの送信も打たない
== R3 GF_UNREAD を立てない（state list の失敗を黙って飛ばす。D8 の形） / test_oss_ops: rc=1 77s
   最後に通った: ok ops/up.sh の 9-2（81）: analytics の出力が読めない（terraform output が rc≠0）なら、terraform のエラーを残し、止めずに（0）確かめていないと警告して先へ進む
   落ちた: AssertionError: ops/up.sh の 9-2（83・D8）: 今回は analytics を作らない回で、残った analytics の state の一覧が読めない（state list が rc≠0）なら、黙って飛ばさず、見出しにそう書いて、止めずに（0）確かめていないと警告する。出力は読まず、grafana_rules_step も打たない
== R4 GF_UNREAD でも出力を読みに行く（[ -z "$GF_UNREAD" ] を消す） / test_oss_ops: rc=1 77s
   最後に通った: ok ops/up.sh の 9-2（81）: analytics の出力が読めない（terraform output が rc≠0）なら、terraform のエラーを残し、止めずに（0）確かめていないと警告して先へ進む
   落ちた: AssertionError: ops/up.sh の 9-2（83・D8）: 今回は analytics を作らない回で、残った analytics の state の一覧が読めない（state list が rc≠0）なら、黙って飛ばさず、見出しにそう書いて、止めずに（0）確かめていないと警告する。出力は読まず、grafana_rules_step も打たない
== R5 grafana_skip_warn が GRAFANA_WARN に入れない（出すだけ。最後の再掲が消える） / test_oss_ops: rc=1 75s
   最後に通った: ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
   落ちた: AssertionError: ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して先へ進む。grafana_rules_step（aws ecs wait と確かめ）は打たない
== M5' state list をパイプの grep -q に戻す（静的検査は外して振る舞いのテストまで進める） / test_oss_ops: rc=1 76s
   最後に通った: ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
   落ちた: AssertionError: ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
== R6 9-2 の外側の if に else grafana_skip_warn を足す（Grafana が無い回にも警告する） / test_oss_ops: rc=1 79s
   最後に通った: ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
   落ちた: AssertionError: ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
== M7 tf_output の空の確かめを消す / test_oss_ops: rc=1 75s
   最後に通った: ok ops/up.sh の 9-2（83）: state の一覧が長くても（パイプの 64 KB を超えても）Grafana を見つける（grep -q だと terraform が SIGPIPE で落ち、pipefail で見落とす）
   落ちた: AssertionError: ops/up.sh の 9-2（81）: analytics の出力（クラスターかサービスの名前）が空なら、どれのことかを言い（NG: の行）、止めずに（0）確かめていないと警告して先へ進む。grafana_rules_step（aws ecs wait と確かめ）は打たない
== M8' GRAFANA_LEFT の ANALYTICS_LEFT の条件を消す / test_oss_ops: rc=1 76s
   最後に通った: ok ops/up.sh の 9-2（83）: 今回は analytics を作らない回（PIPELINE=0 など）でも、残った analytics（ANALYTICS_LEFT）の state に Grafana の ECS サービスがあれば、見出しにそう書いて同じく打つ
   落ちた: AssertionError: ops/up.sh の 9-2（83）: 残った analytics に Grafana の ECS サービスが無ければ（aws_ecs_service.grafana[…] だけを見る）打たない。analytics が残っていなければ state も見ない
戻した
```

Round 1 の分の取り直し（scratchpad の inject015.py。M5〜M8 は 9-2 の形が変わって元の差し替え先が無いので、上の M5'・M7・M8' と R1・R2 に置き換えた）:

```
== M1 ページを追わない / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ: make_fetch はリダイレクト先に Authorization（パスワード）を送らない。JSON がオブジェクトでなければ ValueError（check が待って読み直す）
   落ちた: AssertionError: ルールの確かめ（ページ分け）: data.groupNextToken があれば group_next_token（URL エンコードする）で次のページを読み、グループをつなぐ。どのページにも Authorization を付け、group_limit は送らない（13.2.3 の既定 -1 は全部を 1 ページで返す）
== M2 seen を消す / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: 判定は全部のページのルールで出す（2 ページ目のルールだけがエラーでも NG。1 ページ目だけなら OK になる形）
   落ちた: AssertionError: ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
== M3 未確認の警告を NG と同じにする / test_alerts: rc=1 5s
   最後に通った: ok 9-2: Grafana のサービスが安定しなければ確かめを送らず（0）、安定しない旨とタスクの見方を警告に入れる
   落ちた: AssertionError: 9-2: SSM Run Command を送れなければ、待ち続けずに（set -e の効かない $( ) の中でも ssm_run が 1 を返す）未確認の警告（判定の行が無い。ログの案内なし）を出す
== M3 未確認の警告を NG と同じにする / test_oss_ops: rc=0 89s
   最後に通った: ok check-grafana.sh: 知らない引数は使い方を出して 3 で止まる（terraform と aws には触らない）
   落ちた: 通過 194 / 失敗 0
== M4 ssm_run の締め切りを消す / test_alerts: rc=1 27s
   最後に通った: ok 9-2: 判定が未確認なら、確かめられなかった旨（判定の行。タブの手前まで）と確かめ直すコマンドを警告に入れる。評価のエラーとは限らないので Grafana のログは案内しない
   落ちた: AssertionError: grafana_rules_check の終了コード: OK の判定で SSM も成功なら 0、NG の判定なら 1、未確認・判定の行が無い・送れないは 2。最後の判定の行を GRAFANA_VERDICT に置く
（M5 で止まった。差し替え先が 0 件: AssertionError: ('M5 grep -q に戻す（静的検査は外して振る舞いのテストまで進める）', 'ops/up.sh', "grep '^aws_ecs_service\\.grafana\\[' >/dev/null", 0)。M9〜M12 を引数で打ち直した↓）
== M9 ページの歯止めを 1 つずらす（前の形に戻す） / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
== M10 status の確かめを消す / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
== M12 status の確かめを消す（1 ページ目の {} の検査は外して、2 ページ目の status: error のテストまで進める） / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups 
   落ちた: AssertionError: ルールの確かめ（ページ分け）: 2 ページ目が 200 で status: error を返し続けたら、1 ページ目のルールだけで OK にせず、待ち切れたら未確認（2）
== M11 groups の確かめを消し、空のページとしてつなぐ / test_alerts: rc=1 5s
   最後に通った: ok ルールの確かめ（ページ分け）: 同じ groupNextToken が 2 回来たら読めなかったとして扱い、待ち切れたら未確認（2）。読んだのは 2 ページだけ
   落ちた: AssertionError: ルールの確かめ（ページ分け）: トークンが毎回変わって終わらなければ 100 ページ（最初の 1 ページを含めて 100 回読む）で止めて未確認（2）。data がオブジェクトでない・トークンが文字列でない・status が success でない・data.groups が配列でないも ValueError で未確認（落ちない）
戻した
```

- R6 はセルフレビューの #2（下）。05c1cec の前は通っていた（反対弁護人の devil_r2/e2.py）
- M3 は test_alerts が受ける（Round 1 と同じ。ops/check-grafana.sh は `grafana_rules_step` を使わない）
- 戻したあとの `git status --porcelain -uall`（docs/development.md と design-log.md はこの commit に入れる）:

```
 M docs/cycles/015-grafana-check-followups/design-log.md
 M docs/development.md
```

#### 5. 手元の Grafana 13.2.2

未実行（`ops/grafana_rules_check.py` はこのラウンドで変えていない。Round 1 の検証方法 5 の出力のまま）。

### 設計からの逸脱

- 無し。セルフレビューで採った #2 は、先に design.md の検証方法 2 と 4 に書き足してからテストを直した（05c1cec。design-log.md の Round 2 に 1 行）
- Round 1 の N4（9-2 の `|| exit 1` を消してもテストが落ちない）は、`|| exit 1` が無くなったので無くなった。代わりに静的な検査（9-2 に `|| exit 1` が無い）と R1・R2 が止める形への戻りを落とす

### セルフレビュー

- 自分: opus-5.5 / effort xhigh（`/robust`。入力は design.md と 91e1740 のコード）
- 反対弁護人: opus（`Agent` general-purpose。design.md・build.md のパス、変更ファイルの一覧、PM の判断と実装の方針、迷った点 9 つ、自分の結論を渡した。読み取り専用。返ってきたあと `git status --porcelain -uall` は渡す前と同じ）
- 直すか見送るかは自分で決めた（2026-10-09 のユーザー指示。セルフレビューの指摘は実装したエンジニアが決める）。直すものは先に design.md に入れた
- 行番号は 8da0db0 のもの

#### 直したもの

**#2（反対弁護人）Should fix [missing tests]**

- 場所: `tests/test_oss_ops.py:1477-1489`（9-2 を打つ回 made / left / left_big と、打たない回 left_nogf / none）
- 破綻: 9-2 の外側の `if` に `else grafana_skip_warn ops/check-grafana.sh` を足す退行（Grafana の無い回すべてで、偽の「確かめていない」の黄色い警告が 2 回出る）を入れても、テストが通った。5 つの回とも `GRAFANA_WARN` を見ていなかった
- 再現（反対弁護人の devil_r2/e2.py。91e1740）: 元のまま「判定 True、`WARN=[]`」、退行を注入「判定 True、`WARN=[IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない…`」
- 直した: 05c1cec
  - 先に design.md の検証方法 2 に「どれも `GRAFANA_WARN` は空のまま」を、4 にこの退行の注入を足した（検証項目の追加。設計方針・範囲は変えていない。design-log.md の Round 2 に 1 行）
  - テストは 5 つの回の出力に `WARN=[]` があることを見る
  - R6（この退行）は「残った analytics に Grafana の ECS サービスが無ければ…打たない」で落ちる（上の 4）

#### 見送ったもの（Nit。格下げの根拠は実行して取った）

**#1（反対弁護人）Nit [runtime bugs]**

- 場所: 手順 10 の `tf pipeline/analytics output -raw grafana_port_forward_command`（`ops/up.sh:1328`、`oss/ops/up.sh:615`）。守りが無い
- 破綻: analytics の state が丸ごと読めないと、9-2 は警告して進むが、手順 10 で `set -e` により rc=1 で止まる。警告の再掲と `COST_NOTE` に届かない。最後の赤い行が 9-2 の `NG:` なので、9-2 で止まったと読み違える
- 格下げの根拠（実行）
  - 8da0db0 の 9-2 から手順 10 の最後の警告の再掲までを、偽の terraform で打った（反対弁護人の devil_r2/e1.py）。止まるのは「丸ごと読めない」だけで、ほかの 3 つは rc=0 で最後まで届く

```
===== GRAFANA=1, analytics の出力が全部読めない（state が壊れた形） rc= 1
--- stdout

== 9-2. Grafana のアラートルールが評価でエラーになっていないかを確かめる（Web の EC2 から Grafana のルールの API を読む。最大 5 分）
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh

== できた（x）。利用者に配るコマンド:
out-start_session_command
Grafana（http://localhost:3000/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:

--- stderr
Error: Failed to load state: unsupported checkable object kind
NG: IaC/terraform/aws-managed/pipeline/analytics の出力 analytics_cluster_name が読めない（上のエラー）
Error: Failed to load state: unsupported checkable object kind

--- calls
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw analytics_cluster_name
-chdir=IaC/terraform/aws-managed/base/core output -raw start_session_command
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_port_forward_command
===== GRAFANA=1, analytics_cluster_name だけ not found rc= 0
--- stdout

== 9-2. Grafana のアラートルールが評価でエラーになっていないかを確かめる（Web の EC2 から Grafana のルールの API を読む。最大 5 分）
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh

== できた（x）。利用者に配るコマンド:
out-start_session_command
Grafana（http://localhost:3000/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:
out-grafana_port_forward_command
out-grafana_password_command
LABW
WFW
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh
COST
WARN=[IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh]
DONE

--- stderr
Error: Output "analytics_cluster_name" not found
NG: IaC/terraform/aws-managed/pipeline/analytics の出力 analytics_cluster_name が読めない（上のエラー）

--- calls
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw analytics_cluster_name
-chdir=IaC/terraform/aws-managed/base/core output -raw start_session_command
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_port_forward_command
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_password_command
===== GRAFANA=1, grafana_service_name が空 rc= 0
--- stdout

== 9-2. Grafana のアラートルールが評価でエラーになっていないかを確かめる（Web の EC2 から Grafana のルールの API を読む。最大 5 分）
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh

== できた（x）。利用者に配るコマンド:
out-start_session_command
Grafana（http://localhost:3000/ 。ユーザー admin）を開くポートフォワード（web の EC2 を踏み台にする）と admin のパスワード:
out-grafana_port_forward_command
out-grafana_password_command
LABW
WFW
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh
COST
WARN=[IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh]
DONE

--- stderr
NG: IaC/terraform/aws-managed/pipeline/analytics の出力 grafana_service_name が空

--- calls
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw analytics_cluster_name
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_service_name
-chdir=IaC/terraform/aws-managed/base/core output -raw start_session_command
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_port_forward_command
-chdir=IaC/terraform/aws-managed/pipeline/analytics output -raw grafana_password_command
===== LEFT, state list 失敗 rc= 0
--- stdout

== 9-2. Grafana のアラートルールが評価でエラーになっていないかを確かめる（Web の EC2 から Grafana のルールの API を読む。最大 5 分）。今回は analytics を作らないが、前の回の analytics の state が読めない
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh

== できた（x）。利用者に配るコマンド:
out-start_session_command
LABW
WFW
IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh
COST
WARN=[IaC/terraform/aws-managed/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh]
DONE

--- stderr
Error: Failed to load state

--- calls
-chdir=IaC/terraform/aws-managed/pipeline/analytics state list
-chdir=IaC/terraform/aws-managed/base/core output -raw start_session_command
```

  - GRAFANA=1 の回は、丸ごと読めなければ 9-2 より前に 7-4 の `ops/up.sh:1112`（守りなし）で止まる（上の「012 との噛み合わせ」の sete_probe）。OSS 版は 7-4b の `oss/ops/up.sh:481` で止まる
  - ANALYTICS_LEFT の回は、手順 10 の Grafana の塊が `[ -n "$GRAFANA" ]` なので読まない（e1 の 4 つ目の calls に `grafana_port_forward_command` が無い）
  - 届くのは、7-4 から手順 10 までの間に state が壊れたときだけ
- 見送る理由: 手順 10 は design.md の範囲（9-2）の外。直すなら design.md の E に手順 10 を入れることになり範囲が変わるので、PM に報告する

**#3（反対弁護人）Nit [docs]**

- 場所: `docs/troubleshooting.md:104`
- 破綻: 「どれが読めないかは、その前の terraform のエラーと赤い `NG:` の行に出る」とあるが、state list の失敗（GF_UNREAD）では `NG:` の行が出ず、terraform のエラーだけ。「OSS 版のクラスターの名前は手順 7-4b で読み、読めなければそこで止まる」に、空のときも止まることが無い
- 格下げの根拠: e1 の 4 つ目（state list の失敗）の stderr は `Error: Failed to load state`。読めなかったものは terraform のエラーで分かる。7-4b で空のときは `tf_output` の「…が空」の `NG:` の行が出て止まる（test_oss_ops の「oss/ops/up.sh（81）: 7-4b の analytics のクラスターの名前も tf_output で読み、読めない・空なら 7-4b で止まる」）。読み違えて誤った操作に進む形ではない

**#4（反対弁護人）Nit [docs]**

- 場所: `ops/up-common.sh:313`（`grafana_skip_warn` の文）
- 破綻: 出力が空のときも「読めない（上のエラー）」と言う。OSS 版は 9-2 で state list を打たないのに「state か」と言う
- 格下げの根拠: design.md の E の文と一致している。空のときは上に `NG: … grafana_service_name が空` が出る（e1 の 3 つ目の stderr）。「確かめていない」と確かめ直すコマンドは正しい。文を変えるなら design.md の E ごと

**#5（反対弁護人）Nit [UX]**

- 場所: `ops/up.sh:1306`（9-2 の見出し）
- 破綻: GF_UNREAD の回は、state list のエラーが 9-2 の見出しより前に出て、「確かめる（最大 5 分）」の見出しのすぐ後に「確かめていない」が出る
- 格下げの根拠: 見出しに「。今回は analytics を作らないが、前の回の analytics の state が読めない」が付く（test_oss_ops の「ops/up.sh の 9-2（83・D8）」がこの文を見る）

**#6（反対弁護人）Nit [UX]**

- 場所: 9-2 の `tf_output`（`ops/up.sh:1307`、`oss/ops/up.sh:591`）
- 破綻: よそでは「止まる」を意味する赤い `NG:` の行のあとも進み、rc=0 で終わる
- 格下げの根拠: design-log.md の Round 1 で却下した案（9-2 だけ黄色の `tf_output` を作る）。e1 の 3 つ目で、`NG:` の行のすぐ後に黄色の「確かめていない」が出て最後まで届く。docs/troubleshooting.md:104 に「止めず」と書いてある

**#7（反対弁護人）Nit [tests]**

- 場所: `tests/test_oss_ops.py:1427`（9-2 に `grep -q` が無い）と `:1509`（9-2 に `|| exit 1` が無い）
- 破綻: コメントに同じ文字列を書くと偽の陽性になる。`grep --quiet` は捕まえない
- 格下げの根拠: 振る舞いは別のテストが縛る。M5'（静的な検査を外してパイプの `grep -q` に戻す）、R1・R2（止める形に戻す）は振る舞いのテストで落ちる（上の 4）

**#8（反対弁護人）Nit [tests]**

- 場所: `tests/test_oss_ops.py:1439`（偽の state list の失敗の文「Error acquiring the state lock」）
- 破綻: state list はロックを取らないので、現実には出ない文（反対弁護人の記憶。確かめていない）
- 格下げの根拠: 9-2 は rc だけを見て文を見ない。テストは文が stderr に残ることだけを見る。R3（GF_UNREAD を立てない）は落ちる（上の 4）

**#9（反対弁護人）Nit [運用]。015 より前からある形**

- 場所: `ops/check-grafana.sh:42-43`
- 破綻: 9-2 が「確かめ直すのは ops/check-grafana.sh」と案内する状態（出力が読めない）で打つと、check-grafana.sh も 3（Grafana が無い）で止まり、terraform のエラーは `2>/dev/null` で捨てる
- 格下げの根拠: 3 の文は「出力 grafana_service_name が読めないか空」と言い、偽の OK にはならない（test_oss_ops の「check-grafana.sh: Grafana が無い（grafana_service_name が空）なら理由を言って 3 で止まり、確かめを送らない」）。state を直したあとに打つコマンドとして案内している。design.md の範囲外なので PM に報告する

**N1（自分）Nit [missing tests]**

- 場所: `tests/test_oss_ops.py:1490-1504`
- 破綻: ANALYTICS_LEFT の回で、クラスターは読めてサービスの出力が読めない組み合わせのテストが無い（left_empty はクラスターが空）
- 格下げの根拠: 上の m92_bash5 の left_fail_svc（8da0db0、bash 3.2 と 5.1）で rc=0、`WARN=[IaC/terraform/aws-managed/pipeline/analy…]`、terraform の呼び出しは `list`・`analytics_cluster_name`・`grafana_service_name`。テストのある回と同じ `tf_output && tf_output` の経路

#### 不成立とした論点

- **0 で終わる（確かめていないのに rc=0）**
  - design.md の未確定事項 8 と PM の判断 D2 のとおり
  - 終了コードで Grafana の結果を見る呼び出し元がリポジトリに無い（反対弁護人が確かめた）
- **OSS の 7-4b の `AN_CLUSTER` がまだ止まる**
  - すぐ下の `output -json` も `die` する
  - Splunk / OpenSearch もこのルートが要る（design.md の E）
- **trap・`GF_` の変数の初期化・OSS の `GRAFANA_WARN`**
  - 反対弁護人が読んで成立とした
  - 9-2 は exit しない
  - 3 つとも 9-2 の頭で初期化している
  - OSS はどちらの分岐でも入れる

#### 問題なしとした観点と根拠

- 設計整合性: design.md の検証方法 1〜4 を 8da0db0 で打った（上）。5 は未実行（変えていない）
- 012 との噛み合わせ: 上の hunk の見出しと sete_probe、m92_bash5、マージ後の check.sh
- 退行の縛り: R1〜R6・M5'・M7・M8' と Round 1 の M1〜M4・M9〜M12（M3 は test_alerts が受ける）。全部が狙ったテストで落ちる（上の 4）
- security: 9-2 で読むのは terraform の出力の名前だけで、Grafana のパスワードには触れない（9-2 の差分を読んだだけ）
