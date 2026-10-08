# Cycle 014 kafbat-ui-followups 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: high

エンジニア2（PM の指示）。ブランチ `fix/kafbat-ui-followups`（`docs/cycle-006-design` の eededf8 から）。AWS には何も立てていない。

### 変更ファイル

| ファイル | BACKLOG の行 | 変更 |
|---|---|---|
| `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl` | 72・75 | Docker と Kafbat UI の節を `aws s3 sync` の直後・`exit 0` の前へ移して関数 `kafka_ui_setup`（1 行ずつ `\|\| return 1`、呼び出しは `\|\| echo … >&2`）。`param()` が標準エラーを `/run/<prefix>-kafka-ui.err` に受け、`(ParameterNotFound)` なら 75、それ以外は 69 に AWS CLI のエラー文を添える。`umask 077` をスクリプトの頭へ。ユニットに `RestartPreventExitStatus=75`、`ExecStopPost` で `.err` も消す。Web のユニットに `Wants=<prefix>-kafka-ui.service` |
| `IaC/terraform/aws-managed/base/ecr/outputs.tf` | 71 | description（stream が `<URL>:<tag>` を SSM `/<prefix>/kafka-ui/image` に書き、Web の EC2 の Docker（ユニット `<prefix>-kafka-ui`）が pull する） |
| `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` | 75 | 6〜9 行のコメントだけ（「読めるまで再試行して待つ」→ 75 で 1 回で止まり、8-3（OSS 版は 7-5）の Web の restart が `Wants=` で起こす。読めないときは 69）。OSS 版の stream はシンボリックリンクで同じファイル。セルフレビューの SF3 |
| `tests/test_stream.py` | 72・75 | 75 / 69 の分け方（4 つのパラメータ × ParameterNotFound 2 形・AccessDenied・不達・認証情報なし）、`kafka_ui_setup` の各段の失敗、並び、`Wants=`（`kafka-ui` を含む `Key=` 行はそれ 1 本だけ）、`RestartPreventExitStatus=75`、`ExecStopPost`。Kafbat UI を起こす Web の restart が stream の apply より後で、stream を作る回はいつも通ること（`ops/up.sh` の 8-3 は `if [ -z "$SKIP_STREAM" ] \|\| …` の中、OSS 版の 7-5 は `if` の外。セルフレビューの SF1）。83 → 88 件 |
| `docs/deploy.md` | 73・75 | `SKIP_STREAM` の行、手順 7、手順 8-3 の行、010 より前の環境の DependencyViolation、`sudo systemctl start` の注記 |
| `docs/troubleshooting.md` | 72・74・75 | Kafbat UI の行（image を変えたら `systemctl restart <prefix>-kafka-ui`、75 / 69 / setup failed） |
| `docs/pipeline.md` | 75 | Kafbat UI の段落の再試行の書き方 |
| `docs/verification/20261008-oss-aws.md` | 78 | 注記 2 か所（:118、:421） |
| `docs/architecture/resources/web-ec2.md` | 72・75 | Kafbat UI の行（設計の一覧に無い。下の逸脱） |
| `docs/architecture/resources/ssm-parameter-store.md` | 75 | `/<prefix>/kafka-ui/…` の読む側の説明（設計の一覧に無い。下の逸脱） |
| `docs/cycles/014-kafbat-ui-followups/` | — | design.md、design-log.md、この build.md |

### 設計から逸脱した点

- 文書を 2 本足した: `docs/architecture/resources/web-ec2.md:18` と `docs/architecture/resources/ssm-parameter-store.md:31`。どちらも「読めるまで 30 秒ごとに起こし直す」の旧い説明が残っていたので、75 / 69 / `Wants=` / setup failed に合わせた
- 検証 4 の手元のコンテナに、設計に無いケースを 2 つ足した。f: setup failed のあと直して user_data を打ち直すと 3 つとも起きる。g: Docker の起動が遅い（25 秒）ときに `systemctl restart <prefix>-web` が待たされない
- 検証 3 の変異 28 個のうち 5 個（[7] [10] [11] [22] [24]）は、AssertionError ではなく test_stream.py の `.index()` の `ValueError: substring not found` で落ちて検出している（rc=1。既存の test_stream.py と同じ書き方。残りの検査が走らず集計も出ないのはセルフレビューの Nit 9）
- セルフレビューの SF1〜SF3 を直すのに、設計の一覧に無いものを足した。`kafka_ui.tf` のコメント（SF3）、test_stream.py の検査 1 本（SF1。`ops/up.sh` と `oss/ops/up.sh` は読むだけで触っていない）、変異 23〜27（SF1）。design.md の「変更対象ファイル」「検証方法 2・3」「未確定事項とリスク」（SF2）にも「セルフレビューで追加」として書き足した
- PM の承認の文言は「logger で journald に 1 行」だったが、design.md の設計方針どおり `logger` は使わず標準エラーに 1 行出す（`logger` が無い AMI だと `\|\| logger` の右側が 127 で落ち、`set -e` で user_data が止まるため）。標準エラーは cloud-init が `/var/log/cloud-init-output.log` と cloud-final の journald に入れる（EC2 では未確認）
- 手元のコンテナでは `systemd-networkd-wait-online` が 120 秒で失敗するまで `network-online.target` を待つので、ケース a の最初の起動が約 2 分遅れた（コンテナだけの事情。EC2 の user_data は cloud-final の中で走り、その時点で `network-online.target` は済んでいる）

### 検証 1: `bash ops/check.sh`

セルフレビューの直しの最後の編集（`docs/cycles/014-kafbat-ui-followups/design.md` 00:52:09。コードとテストは `kafka_ui.tf` 00:51:59・`tests/test_stream.py` 00:50:43）のあとに取り直した（00:52:15 開始、00:54:54 終了）。手順 1〜3 と、手順 4 の各テストの最後の行（`tests/test_*.py` の glob 順。test_app の途中に出るトレースバックは失敗の経路を見る検査が出すもので、そのあと `ok` が続く）。

```
$ bash ops/check.sh; echo rc=$?
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
差分なし

== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK

== 3. スクリプトの構文
bash -n: 24 本
構文エラーなし

== 4. 模擬テスト
（test_alerts）通過 140 / 失敗 0
（test_analytics）通過 489 / 失敗 0
（test_app）通過 158 / 失敗 0
（test_dashboard_config）通過 3 / 失敗 0
（test_graph）通過 78 / 失敗 0
（test_kb_index）通過 7 / 失敗 0
（test_lab_debug）通過 91 / 失敗 0
（test_local_compose）通過 121 / 失敗 0
（test_nautobot）68 項目すべて通過
（test_oss）通過 171 / 失敗 0
（test_oss_ops）通過 148 / 失敗 0
（test_oss_roll）通過 66 / 失敗 0
（test_stream）通過 88 / 失敗 0
（test_sync）通過 100 / 失敗 0
（test_workflow）通過 325 / 失敗 0

すべて通過
rc=0
```

（括弧のテスト名は書き足したもの。ログの本体はスクラッチパッドの `check3.log`、2651 行。手順 1〜3 の出力はセルフレビュー前の `check2.log` と同じで、手順 4 は test_stream が 87 → 88 になったほかは同じ本数）

### 検証 2: `uv run --group dev --group web python tests/test_stream.py`

`ops/check.sh` の中の test_stream の部分（抜粋。このサイクルで足した・直した検査）。

```
ok Kafbat UI は Web の EC2 の systemd のユニットが Docker のコンテナを 127.0.0.1:8082 に出す（stream は base/core より後に作られる）。パラメータが無い（標準エラーに (ParameterNotFound)）と 75 で終わって起こし直さず（RestartPreventExitStatus。cycle 014）、それ以外で読めないと 69 で終わって 30 秒ごとに起こし直す
ok 75 で止まった Kafbat UI は、Web のユニットの Wants= で、ops/up.sh の手順 8-3（OSS 版は 7-5）の systemctl restart <接頭辞>-web が起こす。弱い依存だけにして、Kafbat UI が落ちても Web を止めない（Requires / BindsTo / PartOf / Requisite にしない。cycle 014）
ok Kafbat UI を起こす Web の restart は stream の apply より後: ops/up.sh の手順 8-3 は if [ -z "$SKIP_STREAM" ] || … の中、OSS 版の手順 7-5 は条件なし（cycle 014）
ok Docker と Kafbat UI の節は、画面のコードの取得（aws s3 sync）より後、S3 に web/ が無くて exit 0 する所より前で、関数 kafka_ui_setup にまとめて 1 行ずつ || return 1 で繋ぎ、|| echo で呼ぶ（落ちても user_data を止めない。cycle 014）
ok user_data のシェルの部分は描いたあと bash -n が通る（マネージド版と OSS 版の両方。{'': (0, False, False), 'neo4j': (0, False, False)}）
ok パラメータが無い（ParameterNotFound。stream がまだ無い・SKIP_STREAM=1）と、どれか 1 つでもそこで 75 で終わり（systemd は起こし直さない）、env も書かず docker も呼ばず、後ろのパラメータを読まない。標準エラーは 1 行で「無い・再試行しない・起こし方」（古い AWS CLI の形でも同じ。{('image', 'pnf'): (75, None, [], 1, False, True), ('image', 'pnf-old'): (75, None, [], 1, False, True), ('bootstrap_servers', 'pnf'): (75, None, [], 2, False, True), ('bootstrap_servers', 'pnf-old'): (75, None, [], 2, False, True), ('security_protocol', 'pnf'): (75, None, [], 3, False, True), ('security_protocol', 'pnf-old'): (75, None, [], 3, False, True), ('admin_password', 'pnf'): (75, None, [], 4, False, True), ('admin_password', 'pnf-old'): (75, None, [], 4, False, True)}）
ok ParameterNotFound 以外で読めない（AccessDenied 254・エンドポイント不達 255・認証情報がまだ無い 253）と 69 で終わり（systemd が 30 秒後に起こし直す）、「stream が無い」とは言わずに AWS CLI のエラー文を 1 行に添える。env も書かず docker も呼ばない（{('image', 'deny'): (69, None, [], 1, False, True), ('image', 'unreachable'): (69, None, [], 1, False, True), ('image', 'nocreds'): (69, None, [], 1, False, True), ('bootstrap_servers', 'deny'): (69, None, [], 2, False, True), ('bootstrap_servers', 'unreachable'): (69, None, [], 2, False, True), ('bootstrap_servers', 'nocreds'): (69, None, [], 2, False, True), ('security_protocol', 'deny'): (69, None, [], 3, False, True), ('security_protocol', 'unreachable'): (69, None, [], 3, False, True), ('security_protocol', 'nocreds'): (69, None, [], 3, False, True), ('admin_password', 'deny'): (69, None, [], 4, False, True), ('admin_password', 'unreachable'): (69, None, [], 4, False, True), ('admin_password', 'nocreds'): (69, None, [], 4, False, True)}）
ok user_data はスクリプトを #!/bin/bash と set -euo pipefail で始めて 0755 にし、systemd のように直に起こせる（書いた結果と mode: [(0, '0o755')]）
ok Docker と Kafbat UI の節は、どの段（dnf・docker の起動・スクリプトの書き込み・chmod・ユニットの書き込み・daemon-reload・enable）で落ちても、そこで止めて後ろの段を打たず、「setup failed」を 1 行出して user_data の先（Gradio）へ進む。docker があれば dnf を呼ばない（{'ok': (0, 'REACHED\n', False, ['dnf', 'systemctl enable --now docker', 'chmod', 'systemctl daemon-reload', 'systemctl enable --now x-nwc-poc-kafka-ui.service'], True, True), 'ok-docker': (0, 'REACHED\n', False, ['systemctl enable --now docker', 'chmod', 'systemctl daemon-reload', 'systemctl enable --now x-nwc-poc-kafka-ui.service'], True, True), 'dnf': (0, 'REACHED\n', True, ['dnf'], False, False), 'enable-docker': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker'], False, False), 'chmod': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker', 'chmod'], True, False), 'daemon-reload': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker', 'chmod', 'systemctl daemon-reload'], True, True), 'enable-kafka-ui': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker', 'chmod', 'systemctl daemon-reload', 'systemctl enable --now x-nwc-poc-kafka-ui.service'], True, True), 'script-write': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker'], False, False), 'unit-write': (0, 'REACHED\n', True, ['dnf', 'systemctl enable --now docker', 'chmod'], True, False)}）
通過 88 / 失敗 0
```

### 検証 3: 変異の検査（スクラッチパッドの `self/mut14.py`。worktree の写しに 1 つずつ入れて `tests/test_stream.py` を走らせ、戻す）

```
[0] chmod 0755 を消す: 検出 rc=1
[1] スクリプトの shebang を消す: 検出 rc=1
[2] スクリプトの set -euo pipefail を消す: 検出 rc=1
[3] RestartPreventExitStatus=75 を消す: 検出 rc=1
[4] ParameterNotFound の判定を消す（全部 75）: 検出 rc=1
[5] ParameterNotFound の判定を消す（全部 69）: 検出 rc=1
[6] Web のユニットの Wants=kafka-ui を消す: 検出 rc=1
[7] 呼び出しから || echo を外す: 検出 rc=1 | ValueError: substring not found
[8] || return 1 を外す（dnf）: 検出 rc=1
[9] || return 1 を外す（enable docker）: 検出 rc=1
[10] || return 1 を外す（スクリプトの heredoc）: 検出 rc=1 | ValueError: substring not found
[11] || return 1 を外す（ユニットの heredoc）: 検出 rc=1 | ValueError: substring not found
[12] || return 1 を外す（daemon-reload）: 検出 rc=1
[13] 最後の enable --now の失敗を握りつぶす: 検出 rc=1
[14] Kafbat UI の節を aws s3 sync より前に戻す: 検出 rc=1
[15] Kafbat UI の節を exit 0 より後ろに動かす: 検出 rc=1
[16] ExecStopPost から .err を外す: 検出 rc=1
[17] umask 077 を param の呼び出しより後ろに戻す: 検出 rc=1
[18] http_tokens を optional に: 検出 rc=1
[19] Kafbat UI のユニットの Wants=network-online.target を消す: 検出 rc=1
[20] docker pull の失敗を無視: 検出 rc=1
[21] ECR のログインの失敗を無視: 検出 rc=1
[22] heredoc のクォートを外す: 検出 rc=1 | ValueError: substring not found
[23] 8-3 の条件から SKIP_STREAM を外す: 検出 rc=1
[24] 8-3 の節を消す: 検出 rc=1 | ValueError: substring not found
[25] OSS 版の 7-5 の restart を消す: 検出 rc=1
[26] OSS 版の 7-5 を if で包む: 検出 rc=1
[27] OSS 版の 7-5 を stream の apply より前に動かす: 検出 rc=1
```

（[18]〜[22] は 010 からある変異。形が変わっても効いているかを見た。[23]〜[27] はセルフレビューの SF1 で足した変異で、`ops/up.sh` と `oss/ops/up.sh` の写しに入れる。28 個とも SF1 の直しのあと（test_stream が 88 件の状態）に通しで取り直した。行末の AssertionError の文面は省いた。全文はスクラッチパッドの `mut14c.log`。走らせたあと写しと worktree を `diff -rq` で比べて元に戻っていることを確かめた）

### 検証 4: 手元の systemd のコンテナ（PM の条件 1）

PM の条件: 「手元の systemd のコンテナで「exit 75 で failed になった kafka-ui が、web の `systemctl restart` で起きること」と「kafka-ui が落ちても web が巻き込まれないこと」の両方を実測し、コマンドと出力を build.md に貼る」

準備:

- イメージ: `amazonlinux:2023` に `dnf install systemd procps-ng util-linux` を足して `t014-systemd:latest` にした（`systemd 252 (252.23-14.amzn2023)`）。`docker run -d --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw --name t014 t014-systemd /usr/lib/systemd/systemd`
- user_data: `tests/test_stream.py` の `_render_web_ud()` と同じ置き換え（`x-nwc-poc` / `ap-northeast-1` / `x-bucket`）で描いたシェルの部分を `/root/ud.sh` に置いた。いまの tftpl から描き直したものと `diff` で同じ（`SAME`）
- 偽物（AWS には繋いでいない）:
  - `aws`: `s3 sync` は `app.py`（`import gradio`）と空の `requirements.txt` を置く。`ssm get-parameter` は `/etc/fake-ssm-mode` が `pnf` なら ParameterNotFound（254）、`deny` なら AccessDeniedException（254）、`ok` なら値を返す。標準エラーの形は手元の aws-cli 2.36.34 の実測（design.md の表）と同じ
  - `docker`: `run` だけ `exec sleep infinity` で居座る。呼ばれたら `/var/log/fake-docker.log` に 1 行
  - `docker.service`: `ExecStart=/bin/sleep infinity`
  - `python3.13`: `-m`（pip）は何もせず成功、それ以外（`app.py`）は居座る（Gradio の代わり）
  - `dnf`: いつも失敗（ケース e だけで効く）

#### a. SSM にパラメータが無い（ParameterNotFound）で user_data を打つ

```
$ docker exec t014 bash -c 'bash /root/ud.sh; echo "user_data rc=$?"'; echo ----; docker exec t014 bash -c 'sleep 40; for u in x-nwc-poc-kafka-ui x-nwc-poc-web; do echo "== $u"; systemctl show $u -p ActiveState -p SubState -p Result -p ExecMainStatus -p NRestarts -p MainPID; done; echo "== journal"; journalctl --no-pager -u x-nwc-poc-kafka-ui -o cat | grep -v "^$"'
Created symlink /etc/systemd/system/multi-user.target.wants/docker.service → /etc/systemd/system/docker.service.
Created symlink /etc/systemd/system/multi-user.target.wants/x-nwc-poc-kafka-ui.service → /etc/systemd/system/x-nwc-poc-kafka-ui.service.
Created symlink /etc/systemd/system/multi-user.target.wants/x-nwc-poc-web.service → /etc/systemd/system/x-nwc-poc-web.service.
x-nwc-poc-web.service is active (env: /etc/x-nwc-poc-web.env)
user_data rc=0
----
== x-nwc-poc-kafka-ui
MainPID=0
Result=exit-code
NRestarts=0
ExecMainStatus=75
ActiveState=failed
SubState=failed
== x-nwc-poc-web
MainPID=167
Result=success
NRestarts=0
ExecMainStatus=0
ActiveState=active
SubState=running
== journal
Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
/x-nwc-poc/kafka-ui/image does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; restarting x-nwc-poc-web (ops/up.sh does) or 'sudo systemctl start x-nwc-poc-kafka-ui' starts it again.
x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=75/TEMPFAIL
x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
/x-nwc-poc/kafka-ui/image does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; restarting x-nwc-poc-web (ops/up.sh does) or 'sudo systemctl start x-nwc-poc-kafka-ui' starts it again.
x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=75/TEMPFAIL
x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
```

起動が 2 回ある。時刻つきで見ると、1 回目は user_data の `systemctl enable --now x-nwc-poc-kafka-ui`、2 回目は Web の start が `Wants=` で積んだ start ジョブ。どちらも 75 で、systemd の自動の起こし直しは 0 回（`NRestarts=0`）。

```
$ docker exec t014 systemctl show systemd-networkd-wait-online.service -p Result -p ActiveState -p ExecMainStartTimestamp -p ExecMainExitTimestamp
Result=exit-code
ExecMainStartTimestamp=Thu 2026-10-08 15:19:43 UTC
ExecMainExitTimestamp=Thu 2026-10-08 15:21:43 UTC
ActiveState=failed
$ docker exec t014 bash -c 'systemctl show network-online.target -p ActiveState; echo "== journal (時刻つき)"; journalctl --no-pager -o short-precise -u x-nwc-poc-kafka-ui -u x-nwc-poc-web | grep -v "^--"; echo "== fake aws"; cat /var/log/fake-aws.log'
ActiveState=active
== journal (時刻つき)
Oct 08 15:21:44.008337 05c043bcf8c2 systemd[1]: Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
Oct 08 15:21:44.013064 05c043bcf8c2 x-nwc-poc-kafka-ui[134]: /x-nwc-poc/kafka-ui/image does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; restarting x-nwc-poc-web (ops/up.sh does) or 'sudo systemctl start x-nwc-poc-kafka-ui' starts it again.
Oct 08 15:21:44.013332 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=75/TEMPFAIL
Oct 08 15:21:44.016192 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
Oct 08 15:21:44.115416 05c043bcf8c2 systemd[1]: Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
Oct 08 15:21:44.116388 05c043bcf8c2 systemd[1]: Started x-nwc-poc-web.service - x-nwc-poc chat web (Gradio, 127.0.0.1:8080, reached via SSM port forwarding).
Oct 08 15:21:44.118980 05c043bcf8c2 x-nwc-poc-kafka-ui[168]: /x-nwc-poc/kafka-ui/image does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; restarting x-nwc-poc-web (ops/up.sh does) or 'sudo systemctl start x-nwc-poc-kafka-ui' starts it again.
Oct 08 15:21:44.119246 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=75/TEMPFAIL
Oct 08 15:21:44.120329 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
== fake aws
15:19:43 aws s3 sync --delete --region ap-northeast-1 s3://x-bucket/web/ /opt/x-nwc-poc-web/src/
15:21:44 aws ssm get-parameter --region ap-northeast-1 --name /x-nwc-poc/kafka-ui/image --query Parameter.Value --output text
15:21:44 aws ssm get-parameter --region ap-northeast-1 --name /x-nwc-poc/kafka-ui/image --query Parameter.Value --output text
```

（15:19:43〜15:21:43 の 2 分はコンテナの `systemd-networkd-wait-online` のタイムアウト。上の「逸脱」の最後）

#### b. パラメータを置いて Web だけを restart する（failed の Kafbat UI が起きる）、もう一度 restart する（動いている Kafbat UI は起こし直さない）

```
$ docker exec t014 bash -c 'P(){ for u in x-nwc-poc-kafka-ui x-nwc-poc-web; do echo "  $u: $(systemctl show $u -p ActiveState -p SubState -p ExecMainStatus -p MainPID -p InvocationID -p NRestarts | tr "\n" " ")"; done; }
echo ok > /etc/fake-ssm-mode
echo "# b-1: パラメータを置いた状態にして、Web だけを restart する（ops/up.sh 手順 8-3 と同じコマンド）"
time systemctl restart x-nwc-poc-web
sleep 3; P
echo "  docker の呼び出し:"; sed "s/^/    /" /var/log/fake-docker.log
echo "  /run の env: $(stat -c "%a %U %n" /run/x-nwc-poc-kafka-ui.env)"
echo "# b-2: 動いている Kafbat UI があるところで、Web をもう一度 restart する"
sleep 2; systemctl restart x-nwc-poc-web; sleep 3; P
echo "  docker の呼び出し（増えていなければ Kafbat UI は起こし直されていない）:"; sed "s/^/    /" /var/log/fake-docker.log'
# b-1: パラメータを置いた状態にして、Web だけを restart する（ops/up.sh 手順 8-3 と同じコマンド）

real	0m0.007s
user	0m0.000s
sys	0m0.003s
  x-nwc-poc-kafka-ui: MainPID=218 NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=62d7f2f3b9c3493d8b7cbb2ff8d97e1d 
  x-nwc-poc-web: MainPID=224 NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=c99dc1829fed4651bef99869fef939fa 
  docker の呼び出し:
    15:23:00 docker login
    15:23:00 docker pull
    15:23:00 docker rm
    15:23:00 docker run
  /run の env: 600 root /run/x-nwc-poc-kafka-ui.env
# b-2: 動いている Kafbat UI があるところで、Web をもう一度 restart する
  x-nwc-poc-kafka-ui: MainPID=218 NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=62d7f2f3b9c3493d8b7cbb2ff8d97e1d 
  x-nwc-poc-web: MainPID=261 NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  docker の呼び出し（増えていなければ Kafbat UI は起こし直されていない）:
    15:23:00 docker login
    15:23:00 docker pull
    15:23:00 docker rm
    15:23:00 docker run
```

#### d / c-1. SSM が AccessDenied（69 で終わり、30 秒後に起こし直す。Web は巻き込まれない）

```
$ docker exec t014 bash -c 'P(){ for u in x-nwc-poc-kafka-ui x-nwc-poc-web; do echo "  $u: $(systemctl show $u -p ActiveState -p SubState -p Result -p ExecMainStatus -p MainPID -p InvocationID -p NRestarts | tr "\n" " ")"; done; }
echo deny > /etc/fake-ssm-mode
P
systemctl restart x-nwc-poc-kafka-ui; sleep 2
echo "  2 秒後:"; P
sleep 33
echo "  35 秒後:"; P
echo "  journal:"; journalctl --no-pager -o short-precise -u x-nwc-poc-kafka-ui --since "-40s" | grep -v "^--" | sed "s/^/    /"'
  x-nwc-poc-kafka-ui: MainPID=218 Result=success NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=62d7f2f3b9c3493d8b7cbb2ff8d97e1d 
  x-nwc-poc-web: MainPID=261 Result=success NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  2 秒後:
  x-nwc-poc-kafka-ui: MainPID=0 Result=exit-code NRestarts=0 ExecMainStatus=69 ActiveState=activating SubState=auto-restart InvocationID=71a758a810dc44ff894cd14a22bec84b 
  x-nwc-poc-web: MainPID=261 Result=success NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  35 秒後:
  x-nwc-poc-kafka-ui: MainPID=0 Result=exit-code NRestarts=1 ExecMainStatus=69 ActiveState=activating SubState=auto-restart InvocationID=041aae9818864e4aa2f3f3bf6a6b801b 
  x-nwc-poc-web: MainPID=261 Result=success NRestarts=0 ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  journal:
    Oct 08 15:23:15.588185 05c043bcf8c2 systemd[1]: Stopping x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding)...
    Oct 08 15:23:15.589824 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Deactivated successfully.
    Oct 08 15:23:15.589933 05c043bcf8c2 systemd[1]: Stopped x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    Oct 08 15:23:15.590727 05c043bcf8c2 systemd[1]: Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    Oct 08 15:23:15.595953 05c043bcf8c2 x-nwc-poc-kafka-ui[285]: Cannot read /x-nwc-poc/kafka-ui/image (not a missing parameter: e.g. AccessDenied, SSM endpoint unreachable, no credentials yet). Retrying in 30 s. AWS CLI: aws: [ERROR]: An error occurred (AccessDeniedException) when calling the GetParameter operation (reached max retries: 0): User: arn:aws:sts::123456789012:assumed-role/x-nwc-poc-web/i-0 is not authorized to perform: ssm:GetParameter on resource: arn:aws:ssm:ap-northeast-1:123456789012:parameter/x-nwc-poc/kafka-ui/image
    Oct 08 15:23:15.596378 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=69/UNAVAILABLE
    Oct 08 15:23:15.597811 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
    Oct 08 15:23:45.727122 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Scheduled restart job, restart counter is at 1.
    Oct 08 15:23:45.727285 05c043bcf8c2 systemd[1]: Stopped x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    Oct 08 15:23:45.743537 05c043bcf8c2 systemd[1]: Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    Oct 08 15:23:45.749658 05c043bcf8c2 x-nwc-poc-kafka-ui[301]: Cannot read /x-nwc-poc/kafka-ui/image (not a missing parameter: e.g. AccessDenied, SSM endpoint unreachable, no credentials yet). Retrying in 30 s. AWS CLI: aws: [ERROR]: An error occurred (AccessDeniedException) when calling the GetParameter operation (reached max retries: 0): User: arn:aws:sts::123456789012:assumed-role/x-nwc-poc-web/i-0 is not authorized to perform: ssm:GetParameter on resource: arn:aws:ssm:ap-northeast-1:123456789012:parameter/x-nwc-poc/kafka-ui/image
    Oct 08 15:23:45.749993 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Main process exited, code=exited, status=69/UNAVAILABLE
    Oct 08 15:23:45.751683 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Failed with result 'exit-code'.
```

#### c-2 / c-3. Kafbat UI を SIGKILL、docker.service を止める（Web は巻き込まれない）。c-4 の 1 回目（mask）は効かなかった

```
$ docker exec t014 bash -c 'P(){ for u in x-nwc-poc-kafka-ui x-nwc-poc-web docker; do echo "  $u: $(systemctl show $u -p ActiveState -p SubState -p Result -p ExecMainStatus -p MainPID -p InvocationID | tr "\n" " ")"; done; }
echo ok > /etc/fake-ssm-mode; systemctl restart x-nwc-poc-kafka-ui; sleep 2
echo "# c-0: 前提（Kafbat UI と Web が動いている）"; P
echo "# c-2: Kafbat UI のプロセスを SIGKILL で落とす"
kill -9 $(systemctl show -p MainPID --value x-nwc-poc-kafka-ui); sleep 2; P
echo "# c-3: docker.service を止める（Kafbat UI は Requires=docker.service で一緒に止まる）"
systemctl restart x-nwc-poc-kafka-ui; sleep 1
systemctl stop docker; sleep 2; P
echo "# c-4: docker.service を起こせない状態（mask）で Web を restart する"
systemctl mask docker >/dev/null 2>&1; systemctl restart x-nwc-poc-web; echo "  systemctl restart x-nwc-poc-web rc=$?"; sleep 3; P
journalctl --no-pager -o cat -u x-nwc-poc-kafka-ui --since "-5s" | grep -v "^$" | sed "s/^/    /"
systemctl unmask docker >/dev/null 2>&1; systemctl start docker'
# c-0: 前提（Kafbat UI と Web が動いている）
  x-nwc-poc-kafka-ui: MainPID=325 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=231a915d927248c2a76c42df6acf5479 
  x-nwc-poc-web: MainPID=261 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  docker: MainPID=105 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=b06eb0676d03482ca0b36416d099571c 
# c-2: Kafbat UI のプロセスを SIGKILL で落とす
  x-nwc-poc-kafka-ui: MainPID=0 Result=signal ExecMainStatus=9 ActiveState=activating SubState=auto-restart InvocationID=231a915d927248c2a76c42df6acf5479 
  x-nwc-poc-web: MainPID=261 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  docker: MainPID=105 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=b06eb0676d03482ca0b36416d099571c 
# c-3: docker.service を止める（Kafbat UI は Requires=docker.service で一緒に止まる）
  x-nwc-poc-kafka-ui: MainPID=0 Result=success ExecMainStatus=15 ActiveState=inactive SubState=dead InvocationID=33e5963f83b5457d888edf21ee076bc4 
  x-nwc-poc-web: MainPID=261 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=0d04bdabe30242138832636048c10bbe 
  docker: MainPID=0 Result=success ExecMainStatus=15 ActiveState=inactive SubState=dead InvocationID=b06eb0676d03482ca0b36416d099571c 
# c-4: docker.service を起こせない状態（mask）で Web を restart する
  systemctl restart x-nwc-poc-web rc=0
  x-nwc-poc-kafka-ui: MainPID=418 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=e1123712c1a547cdb2eee352a6e1471e 
  x-nwc-poc-web: MainPID=424 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=c4d31b7a7b8542f5b4588547c3527415 
  docker: MainPID=417 Result=success ExecMainStatus=0 ActiveState=active SubState=running InvocationID=34643e2726aa4b7680d046462833a47b 
    Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
```

c-4 の 1 回目は、偽の `docker.service` が `/etc/systemd/system` に置いた実体なので `systemctl mask` が失敗し（出力は捨てていた）、docker が普通に起きた。ただし「止まっていた docker と Kafbat UI が、Web の restart の `Wants=` → Kafbat UI の `Requires=` で起きる」ことは見えている。docker が起きない状態は drop-in で作り直した（次）。

#### c-4. docker.service が起動に失敗する状態で Web を restart する

```
$ docker exec t014 bash -c 'P(){ for u in x-nwc-poc-kafka-ui x-nwc-poc-web docker; do echo "  $u: $(systemctl show $u -p ActiveState -p SubState -p Result -p MainPID -p InvocationID | tr "\n" " ")"; done; }
echo "# c-4: docker.service が起動に失敗する状態（drop-in で ExecStartPre=/bin/false）にして docker を止め、Web を restart する"
mkdir -p /etc/systemd/system/docker.service.d; printf "[Service]\nExecStartPre=/bin/false\n" > /etc/systemd/system/docker.service.d/fail.conf; systemctl daemon-reload
systemctl stop docker; sleep 1; P
systemctl restart x-nwc-poc-web; echo "  systemctl restart x-nwc-poc-web rc=$?"; sleep 3; P
journalctl --no-pager -o cat -u x-nwc-poc-kafka-ui -u docker --since "-5s" | grep -v "^$" | sed "s/^/    /"
rm -r /etc/systemd/system/docker.service.d; systemctl daemon-reload; systemctl start docker'
# c-4: docker.service が起動に失敗する状態（drop-in で ExecStartPre=/bin/false）にして docker を止め、Web を restart する
  x-nwc-poc-kafka-ui: MainPID=0 Result=success ActiveState=inactive SubState=dead InvocationID=e1123712c1a547cdb2eee352a6e1471e 
  x-nwc-poc-web: MainPID=424 Result=success ActiveState=active SubState=running InvocationID=c4d31b7a7b8542f5b4588547c3527415 
  docker: MainPID=0 Result=success ActiveState=inactive SubState=dead InvocationID=34643e2726aa4b7680d046462833a47b 
  systemctl restart x-nwc-poc-web rc=0
  x-nwc-poc-kafka-ui: MainPID=0 Result=success ActiveState=inactive SubState=dead InvocationID=e1123712c1a547cdb2eee352a6e1471e 
  x-nwc-poc-web: MainPID=505 Result=success ActiveState=active SubState=running InvocationID=1eae70081635444daa038136a67d5feb 
  docker: MainPID=0 Result=exit-code ActiveState=failed SubState=failed InvocationID=ea1198c57dec4a878bb796ebdf7694ed 
    Stopping x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding)...
    x-nwc-poc-kafka-ui.service: Deactivated successfully.
    Stopped x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    Stopping docker.service - fake docker daemon (cycle 014 local test)...
    docker.service: Deactivated successfully.
    Stopped docker.service - fake docker daemon (cycle 014 local test).
    Starting docker.service - fake docker daemon (cycle 014 local test)...
    docker.service: Control process exited, code=exited, status=1/FAILURE
    docker.service: Failed with result 'exit-code'.
    Failed to start docker.service - fake docker daemon (cycle 014 local test).
    Dependency failed for x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
    x-nwc-poc-kafka-ui.service: Job x-nwc-poc-kafka-ui.service/start failed with result 'dependency'.
```

#### e. Docker が無く `dnf install -y docker` が落ちる

```
$ docker exec t014 bash -c 'P(){ for u in x-nwc-poc-kafka-ui x-nwc-poc-web; do echo "  $u: $(systemctl show $u -p LoadState -p ActiveState -p SubState -p MainPID | tr "\n" " ")"; done; }
echo "# e: 準備（d まで作ったユニットとスクリプトを消し、docker を無くす。dnf は失敗する偽物）"
systemctl disable --now x-nwc-poc-kafka-ui x-nwc-poc-web docker >/dev/null 2>&1
rm -f /etc/systemd/system/x-nwc-poc-kafka-ui.service /etc/systemd/system/x-nwc-poc-web.service /usr/local/bin/x-nwc-poc-kafka-ui /usr/local/bin/docker /etc/x-nwc-poc-web.env
systemctl daemon-reload; systemctl reset-failed; echo pnf > /etc/fake-ssm-mode
echo "  command -v docker: $(command -v docker || echo なし)"
echo "# e: user_data を流す"
bash /root/ud.sh; echo "  user_data rc=$?"
P
echo "  /usr/local/bin/x-nwc-poc-kafka-ui: $(ls /usr/local/bin/x-nwc-poc-kafka-ui 2>&1)"' 2>&1
# e: 準備（d まで作ったユニットとスクリプトを消し、docker を無くす。dnf は失敗する偽物）
  command -v docker: なし
# e: user_data を流す
dnf: fake failure (no repository reachable)
x-nwc-poc-kafka-ui: setup failed (see the error above). Continuing with the web UI; fix it and reboot the instance.
Created symlink /etc/systemd/system/multi-user.target.wants/x-nwc-poc-web.service → /etc/systemd/system/x-nwc-poc-web.service.
x-nwc-poc-web.service is active (env: /etc/x-nwc-poc-web.env)
  user_data rc=0
  x-nwc-poc-kafka-ui: MainPID=0 LoadState=not-found ActiveState=inactive SubState=dead 
  x-nwc-poc-web: MainPID=598 LoadState=loaded ActiveState=active SubState=running 
  /usr/local/bin/x-nwc-poc-kafka-ui: ls: cannot access '/usr/local/bin/x-nwc-poc-kafka-ui': No such file or directory
```

#### f. （設計に無いケース）e のあと直して user_data を打ち直す（reboot の代わり）

```
$ docker cp $S/ct/docker t014:/usr/bin/docker && docker exec t014 bash -c 'chmod 755 /usr/bin/docker; : > /var/log/fake-docker.log; bash /root/ud.sh >/root/ud-f.log 2>&1; echo "user_data rc=$?"; tail -3 /root/ud-f.log; sleep 3; systemctl show -p ActiveState,SubState,MainPID,InvocationID x-nwc-poc-web x-nwc-poc-kafka-ui docker'
user_data rc=0
Created symlink /etc/systemd/system/multi-user.target.wants/docker.service → /etc/systemd/system/docker.service.
Created symlink /etc/systemd/system/multi-user.target.wants/x-nwc-poc-kafka-ui.service → /etc/systemd/system/x-nwc-poc-kafka-ui.service.
x-nwc-poc-web.service is active (env: /etc/x-nwc-poc-web.env)
MainPID=736
ActiveState=active
SubState=running
InvocationID=a0022822c3014d0c99bc36664162dd23

MainPID=680
ActiveState=active
SubState=running
InvocationID=43fb1b0918a84949aed1557f144dba5e

MainPID=654
ActiveState=active
SubState=running
InvocationID=d5c364ec773145c4a0ee0f9ff00bdf15
```

（ここでは偽の SSM を `ok` に戻してある。順は web / kafka-ui / docker）

#### g. （設計に無いケース）docker の起動が 25 秒かかるときに Web を restart する

```
$ docker exec t014 bash -c 'mkdir -p /etc/systemd/system/docker.service.d; printf "[Service]\nExecStartPre=/bin/sleep 25\n" > /etc/systemd/system/docker.service.d/slow.conf; systemctl daemon-reload; systemctl stop x-nwc-poc-kafka-ui docker; s=$(date +%s.%N); systemctl restart x-nwc-poc-web; rc=$?; e=$(date +%s.%N); echo "restart web rc=$rc elapsed=$(echo "$e - $s" | bc 2>/dev/null || python3 -c "print($e-$s)")"; systemctl show -p ActiveState,SubState,MainPID x-nwc-poc-web x-nwc-poc-kafka-ui docker; systemctl list-jobs --no-pager'
restart web rc=0 elapsed=0.007248401641845703
MainPID=766
ActiveState=active
SubState=running

MainPID=0
ActiveState=inactive
SubState=dead

MainPID=0
ActiveState=activating
SubState=start-pre
JOB  UNIT                       TYPE  STATE
1037 x-nwc-poc-kafka-ui.service start waiting
1038 docker.service             start running

2 jobs listed.
$ docker exec t014 bash -c 'until [ "$(systemctl show -p ActiveState --value x-nwc-poc-kafka-ui)" = active ]; do sleep 2; done; systemctl show -p ActiveState,SubState,MainPID x-nwc-poc-kafka-ui docker; journalctl --no-pager -o short-precise -u docker -u x-nwc-poc-kafka-ui -u x-nwc-poc-web --since "-60s" | tail -12; rm -rf /etc/systemd/system/docker.service.d; systemctl daemon-reload'
MainPID=802
ActiveState=active
SubState=running

MainPID=801
ActiveState=active
SubState=running
Oct 08 15:27:59.965021 05c043bcf8c2 systemd[1]: x-nwc-poc-kafka-ui.service: Deactivated successfully.
Oct 08 15:27:59.965148 05c043bcf8c2 systemd[1]: Stopped x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
Oct 08 15:27:59.965456 05c043bcf8c2 systemd[1]: Stopping docker.service - fake docker daemon (cycle 014 local test)...
Oct 08 15:27:59.965592 05c043bcf8c2 systemd[1]: docker.service: Deactivated successfully.
Oct 08 15:27:59.965677 05c043bcf8c2 systemd[1]: Stopped docker.service - fake docker daemon (cycle 014 local test).
Oct 08 15:27:59.970550 05c043bcf8c2 systemd[1]: Starting docker.service - fake docker daemon (cycle 014 local test)...
Oct 08 15:27:59.970665 05c043bcf8c2 systemd[1]: Stopping x-nwc-poc-web.service - x-nwc-poc chat web (Gradio, 127.0.0.1:8080, reached via SSM port forwarding)...
Oct 08 15:27:59.971282 05c043bcf8c2 systemd[1]: x-nwc-poc-web.service: Deactivated successfully.
Oct 08 15:27:59.971374 05c043bcf8c2 systemd[1]: Stopped x-nwc-poc-web.service - x-nwc-poc chat web (Gradio, 127.0.0.1:8080, reached via SSM port forwarding).
Oct 08 15:27:59.972635 05c043bcf8c2 systemd[1]: Started x-nwc-poc-web.service - x-nwc-poc chat web (Gradio, 127.0.0.1:8080, reached via SSM port forwarding).
Oct 08 15:28:24.989084 05c043bcf8c2 systemd[1]: Started docker.service - fake docker daemon (cycle 014 local test).
Oct 08 15:28:24.990184 05c043bcf8c2 systemd[1]: Started x-nwc-poc-kafka-ui.service - x-nwc-poc Kafbat UI (Docker, 127.0.0.1:8082, reached via SSM port forwarding).
```

`systemctl restart x-nwc-poc-web` は Web のジョブだけを待って 0.007 秒で返り、Kafbat UI と docker の start ジョブは後ろで走る（Web のユニットに `After=<prefix>-kafka-ui.service` を付けていないため）。`ops/up.sh` の 8-3 の `$WEB_ACTIVE` も Docker の起動を待たない。

#### 期待との突き合わせ

| 設計の期待 | 結果 |
|---|---|
| a. user_data は最後まで、Web active。Kafbat UI は 75 で failed、40 秒後も `NRestarts=0`、journald に `Not retrying` | 一致（`user_data rc=0`、`ExecMainStatus=75 ActiveState=failed NRestarts=0`） |
| b. SSM を「ある」にして Web を restart すると Kafbat UI が active | 一致（b-1 `MainPID=218 active running`）。動いている Kafbat UI は Web の restart で起こし直されない（b-2 の InvocationID が同じ、docker の呼び出しが増えない） |
| c. Kafbat UI が落ちても Web は巻き込まれない | 一致。69（d/c-1）・SIGKILL（c-2）・docker 停止（c-3）で Web の MainPID 261 と InvocationID 0d04bdab… が変わらない。docker が起きない状態で Web を restart（c-4）しても rc=0 で Web active、Kafbat UI は `Dependency failed` |
| d. AccessDenied で 69、30 秒後に起こし直す | 一致（`status=69/UNAVAILABLE`、30 秒後に `restart counter is at 1`） |
| e. dnf が落ちると setup failed を出して最後まで、Web active、Kafbat UI のユニットは無い | 一致（`LoadState=not-found`、Web `MainPID=598 active`） |

### 検証 5: コメント以外が ASCII（`tests/test_workflow.py`、check.sh の中）

```
ok web の user_data はコメント以外が ASCII だけで、TITLE を渡さない（タイトルは config.py の既定値）
```

### セルフレビュー

- 自分: claude-opus-5-5 / effort: xhigh（`/robust` を design.md の実装ステップと検証方法に照らして回した）
- 反対弁護人: opus（`Agent` の general-purpose。文脈を渡し、読み取り専用で頼んだ。返ってきたあと `git status --porcelain -uall` は頼む前と同じ 13 件で、何も増えていない）

#### 自分で見た観点（指摘なし）

| 観点 | 根拠として実行したもの |
|---|---|
| 設計整合性 | design.md の実装ステップ・検証方法を 1 行ずつ読み直し、変更ファイルと突き合わせた（読んだだけ）。PM の条件 1 は検証 4（a〜e）の実測、条件 2 は 75 / 69 の分け方を検証 2 の検査と検証 4 の a・d で確かめた |
| correctness | 検証 1（check.sh すべて通過）、検証 2（test_stream 88 / 0）、検証 3 の変異 0〜22 がすべて検出 |
| security | admin のパスワードが標準出力・エラー・`.err` に出ないこと、env ファイルが umask 077 で `/run` にだけ置かれ `ExecStopPost` で消えることを test_stream の検査と変異 [16] [17] で確かめた。`.err` に入るのは AWS CLI のエラー文だけ（読んだだけ） |
| runtime bugs | 検証 4 の手元の systemd のコンテナ（a〜e）。指摘ゼロだったので、実装時に見ていなかった観点を 2 つ走らせた: f（setup failed を直して user_data を打ち直す）、g（Docker の起動が 25 秒かかるときに Web を restart する）。どちらも期待どおり |
| data loss | Kafbat UI は状態を持たない（`docker run --rm` でボリュームを付けず、設定は起動のたびに SSM から読む。tftpl の 71〜72 行を読んだだけ） |
| API compatibility | SSM のパラメータ名・ユニット名・ポート（127.0.0.1:8082）は 010 から変えていない（`git diff eededf8` の tftpl の差分に `8082` と `/kafka-ui/` の変更が無く、ユニット名の行は字下げと `\|\| return 1` だけ。test_stream の既存の検査も通る） |
| missing tests | 変異 0〜22 で各段の `\|\| return 1`、節の位置、終了コードの分け方、ユニットの各行が縛られていることを確かめた。ここは問題なしとしたが、反対弁護人が SF1 を見つけた（下） |
| ASCII | 検証 5（コメント以外が ASCII） |

#### 反対弁護人の指摘（Must 0 / Should 3 / Nit 7）と片付け

**SF1 Should fix [missing tests] `tests/test_stream.py:476`**

- 破綻シナリオ: Kafbat UI を起こすのは `ops/up.sh` 8-3（OSS 版は 7-5）の `systemctl restart <prefix>-web` だけなのに、検査は `"systemctl restart $PREFIX-web.service" in up` の部分一致だけだった。これは 8-3（1222 行）と 8-6（1249 行、WORKFLOW のときだけ）の両方に当たるので、8-3 の条件から `SKIP_STREAM` を外す・8-3 を消す・OSS 版の 7-5 を消すといった退行が通る。退行すると、stream を作った回に Kafbat UI が 75 のまま起きない
- 反対弁護人の再現: 8-3 の条件から SKIP_STREAM を外した写し、8-3 を消した写しで test_stream 87 / 0（test_workflow も 325 / 0）
- 自分の再現（直す前）: 新しい検査（476〜487 行）を消した写し `sf1base` に、変異 23〜27 を入れて `python3 self/mut14.py sf1base 23 24 25 26 27`

```
[23] 8-3 の条件から SKIP_STREAM を外す: 素通り rc=0 | 通過 87 / 失敗 0
[24] 8-3 の節を消す: 素通り rc=0 | 通過 87 / 失敗 0
[25] OSS 版の 7-5 の restart を消す: 素通り rc=0 | 通過 87 / 失敗 0
[26] OSS 版の 7-5 を if で包む: 素通り rc=0 | 通過 87 / 失敗 0
[27] OSS 版の 7-5 を stream の apply より前に動かす: 素通り rc=0 | 通過 87 / 失敗 0
```

- 直した: 検査を 1 本足した（`ops/up.sh` の stream の apply が 8-3 より前、8-3 は `if [ -z "$SKIP_STREAM" ] || …; then` で始まり中に restart の行があって `fi` は 1 つ、OSS 版の stream の apply が 7-5 より前、7-5 の restart は log の次の行、7-4c から 8 までの `if` / `fi` は `STORE_WARN` の 1 組だけ）。直したあとは検証 3 のとおり 23〜27 がすべて検出（[24] は ValueError）。`ops/up.sh` と `oss/ops/up.sh` は読むだけで触っていない

**SF2 Should fix [runtime bugs] `ops/up.sh:919` → `:1219`、`oss/ops/up.sh:335` → `:532`**

- 破綻シナリオ: `ops/up.sh` が stream の apply（919）のあと、8-3（1219）より前で止まる（graph の apply 966、nautobot 989、analytics 1087 などの `die` / `tf_apply`）と、Kafbat UI は 75 で failed のまま残る。010 では 30 秒ごとに起こし直していたので、ここは 010 より悪い。OSS 版も 335 → 532 の間（graph 397、nautobot 423、analytics 458、467 など）で同じ
- 再現: 読んだだけ（`awk` で 919〜1219 と 335〜532 の `die` / `tf_apply` を数えた。AWS は使えないので実行していない）
- 直した（文書）: design.md の「未確定事項とリスク」に足し、`docs/troubleshooting.md:93` の Kafbat UI の行に「stream の apply（手順 7）のあと、8-3（OSS 版は 7-5）より前で止まった回も 75 のまま残る。直して `ops/up.sh` を打ち直すか、`sudo systemctl start <prefix>-kafka-ui`」を書いた。`ops/up.sh` は PM の指示で範囲外なので、コードでは塞いでいない

**SF3 Should fix [設計整合性] `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf:6-7`**

- 破綻シナリオ: コメントが「読めるまで再試行して待つ」のままで、75 で 1 回で止まる今の動きと食い違う。読んだ人が「stream を作れば放っておいても起きる」と思う
- 再現: `grep -rn '読めるまで' IaC/` が kafka_ui.tf に当たった
- 直した: 6〜9 行のコメントを 75 / `Wants=` / 69 に合わせた。直したあと同じ grep は 0 件（rc=1）

**Nit（直さない。最終報告に回した）**

- Nit 4 `ops/up.sh:1219-1223`: 初回のデプロイでは 8-3 で初めて pull（約 640 MB）と Spring の起動が始まるので、最後に出すポートフォワードの案内の時点では Kafbat UI がまだ開かない。文書に書いていない
- Nit 5 `web_user_data.sh.tftpl:87`: 75 で止まったユニットは failed のまま残り、`systemctl is-system-running` が degraded になる。`SuccessExitStatus=75` を足す案があるが、実測していない
- Nit 6 `web_user_data.sh.tftpl:57`、`ops/up.sh:918`: `SKIP_STREAM=1` で stream を terraform だけで作った回は admin-password が作られず、`sudo systemctl start` しても 75 に戻り、文言（stream がまだ無い）が実情と合わない。初回だけ
- Nit 7 `web_user_data.sh.tftpl:138`: `Wants=` なので、わざと止めた Kafbat UI も 8-3・8-6・user_data の 160 行目の Web の restart で起き直す。止めておくには `mask` が要る。文書に書いていない
- Nit 8 `web_user_data.sh.tftpl:94`: reboot のとき Kafbat UI が 69 の 30 秒待ちにいると、`enable --now` が最大 30 秒 user_data を止める。実測していない
- Nit 9 `tests/test_stream.py:382-383`、`:470`: モジュールの頭で `.index()` が ValueError を出すと残りの検査が走らず、集計の行も出ない（rc=1 にはなる。既存と同じ書き方）
- Nit 10 `docs/troubleshooting.md:93`: `daemon-reload` や `enable` で落ちると、ユニットは inactive・disabled で journald も空になる。この形は表に無い

#### 自分で取り消した判断と、反対弁護人の判定

| 取り消した判断 | 反対弁護人 |
|---|---|
| 初回の起動で Kafbat UI の start が何度か重なっても害は無い | 認めた。ただし reboot の 69 待ちは別（Nit 8） |
| 変異の 5 個が ValueError で落ちるのも検出に数えてよい | 認めた。ただし無害ではない（Nit 9） |
| 手元のコンテナで最初の起動が約 2 分遅れたのは `systemd-networkd-wait-online` のせいで、EC2 では起きない | もっともらしい（読んだだけ） |

反対弁護人が退けた反論: tftpl の `$${` のエスケープ、IAM の範囲が `parameter/<prefix>/*` なので無いパラメータは AccessDenied でなく ParameterNotFound になること、CLI のエラー文の形が違えば 69 に落ちて安全側なこと、`.err` に値が入らないこと、終了コードが他とぶつからないこと、s3 sync の失敗は design.md で受け入れ済みなこと。

#### ジンテーゼ

- 「Kafbat UI は 8-3 / 7-5 の restart で起きる」は、`ops/up.sh` が 8-3 / 7-5 まで進んだ回に限る、と条件付きに書き直した。途中で止まった回の残りのリスクは design.md と troubleshooting に書き、8-3 / 7-5 が stream の apply の後でいつも通ることはテストで縛った（SF1・SF2）
- 未確認のまま残すもの: AWS では動かしていない。EC2 の AWS CLI のエラー文の形、setup failed の行が cloud-init-output.log と journald のどちらに入るか、DependencyViolation の再現、Nit 5・Nit 8 の挙動
