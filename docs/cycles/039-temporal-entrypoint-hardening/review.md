# temporal-server の entrypoint の守りを締める（039）レビュー

## Round 1

cold reviewer: 依頼しない。`build.md` の `## Round 1` のセルフレビュー（opus-5.5 / high + 反対弁護人 opus / xhigh）で Must fix 1（設計方針 3 の `initProcessEnabled` は孤児しか回収せず、PID 1 が init になると `/proc/1/environ` にパスワードが戻る。docker `--init` で再現）が出て、`/cycle-design` へ差し戻した。再設計は `design-log.md` の `## Round 1`。確認は PM(fable-5-1)。2026-10-10。

## Round 2

レビューモデル: opus-5.5 / effort: xhigh（cold reviewer に依頼。再実装直後で `review.md` がまだ無い = 初回）。確認は PM(fable-5-1)。2026-10-10。
対象: `main...e1b3a9f`（Dockerfile / entrypoint.sh / namespace.sh / ecs.tf / tests/test_workflow.py / temporal.md）。

## サマリ

temporal-server の entrypoint の守りを締める（039）の Round 2 を、`main...HEAD`（HEAD `e1b3a9f`）の差分でレビューした。対象は 6 ファイル（`Dockerfile` +3/-1、`entrypoint.sh` +40/-29、`namespace.sh` 新規 35 行、`ecs.tf` は main と同一、`tests/test_workflow.py` +95/-6、`docs/architecture/resources/temporal.md` +5/-3）。

実装は design.md の設計方針 1〜5 と変更対象ファイルの表のとおりで、設計に無いものは混ざっていない。手元の docker で、PID 1 が tini、temporal-server の ppid が 1、ゾンビ無し、tini / temporal-server / namespace-rds.sh（とその子の sleep）の `/proc/<pid>/environ` に `NAUTOBOT_DB_PASSWORD` が無いこと、`docker stop` が正常に終わること（exit 0）を自分で確かめた。Must fix は無い。Should fix は 2 件。1 件はテストの穴（namespace を起こす行と `unset` の順序を見ていない）。もう 1 件は脅威の書き方の穴で、healthCheck のプロセスが master のパスワードを env に持ち、uid temporal から読める（手元の docker で再現した）。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - 設計方針 1・3・4・5 と変更対象ファイルの表を、6 ファイルの現物と 1 行ずつ突き合わせた。
  - `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf | wc -l` → `0`（検証 4）。
  - `grep` で `initProcessEnabled` / `linuxParameters` を探した。残っているのは、テストの否定の check、docs の説明、過去サイクルの記録、QUEUE の元の行だけ。
- **correctness / runtime**
  - `uv run --group dev --group web python tests/test_workflow.py` → `通過 369 / 失敗 0`。
  - `sh -n` → 両ファイルとも rc=0。イメージの中の busybox `sh -n` → `entrypoint-rds.sh` / `namespace-rds.sh` とも rc=0。
  - `docker build -t rv039-temporal-server:test docker/images/temporal-server/` → 成功。イメージの中の `entrypoint-rds.sh` / `namespace-rds.sh` が worktree と同じ中身であることを `diff` で確かめた。
  - イメージの中の環境: user temporal、`PATH` に `/sbin` がある、`command -v tini` → `/sbin/tini`、`tini version 0.19.0`、`/etc/temporal/namespace-rds.sh` は `-rwxr-xr-x`（検証 6）。
  - 公式の `/etc/temporal/entrypoint.sh` は最後が `exec temporal-server start` で、間に `sh` は挟まらない（未確定事項 1 は解消）。
  - `postgres:18` と自前のイメージを、036 の手順（`SQL_TLS_ENABLED=false`、`--init` 無し）で起こした。初回は `namespace: default を作った`、同じ DB で 2 つ目を起こすと `namespace: default がある`。
  - その後の `ps -o pid,ppid,stat,comm`: `1 0 S tini` と `39 1 S temporal-server` だけで、`Z` は無い。
  - `temporal operator namespace describe -n default` が通った。
  - `docker stop -t 30`: 1 秒・17 秒（相手のタスクが先に止まっていて membership の接続待ちがある場合）・3 秒（namespace-rds.sh が `sleep` の最中）で、どれも exit 0、ログは `All services are stopped.`。
- **security**
  - 起動中に `/proc/[0-9]*/environ` を回して見た。`unset` の前の PID 1（`entrypoint-rds.sh`）は `1`、`exec` のあとの tini は `0`。psql（ロール temporal）・`namespace-rds.sh`・その子の `sleep`・temporal-server はどれも `0`。`POSTGRES_PWD` は tini / temporal-server が `1`（設計どおり）。
  - 別に `--health-cmd` に ecs.tf と同じコマンドを付けて起こし、healthCheck のプロセスの env を見た（Should fix 2）。
- **data loss**
  - `namespace.sh` が打つのは `describe` と `create` だけ。entrypoint の手順 1〜3 は Round 1 / 036 から変わっていない（`sql_tool` のサブシェル化は Round 1）。消す操作は足されていない。
- **API compatibility**
  - ENTRYPOINT（`/etc/temporal/entrypoint-rds.sh`）、タスク定義の env / secrets / healthCheck、`dir_tag` は変わらない。`dir_tag` は `os.walk` でディレクトリ全体のハッシュを取るので、`namespace.sh` を足せばタグが変わる。
  - `git grep` で調べたかぎり、このイメージを使うのは `ecs.tf` の temporal コンテナだけ。
- **missing tests**
  - テストの check を、行の順番を入れ替えた中身でメモリ上だけで動かした（ファイルは書いていない）。namespace を起こす行を手順 2 の前へ動かしても、テストは全部通る（Should fix 1）。

見ていない観点:

- AWS（Fargate の PID 名前空間、ECS Exec、ECS の healthCheck がコンテナの env で exec されるか、2 タスクが並んだときの停止時間）は確かめていない。Should fix 2 の healthCheck は手元の docker の HEALTHCHECK で再現したもので、ECS の healthCheck でも同じかは推定。
- TLS の経路（`SQL_TLS_ENABLED=true`、`SQL_HOST_VERIFICATION=true`）は手元で通していない（手元の PG は TLS 無し）。設計方針 2 で Round 2 の対象外なので、コードを読むだけにした。`temporal-sql-tool --help` で、env の名前（`SQL_TLS_CA_FILE` / `SQL_TLS_SERVER_NAME` / `SQL_TLS_DISABLE_HOST_VERIFICATION`）がフラグに結び付いていることは見た。
- `./ops/check.sh`（検証 2）は打っていない。`terraform init` が lock を書き換えることがあり、作業ツリーを変えないという制約に触れるため。`ecs.tf` は main と同一なので、terraform の fmt / validate の結果は変わらない見込み。
- shellcheck はこの PC に無いので打っていない。
- 「テストを変える前に落ちること」（検証 1 の前半）は、手順の記録なので見ていない。
- `--init` を付けた場合の挙動（Round 1 の検証）は打ち直していない。
- type safety はシェルとテストの文字列検査なので対象外。

片付け: `rv039-temporal` / `rv039-temporal2` / `rv039-temporal3` / `rv039-temporal4` / `rv039-pg` を `docker rm -f -v`、`docker network rm rv039net`、`docker rmi rv039-temporal-server:test postgres:18`。そのあと `docker ps -a` / `docker network ls` / `docker images` に `rv039` と `postgres:18` が出ないこと、dangling volume に今回の時刻（11 時台 UTC）のものが無いことを確かめた。

## Must fix

None

## Should fix

- [missing tests + security] `tests/test_workflow.py:612-618`（`unset` の check）と `:648-650`（namespace を起こす行の check）は、`namespace-rds.sh … &` の行が `unset NAUTOBOT_DB_PASSWORD` より**後ろ**にあることを見ていない
  - 再現: `entrypoint.sh` の 104 行目（`/etc/temporal/namespace-rds.sh "$TEMPORAL_ADDRESS_LOCAL" … &`）を「# 2. ロールと DB を master で作る」の前へ動かした中身で、2 つの check と「最後の行が `exec tini -- …`」の check をメモリ上で評価した。結果は `(True, True, True)` で全部通る。
  - そのとき起きること: `namespace-rds.sh` は `unset` の前の env で exec される。master のパスワードは、namespace ができるまでの最長 7.5 分（30 回 × (nc 10 秒 + 5 秒)）のあいだ、そのプロセスの `/proc/<pid>/environ` に残り、uid temporal から読める。これは design.md の設計方針 1（「それより後のコードの行に … 手順 4 の namespace … のどれにも渡らない」）と方針 3 の「fork のあと exec するので、`/proc/<pid>/environ` は `unset` 後の env になる」が守ろうとしている性質そのもの。いまのテストは「`unset` の後に `NAUTOBOT_DB_PASSWORD` という文字列が無い」しか見ないので、順番が逆になっても気づけない。
  - 直し方の例: `_ts_ep_logical` の中で、namespace-rds.sh の行の位置が `_ts_unset[0]` より大きいことを check に足す（`temporal-sql-tool` を呼ぶ `for pair` の行も同じ扱いにしてよい）。
  - Should の理由: いまのコードは正しい順番で、すぐには壊れない。ただ、このサイクルの主眼の性質を守る回帰テストが無く、将来だれかが行を動かすと黙って崩れる。

- [security] healthCheck のプロセスが master のパスワードを env に持ち、uid temporal から読める。`docs/architecture/resources/temporal.md:61`（「守れるのは … ECS Exec のシェルは … そこからは見える」）と制約の表 `:115`（ECS Exec の 1 行だけ）は、この経路を書いていない
  - 再現: 手元の docker で、ecs.tf の healthCheck と同じコマンドを `--health-cmd`（`--health-interval 2s`）に付けて起こした。uid 1000（temporal）のプロセスから `/proc/*/cmdline` を回して見ると、`/bin/sh -c temporal operator namespace describe …` とその子の `temporal operator namespace describe …` が uid 1000 で動き、`environ` に `NAUTOBOT_DB_PASSWORD` を持っていた（`master_in_env=1`）。HEALTHCHECK の exec は、`docker exec` と同じくコンテナの Env を引き継ぐ。
  - そのとき起きること: design.md の背景 1 は「タスクの中のプロセス（uid temporal）が `/proc/1/environ` から読める」を問題にしている。ところが ecs.tf の healthCheck（`interval = 10`）で、10 秒ごとに master のパスワード入りのプロセスが同じ uid で立つ。temporal-server の脆弱性などで uid temporal のコードが動けば、`/proc/<pid>/environ` を見張るだけで読める。`ecs:ExecuteCommand` の権限は要らない。docs は「守れない範囲」を ECS Exec だけとしているので、読む人は「タスクの中のプロセスからは読めなくなった」と受け取る。
  - 直し方の例: docs の 61 行目と制約の表に「healthCheck（10 秒ごと）のプロセスもタスク定義の env を持ち、同じ uid から読める」を足す。根本対策（初期化を一回きりのタスクに分ける、背景 1 の別サイクル）の QUEUE の行に、この経路も理由として書く。
  - Should の理由: main の状態（`/proc/1/environ` に常に残る）より悪くはなっておらず、このコードを入れて壊れるものは無い。直すのは「守れる範囲」の書き方で、残る危険を読む人が見誤るので今のうちに書いておく価値がある。なお、ECS の healthCheck が Fargate でも同じくコンテナの env で exec されるかは、AWS で確かめていない（手元の docker での推定）。

## Nit

- [correctness] `docker/images/temporal-server/namespace.sh:7-11`: `set -u` のもとで引数が 3 つ未満だと `$3: unbound variable` で非 0 で抜ける（手元の `sh namespace.sh 127.0.0.1:7233 default` → rc=1）。先頭のコメント（5 行目「失敗しても exit 0」）、およびテストの「どこで抜けても exit 0」（`tests/test_workflow.py:653-658`）と食い違う。
  - Nit の理由: 呼ぶ側は entrypoint の 1 か所だけで、いつも 3 つ渡している。背景のプロセスの終了コードは誰も見ないので、実害は無い。`: "${3:?…}"` で明示するか、コメントを「引数が揃っていれば」に直す程度でよい。
- [design 整合 / docs] `docker/images/temporal-server/Dockerfile:3`: 「入口を entrypoint.sh（初期化してから公式の /etc/temporal/entrypoint.sh へ exec）」が少し古い。いまは `exec tini -- /etc/temporal/entrypoint.sh` で、公式の entrypoint は tini の子として起きる。
  - Nit の理由: 直後の 4 行目が tini を説明しているので、誤解はしにくい。
- [missing tests] `tests/test_workflow.py:557-558`: check 名は「initProcessEnabled が無い」だが、条件は `"linuxParameters" not in _ecs_code` で `linuxParameters` を丸ごと禁じている。将来ほかの理由（`capabilities` の drop など）で `linuxParameters` を足すと、名前と違う理由でこの check が落ちる。
  - Nit の理由: いまは誤判定が無く、テストを読めば原因は分かる。

## 良かった点

- design.md の設計方針と、実装・テスト・docs が 1 対 1 で対応している。`ecs.tf` を main と同じに戻したこと（diff 0 行）、背景のサブシェルの中身をそのまま `namespace.sh` に移し、引数 3 つにしたこと、`log()` の接頭辞を揃えたことが、どれも設計の文言どおり。
- 公式の entrypoint が `exec temporal-server start` なので、tini → temporal-server の親子が直結した（未確定事項 1 は手元で解消）。SIGTERM が届いて正常に終わり、namespace-rds.sh が `sleep` している最中に止めても 3 秒で抜ける。
- `namespace.sh` のテスト（`_ts_ns_run`）は、`nc` / `temporal` / `sleep` を差し替えて 3 つの道（作る / ある / 30 回失敗）を実際に動かしている。引数がそのまま CLI に渡ることと、ログの接頭辞まで見ている。
- `/proc/<pid>/environ` の観点（fork だけのサブシェルは親の最初の env を持ち続ける）を、別の実行ファイルを exec する形で正しく解いている。手元でも `namespace-rds.sh` とその子が `0` になることを確かめた。

## ユーザーへの質問

- Should fix 2 の healthCheck の経路について。036 のリスク 7 と同じく受け入れ、docs と QUEUE に書くだけでよいか。それとも「初期化を一回きりのタスクに分ける」別サイクルの優先度を上げるか。このサイクルの「守れる範囲」は、healthCheck が 10 秒ごとに立てるプロセスを含めると「タスクの中のプロセスから読めなくする」までは届いていない。

### PM の確認（2026-10-10、fable-5-1）

- 前ラウンドの Must fix（`initProcessEnabled`）: `git diff main -- IaC/terraform/aws-managed/workflow/ecs.tf | wc -l` → `0`。`grep -n initProcessEnabled IaC/terraform/aws-managed/workflow/ecs.tf` → 無し。cold reviewer が docker で PID 1 = tini、ゾンビ無し、tini / temporal-server / namespace-rds.sh の environ に `NAUTOBOT_DB_PASSWORD` 無しを実測（上のサマリ）。解消。
- Should fix 1 [missing tests + security]: 再現した。`entrypoint.sh` の namespace の行（104 行目）を「# 2. ロール」の前へ動かして `tests/test_workflow.py` → `通過 369 / 失敗 0`（見逃す）。check「entrypoint は temporal-sql-tool（for pair）と namespace-rds.sh を unset NAUTOBOT_DB_PASSWORD より後の行で起こす」を足したら、同じ注入で `AssertionError: …（行: unset [53] / for pair [63] / namespace [37]）`。戻して `通過 370 / 失敗 0`。直した（エンジニアのセルフレビュー Round 2 の Should fix 3 と同じ指摘）。
- Should fix 2 [security]: 再現した。手元の docker で `--health-cmd 'sleep 4' -e SECRET_X=dummy` を付けて起こし、`/proc/[0-9]*/environ` を回した → healthCheck の `sh` と `sleep` が同じ uid で `SECRET_X=1`。Fargate では未確認。直した: `docs/architecture/resources/temporal.md` の知見の行と制約の表（`ECS Exec と healthCheck`）、design.md の背景 1「守れない範囲」とリスク 5 に healthCheck の経路を足し、根本対策（初期化を一回きりのタスクに分ける）を QUEUE に足した。cold reviewer の「ユーザーへの質問」は 036 のリスク 7 と同じく受け入れる（PM の判断。優先度はユーザーが QUEUE で上げられる）。
- エンジニアのセルフレビュー Round 2 の Should fix 4（`exec tini` より前は PID 1 が sh で SIGTERM を無視。039 より前からあり未再現）: QUEUE に足した。
- Nit 3（cold）と Round 2 の Nit 5〜9・11、Round 1 の Nit 5〜8・11: 直さず QUEUE に 2 行で足した。
- `./ops/check.sh` → rc 0、`通過 370 / 失敗 0`、`すべて通過`（`.terraform` の lock の差分は無し）。
- cold reviewer の 2 回目: 呼ばない。Round 2 のあとに変えたのはテストと docs だけで、実装ファイル（Dockerfile / entrypoint.sh / namespace.sh / ecs.tf）は `e1b3a9f` から 1 バイトも変わっていない。
- 全体設計 HTML: 触らない（構成要素・データの流れ・配置は変わらない。temporal コンテナの中の PID 1 が変わっただけ）。
- 見ていない観点: AWS（Fargate の healthCheck の env、ECS Exec、2 タスクの停止時間）。QUEUE の「残った修正をまとめて AWS で動作確認して直す」で見る。

結論: Must 0 / Should 0（2 件とも直した）/ Nit 3 + セルフレビューの Nit（QUEUE）。サイクル完了。
