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
