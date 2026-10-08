# Kafbat UI の残りの Nit を片付ける（016）

設計: PM(fable-5.1) / effort: high。実装はエンジニア2（opus-5.5 以下）。2026-10-09。

## 背景

「Kafbat UI を Web の EC2 に移した残りを直す（014）」のセルフレビューと cold review で、直さずに BACKLOG へ回した Nit が 10 件ある（`docs/cycles/BACKLOG.md` の末尾、`docs/cycles/014-kafbat-ui-followups/build.md` の `### セルフレビュー`、同 `review.md` の `## Nit`）。そのうち **AWS で測らないと決められない 2 件**（`SuccessExitStatus=75` にすると degraded が消えるか、reboot のときの 69 の待ち）と、**PM が design.md を直して済んだ 1 件**を除いた 7 件と、「Kafbat UI を Web の EC2 に同居させる（010）」の Nit 10（`ops/up.sh` の ECR エンドポイントのコメント）を、docs と tests だけで片付ける。

このサイクルで触るのは docs・コメント・`tests/test_stream.py` だけで、**user_data / ユニット / Terraform / `ops/up.sh` の動きは変えない**（AWS で確かめ直す項目を増やさない）。

### 調査で分かった事実（2026-10-09、`docs/cycle-006-design` f766351）

- Kafbat UI のスクリプトは `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:41-52`。`param()` は `aws ssm get-parameter` が `(ParameterNotFound)` 以外で落ちると 69（30 秒ごとに起こし直す）、`ParameterNotFound` なら `"$PARAM/$1 does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; …"` を出して 75。ユニットは `Restart=always`（:85）と `RestartPreventExitStatus=75`（:87）。読むパラメータは `image` / `bootstrap-servers` / `admin-password`（`$PARAM/$1` の名前がログに出るので、どれが無いかは読める）。
- `/<prefix>/kafka-ui/image` と `bootstrap-servers` は `pipeline/stream/kafka_ui.tf` が作る。**`admin-password` は `ops/up.sh:919` の `ensure_secret` が作る**（`kafka_ui.tf:23` は名前を参照するだけ）。`SKIP_STREAM=1` で立てたあと `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream apply` だけで stream を上げると、`image` / `bootstrap-servers` はできるが `admin-password` が無く、`sudo systemctl start <prefix>-kafka-ui` は `…/kafka-ui/admin-password does not exist (pipeline/stream is not applied …)` を出して 75 で止まる（文言が実態とずれる）。`docs/deploy.md:26` と `docs/troubleshooting.md:94` は「stream だけを上げたら `sudo systemctl start`」と案内していて、この穴に触れていない。
- `ops/up.sh` の手順 8-3（Web の restart。`deploy.md:95`）で Kafbat UI が起きる。手順 10（`deploy.md:99`）はその直後にポートフォワードを開く。コンテナが上がるまで（イメージの pull を含む）は 8082 が開かず、ブラウザは接続拒否になる。`docs/deploy.md` にその旨が無い（014 のセルフレビューの Nit 4）。
- `docs/deploy.md:102` は 010 より前の環境で Web の EC2 が作り直されることを書いているが、**014 でも user_data を変えた**ので、014 より前に立てたままの環境は次の base/core の apply で作り直される（`web.tf:101` の `user_data_replace_on_change = true`、コメントは `:77`）。インスタンス ID が変わるので `start_session_command` を取り直す（cold review の Nit 3）。
- `docs/troubleshooting.md:94` は「画面に入れない」の表の 1 行に Kafbat UI の症状と対処を全部詰めていて 1,800 文字を超える。`daemon-reload` / `enable` が落ちた形（`kafka_ui_setup` の `|| return 1` で user_data は止まらず、`cloud-init-output.log` に `<prefix>-kafka-ui: setup failed` が出る）は書いていない（014 のセルフレビューの Nit 10、cold review の Nit 6）。
- `RestartPreventExitStatus=75` は **ユニットの main プロセスの終了コード**を見る。スクリプトは `exec docker run …` するので、コンテナ（Kafbat UI の Java）が 75 で終わった場合も起こし直さない。Kafbat UI が 75 を返す場面は知られていないが、docs にその前提が無い（cold review の Nit 4）。
- `tests/test_stream.py:479-492` の「8-3 / 7-5」の検査は、`_s75.split("\n")[2] == 'run_on_instance …'` と行の位置に依り、`_up_sh.index(…)` / `_oss_up_sh.index(…)` の `.index()` が外れると `ValueError` で**以降の検査が全部止まる**（`check()` の中で例外が出るので失敗数に数えない）。014 の変異 5 個のうちいくつかは `ValueError` で「落ちた」ことになっていた（014 のセルフレビューの Nit 9、cold review の Nit 5）。
- 同 `:471-476` の `Wants=${name_prefix}-kafka-ui.service` の検査はテンプレート `_wunit`（`web_ud` = 描く前の tftpl）だけを見る。描いたあと（`_krendered` = `_render_web_ud()`、`:541`）の Web のユニットに `Wants=x-nwc-poc-kafka-ui.service` があるかは見ていない（cold review の Nit 1）。
- `ops/up.sh:506-507` の `pipeline/stream) add_endpoints ecr.api ecr.dkr logs` のコメントは「Telegraf（ECS）: イメージを ECR から引き…」だけで、Web の EC2 の Kafbat UI も同じエンドポイントで ECR から pull する（`:691-692` でミラーしたイメージ）ことを書いていない（010 の Nit 10、BACKLOG 77 行目）。

## 設計方針

1. **`docs/deploy.md`**（3 か所）
   - 手順 10 の行（:99）か直後の注記に「初回の `ops/up.sh` では 8-3 で起こした直後なので、Kafbat UI（8082）はポートフォワードが開いた時点でまだ上がっていないことがある（イメージの pull を含めて 1〜2 分、未計測）。接続拒否なら少し待って開き直す。`journalctl -u <prefix>-kafka-ui` で `Started` を待ってもよい」を足す。
   - :102 の箇条書きを「010 より前」限定から「**user_data を変えたサイクル（010、014）より前に立てたままの環境**は、次の base/core の apply で Web の EC2 が作り直される」に広げる。`user_data_replace_on_change`（`web.tf:101`）に触れ、`start_session_command` を取り直すことは残す。
   - :26（`SKIP_STREAM`）の括弧の中の「あとで `ops/up.sh` を通さずに stream だけを上げたら … `sudo systemctl start`」を、「`ops/up.sh` を打ち直す（手順 8 の `ensure_secret` が `admin-password` を作り、8-3 で起きる）。terraform だけで stream を上げると `/<prefix>/kafka-ui/admin-password` が無く（これは `ops/up.sh` が作る）、`start` しても 75 で止まる」に直す。**`admin-password` を Terraform で作る案は採らない**（このリポジトリの決まり: シークレットは `ops/up.sh` が SSM の SecureString として作る）。
2. **`docs/troubleshooting.md`**
   - :94 の 1 行を表から外し、「画面に入れない」の節の下に小節 `### Kafbat UI` を作って、journald の 1 行で分ける形の箇条書きにする。項目: 75（stream が無い / `admin-password` だけ無い）、69（読めない）、ユニットが無い（user_data の Docker の節が落ちた）、**`daemon-reload` / `enable --now` が落ちた形**（`cloud-init-output.log` に `<prefix>-kafka-ui: setup failed` が出て、`systemctl status <prefix>-kafka-ui` は `could not be found` か `disabled`。`sudo systemctl daemon-reload && sudo systemctl enable --now <prefix>-kafka-ui` で手で続きを打つ）、`docker login` / `pull` で落ちる、画面に MSK が出ない、パスワード / イメージを変えたとき、手で止めたのに戻る（`mask --runtime`）。いまの行の中身を落とさない（表の行を消して小節に移すだけ。文は短く切る）。
   - 75 の項目に「`RestartPreventExitStatus=75` は main プロセスの終了コードを見るので、`exec docker run` したコンテナが 75 で終わったときも起こし直さない。journald に `does not exist` の行が無く 75 で止まっていたらコンテナ側（`docker logs <prefix>-kafka-ui`）を見る」を足す（cold review の Nit 4 は docs で扱う。設定は変えない）。
   - 表の同じ節に 1 行だけ残す: `| Kafbat UI のポートフォワードがつながらない、画面が開かない | 下の「Kafbat UI」を見る |`。
3. **`ops/up.sh:506`** のコメントに「Web の EC2 の Kafbat UI（Docker）も同じ ecr.api / ecr.dkr で ECR から pull する」を足す（コードは変えない）。
4. **`tests/test_stream.py`**
   - `:479-492` の検査を、行の位置（`split("\n")[2]`）ではなく「7-5 の節の中に `run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"` の行がある」で見る形にし、`.index()` は `.find()` + `-1` の判定（または小さなヘルパー `_between(text, start, end)` が無ければ `""` を返す）にして、**外れても `ValueError` で止まらず、その `check` だけが失敗する**ようにする。他の `check` の中の `.index()` は触らない（範囲を広げない。ここは 014 で足した 2 本だけ）。
   - `:471-476` の `Wants=` の検査の隣に、`_krendered` から Web のユニット（`cat > /etc/systemd/system/x-nwc-poc-web.service` のヒアドキュメント）を切り出して `Wants=x-nwc-poc-kafka-ui.service\n` がある、かつ `kafka-ui` を含む行がその 1 行だけ、を見る `check` を 1 本足す。
   - 件数は 88 → 89（1 本足す。直した 2 本は本数が変わらない）。`docs/development.md:37` の `test_stream` の数を合わせる。
5. **変異で確かめる**（build.md に貼る）: (a) `oss/ops/up.sh` の 7-5 の `run_on_instance` 行の前に空行を 1 つ入れる → 直す前は `ValueError`（以降が止まる）、直した後はその `check` だけ失敗、戻すと通る。(b) `_s83` の開始マーカー `# ---- 8-3. Web ` を一時的に `# ---- 8-3. web ` に変える → 直した後は `ValueError` にならず失敗 1。(c) テンプレートの `Wants=${name_prefix}-kafka-ui.service` を消す → 新しい `check` と既存の `check` の両方が失敗。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `docs/deploy.md` | :26 の `SKIP_STREAM` の括弧（terraform だけで上げると `admin-password` が無い）、:99 の手順 10 か直後の注記（Kafbat UI がまだ上がっていない）、:102（014 でも作り直し） |
| `docs/troubleshooting.md` | :94 の行を小節 `### Kafbat UI` に移し、`daemon-reload` / `enable` の失敗の形と、コンテナの 75 の注意を足す。表には 1 行の案内だけ残す |
| `ops/up.sh` | :506 のコメントに Kafbat UI の pull を足す（コードは不変） |
| `tests/test_stream.py` | :479-492 の 2 本を `ValueError` で止まらない形に。`_krendered` の `Wants=` の `check` を 1 本足す |
| `docs/development.md` | :37 の `test_stream` 88 → 89 |
| `docs/cycles/016-kafbat-ui-nits/` | design.md / design-log.md / build.md |

触らない: `web_user_data.sh.tftpl`、`web.tf`、`kafka_ui.tf`、`ops/up.sh` のコード、`oss/ops/up.sh`、`docs/architecture/**`（014 で揃えてある）。

## 再利用するもの

- `tests/test_stream.py` の `check()`、`_render_web_ud()`（:541 の `_krendered`）、`_wunit` の切り出し方（:471）。
- `docs/troubleshooting.md:94` のいまの文（小節へ移す素材）。
- `docs/deploy.md:102` の書き方（010 の注記）。

## 実装ステップ

1. `tests/test_stream.py` を直す（4）。直す前に変異 (a) で `ValueError` になることを取って build.md に貼る。
2. docs 3 本とコメント（1〜3）。
3. `docs/development.md` の件数。
4. `bash ops/check.sh` と変異 (a)(b)(c)。
5. build.md に Round 1（実装モデル、commit、検証の生ログ）と `### セルフレビュー`。

commit は「tests」「docs とコメント」の 2 つに分ける。

## 検証方法

| # | 確認 | 期待 |
|---|---|---|
| 1 | `uv run --group dev --group web python tests/test_stream.py` | `通過 89 / 失敗 0` |
| 2 | 変異 (a)（7-5 の行の前に空行）で同じコマンド | `ValueError` ではなく `通過 88 / 失敗 1`、失敗は「Kafbat UI を起こす Web の restart は stream の apply より後」の 1 本。戻すと 89 / 0 |
| 3 | 変異 (b)（`_s83` のマーカーを変える） | `ValueError` ではなく失敗 1。戻すと 89 / 0 |
| 4 | 変異 (c)（テンプレートの `Wants=` を消す） | 失敗 2（既存の `_wunit` の `check` と新しい `_krendered` の `check`）。戻すと 89 / 0 |
| 5 | `bash ops/check.sh` | 最後の行が `すべて通過`。`test_stream` 89 |
| 6 | `grep -c 'Kafbat UI' docs/troubleshooting.md` と `grep -n '^### Kafbat UI' docs/troubleshooting.md` | 小節が 1 つあり、表の行は案内の 1 行だけ（`grep -n 'Kafbat UI のポートフォワード' docs/troubleshooting.md` が 1 行） |
| 7 | `grep -n 'admin-password' docs/deploy.md docs/troubleshooting.md` | 「terraform だけで上げると無い」の文が deploy.md:26 の行と troubleshooting の 75 の項目にある |
| 8 | `grep -n 'Kafbat' ops/up.sh | grep -n 506` 相当（`sed -n '506p' ops/up.sh`） | コメントに Kafbat UI が入る。`git diff --stat ops/up.sh` が 1 行の変更だけ |

AWS では確かめない（docs・コメント・tests だけ）。

## 未確定事項とリスク

- 初回の 8-3 からポートフォワードまでに Kafbat UI が上がるまでの時間は未計測（イメージ 640 MB の pull を含む）。docs には「1〜2 分（未計測）」と書き、数字は AWS 検証で取れたら直す。
- `daemon-reload` / `enable --now` が落ちたときに `systemctl status` が `could not be found` と `disabled` のどちらになるかは、落ちた場所（ユニットファイルを書く前か後か）で変わる。docs には両方を書く。
- コンテナが 75 で終わる場面は知られていない。docs に前提として書くだけで、`RestartPreventExitStatus` は変えない。
- `docs/troubleshooting.md` の表の行を小節に移すと、`tests/` に表の行の文言を見る検査があれば落ちる。実装の最初に `grep -rn 'Kafbat UI のポートフォワード\|mask --runtime' tests/` で確かめ、あれば小節の文言に合わせる。
- 012（`feat/collectors-scram-syslog-goflow`）が `docs/deploy.md` / `docs/troubleshooting.md` / `ops/up.sh` を触っているので、マージの順番で衝突する。衝突は PM が解消する。
