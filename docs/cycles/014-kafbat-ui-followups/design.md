# Kafbat UI を Web の EC2 に移した残りを直す（014）

設計: エンジニア2(opus-5.5) / effort: xhigh

## 背景

「Kafbat UI を Web の EC2 に同居させる（010）」で、Kafbat UI を Fargate から Web の EC2 の Docker に移した。010 の cold review と完了時の Nit から、BACKLOG に次の 6 行が残った（PM の割り当て。ほかの行は扱わない）。

- 71: `IaC/terraform/aws-managed/base/ecr/outputs.tf` の `kafka_ui_repository_url` の description「pipeline/stream runs it on ECS.」を Web の EC2 の Docker が pull する形に直す
- 72: Web の EC2 の user_data で Docker の `dnf install` / `systemctl enable --now docker` が落ちても Gradio の設定（`aws s3 sync`）まで進むようにする
- 73: `docs/deploy.md` の 010 より前の環境の注意に、Fargate の kafka_ui が動いたままだと base/core の SG の削除が DependencyViolation で止まることを足す
- 74: Kafbat UI の SSM の image を変えたときに Web の EC2 のコンテナを入れ替える手順を `docs/troubleshooting.md` に書く
- 75: `SKIP_STREAM=1` のとき Web の EC2 の Kafbat UI の systemd ユニットが 30 秒ごとに再試行し続けるのを止める手段を作る（exit 75 の文言も AccessDenied やエンドポイント不達で「stream がまだ無い」と言う）
- 78: Kafbat UI のクラスター名を `<prefix>-stream` から `<prefix>` にしたことを `docs/verification/20261008-oss-aws.md` の手筋に注記する

### 現物（eededf8）

`IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl`（OSS 版はこのファイルへのリンク）。Docker と Kafbat UI の節が Gradio の `aws s3 sync` より前にあり、`set -euo pipefail` の下で直に打っている。

```bash
command -v docker >/dev/null || dnf install -y docker        # :27
systemctl enable --now docker                                 # :28
...
if ! IMAGE=$(param image) || ! SERVERS=$(param bootstrap-servers) || ! PROTOCOL=$(param security-protocol) \
  || ! PASSWORD=$(param admin-password --with-decryption); then
  echo "SSM parameters under $PARAM are not readable yet (apply IaC/terraform/aws-managed/pipeline/stream). Retrying." >&2
  exit 75                                                     # :37-41
fi
...
Restart=always                                                # :70 ユニット
RestartSec=30                                                 # :71
...
aws s3 sync --delete --region ${region} s3://${bucket}/web/ $APP/src/   # :81
```

そのため、(1) `dnf install -y docker` か `systemctl enable --now docker` が落ちると user_data がそこで止まり、Gradio の画面が入らない（行 72）。(2) どのパラメータの読み取り失敗も 75 で、ParameterNotFound でも AccessDenied でも同じ「not readable yet」を出して 30 秒ごとに回り続ける（行 75）。

`IaC/terraform/aws-managed/base/ecr/outputs.tf:34` の description は「… IaC/terraform/aws-managed/pipeline/stream runs it on ECS.」のまま（行 71）。

### AWS CLI のエラーの形（実測）

手元の aws-cli 2.36.34 を、127.0.0.1 に立てた偽の SSM（`--endpoint-url`、ダミーの認証情報。AWS には繋いでいない）に当てて取った。

| 状況 | 終了コード | 標準エラー（空行のあとの 1 行） |
| :--- | :--- | :--- |
| ParameterNotFound | 254 | `aws: [ERROR]: An error occurred (ParameterNotFound) when calling the GetParameter operation (reached max retries: 0):` |
| AccessDeniedException | 254 | `aws: [ERROR]: An error occurred (AccessDeniedException) when calling the GetParameter operation (reached max retries: 0): User: … is not authorized to perform: ssm:GetParameter` |
| エンドポイントに届かない | 255 | `aws: [ERROR]: Could not connect to the endpoint URL: "http://127.0.0.1:1/"` |
| 認証情報が無い | 253 | `aws: [ERROR]: An error occurred (NoCredentials): Unable to locate credentials. …` |
| 成功 | 0 | （空。値は標準出力） |

ParameterNotFound と AccessDenied は終了コードが同じ（254）なので、終了コードでは分けられない。分けられるのは標準エラーの `(ParameterNotFound)` だけ。AL2023 の EC2 に入っている AWS CLI の版でも `An error occurred (<コード>)` の形は同じと見ている（未確認。下の「未確定事項とリスク」）。

## 設計方針

PM との Round 0（design-log.md）で決めた結論。

### 行 75: 再試行を止める手段と、終了コードの分け方

- スクリプトの `param()` が AWS CLI の標準エラーを `/run/<prefix>-kafka-ui.err` に受け、失敗したら次のように分ける。
  - 標準エラーに `(ParameterNotFound)` がある → **75 で終わる。** ユニットの `RestartPreventExitStatus=75` で systemd は起こし直さない（failed のまま止まる）。文言は「`<パス>` が無い（pipeline/stream が無い。例: `SKIP_STREAM=1`）。再試行しない。apply したら `sudo systemctl start <prefix>-kafka-ui`」。
  - それ以外（AccessDenied、エンドポイント不達、認証情報がまだ無い、など） → **69 で終わる。** `Restart=always` / `RestartSec=30` のまま 30 秒ごとに起こし直す。文言は「`<パス>` が読めない（無いのではない。AccessDenied、SSM のエンドポイント不達、認証情報がまだ無い など）。30 秒後に再試行」に AWS CLI のエラー文（改行を空白にした 1 行）を添える。
- 値は標準出力だけで受け、標準エラーと混ぜない。`get-parameters` にはしない（Web のロールの権限は `ssm:GetParameter` だけ）。
- `/run/<prefix>-kafka-ui.err` は env ファイルと一緒に `ExecStopPost` で消す。
- **自動で起きる道を残す。** Web のユニット `<prefix>-web.service` の `[Unit]` に `Wants=<prefix>-kafka-ui.service` を足す。`systemctl restart <prefix>-web` の restart ジョブが Kafbat UI に start ジョブを積むので、failed で止まっていた Kafbat UI がそこで起きる（動いているときは何も起きない）。`Wants=` は弱い依存なので、Kafbat UI が落ちても Web は巻き込まれない。どちらも手元の systemd のコンテナで実測する（検証方法 4）。
  - これで `ops/up.sh` の手順 8-3（stream を作る回は必ず走る `systemctl restart $PREFIX-web.service`）と、OSS 版の `oss/ops/up.sh` の手順 7-5（stream の apply のあとの同じ restart）が Kafbat UI を起こす。手順 4-4 の reboot の時点では stream がまだ無いので 75 で止まり、8-3 / 7-5 で起きる。**up.sh は変えない。**
  - `docs/deploy.md` の `sudo systemctl start <prefix>-kafka-ui` は、`SKIP_STREAM=1` で立てたあとに `ops/up.sh` を通さず stream だけを上げた場合のための手順として残す（`ops/up.sh` を打ち直せば 8-3 で起きる）。

### 行 72: Docker と Kafbat UI の節を落ちても止めない

- user_data の並びを「python3.13 → `install -d` → `aws s3 sync` → Docker と Kafbat UI（関数 `kafka_ui_setup`） → web/ が無いときの `exit 0` → 残りの Gradio」にする。
- `kafka_ui_setup` の中は 1 行ずつ `|| return 1` で繋ぐ。`kafka_ui_setup || …` の形で呼ぶと、関数の中では `set -e` が効かない（bash の仕様）ので、`set -e` に頼らない。
- 落ちたら標準エラーに 1 行（`<prefix>-kafka-ui: setup failed (…); continuing with the web UI. …`）を出して進む。user_data の標準エラーは `/var/log/cloud-init-output.log` と、cloud-final.service の journald（`journalctl -u cloud-final`）に入る。直前に落ちたコマンド（`dnf` など）自身のエラーも同じ所に並ぶ。
  - PM との合意は「logger で journald に 1 行」だったが、`logger` は使わない。AL2023 のコンテナイメージには `logger`（util-linux）が無く（実測: `command -v logger` が無い、`rpm -q util-linux` が not installed）、AMI に入っているかを AWS で確かめていない。`logger` が無いと `kafka_ui_setup || logger …` の右側が 127 で落ちて、`set -e` で user_data が止まる（行 72 の逆）。stderr なら必ず出る。
- 直したあとのやり直しは Web の EC2 の reboot（user_data は毎回の起動で走る）。

### 行 71・73・74・78（文書だけ）

- 71: description を「Web の EC2 の Docker（systemd のユニット `<prefix>-kafka-ui`）がここから pull する。`IaC/terraform/aws-managed/pipeline/stream` はこの URL とタグを SSM の `/<prefix>/kafka-ui/image` に書く」の意味に直す（ASCII、em dash なし）。
- 73: `docs/deploy.md` の 010 より前の環境の注意に足す。Fargate の `<prefix>-kafka-ui`（クラスター `<prefix>-telegraf`）が動いたままだと、そのタスクの ENI が SG `<prefix>-kafka-ui` を使っているので、手順 3 の base/core の apply がその SG の削除で DependencyViolation になる（Fargate のサービスを消すのは stream の手順 7 で、base/core の手順 3 の方が先）。どれだけ待って止まるかは未確認。直し方は先に `ops/down.sh`、または `aws ecs delete-service --cluster <prefix>-telegraf --service <prefix>-kafka-ui --force` で消してタスクが止まるのを待ってから打ち直す。OSS 版の順番も同じ。
- 74: `docs/troubleshooting.md` の Kafbat UI の行に、SSM の image を変えたら（`KAFKA_UI_TAG` を上げて stream を apply したら）`sudo systemctl restart <prefix>-kafka-ui` でコンテナを入れ替える（スクリプトは起動時に 1 回だけ読むので、restart か reboot まで旧版のまま）を書く。同じ行の文言を 75 / 69 / setup failed の 3 つに直す。
- 78: `docs/verification/20261008-oss-aws.md` の 2 か所（Kafka を見た手筋と、クラスター名の注意）に、本文は書き換えずに注記を足す。010 からは Kafbat UI は Web の EC2 の `127.0.0.1:8082`（Docker）で、クラスター名（REST の `/api/clusters/{name}/…` の識別子）は `<prefix>`（例 `efukuda-nwc-oss`）。
- 行 75 に合わせて、`docs/pipeline.md` の Kafbat UI の段落（「読めるまで 30 秒ごとに起こし直すので、`SKIP_STREAM=1` の回はユニットが待ち続けるだけ」）と、`docs/deploy.md` の `SKIP_STREAM` の行・手順 7・手順 8-3 の書き方を直す。

## 変更対象ファイル

| ファイル | 行 | 変更 |
| :--- | :--- | :--- |
| `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl` | 72・75 | 並び替えと `kafka_ui_setup`、`param()` の 75 / 69、`RestartPreventExitStatus=75`、`ExecStopPost` に `.err`、Web のユニットに `Wants=` |
| `IaC/terraform/aws-managed/base/ecr/outputs.tf` | 71 | description |
| `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` | 75 | 冒頭のコメント（「読めるまで再試行して待つ」を 75 / 69 と `Wants=` に。OSS 版はリンクで同じファイル。セルフレビューで追加） |
| `tests/test_stream.py` | 72・75 | 既存の検査を新しい形に合わせ、足す（検証方法 2） |
| `docs/deploy.md` | 73・75 | SKIP_STREAM の行、手順 7・8-3、010 より前の注意、Kafbat UI の開き方 |
| `docs/troubleshooting.md` | 72・74・75 | Kafbat UI の行 |
| `docs/pipeline.md` | 75 | Kafbat UI の段落の再試行の書き方 |
| `docs/verification/20261008-oss-aws.md` | 78 | 注記 2 か所 |
| `docs/architecture/resources/web-ec2.md`、`docs/architecture/resources/ssm-parameter-store.md` | 72・75 | Kafbat UI の行と、読む側の説明を 75 / 69 / setup failed に合わせる（実装で足した。cold review Round 1 の Nit 2 で一覧に追加） |
| `docs/cycles/014-kafbat-ui-followups/` | — | design.md / design-log.md / build.md |

触らない: `ops/up.sh`・`oss/ops/up.sh`・`app/containerlab/lab.sh`・`docker/compose/`・`docs/cycles/BACKLOG.md`。

## 再利用するもの

- `tests/test_stream.py` の `_render_web_ud`（テンプレートの変数の仮置き）、偽の `aws` / `docker`（`_kbin`）、`_krun`（スクリプトを書いて直に起こす）。偽の `aws` に ParameterNotFound 以外の失敗（AccessDenied 254・不達 255・認証情報なし 253）を出すモードを足す。
- 010 の変異の検査 `self/mut.py`（スクラッチパッド）を、このサイクルの変異に入れ替えて使う。
- 手元の systemd のイメージ `t014-systemd:latest`（AL2023 + systemd 252.23 + util-linux。検証が済んだら消す）。

## 実装ステップ

1. `web_user_data.sh.tftpl`
   1. Kafbat UI の節（コメント・Docker・スクリプト・ユニット・`daemon-reload`・`enable --now`）を `aws s3 sync` の直後、`requirements.txt` が無いときの `exit 0` より前に移し、関数 `kafka_ui_setup` に包む。各行を `|| return 1` で繋ぐ（heredoc は `cat > … <<'__KAFKA_UI__' || return 1`）。呼ぶ所は `kafka_ui_setup || echo "${name_prefix}-kafka-ui: setup failed (see the error above). Continuing with the web UI; fix it and reboot the instance." >&2`。
   2. スクリプトの `param()` を、標準エラーを `ERR_FILE=/run/${name_prefix}-kafka-ui.err` に受けて 0 / 69 / 75 に分ける形にし、読む所を `IMAGE=$(param image) || exit $?` の 4 行にする。`umask 077` はスクリプトの頭（`set -euo pipefail` の次）に上げ、`.err` も env と同じく 0600 で作る。
   3. Kafbat UI のユニットに `RestartPreventExitStatus=75` を足し、`ExecStopPost` で `.err` も消す。
   4. Web のユニットの `[Unit]` に `Wants=${name_prefix}-kafka-ui.service` を足す（`Wants=network-online.target` は残す）。
   5. 冒頭のコメントを新しい動き（75 で止まる、69 で 30 秒ごと、8-3 / 7-5 の Web の restart で起きる）に直す。コメント以外は ASCII だけ。
2. `ecr/outputs.tf` の description。
3. `tests/test_stream.py` を直し、足す（検証方法 2 の一覧）。
4. 文書（deploy.md・troubleshooting.md・pipeline.md・verification）。
5. 検証方法を全部取り、build.md に生ログを貼る。セルフレビュー（`/robust` と反対弁護人）。

## 検証方法

1. `bash ops/check.sh` の最後の行が「すべて通過」。各テストの本数を build.md に書く。
2. `uv run --group dev --group web python tests/test_stream.py` の最後の行が「通過 N / 失敗 0」で、次の検査が入っていること。
   - ParameterNotFound（4 つのパラメータのどれが無くても）→ 75、env を書かない、docker を呼ばない、標準エラーに `Not retrying` と `systemctl start x-nwc-poc-kafka-ui` があり `Retrying in 30 s` が無い、値が出ない。
   - ParameterNotFound 以外（AccessDenied 254・不達 255・認証情報なし 253。4 つのパラメータのそれぞれで）→ 69、env を書かない、docker を呼ばない、標準エラーに `Retrying in 30 s` と AWS CLI のエラー文（例 `(AccessDeniedException)`）があり `Not retrying` が無い。
   - ユニットに `Restart=always`・`RestartSec=30`・`RestartPreventExitStatus=75`、`ExecStopPost` が env と `.err` の両方を消す。
   - Web のユニットに `Wants=x-nwc-poc-kafka-ui.service`（描いた user_data で）と `Wants=network-online.target`。
   - user_data の中の位置が `aws s3 sync` < `kafka_ui_setup` の呼び出し < `requirements.txt` が無いときの `exit 0`。
   - Kafbat UI を起こす Web の restart が stream の apply より後で、stream を作る回はいつも通ること: `ops/up.sh` の `tf_apply pipeline/stream` < 手順 8-3 で、8-3 の `if` が `[ -z "$SKIP_STREAM" ] ||` で始まる。`oss/ops/up.sh` の `tf_apply pipeline/stream` < 手順 7-5 で、7-5 は `if` の外（セルフレビューで追加）。
   - `kafka_ui_setup` を偽の `dnf` / `systemctl` / `chmod` で動かし、Docker が無くて `dnf` が落ちる・`systemctl enable --now docker` が落ちる・`chmod` が落ちる・`daemon-reload` が落ちる・`enable --now` が落ちる・書き先が無い、のどれでも、`set -euo pipefail` の下で呼び出しの次の行まで進み、`setup failed` の 1 行が出て、落ちた所より後のコマンドを打たないこと。全部通る場合は `setup failed` が出ず、`systemctl` を `enable --now docker`・`daemon-reload`・`enable --now x-nwc-poc-kafka-ui.service` の順に打つこと。
   - 既存の検査（SASL_SSL / PLAINTEXT の env、ECR のログインや pull の失敗、shebang と `set -euo pipefail` と 0755、bash -n をマネージド版と OSS 版の両方で）がそのまま通ること。
3. 変異の検査（`self/mut.py`）で、次の変異を入れると `tests/test_stream.py` が落ちること（期待: 全部「検出」）。chmod を消す / shebang を消す / スクリプトの `set -euo pipefail` を消す / `RestartPreventExitStatus=75` を消す / ParameterNotFound の判定を消す（全部 75 にする・全部 69 にする）/ Web の `Wants=` を消す / `kafka_ui_setup` の呼び出しから `|| echo` を外す（落ちたら止まる） / 関数の中の `|| return 1` を 1 つ外す / Kafbat UI の節を `aws s3 sync` より前に戻す / `exit 0` より後ろに動かす / `ExecStopPost` から `.err` を外す / `umask 077` を `param()` より後ろに戻す / 8-3 の条件から `SKIP_STREAM` を外す / 8-3 の節を消す / OSS 版の 7-5 の restart を消す・`if` で包む・stream の apply より前に動かす（最後の 5 つはセルフレビューで追加）。
4. 手元の systemd のコンテナ（`t014-systemd:latest`。AL2023、systemd 252.23）で、描いた user_data を、偽の `aws` / `docker` / `python3.13` / `dnf` と偽の `docker.service` で実際に打ち、コマンドと出力を build.md に貼る。期待は次のとおり。
   - a. SSM に `/x-nwc-poc/kafka-ui/image` が無い（ParameterNotFound）: user_data は最後まで進み Web が active。Kafbat UI は 1 回 75 で終わって `ActiveState=failed`・`Result=exit-code`・`ExecMainStatus=75` になり、40 秒待っても `NRestarts=0` のまま。journald に `Not retrying` の 1 行。
   - b. そこで偽の SSM を「ある」にして `systemctl restart x-nwc-poc-web` を打つと、Kafbat UI が `active (running)` になる（failed から restart の Wants= で起きる。PM の条件 1）。
   - c. Kafbat UI が落ちても Web は巻き込まれない（PM の条件 1）: Web が active のまま、Kafbat UI を 69 で落ちる状態にして restart する・`docker.service` を止める（`Requires=` で Kafbat UI が止まる）・`docker.service` が起きない状態で `systemctl restart x-nwc-poc-web` を打つ、のどれでも Web の `ActiveState=active`（restart した場合以外は `MainPID` も `InvocationID` も変わらない）。
   - d. ParameterNotFound 以外（偽の SSM が AccessDenied）: Kafbat UI が 69 で終わり、30 秒後に起こし直される（`NRestarts` が 1 以上に増える）。
   - e. Docker が無く `dnf install -y docker` が落ちる: user_data は `setup failed` の 1 行を出して最後まで進み、Web が active。Kafbat UI のユニットは無い（Web の `Wants=` の先が無くても Web は起きる）。
5. コメント以外が ASCII（`tests/test_workflow.py` の検査。check.sh の中で走る）。

## 未確定事項とリスク

- **AWS で確かめていない**（このエンジニアは AWS を使わない）。実物の Web の EC2 で、Kafbat UI が 75 で止まり 8-3 の restart で起きるか、`setup failed` の行が `journalctl -u cloud-final` に出るかは未確認。systemd の動きは AL2023 と同じ 252.23 のコンテナで測るが、cloud-init と実物の Docker・ECR・SSM は通さない。
- **AL2023 の EC2 の AWS CLI の版で、標準エラーの形が同じか**は未確認（手元は 2.36.34）。`(ParameterNotFound)` が出なくなる版なら、無いパラメータも 69 で 30 秒ごとに回る（いまの動きに戻るだけで、止まる側には倒れない）。
- **`aws s3 sync` が落ちると Kafbat UI も入らない。** 並びを入れ替えたので、S3 の取得に失敗すると `set -e` で Docker の節まで行かない（いまは Kafbat UI が先に入る）。PM の決定（Gradio を先に）どおりで、S3 が読めない EC2 は Web も動かないので、まず S3 を直す。
- **75 で止まったあと、stream を `ops/up.sh` を通さずに上げると Kafbat UI は起きない。** 起こすのは `sudo systemctl start <prefix>-kafka-ui` か Web の restart か reboot（docs/deploy.md に書く）。
- **ParameterNotFound が一時的に出る場合**（stream の apply の途中でパラメータの一部だけができている間に Kafbat UI が起きる）は 75 で止まる。8-3 / 7-5 の restart は stream の apply が終わったあとなので、up.sh の流れでは起きる。
- **`ops/up.sh` が stream の apply のあと、8-3（OSS 版は 7-5）の restart より前で止まると、Kafbat UI は 75 のまま残る**（graph・nautobot・analytics の apply の失敗や `die` で。初回の起動は stream より前なので、その回の Kafbat UI はもう 75 で止まっている）。直して `ops/up.sh` を打ち直せば 8-3 / 7-5 で起きる。打ち直さないなら `sudo systemctl start <prefix>-kafka-ui`。docs/troubleshooting.md の Kafbat UI の行に書く。restart が stream の apply より後にあって、stream を作る回はいつも通ること（8-3 の if に `[ -z "$SKIP_STREAM" ]`、7-5 は if の外）は tests/test_stream.py で縛る（up.sh には触らない）。
- **010 より前の環境の DependencyViolation** は 010 の cold review の読みで、AWS で再現していない。どれだけ待って失敗するか（Terraform の SG の削除の既定のタイムアウト）も未確認として書く。
- `/run/<prefix>-kafka-ui.err` に入るのは AWS CLI のエラー文だけ（値は標準出力に分けている）なので秘密は入らないが、スクリプトの頭の `umask 077` で 0600 にする。止まったら `ExecStopPost` で消す。エラー文はスクリプトの 1 行（69 / 75）に添えて journald に出すので、ファイルを読みに行く必要は無い。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261009-cycle-014-kafbat-ui-followups-design.html -->
