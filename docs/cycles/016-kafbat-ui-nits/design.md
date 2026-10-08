# Kafbat UI の残りの Nit を片付ける（016）

設計: PM(fable-5.1) / effort: high。実装はエンジニア2（opus-5.5 以下）。2026-10-09。Round 1 で、実装者（エンジニア2、opus-5.5）が事実と食い違っていた所と、セルフレビューで採った変更を上書きした（PM の 2026-10-09 の指示「設計を正本にする」。経緯は design-log.md の Round 1）。

## 背景

「Kafbat UI を Web の EC2 に移した残りを直す（014）」のセルフレビューと cold review で、直さずに BACKLOG へ回した Nit が 10 件ある（`docs/cycles/BACKLOG.md` の末尾、`docs/cycles/014-kafbat-ui-followups/build.md` の `### セルフレビュー`、同 `review.md` の `## Nit`）。そのうち **AWS で測らないと決められない 2 件**（`SuccessExitStatus=75` にすると degraded が消えるか、reboot のときの 69 の待ち）と、**PM が design.md を直して済んだ 1 件**を除いた 7 件と、「Kafbat UI を Web の EC2 に同居させる（010）」の Nit 10（`ops/up.sh` の ECR エンドポイントのコメント）を、docs と tests だけで片付ける。

このサイクルで触るのは docs・コメント・`tests/test_stream.py` だけで、**user_data / ユニット / Terraform / `ops/up.sh` の動きは変えない**（AWS で確かめ直す項目を増やさない）。

### 調査で分かった事実（2026-10-09、`docs/cycle-006-design` c4c378a）

- Kafbat UI のスクリプトは `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:45-57`。`param()` は `aws ssm get-parameter` が `(ParameterNotFound)` 以外で落ちると 69（30 秒ごとに起こし直す）、`ParameterNotFound` なら `"$PARAM/$1 does not exist (pipeline/stream is not applied, e.g. SKIP_STREAM=1). Not retrying; …"` を出して 75。ユニットは `Restart=always`（:85）と `RestartPreventExitStatus=75`（:87）。読むパラメータは順に `image` / `bootstrap-servers` / `security-protocol` / `admin-password`（`$PARAM/$1` の名前がログに出るので、どれが最初に無かったかは読める）。
- `/<prefix>/kafka-ui/image` / `bootstrap-servers` / `security-protocol` は `pipeline/stream/kafka_ui.tf` が作る。**`admin-password` は `ops/up.sh` の手順 7 の中の `ensure_secret`（`ops/up.sh:919`。OSS 版も手順 7 の `oss/ops/up.sh:324`）が作る**（`kafka_ui.tf:23` は名前を参照するだけ）。`SKIP_STREAM=1` で立てたあと `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream apply` だけで stream を上げると、`image` などはできるが `admin-password` が無く、`sudo systemctl start <prefix>-kafka-ui` は `…/kafka-ui/admin-password does not exist (pipeline/stream is not applied …)` を出して 75 で止まる（文言が実態とずれる）。`docs/deploy.md:26`・`:276`、`docs/troubleshooting.md:94`、`docs/pipeline.md:156` は「stream だけを上げたら `sudo systemctl start`」と案内していて、この穴に触れていない。
- `ops/up.sh` の手順 8-3（Web の restart。`deploy.md:95`）で Kafbat UI が起きる。手順 10（`deploy.md:99`）が自分で開くのは Web（8080）のポートフォワードだけで、Kafbat UI（8082）は表示されたコマンド（`kafka_ui_port_forward_command`）を別に打つ。コンテナが上がるまで（イメージの pull を含む）は 8082 が開かず、ブラウザは接続拒否になる。`docs/deploy.md` にその旨が無い（014 のセルフレビューの Nit 4）。上がった合図は Kafbat UI 自身の `Started KafkaUiApplication`。systemd の `Started <prefix>-kafka-ui.service` はスクリプトが動き出した時点で出るので合図にならない（手元の Docker で `ghcr.io/kafbat/kafka-ui:v1.5.0` を起こし、7 秒で HTTP が返り、その直前に `Started KafkaUiApplication in 4.98 seconds` が出た）。
- `docs/deploy.md:102` は 010 より前の環境で Web の EC2 が作り直されることを書いているが、**014 でも user_data を変えた**ので、014 より前に立てたままの環境は次の base/core の apply で作り直される（`web.tf:101` の `user_data_replace_on_change = true`、コメントは `:77`）。インスタンス ID が変わるので `start_session_command` を取り直す（cold review の Nit 3）。
- `docs/troubleshooting.md:94` は `## パイプラインと WORKFLOW` の表の 1 行に Kafbat UI の症状と対処を全部詰めていて 1,800 文字を超える。`daemon-reload` / `enable` が落ちた形（`kafka_ui_setup` の `|| return 1` で user_data は止まらず、`cloud-init-output.log` に `<prefix>-kafka-ui: setup failed` が出る）は書いていない（014 のセルフレビューの Nit 10、cold review の Nit 6）。行の中の「戻すのは `unmask`」は誤りで、`mask --runtime` は `/run/systemd/system` に置くので `unmask --runtime` でないと外れない（systemd の `unit_file_unmask` は `UNIT_FILE_RUNTIME` のときだけ runtime 側を見る。AL2023 では未確認）。`docs/pipeline.md:156` にも同じ誤りがある。
- `RestartPreventExitStatus=75` は **ユニットの main プロセスの終了コード**を見る。スクリプトは `exec docker run --rm …` するので、コンテナ（Kafbat UI の Java）が 75 で終わった場合も起こし直さない。コンテナは `--rm` で消えるので、出力は `docker logs` ではなく `journalctl -u <prefix>-kafka-ui` に残る。Kafbat UI が 75 を返す場面は知られていないが、docs にその前提が無い（cold review の Nit 4）。
- `tests/test_stream.py:477-492` の「8-3 / 7-5」の検査は、`_s83` / `_s75` を `check()` の外（モジュールの最上位）で `_up_sh[_up_sh.index(…):…]` と切り出すので、印が外れると `ValueError: substring not found` で止まり、どの検査が落ちたかの名前が出ない。7-5 は `_s75.split("\n")[2] == 'run_on_instance …'` と行の位置に依る（014 のセルフレビューの Nit 9、cold review の Nit 5）。`check(name, cond)`（`:10-12`）は `assert cond, name` なので、どの形でも最初の失敗でテスト全体が止まる。直すのは「止まり方が `ValueError` か、検査の名前の付いた `AssertionError` か」。
- 同 `:471-476` の `Wants=${name_prefix}-kafka-ui.service` の検査はテンプレート `_wunit`（描く前の tftpl）だけを見る。描いたあと（`_krendered = _render_web_ud()`）の Web のユニットに `Wants=x-nwc-poc-kafka-ui.service` があるかは見ていない（cold review の Nit 1）。
- `ops/up.sh:506-507` の `pipeline/stream) add_endpoints ecr.api ecr.dkr logs` のコメントは「Telegraf（ECS）: イメージを ECR から引き…」だけで、Web の EC2 の Kafbat UI も同じエンドポイントで ECR から pull する（`:691-692` でミラーしたイメージ）ことを書いていない（010 の Nit 10、BACKLOG 77 行目）。

## 設計方針

1. **`docs/deploy.md`**（4 か所）
   - :26（`SKIP_STREAM`）の括弧の中の「あとで `ops/up.sh` を通さずに stream だけを上げたら … `sudo systemctl start`」を、「stream を足すときは `SKIP_STREAM` を外して `ops/up.sh` を打ち直す。手順 7 で `/<prefix>/kafka-ui/admin-password` を作り、手順 8-3 の Web の再起動で Kafbat UI が起きる。terraform だけで stream を上げると `admin-password` が無い（Terraform ではなく `ops/up.sh` が作る）ので、`start` しても 75 で止まる」に直す。**`admin-password` を Terraform で作る案は採らない**（このリポジトリの決まり: シークレットは `ops/up.sh` が SSM の SecureString として作る）。
   - :276（ポートフォワードの節）の同じ案内も同じ趣旨に直す。
   - 手順の表の下の箇条書き（`-auto-approve` の行と :102 の間）に注記を 1 つ足す: 手順 10 が開くのは Web（8080）だけで、Kafbat UI（8082）は表示されたコマンドを別のターミナルで打つ。初回の `ops/up.sh` では 8-3 で起こした直後なので、まだ上がっていないことがある（イメージの pull を含めて 1〜2 分の見込み、AWS では未計測。手元の Docker では pull 済みのイメージで 7 秒）。つながらなければ少し待って開き直し、ポートフォワードが閉じていたらコマンドを打ち直す。上がったかは `journalctl -u <prefix>-kafka-ui` の `Started KafkaUiApplication` で見る（systemd の `Started <prefix>-kafka-ui.service` は合図にならない）。
   - :102 の箇条書きを「010 より前」限定から「**user_data を変えたサイクルより前に立てたままの環境**は、次の base/core の apply で Web の EC2 が作り直される（`web.tf` の `user_data_replace_on_change = true`）。いまのところ 010 と 014」に広げる。010 より前はインスタンスタイプ・ホップ数・ボリュームも変わること、`start_session_command` を取り直すことは残す。
2. **`docs/troubleshooting.md`**
   - :94 の 1 行を表から外し、表の同じ位置には案内の 1 行だけ残す: `| Kafbat UI のポートフォワードがつながらない、画面が開かない | 下の「Kafbat UI」を見る |`。
   - その表（`## パイプラインと WORKFLOW`）の直後に小節 `### Kafbat UI` を置き、`systemctl status` / `journalctl -u` の行で分ける箇条書きにする（「下の」がそのまま当たる位置）。項目は次の 10 個で、いまの行の中身は落とさない。
     1. ユニットは動いているのに 8082 につながらない（まだ上がっていない。合図は `Started KafkaUiApplication`）
     2. `does not exist … Not retrying` で止まっている（75）。読む順（`image`、`bootstrap-servers`、`security-protocol`、`admin-password`）と、3 つの下位項目: `image` など（stream がまだ無い。`SKIP_STREAM` を外して `ops/up.sh`。OSS 版は `SKIP_STREAM` を読まず 7-5 で起こす）、`admin-password`（terraform だけで上げた。`admin-password` は手順 7 が作る。`start` しても 75。`ops/up.sh` を打ち直す）、stream の apply のあと 8-3 より前で `ops/up.sh` が止まった回
     3. `status=75` なのに `does not exist` の行が無い（コンテナが 75 で終わった。`RestartPreventExitStatus=75` は main プロセスの終了コードを見て、`exec docker run` なのでコンテナの終了コードがそのまま届く。`--rm` なので `docker logs` ではなく `journalctl -u`。75 で終わる場面は知られていない）
     4. `Cannot read … Retrying in 30 s`（69）
     5. ユニットが無い（`could not be found`。user_data の Docker の節が落ちた）
     6. **`setup failed` の行があり、ユニットが動いていない（`daemon-reload` / `enable --now` が落ちた）**。`systemctl status` は落ちた場所で `could not be found` か `disabled`（どちらかは未確認）。`sudo systemctl daemon-reload && sudo systemctl enable --now <prefix>-kafka-ui` で手で続きを打つか、EC2 を再起動する
     7. `docker login` / `docker pull` で落ちる
     8. 起きたのに画面に MSK が出ない
     9. SSM のパスワードかイメージを変えた
     10. 手で止めたのに戻ってくる（`mask --runtime`。戻すのは `unmask --runtime`、`--runtime` が無いと外れない）
3. **`docs/pipeline.md:156`** の Kafbat UI の段落も同じ穴と誤りを直す: 戻すのは `sudo systemctl unmask --runtime <prefix>-kafka-ui`（`--runtime` が無いと外れない）。「stream だけを上げたら `sudo systemctl start`」は、「terraform だけで上げると `admin-password`（`ops/up.sh` の手順 7 が作る。OSS 版も手順 7）が無く 75 で止まる。stream を足すときは `ops/up.sh` を打ち直す（8-3 で起きる）」にする。
4. **`ops/up.sh:506`** のコメントに「Web の EC2 の Kafbat UI（Docker）も同じ ecr.api / ecr.dkr で ECR から pull する」を足す（コードは変えない）。
5. **`tests/test_stream.py`**
   - ヘルパー `_between(text, start, end)` を足す。`start` から `end` の手前までを返し、どちらかが無ければ `""`。`_s83` / `_s75` をこれで切り出し、印が外れても `ValueError` で止まらず、「Kafbat UI を起こす Web の restart は stream の apply より後」の `check` の名前で落ちるようにする。他の `check` の中の `.index()` は触らない（範囲を広げない。ここは 014 で足した 2 本だけ）。
   - 8-3 は「節の頭が `# ---- 8-3. Web …` の見出しと `if [ -z "$SKIP_STREAM" ] || …; then` の行」「節に `  run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"` の行がある」「`fi` は 1 つ」で見る。
   - 7-5 は行の位置（`split("\n")[2]`）ではなく、`_s75` 全体が「`log "7-5. …"` の行、空行かコメント行だけ、`run_on_instance "$INSTANCE_ID" "systemctl restart $PREFIX-web.service; $WEB_ACTIVE"` の行、空行かコメント行だけ」に一致する（`re.fullmatch`）かで見る。空行・コメントの出し入れは通し、`run_on_instance` 行を条件・関数・ループ・ヒアドキュメント・行の継続で包んだり、前に `exit` を置いたりしたら落とす（014 の `split("\n")[2]` が縛っていた範囲を保つ）。7-4c から 8 の手前までの `if` / `fi` が `STORE_WARN` の 1 組だけ、も残す。
   - `_krendered` を作った直後に、描いた Web のユニット（`cat > /etc/systemd/system/x-nwc-poc-web.service <<__UNIT__` 〜 `__UNIT__`）を `_between` で切り出し、`Wants=x-nwc-poc-kafka-ui.service` の行があり、`kafka-ui` を含む行がその 1 行だけ、を見る `check` を 1 本足す（`_krendered` は `:471-476` より後ろで作るので、隣には置けない）。
   - 件数は 88 → 89（1 本足す。直した 1 本は本数が変わらない）。`docs/development.md:37` の `test_stream` の数を合わせる。
6. **変異で確かめる**（build.md に貼る）。`check()` は `assert` なので、そのまま（assert）と、scratch の写しで `check()` だけを失敗を数える形に替えたもの（count）の両方で打つ。変異は scratch の写しの中だけで、ワークツリーは触らない。
   - (a) OSS 版の 7-5 の `run_on_instance` 行の前に空行 / (a2) `log "7-5.` を `7-6.` に（`_s75` の開始の印が外れる）/ (a3) 7-5 の `run_on_instance` 行を消す / (b) `# ---- 8-3. Web ` を `# ---- 8-3. web ` に / (c) テンプレートの `Wants=${name_prefix}-kafka-ui.service` を消す。
   - 7-5 を包む変異（OC1）: (o1) 行の頭に `[ -n "${X:-}" ] &&` / (o2) 関数で包む / (o3) 前に `[ -z "${X:-}" ] || exit 0` / (o4) `while false; do … done` で包む / (o5) `: <<'__X__' … __X__` で殺す / (o6) 後ろに空行とコメント行（退行ではない）/ (o7) 前の行に `false &&`（行の継続）。

## 変更対象ファイル

行番号は c4c378a のもの（5be0288 のマージのあとはずれる）。`docs/pipeline.md` と `docs/deploy.md:276` は PM が範囲に入れた（2026-10-09）。

| ファイル | 変更 |
|---|---|
| `docs/deploy.md` | :26 の `SKIP_STREAM` の括弧、:276 の同じ案内（terraform だけで上げると `admin-password` が無い）、手順の表の下の注記（Kafbat UI がまだ上がっていない）、:102（014 でも作り直し） |
| `docs/troubleshooting.md` | :94 の行を案内の 1 行にし、表の直後に小節 `### Kafbat UI`（10 項目） |
| `docs/pipeline.md` | :156 の `unmask --runtime` と、terraform だけで上げると `admin-password` が無いこと |
| `ops/up.sh` | :506 のコメントに Kafbat UI の pull を足す（コードは不変） |
| `tests/test_stream.py` | `_between`。8-3 / 7-5 の 1 本を `ValueError` で止まらず、7-5 は `fullmatch` で見る形に。`_krendered` の `Wants=` の `check` を 1 本足す |
| `docs/development.md` | :37 の `test_stream` 88 → 89 |
| `docs/cycles/016-kafbat-ui-nits/` | design.md / design-log.md / build.md |

触らない: `web_user_data.sh.tftpl`、`web.tf`、`kafka_ui.tf`、`ops/up.sh` のコード、`oss/ops/up.sh`、`docs/architecture/**`（014 で揃えてある）。

## 再利用するもの

- `tests/test_stream.py` の `check()`、`_read()`、`_render_web_ud()`（`_krendered`）、`_wunit` の切り出し方（:471）。
- `docs/troubleshooting.md:94` のいまの文（小節へ移す素材）。
- `docs/deploy.md:102` の書き方（010 の注記）。

## 実装ステップ

1. `tests/test_stream.py` を直す（5）。直す前に変異 (a)(a2)(a3)(b)(c) を c4c378a の tests で打ち、`ValueError` の出力を取って build.md に貼る。
2. docs 4 本とコメント（1〜4）。
3. `docs/development.md` の件数。
4. `bash ops/check.sh` と変異 (a)〜(c)、(o1)〜(o7)。
5. build.md に Round 1（実装モデル、commit、検証の生ログ）と `### セルフレビュー`。

commit は「tests」「docs とコメント」の 2 つに分ける（セルフレビューで直した tests は 2 つ目に入れ、commit メッセージにそう書く）。

## 検証方法

| # | 確認 | 期待 |
|---|---|---|
| 1 | `uv run --group dev --group web python tests/test_stream.py` | 最後の行が `通過 89 / 失敗 0`（012 のマージ後は `docs/development.md:37` の `test_stream` の数と同じで、失敗 0） |
| 2 | 変異 (a)(a2)(a3) で同じコマンド | 直す前（c4c378a の tests）: (a2) は `ValueError: substring not found`、(a)(a3) は `AssertionError`。直したあと: (a) は通る（89 / 0。空行は退行ではない）。(a2)(a3) は `ValueError` ではなく「Kafbat UI を起こす Web の restart は stream の apply より後」の `AssertionError`、count で `通過 88 / 失敗 1` |
| 3 | 変異 (b) | 直す前は `ValueError`。直したあとは同じ `check` の `AssertionError`、count で `通過 88 / 失敗 1` |
| 4 | 変異 (c) | count で `通過 87 / 失敗 2`（既存の `_wunit` の `check` と新しい `_krendered` の `check`） |
| 5 | 変異 (o1)〜(o7) | (o1)〜(o5)・(o7) は「Kafbat UI を起こす Web の restart …」で落ちる（count で失敗 1）。(o6) は通る。c4c378a の tests と同じ（(o1)〜(o5)・(o7) を落とし (o6) を通す） |
| 6 | `bash ops/check.sh` | 最後の行が `すべて通過`、rc=0。`test_stream` の数が `docs/development.md:37` と同じ |
| 7 | `grep -c 'Kafbat UI' docs/troubleshooting.md` と `grep -n '^### Kafbat UI' docs/troubleshooting.md` と `grep -n 'Kafbat UI のポートフォワード' docs/troubleshooting.md` | 小節が 1 つあり、表の行は案内の 1 行だけ |
| 8 | `grep -n 'admin-password' docs/deploy.md docs/troubleshooting.md docs/pipeline.md` と `grep -n 'unmask' docs/troubleshooting.md docs/pipeline.md` | 「terraform だけで上げると無い」の文が deploy.md の :26 と :276、troubleshooting の 75 の項目、pipeline.md:156 にある。`unmask` はどれも `--runtime` 付き |
| 9 | `sed -n '506p' ops/up.sh` と `git diff --stat c4c378a -- ops/up.sh` | コメントに Kafbat UI が入る。`ops/up.sh` は 1 行の変更だけ |

AWS では確かめない（docs・コメント・tests だけ）。

## 未確定事項とリスク

- 初回の 8-3 からポートフォワードまでに Kafbat UI が上がるまでの時間は AWS では未計測。docs には「1〜2 分の見込み（AWS では未計測）」と、手元の Docker の 7 秒（pull 済み）を書き、数字は AWS 検証で取れたら直す。イメージの大きさ（`docker images` の 637MB は展開後で pull の量ではない）は docs に書かない。
- `daemon-reload` / `enable --now` が落ちたときに `systemctl status` が `could not be found` と `disabled` のどちらになるかは、落ちた場所で変わる。docs には両方を書き、「どちらになるかは未確認」とする。
- コンテナが 75 で終わる場面は知られていない。docs に前提として書くだけで、`RestartPreventExitStatus` は変えない。
- `unmask --runtime` は systemd のソースで確かめただけで、AL2023 の systemd では打っていない。
- 「`enable --now` の enable は通り start だけ落ちた」形（`systemctl status` が `enabled` で `failed`）は、ほかの項目（75 / 69 / docker）の行で分かるので小節の項目にしない。
- 8-3 は `if` の中の `run_on_instance` 行の有無と `fi` の数を見るだけで、7-5 のように行を包む変異（関数で包むなど）は落とせない。c4c378a の tests も同じで、このサイクルでは広げない（次の候補）。
- `docs/troubleshooting.md` の表の行を小節に移すと、`tests/` に表の行の文言を見る検査があれば落ちる。実装の最初に `grep -rn 'Kafbat UI のポートフォワード\|mask --runtime' tests/` で確かめ、あれば小節の文言に合わせる。
- 012（`feat/collectors-scram-syslog-goflow`）が `docs/deploy.md` / `docs/troubleshooting.md` / `ops/up.sh` / `tests/test_stream.py` / `docs/development.md` / `docs/pipeline.md` を触っている。PM の指示で、エンジニア2 が `docs/cycle-006-design`（5be0288。012 のマージ後）を `fix/kafbat-ui-nits` へマージし、両方の中身を残して衝突を解く。マージ後に 8-3 / 7-5 の節がこの検査に合うかと、変異 (a)〜(c)・(o1)〜(o7) を取り直し、`test_stream` の数を `docs/development.md:37` に合わせる。
