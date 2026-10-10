# 042 のレビュー

## Round 1（2026-10-10。PM(fable-5-1)。cold reviewer は呼んでいない）

- 対象: Round 1 の実装 `b1f91f7`（18 ファイル、+1352 / -264）。レビューは `build.md` Round 1 の `### セルフレビュー`（実装モデル opus-5.5 + 反対弁護人）に依った。
- cold reviewer を呼ばなかった理由: セルフレビューで Should 4 件（2 イメージを戻すと待ちが終わらない / 3 ループが healthCheck より長い / 4 psql の失敗が「まだ無い」に見える / 5 init の同時実行でスキーマが壊れる）が既に出ていて、いずれも correctness / runtime / data loss なので自動で直す。直す前のコードに 2 回しか無い cold review の 1 回を使わない。cold review は Round 2（直した後）と完了判定の直前で呼ぶ。
- Must 1（エンジニアが直した。読んで確かめた）: タスクのロールの `Parameters`（`iam.tf:141-145`）が `/<prefix>/*` を許すので、env から外しても ECS Exec から `get-parameter` で `/<prefix>/nautobot/db-password` を引けた。`DenyNautobotParameters` を足した。`app/temporal/awsio.py` の ssm の呼び出しは `send_command` / `get_command_invocation` だけ、`IaC/terraform/aws-managed/workflow/` に `/nautobot/` の SSM を読む経路は無い（`grep -rn 'nautobot/'` で確認）ので、worker は壊れない。AWS で Deny が効くことは 147 で見る。
- Should 2〜5: design.md の設計方針 2〜3 と検証 1 に取り込み（`40bedb1`。経緯は design-log.md の「Round 1 の補正」）、エンジニアに Round 2 として振った。
- Nit 6〜9: 直さない。最終報告に載せる。
- 設計に無いファイルの変更: `docs/architecture/resources/nautobot.md`（master のパスワードを読むタスクの記述）と `tests/test_oss_ops.py`（`ops/oss/up.sh` の 8 の順序）。読んで妥当。
- 見た観点: design 整合性（build.md の記録とファイルの一覧）/ security（Must 1 と SSM の経路）。見ていない観点: correctness / runtime / data loss / API compatibility / type safety / missing tests は Round 2 の cold review で見る。
- 事故: エンジニアが手元の docker のイメージ 126 本を消した（原因と再発防止は build.md の末尾）。復旧は「必要になったときでいい」（2026-10-10 のユーザー決定。まとめて pull / build し直さない。手元の検証でイメージが要るときに、そのイメージだけ pull / build する）。
# temporal の初期化を一回きりのタスクに分け、サーバーのタスク定義から master のパスワードを外す（042）のコールドレビュー Round 1

- 対象: `git diff fa99bdf..f512cfc`（22 files changed, 1939 insertions(+), 265 deletions(-)）
- レビュー担当: cold reviewer（読み取りだけ。既存のファイルは変えていない）

## サマリ

- Must fix は 0 件。
  - サーバーのタスク定義から `NAUTOBOT_DB_USER` / `NAUTOBOT_DB_PASSWORD` が env からも secrets からも消えている。
  - init のタスク定義、`run_temporal_init`、待ちの判定（DB の版 ≥ イメージの版）、startPeriod 300 は、design.md の設計方針 1〜6 と検証 1 のとおり。
- Should fix は 2 件。
  - Round 2 で書き換えた待ちの判定は、イメージの中の busybox でまだ一度も動いていない。
  - `run-task` の `failures` の理由が捨てられている。
- Nit は 4 件。design の表の食い違い、範囲外の既存の SSM の読み取り範囲、docs の表記 2 件。

### 見た観点 / 見ていない観点

- 見た観点
  - design 整合性
    - design.md の設計方針 1〜6、変更対象ファイルの表、検証 1 の各項目と、差分を 1 つずつ突き合わせた。
    - build.md の「設計からの逸脱」と照らした。
  - correctness
    - `entrypoint.sh` の `schema_want` / `schema_read` / `version_ge` / 30 回のループを読んだ。
    - `run_temporal_init` の出力の切り分け（`${out%%$'\t'*}`、`${out##*$'\t'}`、`${prev//$'\t'/ }`、`read -r status code stop_code reason`）は、`/bin/bash` 3.2.57 で自分で動かした。`arn…\t0`、`None\t1`、`STOPPED\tNone\tTaskFailedToStart\t…` のどれも、期待どおりに分かれた。
    - family の導出（`${taskdef##*/}` → `${family%:*}`）を確かめた。
    - OSS 版の `IaC/terraform/oss/workflow/*.tf` が aws-managed へのシンボリックリンクで、outputs が両方に入ることを確かめた。
  - security
    - サーバー・init の env と secrets を見た。
    - `iam.tf` の `DenyNautobotParameters` は ARN の形が Allow（`iam.tf:144`）と同じで、Allow に当たる名前を拒むことを確かめた。
    - worker（`app/temporal/awsio.py`）の SSM は `send_command` / `get_command_invocation` だけで、Deny で壊れない。
    - psql の stderr の 1 行目をログに出す経路にパスワードが載らないことを確かめた（`PGPASSWORD` は env、`\getenv`、`SQL_PASSWORD` は変わっていない）。
    - workflow の root の他のロールの SSM の範囲も見た（Nit 2）。
  - runtime bugs
    - trap（`common.sh` の `exit 143`）、待ちの秒数と ECS の healthCheck（startPeriod 300 + 6 回 × 10 秒）の関係を見た。
    - init とサーバーが同時に起きる順序と、apply だけした環境を見た。
  - data loss
    - `init.sh` に DROP / drop-schema が無いこと、`setup-schema -v 0.0` が `schema_version` が無いときだけ走ることを見た。
    - 前の init が走っている間は次の init を起こさない（`list-tasks` → `wait tasks-stopped`）ことを見た。
  - API compatibility
    - workflow の outputs は 4 つ足しただけで、消した output は無い。
    - Dockerfile の ENTRYPOINT は変えていない。
  - missing tests
    - `tests/test_workflow.py` に、検証 1 の各項目と Round 2 の check（version_ge の 15 通り、psql の失敗、前の init が 2 つ）があることを見た。
  - 自分で走らせたテスト（`uv run --group dev --group web python tests/<file>`）
    - `tests/test_workflow.py`: 「通過 435 / 失敗 0」
    - `tests/test_oss_ops.py`: 「通過 207 / 失敗 0」
    - 走らせたあとの `git status --short` は空だった。
- 見ていない観点
  - `./ops/check.sh` は走らせていない。
    - 中の `terraform init` がネットワークを使い、追跡している `.terraform.lock.hcl` を変えうるため、上の 2 本だけを直接走らせた。
    - そのため `terraform fmt` / `validate` と、他のテストファイルの結果は自分では確かめていない。
  - docker は使っていない（PM の補足のとおり pull しない）。Round 2 の `entrypoint.sh` を busybox の sh / sed で動かしたかどうかは Should 1。
  - AWS（design の検証 6）はこのサイクルではやらないので、見ていない。次の 3 つは未確認。
    - `DenyNautobotParameters` が ECS Exec の `get-parameter` を AccessDenied にすること。
    - Fargate でのイメージの取得とスキーマにかかる時間。
    - `ops/up.sh` の 8-5 が init の待ちを含めて通ること。
  - init のタスクが走っている間に `ops/down.sh` を打ったときの、クラスターの削除の振る舞いは確かめていない（init は数分で自分で止まるので、読んだ範囲では問題にならない）。
  - type safety は、シェルと HCL なので型の検査器は無い。HCL の型は読んだだけ（`concat` に渡すリストの要素の形が揃っていること）。

## Must fix

None

## Should fix

- [runtime bugs / missing tests] Round 2 で書き換えた `entrypoint.sh` の待ちの判定（`entrypoint.sh:32-54`）は、イメージの中の busybox ash / busybox sed でまだ一度も動いていない。
  - 分類の理由: 壊れていると示せたわけではないので Must ではない。ただし壊れていれば全部のデプロイでサーバーが起きなくなり、確かめるのは安いので Should。
  - 根拠
    - build.md の Round 2 の「2〜5. 手元の docker」は未実行。R2-3 も「busybox で確かめられていない」として残っている。
    - 手元で取れたのは macOS の `/bin/sh` と `/bin/dash` だけ。
    - Round 1 の docker の検証 2〜5 は Round 1 のコード（`2>/dev/null` で版を読み、揃ったかを比べる形）に対するもの。
    - `git diff b1f91f7 33f3e21 --stat -- docker/` は `entrypoint.sh | 37 ++++++++++++++++++++---------` で、次の部分は Round 2 で新しく入った。
      - `2>&1` で取ってから `sed -n '/^[0-9][0-9]*\.[0-9][0-9]*$/p'` と `sed -e '/…/d' -e '/^$/d'` で分ける部分
      - `version_ge` の `case` と `-gt` / `-eq` / `-ge`
    - なお `schema_want` の `sort -t. -k1,1n -k2,2n` は Round 1 にもあり、docker の検証 3 で通っている。
  - 壊れる入力と状態: busybox の sed が上の正規表現を macOS の sed と違うように扱うと、`schema_have` が空のまま 30 回待って `exit 1` になる。`case` の扱いが違っても、版が揃っているのに `version_ge` が偽になり、同じく `exit 1` になる。そうなると ECS がタスクを作り直し続け、`ops/up.sh` の services-stable は 20 分で警告になる。
  - 直し方の案: 2026-10-10 のユーザー決定（必要になったときに、そのイメージだけ pull / build する）の範囲で、temporal-server のイメージだけを build する。build.md の `ep-sh.sh` と同じ偽の psql を、イメージの中の `/bin/sh` で動かす。あるいは `postgres:18` を相手に検証 3（init の前に待つ → init のあとに先へ進む）と、認証の失敗の行を 1 回ずつ取る。
- [correctness / 運用] `run-task` の返りに `failures` があると、die のメッセージは件数しか出さず、理由が捨てられる（`ops/up-common.sh:384-390`）。
  - 分類の理由: design の設計方針 2 が求めているのは「`failures` が空でないときも `die`」だけなので Must ではない。ただしこの経路では失敗の手がかりがこれしか無いので Should。
  - 根拠
    - クエリは `--query '[tasks[0].taskArn, length(failures)]' --output text`。die は `Temporal の初期化のタスクを起こせない（run-task の返り: ${out}）` なので、出るのは `None	1` だけになる（自分で bash 3.2 で `printf 'None\t1'` を切り分けて確かめた）。
    - `run-task` は `failures` があっても終了コード 0 で返るので、「上のエラー」も出ない。
    - タスクが作られないので、あとから `describe-tasks` で理由を引くこともできない。
  - 壊れる入力と状態: Fargate のキャパシティ不足や ENI の上限など、`failures[].reason` で返る失敗が起きると、打ち直す人は理由を知る手段が無い。もう一度手で `run-task` を打つしかない。
  - 直し方の案: クエリに `failures[0].[arn, reason, detail]` を足して die に載せる。`tests/test_oss_ops.py` か `tests/test_workflow.py` の偽の aws の `failures` の経路で、理由の文字列が出力に出ることを check する。

## Nit

- [design 整合性] design.md の変更対象ファイルの表と設計方針 1 が、`iam.tf` を「コメントだけ」のままにしている。
  - 分類の理由: 実装と検証 1 は正しく、食い違っているのは design.md の中だけなので Nit。
  - 根拠
    - `design.md:30` は「`iam.tf` の `execution_db_passwords` はそのまま。コメントだけ」、`design.md:53` の表は「`iam.tf` | コメントだけ」。
    - 一方、`design.md:86`（検証 1）は `DenyNautobotParameters` があることを求めている。
    - 実装は `iam.tf:149-154` に Deny を足し、build.md:85 に逸脱として書いている。
  - 直し方の案: 表の行を「コメントと `DenyNautobotParameters`（セルフレビューの Must 1）」にする。
- [security（範囲外・既存）] 同じ workflow の root で、他のロールは SSM の `/<prefix>/*` を読めるままで、master のパスワード `/<prefix>/nautobot/db-password` も入る。
  - 対象のロール
    - tools の Lambda のロール: `gateway.tf:84-88` の `Parameters`
    - チャットの Runtime のロールと Web の EC2 のロール: `proposals.tf:20-25` の `reader_access`。`locals.tf:121` の `reader_role_names`
  - 分類の理由: このサイクルの差分で入ったものでも design の範囲でもないので Nit。ただ、042 の目的（master のパスワードを読める経路を減らす）と同じ種類の穴なので、QUEUE の候補として挙げる。
  - 根拠: この 3 か所の resources は `parameter${local.param_prefix}/*` で、Deny は無い。042 の Deny が付くのは temporal のタスクのロール（`aws_iam_role_policy.task`）だけ。
  - 未確認: これらのロールが実際に `/<prefix>/nautobot/` の下のどの名前を読む必要があるかは確かめていない。URL や API の token を読むなら、`nautobot/*` を丸ごと Deny すると壊れる。
  - 直し方の案: QUEUE に「`/<prefix>/nautobot/db-password` を、temporal 以外の workflow のロールでも Deny する（または読む名前に絞る）」を足す。
- [docs] `docs/architecture/resources/temporal.md:18`（シークレットの行）の末尾が `… と `DenyNautobotParameters` ||` で、空のセルが 1 つ多い。
  - 分類の理由: GFM は見出しより多いセルを捨てるので表示は崩れず、表記の誤りだけなので Nit。
- [docs] `docs/architecture/resources/temporal.md:120` の「手で `terraform apply` だけした環境 | init のタスクが走らないので、サーバーは 5 分（30 回）待って exit 1 を繰り返す」は、DB のスキーマがイメージの版より古いときだけ当てはまる。
  - 分類の理由: 誤読の余地があるだけで、手順を誤らせる書き方ではないので Nit。
  - 根拠: `entrypoint.sh:54` は `version_ge` が両方真なら 1 回目で抜ける。そのため init が同じイメージで一度走った環境なら、apply だけでもサーバーは起きる。`docs/workflow.md:158` の「手で `terraform apply` だけした環境では init が走らないので、`ops/up.sh` を打ち直す」も同じ書き方。
  - 直し方の案: 「新しい環境か、スキーマの版が上がるイメージに替えたとき」と条件を付ける。

## 良かった点

- サーバーのタスク定義の temporal のコンテナで、env と secrets のどちらにも `NAUTOBOT_DB_` で始まる名前が無いことを check にした。ECS Exec / healthCheck / `/proc/1/environ` の経路を、タスク定義の段階で断っている。
- design が見落としていたタスクのロールの SSM の経路（`Parameters` が `/<prefix>/*`）を、セルフレビューで見つけて Deny で塞いだ。Deny の ARN は state が無いと空になる `local.nautobot_db_password_arn` を避け、Allow と同じ `param_prefix` から組んでいる（build.md:563）。
- 待ちの判定を「DB の版 ≥ イメージの版」にし、major / minor を数で比べている。イメージを戻した環境でも、Temporal 本体と同じく起きる。`version_ge` は形の違う入力（3 つ組、`v` 付き、空）を `[` のエラーなしで偽にし、その 15 通りを check している。
- psql の失敗の 1 行目を待ちの行に出す。init の前は `password authentication failed` が出るのが正常なことを、docs とコメントに書いている。運用で SSM のパスワードを作り直しに行く誤解を防いでいる。
- `run_temporal_init` は、前の init が走っていれば止まるのを待ってから起こす。理由（新しい DB で `setup-schema -v 0.0` が版を戻す）もコメントに残している。`exitCode` が null（text では `None`）のときも、0 以外として die する。
- DB 系の env を `local.temporal_db_env` に 1 つにまとめ、サーバーと init で二重に書いていない。
- check を 1 本ずつ壊して落ちることを確かめている（Round 1 の 37 通りと、Round 2 の mutate2）。

## ユーザーへの質問

None

### Round 2 の PM の確認（2026-10-11。PM(fable-5-1)。cold reviewer（opus、effort xhigh）を 1 回目として呼んだ。上の「コールドレビュー Round 1」がその結果）

- 対象: Round 2 の実装 `33f3e21`（HEAD `f512cfc`。22 ファイル、+1939 / -265）。cold reviewer の返り: 新規ファイル 1 本だけ（`git status --short --ignored` で `!! review-r01.md`）。
- Must fix: 0。
- Should 1（busybox で未確認）: **PM が再現して解消。** `alpine:3.20`（busybox 1.36.1。`temporalio/server` の base も alpine）を pull し、`entrypoint.sh:29-45` の `schema_want` / `schema_read` / `version_ge` を写したまま、偽の psql（認証失敗 / DB 無し / 表無し / WARNING + 版 / 版 / 古い版）で動かした。結果（抜粋。全文は scratchpad の `bb/t.sh`）:
  ```
  shell: /bin/busybox BusyBox v1.36.1 (2025-11-23 14:32:18 UTC) multi-call binary.
  want: 1.19 / 1.14   (expect 1.19 / 1.14)
  version_ge [1.19] [1.19] -> true / [1.20] [1.19] -> true / [2.0] [1.19] -> true / [10.2] [9.9] -> true
  version_ge [1.9] [1.19] -> false / [1.18] [1.19] -> false / [] [1.19] -> false / [1.19] [] -> false / [x] [1.19] -> false / [1.19.1] [1.19] -> false / [.5] [1.19] -> false
  mode=auth have=[] err=[psql: error: connection to server at "h" (1.2.3.4), port 5432 failed: FATAL:  password authentication failed for user "temporal"]
  mode=nodb have=[] err=[psql: error: ... FATAL:  database "temporal" does not exist]
  mode=notable have=[] err=[ERROR:  relation "schema_version" does not exist]
  mode=warn have=[1.19] err=[WARNING:  some warning]
  mode=ok have=[1.19] err=[]
  mode=old have=[1.2] err=[]
  t: 待つ（1/30。スキーマの版 temporal=無し/1.19 temporal_visibility=無し/1.14。temporal の psql: psql: error: ... password authentication failed for user "temporal"。temporal_visibility の psql: ...）
  t: スキーマは temporal=1.19 / temporal_visibility=1.19（イメージの版 1.19 / 1.14 以上）
  t: 上限: i=31 で exit 1 の分岐に入った（版 temporal=1.2/1.19）
  ```
  busybox の sed の正規表現と `case` は macOS / dash と同じに動く。pull した alpine は `docker rmi` で消した（`docker images -q | wc -l` → 0）。実イメージの build は 147 の AWS の確認で ECR に push するときに済む。
- Should 2（`run-task` の `failures` の理由が捨てられる）: **再現した**（`ops/up-common.sh:384-390` を読んだ。クエリは `length(failures)` だけで、die の文面は `run-task の返り: None\t1` になる。bash 3.2 で `out=$'None\t1'` を切り分けて確認）。correctness / runtime なので自動で直す。design の設計方針 2 と検証 1 に取り込み、Round 3 としてエンジニアに振る。
- Nit 1（design.md の `iam.tf` が「コメントだけ」）: design.md の設計方針 1 と表を直した（PM）。
- Nit 2（workflow の他のロールが `/<prefix>/*` を読める。範囲外・既存）: QUEUE に `- [ ]` で足した。
- Nit 3〜4（`temporal.md:18` の空セル、`temporal.md:120` / `workflow.md:158` の「apply だけ」の条件）: 直さない。最終報告に載せる。
- cold reviewer の 2 回目は Round 3 の後（完了判定の直前）に呼ぶ。
