# Grafana のルール検査の残りを直す（015）

設計: main(opus-5.5) / effort: xhigh

この文書は現行の設計だけを書く。PM と前提を合わせたやりとりは design-log.md の Round 0、セルフレビューの D2・D8 の PM の判断は Round 1。

## 背景

PM の依頼（2026-10-08）。対象は BACKLOG の 79〜83 で、どれも「AWS 検証で見つけた不具合 3 件を直す（008）」の (b)（Grafana のルールの評価のエラーを ops で検出する）のセルフレビューで直さなかった Nit。

- 79（N2）: `ops/grafana_rules_check.py` が rules API のページ分け（`groupNextToken`）を追う
- 80（N4）: `ops/check-grafana.sh` の NG・未確認・止まった（die）の終了コードを分け、未確認では「Failed to evaluate rule」のログを案内しない。`ops/up.sh` と `oss/ops/up.sh` の 9-2 の受け側（`grafana_rules_step`）もこの値で警告を変える
- 82（N6）: `ops/up-common.sh` の `ssm_run` の読み直しに全体の締め切りを付け、超えたら未確認として返す
- 81（N5）: 9-2 で `tf output` が失敗・空のとき、空の引数で `aws ecs wait` に進まない（止めずに警告する。下の PM の判断）
- 83（N7）: 前の回の analytics が残っている回（`PIPELINE=0` など）でも 9-2 を打つ

PM が決めたこと: 終了コードの値は設計で決めて `docs/troubleshooting.md` に 4 つの値の表を 1 つ書く。締め切りは環境変数で変えられる形（`deploy.env` のキーにはしない。`.env.example` にも書かず、`docs/deploy.md` に 1 行）。N2 は「13.2.3 の既定は `group_limit=-1` なのでトークンは来ない。来たときのための道」と書き、テストの偽サーバーで 2 ページ返す形と、同じトークン 2 回で未確認になる形で縛る。81・83 は `docs/cycle-006-design` に先に入る「MSK を SCRAM にし…（012）」と同じ `ops/up.sh` / `oss/ops/up.sh` を触るので、commit を最後にする。触らない: `docker/compose/`、`app/containerlab/lab.sh`、`docs/cycles/BACKLOG.md`。

PM の判断（2026-10-09。Round 1 のセルフレビューの D2・D8）: 9-2 で terraform が読めないとき（`tf output` の失敗・空、`state list` の失敗のどちらも）は `exit 1` にしない。`GRAFANA_WARN` に「state が読めず Grafana のルールを確かめていない。Grafana が安定するのも待っていない。確かめ直すのは `ops/check-grafana.sh`」を入れ、9-2 の `aws ecs wait` とルールの検査を飛ばして最後の案内まで進む。理由: 81 の「止める」の意図は「空の引数で `aws ecs wait` に進まない」で、up.sh 全体を止めることではない。`ops/up.sh` の 8-5 の方針（「set -e で up.sh ごと止まると、Web の再起動と最後の案内まで届かない」）に揃える。D8 の「黙って飛ばす」も無くし、同じ警告にする。

調査で分かった事実（実物を読んだもの）:

- **Grafana のルールの API のページ分け。** 使っている版は `ops/up-common.sh:232` の `GRAFANA_VERSION=13.2.3`。v13.2.3 のソースを読んだ
  - `pkg/services/ngalert/api/tooling/definitions/prom.go` の swagger の定義（`GET /prometheus/grafana/api/v1/rules`）:
    - `group_limit` は「`// Limit the number of rule groups returned.` `// in: query` `// required: false` `// default: -1`」
    - `group_next_token` は「`Continuation token for pagination. Use the value returned in the previous response's "groupNextToken" field.`」
    - 応答の `data`（`RuleDiscovery`）に `NextToken string json:"groupNextToken,omitempty"`。`rule_limit` も既定 -1
  - `pkg/services/ngalert/api/prometheus/api_prometheus.go` の実装:
    - `maxGroups := getInt64WithDefault(opts.Query, "group_limit", -1)` と `nextToken := opts.Query.Get("group_next_token")`
    - 打ち切りは `if (maxGroups > 0 && groupsReturned == maxGroups) || (maxRules > 0 && rulesReturned >= maxRules) { return ..., page.nextToken, nil }` だけで、そのあと `ruleResponse.Data.NextToken = continueToken`
  - つまり **引数を付けなければ全部のグループが 1 ページで返り、`groupNextToken` は付かない**（`omitempty`）。サーバー側の既定のページの大きさは無い。13.2.2 でも引数なしで全部返った（008 (b) の手元の実測）。ページを追う道は、今の版では通らない「来たときのための道」になる
- **`ops/grafana_rules_check.py`。** `make_fetch` の `fetch()` は `/api/prometheus/grafana/api/v1/rules` を 1 回 GET して、応答の JSON（オブジェクトでなければ `ValueError`）を返す。401 / 403 は `Unauthorized`。`check()` は `Unauthorized` ならすぐ未確認（2）、`OSError` / `ValueError` / `http.client.HTTPException` は読めなかったとして 10 秒おきに読み直し、`GRAFANA_WAIT`（既定 300 秒）を過ぎたら未確認（2）。`data` がオブジェクトでないと `rules_of` が `AttributeError` で落ちる（判定の行が出ない）。import は `tests/test_alerts.py` が標準ライブラリの決まった名前（`urllib` を含む）だけに縛っている
- **`ops/check-grafana.sh`。** `set -uo pipefail`（`-e` なし）。使い方の誤りは `exit 2`、`die`（`ops/common.sh:10`）は `exit 1`、`grafana_rules_check` が 0 以外なら Grafana のログの案内を出して `exit 1`。**NG・未確認・止まったの 3 つが同じ 1** で、未確認（401・届かない）にもログの案内が出る
- **`ops/up-common.sh` の `grafana_rules_step`（:256-271）。** `out=$(grafana_rules_check "$1" 2>&1)` が 0 以外なら、`判定:` の行を拾って「…確かめた結果が OK ではない（…）。評価のエラーの理由は Grafana のログ（…）。直したら <確かめ直すコマンド>」。NG と未確認が同じ文
- **`ops/up-common.sh` の `ssm_run`（:33-57）。** `send-command --timeout-seconds 900`（これは届けるまでの締め切り。`AWS-RunShellScript` の実行の締め切り `executionTimeout` は既定 3600 秒）のあと、`get-command-invocation` が失敗すると `Pending` として 10 秒おきに読み直し続ける（**締め切りが無い**）。`Success` なら標準出力を出して 0、ほかの終わりの状態は標準出力と標準エラーを標準エラーに出して 1
  - 呼ぶ側: `run_on_instance`（失敗で止まる。`ops/up.sh` の 4-4・7-3b・8-3・8-6、`oss/ops/up.sh` の同じところ）、lab の 7-2（`|| LAB_STATE=""` で警告）、7-2b の `lab forward`（`||` で警告）、`grafana_rules_check`
  - 先頭で `cloud-init status --wait` を打つので、EC2 を作った直後は cloud-init の分だけ長い。`ops/up.sh` の見積もりは Web の EC2 を含む base/core が「初回は 3〜5 分」、lab が「トポロジが上がるまで 10 分ほど」
- **9-2 の `tf output`（N5）。** `ops/up.sh:1272` は `grafana_rules_step "$INSTANCE_ID" "$(tf … analytics_cluster_name)" "$(tf … grafana_service_name)" …`。引数の中の `$( )` は失敗しても `set -e` で止まらず、空の引数で `aws ecs wait services-stable` に進んで「10 分たっても安定しない」と出る。`oss/ops/up.sh` はクラスターを `:463` の `AN_CLUSTER=$(tf …)`（代入なので失敗は止まるが、空は止まらない）で読み、サービスは `:572` で同じく引数の中で読む
- **前の回の Grafana（N7）。** `ops/up.sh` の `GRAFANA` は、今回 analytics を作り、格納先に Prometheus か OpenSearch があるときだけ 1（:324-325）。今回 analytics を作らないが state に残っているときは手順 3 で `ANALYTICS_LEFT=1`（:492、:720。そのとき `pipeline/analytics` は `tf_init` 済み）。Grafana の ECS のサービスは `IaC/terraform/aws-managed/pipeline/analytics/grafana.tf:104` の `aws_ecs_service.grafana`（`count`）。出力 `analytics_cluster_name` / `grafana_service_name` は無いとき空文字（`outputs.tf:140-143`、`:175-178`）。OSS 版は analytics をいつも作るので 9-2 もいつも打つ
- **`tf state list | grep -q` と `pipefail`。** `grep -q` は見つけた時点で読むのをやめるので、書き手が `SIGPIPE` で落ちると `pipefail` で失敗になる。`ops/up.sh:731` に同じ形があり、state の一覧は小さいので今は起きていない

## 設計方針

### 終了コード（4 つの値）

`ops/check-grafana.sh` の終了コードと、`grafana_rules_check`（`ops/up-common.sh`）の戻り値 0〜2 を同じ意味にする。`docs/troubleshooting.md` に同じ表を 1 つ書く。

| 値 | 意味 | 当たるもの | 出す案内 |
|---|---|---|---|
| 0 | OK | 全部のルールの打ったあとの評価にエラーが無い | なし |
| 1 | NG | 評価がエラーのルールがある（`判定: NG`） | Grafana のログ（`Failed to evaluate rule`） |
| 2 | 未確認 | 確かめに行ったが結果が分からない。`判定: 未確認`（401 / 403、Grafana に届かない・起動中、待ち切れ、ルールが 0 本、SSM の admin のパスワードが読めない、ページのトークンが繰り返す）、SSM Run Command を送れない・失敗した・`SSM_RUN_WAIT` を過ぎた、`判定:` の行が無い | 確かめ直すコマンド（`ops/check-grafana.sh [--oss]`）。ログの案内は出さない |
| 3 | 確かめる前に止まった | 使い方の誤り、`deploy.env` の誤り、Web の EC2 か Grafana が無い（`tf output` が読めないか空）、`SSM_RUN_WAIT` の値の誤り | `NG:` の赤い行（`die`） |

- 使い方の誤りは 2 から 3 に変わる（未確認と分けるため）
- `ops/up.sh` / `oss/ops/up.sh` の 9-2 では、1 と 2 は止めずに警告を変える。3 に当たるもの（analytics の `tf output` が読めない・空、`state list` が読めない）も止めず、確かめていないと警告する（E）

### A. ページを追う（N2、`ops/grafana_rules_check.py`）

- `make_fetch` の中を、1 ページを読む `get(url)`（今の `fetch` の中身。401 / 403 は `Unauthorized`、オブジェクトでなければ `ValueError`）と、全部のページを読む `fetch()` に分ける
- `fetch()` は最初のページを引数なしで読み、`data.groupNextToken` が空でない文字列のあいだ `?group_next_token=<トークン>`（`urllib.parse.quote(token, safe="")`）で次のページを読み、`data.groups` をつないで `{"data": {"groups": [...]}}` を返す。`group_limit` は送らない（既定 -1 で全部返る）
- 次の場合は `ValueError` にする（`check()` が読めなかったとして読み直し、待ち切れたら未確認（2））
  - 同じトークンが 2 回来た（「`groupNextToken` が繰り返された」）
  - ページが `MAX_PAGES = 100` を超えた（読むのは最初の 1 ページを含めて 100 ページまで。トークンが毎回変わって終わらない場合の歯止め）
  - `data` がオブジェクトでない、`groupNextToken` が文字列でない
  - 応答の `status` が `success` でない、`data.groups` が配列でない（13.2.2 は 200 の応答に必ず `"status": "success"` と `groups` の配列を付ける。手元で実測。どのページでも同じに確かめ、空のページとしてつながない）
- Authorization は今と同じくどのページにも `add_unredirected_header` で付ける。トークンは例外の文に先頭 40 文字だけ出す
- 先頭のコメントに「13.2.3 の既定は `group_limit=-1` なのでトークンは来ない。来たときのための道」と書く

### B. 終了コードで警告を変える（N4、`ops/up-common.sh`）

- `grafana_rules_check <Web のインスタンス ID>` は `ssm_run` の出力（標準エラーも）を受け取ってから出し、最後の `判定:` の行をグローバル変数 `GRAFANA_VERDICT` に置き、次を返す
  - `ssm_run` が 0 で `判定: OK` → 0
  - `判定: NG`（`ssm_run` は Python の終了コード 1 で `Failed` → 1） → 1
  - それ以外（`判定: 未確認`、判定の行が無い、送れない・失敗・締め切り） → 2
- `grafana_rules_step` は `grafana_rules_check "$1" || rc=$?` で受け、
  - 1: 今の文のまま（「…確かめた結果が OK ではない（判定: NG…）。評価のエラーの理由は Grafana のログ（…）。直したら <コマンド>」）
  - 2: 「Grafana のアラートルールの評価を確かめられなかった（<判定の行。無ければ「判定の行が無い。上の出力」>）。理由は上の出力。確かめ直すのは <コマンド>」。ログの案内は出さない
  - `aws ecs wait services-stable` が切れたときの文は今のまま（これも未確認の一種で、確かめ直すコマンドを出している）

### C. `ops/check-grafana.sh` の終了コード（N4）

- 使い方の誤りは `exit 3`
- `ops/common.sh` を読んだ直後に `die` を「赤い `NG:` の行を出して `exit 3`」に置き換える（`ops/up-common.sh` を読む前。`load_deploy_env` / `resolve_name_prefix` / `tf output` の `die` と、`SSM_RUN_WAIT` の値の誤りが 3 になる）
- `grafana_rules_check` の戻り値で分ける: 0 → `exit 0`、1 → ログの案内を出して `exit 1`、2 → `exit 2`（案内は出さない。`判定: 未確認` の理由は出力にある）
- 先頭のコメントの終了コードの説明を 4 つの値に書き換える
- 出力は `grafana_rules_check` がまとめて標準出力に出す（今は SSM が失敗のとき標準エラーに出ていた）。どちらも SSM が終わってから出るので、出る時刻は変わらない

### D. `ssm_run` の締め切り（N6、`ops/up-common.sh`）

- 環境変数 `SSM_RUN_WAIT`（秒。既定 1800）。`ops/up-common.sh` を読んだときに正の整数か確かめ、違えば `die`（`deploy.env` のキーにはしない）
- `ssm_run` は送ってから `SECONDS` で測り、`Pending` / `InProgress` / `Delayed` と「読めない」（`get-command-invocation` の失敗。送った直後はまだ無いことがある）のあいだ読み直す。`SSM_RUN_WAIT` を過ぎたら、標準エラーに「SSM Run Command（<ID>）の結果が <秒> 秒たっても分からない（最後の状態: <状態>）。あとで見るのは aws ssm get-command-invocation --region … --command-id … --instance-id …（待つ秒数は SSM_RUN_WAIT）」を出して **2** を返す（失敗の 1 と分ける。呼ぶ側の `run_on_instance` と lab の 7-2 は 0 以外をまとめて扱うので変えない）
- 既定 1800 秒の理由: 待つのは cloud-init（Web の EC2 は 3〜5 分、lab は 10 分ほど）とコマンド本体（Grafana の確かめは最大 5 分、`seed_graph.py` は数分）。その 3 倍ほどを取り、届けるまでの 900 秒より長く、実行の締め切り 3600 秒より短くする（3600 秒を待つなら SSM 自身が `TimedOut` にする）
- 締め切りで返っても、インスタンスの上のコマンドは止めない（`cancel-command` は打たない。表示した `get-command-invocation` で結果を見られる）

### E. 9-2 で terraform が読めなければ、確かめずに警告して先へ進む（81、PM の判断）

- `ops/up-common.sh` に `tf_output <ルート> <出力名>` を足す。`tf <ルート> output -raw <名前>` が失敗すれば「`$TF_DIR/<ルート>` の出力 <名前> が読めない（上のエラー）」、空なら「…が空」で `die`、読めれば値を出す。`$( )` の中の `die` はサブシェルだけを抜けるので、止めるかどうかは呼ぶ側が決める（`X=$(tf_output …) || exit 1` か `if X=$(tf_output …); then`）。どちらでも、どの出力が読めない・空かを赤い `NG:` の行で出す
- `ops/up-common.sh` に `grafana_skip_warn <確かめ直すコマンド>` を足す。`GRAFANA_WARN` に「`$TF_DIR`/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは <コマンド>」を入れて黄色で出す。最後の再掲は今の `GRAFANA_WARN` の行が出す
- `ops/up.sh` の 9-2: F の state の一覧が読めない（`GF_UNREAD`）か、`GF_CLUSTER` / `GF_SERVICE` を `tf_output` で読めない・空なら `grafana_skip_warn ops/check-grafana.sh`。`aws ecs wait` も確かめの送信も打たない。読めたら今までどおり `grafana_rules_step`
- `oss/ops/up.sh` の 9-2: `GF_SERVICE` を `tf_output` で読めない・空なら `grafana_skip_warn "ops/check-grafana.sh --oss"`、読めたら `grafana_rules_step`
- `oss/ops/up.sh` の `:464`（7-4b）の `AN_CLUSTER` は `tf_output … || exit 1` のまま止める。7-4b の手順で、すぐ下の同じ state の `opensearch_service_names` / `victoriametrics_service_names` も読めなければ `die` で止まる（PM の判断の範囲は 9-2）。9-2 のクラスターはこの値なので、9-2 に来たときは空でない
- 9-2 のあと（10 の案内とポートフォワーディング）は変えない

### F. 前の回の Grafana も確かめる（83、`ops/up.sh` だけ）

- 9-2 の前で `GRAFANA_LEFT` と `GF_UNREAD` を決める。`GRAFANA` が空で `ANALYTICS_LEFT=1` のときだけ `tf pipeline/analytics state list` を読む（標準エラーは捨てない。読めないときの理由として出す）
  - 読めて、`aws_ecs_service.grafana[` で始まる行があれば `GRAFANA_LEFT=1`。一覧は変数に読み切ってから `grep … >/dev/null` で見る（`state list` の失敗と「Grafana が無い」を分けるため、パイプにしない。`grep -q` も使わない）
  - 読めなければ `GF_UNREAD=1`（D8。黙って 9-2 を飛ばさない）。Grafana が残っていなくても、無いのか読めないのかを分けられないので警告する
- 9-2 は `GRAFANA` か `GRAFANA_LEFT` か `GF_UNREAD` があれば打つ。ログの見出しに、`GRAFANA_LEFT` なら「今回は analytics を作らないが、前の回の Grafana が残っている」、`GF_UNREAD` なら「今回は analytics を作らないが、前の回の analytics の state が読めない」を足す
- 最後の Grafana のポートフォワードの案内（`if [ -n "$GRAFANA" ]`）は変えない（83 の範囲は 9-2 だけ）
- OSS 版は analytics をいつも作るので変えない

## 変更対象ファイル

- `ops/grafana_rules_check.py`（A。先頭のコメントも）
- `ops/up-common.sh`（B・D・E。`ssm_run`、`grafana_rules_check`、`grafana_rules_step`、`tf_output`、`grafana_skip_warn`、`SSM_RUN_WAIT` の確かめ、先頭のコメント）
- `ops/check-grafana.sh`（C）
- `ops/up.sh`（E・F。9-2 だけ）
- `oss/ops/up.sh`（E。`:464`（7-4b）と 9-2）
- `tests/test_alerts.py`（A・B・D の単体）
- `tests/test_oss_ops.py`（C の `ops/check-grafana.sh`、E の OSS 版の通し、`ops/up.sh` の 9-2 の形）
- `docs/troubleshooting.md`（4 つの値の表と、9-2 での受け方）
- `docs/deploy.md`（`SSM_RUN_WAIT` の 1 行）
- `docs/architecture/resources/grafana.md`（ページを追うこと、`PIPELINE=0` の回も打つこと、終了コード、terraform が読めないときは警告して進むこと）
- `docs/pipeline.md`（「Grafana のアラート」の `ops/check-grafana.sh` の説明に終了コードの表への参照）
- `docs/development.md`（テストの本数）
- `docs/cycles/015-grafana-check-followups/`（design.md、design-log.md、build.md）

## 再利用するもの

- `tests/test_alerts.py` の `_Rules`（手元の HTTP サーバー。`SEEN` にパスと Authorization を残す）と、`_GFAKE` / `_GSTEP` / `gstep()`（偽の aws と `grafana_rules_step` の呼び出し。`sleep` は 0.05 秒に縮めてある）
- `tests/test_oss_ops.py` の偽の aws（`FAKE_GRAFANA`）と偽の terraform（`FAKE_TF_UP` / `FAKE_TF_EMPTY`）、`oss/ops/up.sh` の通しのテスト
- `ops/common.sh` の `tf` / `die` / `log`
- 手元の `grafana/grafana:13.2.2`（イメージはある）。ページのトークンの形を実物で見るのに使う

## 実装ステップ（commit はこの単位）

1. A: `ops/grafana_rules_check.py` のページ分けと、`tests/test_alerts.py` の偽サーバーの 2 ページ・同じトークン 2 回・終わらないトークン・`data` がオブジェクトでないのテスト
2. B・C: `grafana_rules_check` / `grafana_rules_step` / `ops/check-grafana.sh` の終了コードと、テスト（`test_alerts.py` の `gstep`、`test_oss_ops.py` の `ops/check-grafana.sh`）
3. D: `ssm_run` の締め切りと `SSM_RUN_WAIT` の確かめ、テスト
4. docs（表・`SSM_RUN_WAIT`・grafana.md・pipeline.md）
5. E・F（81・83）: `tf_output`、`grafana_skip_warn`、`ops/up.sh` / `oss/ops/up.sh` の 9-2、テスト、docs の 81・83 の分。**このときまでに `docs/cycle-006-design` に 012 が入っていれば、先にマージしてから書く**（入っていなければ書いてから、報告の前にマージして衝突を解く）
6. build.md（テストの本数を `docs/development.md` に合わせる）、セルフレビュー

## 検証方法（期待出力まで）

1. `uv run --group dev --group web python tests/test_alerts.py` が全部通り、次を含む
   - 偽サーバーが 1 ページ目にルール 1 本と `groupNextToken: "t/1+="`、2 ページ目にもう 1 本を返すと、`判定: OK（2 本とも評価のエラーなし）` で 0。2 回目の要求のパスが `/api/prometheus/grafana/api/v1/rules?group_next_token=t%2F1%2B%3D` で、2 回とも Authorization が付いている
   - 同じ形で 2 ページ目のルールだけがエラー（`Normal (Error, KeepLast)`）を続けると `判定: NG（2 本のうち 1 本…）` で 1（1 ページしか読まないと OK になる形）
   - 2 ページ目も同じトークンを返すと、`wait=0` で `判定: 未確認（0 秒待った。Grafana のルールの API が読めない（ValueError: groupNextToken が繰り返された…））` で 2
   - トークンが毎回変わると 100 ページ（最初の 1 ページを含めて 100 回読む）で `ValueError`、未確認で 2。2 ページ目が 200 で `status: error` を返し続けると、1 ページ目だけで OK にせず未確認で 2。`data` が配列なら未確認で 2（今は `AttributeError` で落ちる）
   - `grafana_rules_check` が OK で 0、NG で 1、`判定: 未確認` で 2、送れないとき 2、判定の行が無いとき 2 を返す
   - `gstep` で NG の警告は今の文（ログの案内と「直したら ops/check-grafana.sh」）、未確認の警告は「確かめられなかった（判定: 未確認（…））。理由は上の出力。確かめ直すのは ops/check-grafana.sh」で `Failed to evaluate rule` を含まない。送れないときは「確かめられなかった（判定の行が無い。上の出力）」
   - 偽の aws が `InProgress` を返し続け、`SSM_RUN_WAIT=1` なら、20 秒以内に終わり、出力に「1 秒たっても分からない（最後の状態: InProgress）」と `aws ssm get-command-invocation --region ap-northeast-1 --command-id cmd-1 --instance-id i-web`、警告は未確認の文
   - `get-command-invocation` が失敗し続けると、最後の状態が「読めない」で同じく 2
   - `SSM_RUN_WAIT=abc` で `ops/up-common.sh` を読むと `NG: SSM_RUN_WAIT は…` で止まる
2. `uv run --group dev --group web python tests/test_oss_ops.py` が全部通り、次を含む
   - `ops/check-grafana.sh`: OK で 0、NG で 1 とログの案内、偽の aws が `判定: 未確認（…）` で `Failed` を返すと 2 でログの案内なし、`FAKE_TF_EMPTY=grafana_service_name` で 3 と「Grafana が無い」、`--yes` で 3 と「使い方」
   - `oss/ops/up.sh` の通しで `FAKE_TF_EMPTY=grafana_service_name` なら 0 で最後の配るコマンドまで進み、標準エラーに「NG: IaC/terraform/oss/pipeline/analytics の出力 grafana_service_name が空」、警告「IaC/terraform/oss/pipeline/analytics の state か出力が読めない（上のエラー）ので、Grafana のアラートルールの評価を確かめていない（Grafana のサービスが安定するのも待っていない）。確かめ直すのは ops/check-grafana.sh --oss」が 2 回（9-2 と最後）。偽の aws の記録に Grafana の確かめの `send-command` も、空の `--services` の `ecs wait` も無い
   - `ops/up.sh` の 9-2 のブロックを偽の terraform で bash に打つ（`grafana_rules_step` は引数を出すだけ）
     - `GRAFANA=1` なら state を見ずに出力のクラスターとサービスで `grafana_rules_step` を 1 回。`ANALYTICS_LEFT=1` で state に `aws_ecs_service.grafana[0]` があれば見出しに「前の回の Grafana が残っている」を足して 1 回（state が 64 KB を超えても）。無いか `ANALYTICS_LEFT` が空なら打たない。どれも `GRAFANA_WARN` は空のまま（確かめていないの警告を出さない）
     - 出力が空（`GRAFANA=1` の `grafana_service_name`、`ANALYTICS_LEFT=1` の `analytics_cluster_name`）か読めない（rc=1）なら、0 で最後まで進み、`grafana_rules_step` を打たず、`GRAFANA_WARN` は上の文で確かめ直すのは `ops/check-grafana.sh`。標準エラーに `NG: …の出力 <名前> が空` か `…が読めない（上のエラー）` と terraform のエラー
     - `ANALYTICS_LEFT=1` で `state list` が rc=1 なら、0 で最後まで進み、見出しに「前の回の analytics の state が読めない」、出力を読まず（terraform の呼び出しは `state list` だけ）、`grafana_rules_step` を打たず、`GRAFANA_WARN` は同じ文。terraform のエラーは標準エラーに残る
     - 9-2 に `grep -q`（コメントを除く）も `|| exit 1` も無い
3. `bash ops/check.sh` の最後の行が `すべて通過`
4. 退行の注入（セルフレビューで実際に落ちるのを見る）: ページの繰り返しを消すと 2 ページの NG のテストが落ちる。`seen` を消すと同じトークンのテストが落ちる（100 ページの歯止めで終わる形でも文が違うので落ちる）。`grafana_rules_step` の 2 の分岐を 1 と同じにすると未確認のテストが落ちる。`ssm_run` の締め切りを消すとテストが 20 秒で切れて落ちる。9-2 の `grafana_skip_warn` を `exit 1` に戻す（`ops/up.sh` と `oss/ops/up.sh` のそれぞれ）と警告のテストが落ちる。`state list` が読めないときの `GF_UNREAD` を消す（黙って飛ばす形）と state list のテストが落ちる。`grafana_skip_warn` の中で `GRAFANA_WARN` を入れないと最後の再掲のテストが落ちる。9-2 の外側の `if` に `else grafana_skip_warn` を足す（Grafana が無い回にも警告する形）と打たない回のテストが落ちる
5. 手元の Grafana 13.2.2（`docker run` の使い捨て。`__expr__` の式だけのルールを 2 つのグループに置く）で、引数なしの応答に `groupNextToken` が無く、`?group_limit=1` の応答の `data.groupNextToken` が空でなく、それを `group_next_token` に入れると 2 つ目のグループが返る。終わったら `docker rm -f -v` で消す

## 未確定事項とリスク

1. **ページを追う道は AWS の Grafana では通らない。** 13.2.3 は引数なしで 1 ページなので、実物で通るのは手元で `group_limit=1` を付けたときだけ。トークンの形や意味が版で変わると、テストは通っても実物でずれる（そのときは同じトークンか 100 ページで未確認になり、NG を見落として OK にはならない）
2. **締め切りで返してもリモートのコマンドは動き続ける。** Grafana の確かめは最大 5 分で害は無い。`run_on_instance`（Web の再起動・グラフの初期ロード）が締め切りで止まると、up.sh は止まるが EC2 の上では続いている。案内した `get-command-invocation` で結果を見て、打ち直す
3. **1800 秒が足りない場合。** lab の cloud-init は 10 分ほどの見積もりで、AWS での実測はこの設計では取っていない。足りなければ `SSM_RUN_WAIT` で延ばす（`docs/deploy.md` に書く）
4. **使い方の誤りの終了コードが 2 から 3 に変わる。** `ops/check-grafana.sh` を呼ぶのは人と docs だけ（リポジトリの中に終了コードを見るスクリプトは無い）
5. **83 の判定は state の一覧に頼る。** state にサービスが載っていても、Grafana のタスクが止まっている・データソースが消えている場合は `aws ecs wait` か評価で未確認・NG になる（そのときの警告は正しい）。一覧が読めないときは、Grafana が残っていなくても「確かめていない」と警告する（無いのか読めないのかを分けられない）
6. **012 との衝突。** 012 は `ops/up.sh` と `oss/ops/up.sh` の MDT の行を消す。9-2 と `:464` に近ければ衝突するので、マージで解き、`test_oss_ops.py` の通しを取り直す
7. **AWS では確かめない**（PM の指示）。実物で確かめるのは手元の Grafana 13.2.2 だけ
8. **9-2 で terraform が読めなくても up.sh は 0 で終わる。** 黄色の警告は 9-2 と最後に出るが、終了コードだけを見る呼び出し元は Grafana を確かめていないことに気付かない（9-2 の NG・未確認と同じ扱い）。`tf_output` の赤い `NG:` の行が出ても止まらないので、読む人には「止まった」と見えるかもしれない。続けて黄色の警告で「確かめていない」と言う
<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261009-cycle-015-grafana-check-followups-design.html -->
