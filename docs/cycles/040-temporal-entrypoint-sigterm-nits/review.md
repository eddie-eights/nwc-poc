# レビュー: temporal-server の entrypoint を起動中の SIGTERM で止められるようにし、039 の Nit を消化する（040）

## Round 1

- 対象: `5693378`（main `865dfcc` から）。cold reviewer: 依頼した（1 回目 / 2 回。opus / effort xhigh）。PM の確認: fable-5-1 / effort high。

### cold reviewer（review-r01.md）

# temporal-server の entrypoint を起動中の SIGTERM で止められるようにし、039 の Nit を消化する（040）コールドレビュー Round 1

対象: ブランチ `feat/040-temporal-entrypoint-sigterm-nits`（HEAD `5693378`）と `main`（`865dfcc`）の差分。前のラウンドは無いので、持ち越しの Must fix はありません。

## サマリ

- 結論: Must 0 / Should 1 / Nit 8。
- design.md の方針 1〜9 は実装に入っています。ずれ（`${step:-0}`、`_ts_ep_tls` の戻り値が 4 つ、コメントの位置、振る舞いの検査の追加）は build.md に理由付きで書いてあり、妥当です。
- Should は 1 件です。このサイクルの中心の性質「update-schema の途中で切らない」（temporal.md:64）は、`sql_tool` を前景で呼んでいることだけで成り立っています。それを守るテストがありません。
- 自分で走らせた検証:
  - `uv run --group dev --group web python tests/test_workflow.py`: 末尾「通過 378 / 失敗 0」、rc=0。
  - `./ops/check.sh`: 末尾「通過 378 / 失敗 0」「すべて通過」、rc=0。
  - `sh -n docker/images/temporal-server/entrypoint.sh` と `sh -n docker/images/temporal-server/namespace.sh`: どちらも rc=0。
  - `git status`: 未コミットの変更は 0 件。
- Docker の確認は別タグ `nwc-temporal-server:cold-040` で build して行いました。終わったあと `docker rmi` でイメージを消し、`cold040` の名前のコンテナとイメージが残っていないことも確かめています。確認した中身は次のとおりです。
  - イメージの `/bin/sh` と `/usr/bin/tr` は busybox（Alpine 3.24.1）です。`tr '[:upper:]' '[:lower:]'` は `True` / `TRUE` / `tRuE` をどれも `true` にします。
  - 公式の `/etc/temporal/entrypoint.sh` の末尾は `exec temporal-server start` です。
  - PID 1 の sh で `unset` をしても、`/proc/1/environ` には変数が残ります（unset のあとも件数 1）。
  - `POSTGRES_SEEDS` を届かない宛先にし、起動 3 秒後に `docker stop -t 60` を送りました。7.0 秒で止まり exit 143。ログは「SIGTERM を受けたので初期化を止める（手順 1 のあと…」の 1 行だけです。build.md の検証 5（このブランチ 7.1 秒 / 143、main 60.1 秒 / 137）と合います。
  - `SQL_HOST_VERIFICATION=TRUE` で `SQL_CA` が無いとき: 即時失敗のログを出して rc=1。
  - `namespace-rds.sh` の引数が 2 つのとき: 「line 13: 3: … の 3 つ目が無い」で rc=2。引数が 0 のとき: 9 行目のメッセージで rc=2。
  - PID 1 の sh に trap を置き、前景の子が走っている最中に SIGTERM を送りました。
    - サブシェル `( exec sleep 4 )` の最中に送ったとき: 3.1 秒で exit 143。サブシェルの後ろの `echo sub-done` は出ません。
    - コマンド置換 `x=$(sleep 4 …)` の最中に送ったとき: 2.1 秒で exit 143。置換が返ってから trap が走りました。
    - どちらも、走っているコマンドは途中で切られず、返ってから trap が走ることを示しています。

### 見た観点 / 見ていない観点

- 見た観点
  - 設計どおりか: design.md の方針 1〜9、変更対象ファイル、検証方法と、差分を突き合わせた。
  - 正しさと実行時のバグ:
    - trap の発火のしかた（前景の子が返るまで待つこと）を Docker で確かめた。
    - `set -u` の下での `${step:-0}`、`exec` で trap が既定に戻ること、`tr` の小文字化、`${N:?}` の rc も見た。
  - セキュリティ: master のパスワードが `/proc/1/environ` に残る範囲を Docker で確かめた。ログとコマンドラインにパスワードが出ないことも見た。
  - データ損失: 停止で update-schema が途中で切られないか。
  - API の互換: ecs.tf のタスク定義で env / secrets / healthCheck が変わっていないこと。`stopTimeout` の置き場所（コンテナ定義）。
  - 型: テストの Python のコード（`next` / `find` / f-string）。
  - テストの不足。
- 見ていない観点
  - AWS / Fargate での実際の挙動: `stopTimeout = 120` が受け付けられるか、`aws ecs stop-task` で ExitCode 143 になるか。AWS には触っていません。
  - RDS の TLS で `SQL_HOST_VERIFICATION=true` にした経路。
  - サーバーの YAML が `yes` / `on` / `1` を true と見るかどうか。
  - postgres:18 を相手にした正常な起動の経路。自分では走らせ直しておらず、build.md の検証 6（1.2 秒 / ExitCode 0、PID 1 が tini）に頼っています。
  - `exec /sbin/tini` の直前の、ごく短い窓に届いた SIGTERM。この窓では SIGTERM を取りこぼし、120 秒待たされる可能性があります。窓は数マイクロ秒なので、無視できると判断しました。

## Must fix

None

## Should fix

- [テストの不足] **「update-schema の途中で切らない」を守るテストが無い。** 該当箇所は `docker/images/temporal-server/entrypoint.sh:113` と `tests/test_workflow.py:604` / `:665-686`。
  - 何が性質を支えているか:
    - temporal.md:64 と ecs.tf:79 は「update-schema の途中で切らない」「走っている手順が返るまで待って」と書いています。
    - この性質は、`sql_tool "$db" update-schema …` を前景で呼んでいることだけで成り立っています。trap は前景の子が返ってから走るためです（上の Docker の確認で、サブシェルもコマンド置換も切られなかった）。
  - 壊れる筋書き:
    - 誰かが 113 行を `sql_tool … & wait $!` に変えたとします（たとえば、ログを並行で出すため）。
    - `wait` は trap で割り込まれるので、SIGTERM が来ると sh は即 exit 143 で抜けます。
    - コンテナの PID 1 が抜けるので、temporal-sql-tool はスキーマの移行の途中で殺されます。
  - いまのテストでは止まらない理由:
    - `test_workflow.py:604` は `'update-schema -d "$SCHEMA_DIR/$dir/versioned"' in _ts_ep` という部分文字列の一致だけを見ています。
    - `:665-674` は trap と `step=` の並びしか見ていません。
    - `:675-686` は前景の子が無い状態で `kill -TERM $$` を送っています。
    - このため上の書き換えをしても、どの検査も通ります。build.md の自己レビュー Should 1 も、同じ書き換えで rc=0 になることを再現しています。
  - 直し方の例: コメントを除いた行で、末尾が `&` の行は `namespace-rds.sh` の 1 行だけであること、`wait` の行が無いことを静的に検査する。
  - Should の理由: いまのコードは正しいので Must ではありません。ただ、このサイクルが約束した中心の性質（データ損失を防ぐもの）を守るものが無く、壊れても誰も気づかないので Should にしました。

## Nit

- [設計どおりか] **「手順」の言い回しと番号が、実際の動きとずれている。**
  - 「手順 N のあと」: `entrypoint.sh:36` の trap は「手順 ${step:-0} のあと」と出します。ところが `step=` は手順の先頭に置かれています（62 / 72 / 104 / 118 行）。手順 1 の DB 待ちの最中に止めても「手順 1 のあと」と出ます（上の Docker の確認で出た 1 行がまさにこれです）。
  - 待つ単位: `ecs.tf:79` は「走っている手順が返るまで待って」、`temporal.md:64` は「走っている手順の区切りで」と書いています。trap が実際に待つのは前景の 1 コマンドだけで、手順全体ではありません。たとえば temporal の DB の update-schema のあと、visibility の DB の update-schema の前に止めると、手順 3 の途中で抜けます。
  - 番号の振り方が 2 通り: 先頭のコメント（5〜9 行）は 1 = ロールと DB … 4 = exec です。`step` の値と本文のコメントは 1 = DB 待ち … 4 = namespace です。`temporal.md:63-64` の「手順 1〜3」がどちらの番号かも書いてありません。
  - Nit の理由: 動作には影響しません。ログとコメントを読み違える余地があるだけです（build.md の自己レビュー Nit 3 / 4 と同じ件）。
- [設計どおりか] **見える経路の数と、パスワードが残る範囲の書き方が食い違っている。**
  - 経路の数: `docs/architecture/resources/temporal.md:61` は「見える経路が 2 つ残る」のままです。ところが同じ節の `:63` で、起動中の `/proc/1/environ` という 3 つ目の経路を足しています。
  - 範囲: `docs/cycles/040-temporal-entrypoint-sigterm-nits/design.md:90`（未確定事項 2）と `docs/cycles/QUEUE.md:142` は「手順 1〜2 の間」と書いています。上の Docker の確認で、`unset` をしても `/proc/1/environ` は変わらないことを確かめました。残る範囲は `exec tini` までで、正しいのは temporal.md:63 の「手順 1〜3（exec まで）」のほうです。
  - Nit の理由: 利用者が読む temporal.md は正しく、食い違いは記録の側だけです。
- [正しさ] **TLS を使わない設定でも、即時失敗の検査が走る。** 該当箇所は `entrypoint.sh:44`。
  - `SQL_TLS_ENABLED=false`、`SQL_HOST_VERIFICATION=true`、`SQL_CA` 無しの組み合わせでも rc=1 で起動を止めます（イメージで確認）。
  - サーバーは TLS が無効ならホスト名の検証を見ないので、この組み合わせを止めるのは誤検知です。
  - design どおりの実装で、いまの値の出どころは ecs.tf だけなので、この組み合わせには届きません（自己レビュー Nit 5 と同じ件）。
- [保守性] **`SQL_CA` の空チェックが重複している。** 該当箇所は `entrypoint.sh:50`。
  - verify-full の枝の `if [ -n "${SQL_CA:-}" ]; then export PGSSLROOTCERT=…` は、44 行の即時失敗の検査の後では常に真です。
  - 残しておくと「CA が無くても verify-full で進む道がある」と読めてしまいます。
- [設計どおりか] **design の方針 4 でやめた言い回しが 2 か所に残っている。**
  - 方針 4 は「道連れにしない / 落とさない」をやめ、namespace.sh:5 の書き方を直しました。
  - `entrypoint.sh:116` の「失敗しても本体は落とさない」と、`tests/test_workflow.py:716` の検査名の「temporal-server を道連れにしない」が、まだ残っています。
- [テストの不足] **テストの目印と引数の検査が、名前の変更や片方の壊れ方を見逃す。**
  - `tests/test_workflow.py:638`: `log_line = next(...)` に既定値がありません。`log() {` の名前を変えると、検査が失敗と出る代わりに StopIteration で落ちます（同じ関数の `find` の目印には、ガードが入っているのと対照的です）。
  - `:672`: `_ts_first(r"log\(\) \{") < _ts_trap[0]` は、`log()` が無いと `_ts_first` が -1 を返すので常に真になります。
  - `:727-729`: 引数 2 つの検査は stderr に "retention" があるかしか見ていません。どの使い方のメッセージも `<retention>` を含むので、`${3:?}` 以外（`${1:?}` / `${2:?}`）が壊れても区別できません（自己レビュー Nit 2 / 8 と同じ件）。
- [テストの不足] **`${step:-0}` を `$step` に戻しても検査で止まらない。**
  - `_ts_trap_run`（`tests/test_workflow.py:675-686`）は `step=2` を設定してから SIGTERM を送ります。そのため `step` が未設定のときの経路を通りません。
  - `$step` に戻すと、`set -u` の下で手順 1 より前に来た SIGTERM が trap の中で落ちます。ただしその場合でも、非 0 で抜けることに変わりはありません（自己レビュー Nit 1 と同じ件）。
- [プロセス] **design の方針 9 の AWS での確認項目が、まだ QUEUE の行に入っていない。**
  - 方針 9（`design.md:44`）は、`docs/cycles/QUEUE.md:145`「残った修正をまとめて AWS で動作確認…」の行に、次の項目を PM が足すと決めています。
    - `stopTimeout: 120` があること。
    - `aws ecs stop-task` で ExitCode 143 と「SIGTERM を受けたので初期化を止める」のログが出ること。
  - このブランチの QUEUE.md:145 には `stopTimeout` も `143` もありません（`grep -c` で 0）。
  - PM の仕事なので Nit にしました。

## 良かった点

- design の方針はすべて実装に入っています。ずれは build.md に理由付きで書いてあります。
  - 特に `${step:-0}` は、`set -u` の下で trap の中が落ちるのを避ける正しい判断です。
- design に無い振る舞いの検査（`_ts_trap_run`）を足し、trap が rc 143 で次の行へ進まないことを実際に sh で動かして見ています。
- ecs.tf は、コメントと `stopTimeout = 120` を足しただけです。env / secrets / healthCheck は変えておらず、タスク定義の互換を壊していません。
- main と比べた動的な検証（このブランチ 7.1 秒 / 143、main 60.1 秒 / 137）で、直ったことを数字で示しています。自分の再現（7.0 秒 / 143）とも合いました。
- 確認用の Docker のイメージとコンテナを片付けています。ログにもコマンドラインにも、パスワードは出ていません。

## ユーザーへの質問

None

### PM の確認（Round 1）

- 先に `origin/main`（`45e7f5f`。041 まで）を取り込んだ（`e3aa3d0`。QUEUE.md のコンフリクトは main の行に `→ 040-…` を付け直して解消）。
- Should fix 1（update-schema を前景で呼ぶことを守るテストが無い）: 再現した。`grep -nE '&\s*$|\bwait\b' entrypoint.sh` は namespace-rds.sh の 1 行だけで、これを守る check が無かった。`tests/test_workflow.py` に「末尾 `&` の行は namespace-rds.sh の 1 行だけ、`wait` の行は無い」の静的な check を足した。113 行目を `… & wait $!` に変えると `AssertionError: entrypoint が背景（末尾 &）で起こすのは namespace-rds.sh の 1 行だけで…` で落ちることを確かめた（戻して 通過 381 / 失敗 0）。
- Nit 1（手順の言い回しと番号）: 読んだだけ。直さない（ログとコメントの読み違えの余地。QUEUE には足さない ── 根本対策の一回きりの初期化タスクで手順そのものが変わる）。
- Nit 2（見える経路の数と残る範囲）: 再現した（temporal.md:61「2 つ残る」と :63 の 3 つ目、design.md:90 と QUEUE:142「手順 1〜2 の間」）。temporal.md:61 を「起動が終わったあとも 2 つ（起動中の 3 つ目は下）」に、design.md:90 と QUEUE:142 を「`exec tini` までの手順 1〜3。`unset` では消えない」に直した。
- Nit 3（TLS 無効でも fail fast が走る）、Nit 4（`SQL_CA` の空チェックの重複）: 読んだだけ。直さない（値の出どころは ecs.tf だけで届かない。design どおり）。
- Nit 5（方針 4 でやめた言い回し）: entrypoint.sh:116 と test_workflow.py:716 の文言を namespace.sh:5 に揃えた。
- Nit 6（テストの目印）: `log_line = next(…, None)` にしてガードを足し（`log() {` を `lg() {` に変えると `AssertionError: entrypoint の log() { の行がある` で止まる。StopIteration ではない）、`0 <= _ts_first(r"log\(\) \{") < _ts_trap[0]` にし、引数 2 つの check を「の 3 つ目が無い」があり 1 つ目・2 つ目が無いことに変えた。
- Nit 7（`${step:-0}`）: `_ts_trap_run(step="")` で未設定の経路を足した。`$step` に戻すと `AssertionError: entrypoint の trap: step が未設定（手順 1 より前）でも…` で止まることを確かめた。
- Nit 8（QUEUE 145 の 040 の項目）: `stopTimeout: 120`、起動中の `stop-task` で ExitCode 143 とログ、実イメージの `tr` を足した。
- `./ops/check.sh`: 末尾「すべて通過」（test_workflow.py 通過 381 / 失敗 0）。
- cold reviewer の 2 回目: 呼ばない。このラウンドで変わった実装ファイルは entrypoint.sh のコメント 1 行だけで、あとはテストと docs（テストだけのラウンドに外部レビューは掛けない）。
- 結論: Must 0 / Should 1（直した）/ Nit 8（5 件直した、3 件は記録のみ）。サイクル完了。QUEUE の 3 行を完了にした。
