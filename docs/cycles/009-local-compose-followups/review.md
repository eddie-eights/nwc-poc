# Cycle 009 local-compose-followups レビュー

## Round 1

- 対象: `a5097ab^1..834a1dd`（`fix/local-compose-followups` を a5097ab で、Round 2 の `fix/009-spark-restart-env-get`（468fd46）を b983215 で docs/cycle-006-design にマージ済み）。実装モデル: opus-5.5（エンジニア2）。レビューモデル: cold reviewer = opus、確認 = fable-5.1（PM）
- cold reviewer に依頼した（初回ビルド直後。1 サイクル 1 回目）。入力は design.md と変更ファイル 16 本のパス、未解消の Must fix「無し」、書き出し先だけ
- マージ後の `bash ops/check.sh`（834a1dd の直前、scratchpad `check-009fix.log`）: 最終行「すべて通過」、exit=0、`test_local_compose` は `通過 121`

### cold reviewer の結果（review-r01.md をそのまま連結）

# Cycle 009 local-compose-followups レビュー Round 1（cold reviewer）

対象: `a5097ab^1..HEAD` の 16 ファイル（`.gitignore`、`app/containerlab/lab.sh`、`app/telegraf/telegraf.conf.in`、`app/telegraf/telegraf.sh`、`docker/compose/{.env.example,README.md,check.sh,compose.yaml,lab.sh,up.sh}`、`docs/development.md`、`ops/check.sh`、`tests/{test_lab_debug,test_local_compose,test_oss,test_stream}.py`）。突き合わせた設計は `docs/cycles/009-local-compose-followups/design.md`、逸脱の記録は同じディレクトリの `build.md`。

## サマリ

Must fix 0 / Should fix 2 / Nit 7。

ECS 側の既定値は変わっておらず（`telegraf.sh` の既定は `TRAP_PORT=1162` / `MDT_PORT=57000` / `HEALTH_PORT=8080` / `LOG_PORT=5140`、`TELEGRAF_BIND` は空。Terraform と OSS 版はこれらの環境変数を渡さない）、壊れる・危険になる筋は見つからなかった。いちばん大きいのは、Round 2 で変えた中身（`env_get` を compose 自身に読ませる、Spark を送り先の healthy まで待たせる、Kafka の数え方）が `design.md` に戻っておらず、「現行の設計だけを書く」と宣言した文書が古い設計のまま残っていること。

自分で走らせたテスト（Mac、`docker compose version` は v5.1.3）:

- `uv run python tests/test_local_compose.py` → `通過 121 / 失敗 0`
- `uv run python tests/test_lab_debug.py` → `通過 84 / 失敗 0`
- `uv run python tests/test_stream.py` → `通過 75 / 失敗 0`
- `uv run python tests/test_oss.py` → `通過 171 / 失敗 0`

`docs/development.md` の `test_local_compose` 121 は上の実行と一致する。

### 見た観点 / 見ていない観点

見た観点:

- design.md の 11 項目（BACKLOG 39・41・40・45・50・42・49・48・47・53・54）と実装の対応、build.md の逸脱 11 件の妥当性
- `telegraf.sh` のポートとアドレスの検証が sed より前にあるか（sed への注入）、ECS の既定値が変わらないか
- `check.sh` / `lab.sh` の `env_get`（`docker compose config --environment`）の読み方と、値が画面に出ないか（stderr を捨てているか）
- `check.sh` の judge の WARN（`注意:`）が `ng` を増やさないこと、Kafka・Spark・Telegraf health の判定
- `compose.yaml` の `restart: on-failure:5`、Spark の `depends_on` と送り先の healthcheck
- `app/containerlab/lab.sh` の `LAB_CMD`（表示だけで実行しないこと）、`hint()` の第 3 引数
- `ops/check.sh` の 3（`git ls-files '*.sh'` を 1 本ずつ `bash -n`）
- テストの変更（正規表現のプレースホルダ化、fake docker / iptables による挙動テスト）

見ていない観点:

- 実機での動作: WSL での `203.0.113.1` への bind、containerlab、Splunk（Mac では amd64 のイメージが起動しない）、compose 全体を上げた状態での `check.sh` の通し実行、Splunk の healthcheck（`/sbin/checkstate.sh`）が実際に healthy になるまでの時間
- `ops/check.sh` の全体（terraform の validate を含む節）と shellcheck は走らせていない
- `tests/test_oss.py` の差分に混ざっている 008 の Neo4j の変更（範囲外）
- EC2 上の `/usr/local/bin/lab`（`setup.sh` の symlink）経由で `$0` が `lab` 以外になる場合の表示

## Must fix

None

## Should fix

- [design.md との整合性] `docs/cycles/009-local-compose-followups/design.md:43`, `:49`, `:118`, `:120`。design.md は冒頭で「この文書は現行の設計だけを書く」と言っているが、実装後の設計に直っていない。43 行目は `env_get` を「sed の 1 本で済ませ」としているが、実装は `docker/compose/check.sh:11-12` / `docker/compose/lab.sh` の `docker compose --env-file … config --environment` で compose 自身に読ませている。49 行目は `kafka-get-offsets.sh` とトピック `snmp` / `snmp_trap` を挙げるが、実装は Kafbat の `messagesCount` とトピック `metrics` / `traps`（`check.sh:59-69`）。120 行目の検証は `grep -c 'on-failure:5'` が `3` だが、アンカーも出るので実際は 4（build.md の逸脱 2）。さらに Round 2 で入れた Spark の `depends_on: {condition: service_healthy}` と splunk / opensearch / prometheus の healthcheck（`compose.yaml:141-165`, `:181-201`, `:225-232`）、`check.sh` の Spark の判定、`LAB_CMD` の仕組み、`.env.example` に置くキー（`MDT_PORT` / `HEALTH_PORT` だけ）は design.md のどこにも無い。起こること: 次のサイクルや My Repo の設計 HTML が design.md を正本として読むと、手元の compose が使っていない `kafka-get-offsets.sh` 前提や sed 版の `env_get` を「設計」として再実装・検証してしまう。逸脱は build.md にしか無く、正本が 2 つに割れている。Should にする理由: 動作は壊さないが、design.md を正本にする運用（仕様は design.md に残す）の前提を崩し、次の作業者が誤った設計を拾う具体的な筋があるため。
- [design.md との整合性 / runtime] `docker/compose/compose.yaml:225-232`（splunk の healthcheck、`start_period: 10m`）と `:141-145`（spark-splunk の `depends_on: splunk: {condition: service_healthy}`）。Round 2 で範囲を広げ、`up.sh`（`docker compose up -d --build`）が Splunk の healthy まで戻らなくなったが、この healthcheck は一度も実行で確かめていない（Mac では Splunk が起動しない、build.md の未確認の欄）。起こること: WSL で `/sbin/checkstate.sh` がイメージの版や構成で非 0 を返し続ける場合、`up.sh` が最長で start_period 10 分 + retries 分ブロックしたあと `dependency failed to start` で止まり、`spark-splunk` が `Created` のまま残る（README:52 はこの失敗を説明しているが、それが起きるかどうかは未確認）。Telegraf・Kafka 側は上がっているので致命ではないが、手元の compose の「`up.sh` 1 本で上がる」という主経路の振る舞いを変えた変更なので、design.md に書いて未確認であることを明記し、WSL での初回の実行で `docker compose ps` の `health` と所要時間を記録するべき。Should にする理由: 主経路の起動を未検証の前提に依存させた変更で、外れたときの影響（`up.sh` が長時間止まった末に失敗）がユーザーに直接見えるため。

## Nit

- [security] `docker/compose/README.md:84`。「`203.0.113.1` だけで待つ（lab の外から偽の trap や syslog を入れられないように）」と書いているが、Linux は weak host model なので、`203.0.113.1` に bind したソケットは別のインターフェースから届いた宛先 `203.0.113.1` のパケットも受ける。同じ L2 にいて `203.0.113.1` への経路を持つホスト（WSL の mirrored モードなど）からは届き得る。bind で 127.0.0.1 や LAN の IP からの受け付けは減るので悪化ではないが、「lab の外から入れられない」の保証は言い過ぎ。厳密にするなら iptables の INPUT で `-i <containerlab の bridge>` に絞る。Nit にする理由: 手元の検証用の構成で、変更前（全インターフェース）より狭まっているため。
- [correctness] `ops/check.sh:46-48`。`git ls-files` は非 ASCII のファイル名を `"\343\201…"` のようにクォートして返すので、そういう `.sh` があると `bash -n` が「ファイルが無い」で落ち、メッセージは「構文エラーがある」になる。作業ツリーから消したが index に残っている `.sh` も同じく誤った理由で落ちる。`git ls-files -z` と `while IFS= read -r -d ''` にすると避けられる。Nit にする理由: いまのリポジトリに該当するファイルは無く、落ちる方向（見逃しではない）に倒れるため。
- [runtime] `docker/compose/check.sh:66-69`。`cnt` は `t['messagesCount']` を直に引くので、Kafbat の版で `messagesCount` が無い・`null` のトピックがあると `KeyError` / `TypeError` になり、traps が `注意`（WARN）ではなく NG になる。`t.get('messagesCount') or 0` にすれば設計どおり WARN に留まる。Nit にする理由: いまの Kafbat では値が入っており、壊れても NG に倒れる（見逃しにはならない）ため。
- [runtime] `docker/compose/up.sh:16-19`。`up.sh spark-splunk` のように Telegraf を含まない引数で打っても、lab が無ければ「Telegraf は WSL の全部のインターフェースで待つ」の WARNING が出る。Telegraf を上げない呼び方では誤解を招く。Nit にする理由: 表示だけの問題で動作は変わらないため。
- [design.md との整合性] `app/telegraf/telegraf.sh:67`。IPv4 の検査 `^[0-9]{1,3}(\.[0-9]{1,3}){3}$` は `999.1.1.1` を通す（セルフレビューの Nit のまま）。sed への注入は防げており、不正な値は Telegraf の bind で落ちるので害は小さい。Nit にする理由: 値は `up.sh` が固定値か空しか渡さないため。
- [missing tests] `tests/test_oss.py` の `ops/check.sh` の検査は、`SH=` の行と OSS のファイルが `git ls-files` に入っていることを見る形に弱まった。`tests/test_local_compose.py` が `ops/check.sh` の 3 を挙動で走らせているので全体としての抜けは無いが、test_oss 単独で走らせたときの保証は下がっている。Nit にする理由: 別のテストで挙動を押さえているため。
- [runtime] `docker/compose/check.sh:11-12` / `docker/compose/lab.sh`。`config --environment` はシェルの環境変数も全部出すので、シェルに改行を含む値の変数があると、その 2 行目が `KEY=` の形をしていれば `env_get` が取り違える（`tail -1` で後勝ち）。理論上の話で、`.env` のキーは compose が後から出すので通常は `.env` 側が勝つ。Nit にする理由: 再現する現実的な筋が見当たらないため。

## 良かった点

- `telegraf.sh` がポート（`''|0*|*[!0-9]*` と `-le 65535`）と `TELEGRAF_BIND`（空か IPv4 の形）を sed より前に検証して `exit 1` するので、`s#…#$VAR#` への注入や `#` 混入で設定ファイルが壊れる筋を塞いでいる。既定値は ECS と同じで、NLB の health check 先 8080 も変わらない。
- `env_get` を自前の sed で compose の書式を真似るのをやめ、`docker compose config --environment` に読ませたことで、`export ` / CRLF / 行末コメント / クォートの差を compose と完全に一致させた。`2>/dev/null` で値の断片を含み得るエラー出力を捨て、`ENV_ALL` は変数に留めて表示しない。
- `check.sh` の judge に `注意:` の WARN を足し、traps の 0 件を設計どおり NG にしない。理由の 200 字切り詰めと Splunk の理由の重複除去で出力が読みやすい。
- `LAB_CMD` は表示に使うだけで実行しない。ラッパーが渡す変数を `SRLINUX_IMAGE` / `MULTITOOL_IMAGE` / `TELEGRAF_LOCAL` / `LAB_CMD` の 4 つに絞り、シェルの `REGISTRY` / `AWS_REGION` が ECR や SSM へ流れない。
- `ops/check.sh` の 3 を `bash -n a b` の「先頭しか見ない」問題から 1 本ずつの検査に直し、`tests/test_local_compose.py` で fake docker / iptables を使って 3 通りの呼び方・4 分岐を挙動で検査している。build.md に red → green の記録がある。

## ユーザーへの質問

None

### PM の確認（Round 1）

- Must fix 0 / Should fix 2 / Nit 7
- Should fix 1（design.md が実装後の設計に直っていない）: 読んで確かめた。43 行目の sed 版 `env_get`、49 行目の `kafka-get-offsets.sh` と `snmp` / `snmp_trap`、120 行目の `3`、Round 2 の healthcheck と `depends_on`、`LAB_CMD`、`.env.example` のキーの 6 点とも design.md に無かった。**PM が design.md を現行の設計に書き直した**（このラウンドと一緒に commit）。冒頭に取り込んだ旨、設計方針 1・2 / 4 / 5・6 / 9 / 10 / 11、変更対象ファイル、検証方法 4 / 6 / 8・9・11 / 10、リスク 6・7 を直した。「実物で確認した契約」は設計時の行番号なのでその旨を見出しに書いた
- Should fix 2（Splunk の healthcheck を実行で確かめていない）: Mac（Apple Silicon、`platform: linux/amd64` のエミュレーション）で compose の splunk サービスだけを別プロジェクト名 `pmsplunk009` で上げ、health を 15 秒ごとに見た（scratchpad `splunk-health.sh` / `splunk-health.log`）

```
up done 22:45:26
15s running starting 2
…
106s running starting 5
121s running healthy 5
splunk Up 2 minutes (healthy)
 Volume pmsplunk009_splunk-var Removed
 Volume pmsplunk009_splunk-etc Removed
```

  `/sbin/checkstate.sh` は **121 秒で healthy**（`start_period 10m` の中）。`down -v` でコンテナ 0・volume 0 を確認。WSL の x86_64 では未確認のまま（design.md のリスク 6 に「初回の `up.sh` で記録する」と書いた）。Mac でも受かったので、ユーザーに見える「`up.sh` が長時間止まって失敗する」筋は手元では再現しない
- Nit 1（README:84 の「lab の外から入れられない」は言い過ぎ。weak host model）: 読んだだけ。直さない。最終報告に載せる
- Nit 2（`git ls-files` の非 ASCII のクォート）: `git ls-files '*.sh' | grep -c '^"'` → `0`（24 本中）。該当なし。直さない
- Nit 3（`messagesCount` が無い / null で NG に倒れる）: `check.sh` の `cnt` の式を Python で再現 → `{"name":"metrics"}` で `KeyError 'messagesCount'`、`null` で `TypeError`。再現した。NG 側に倒れる（見逃しにならない）ので Nit のまま直さない。最終報告に載せる
- Nit 4（`up.sh spark-splunk` でも bridge の WARNING）/ Nit 5（IPv4 の検査が `999.1.1.1` を通す）/ Nit 6（test_oss の `ops/check.sh` の検査が弱まった）/ Nit 7（シェルの改行入りの変数）: 読んだだけ。直さない。最終報告に載せる
- 未解消の Must fix / Should fix: 無し（Should fix 1 は design.md、2 は実測で解消）。実装ファイルはこのラウンドで変えていない。次は完了判定の直前の cold reviewer 2 回目

## Round 2

- 対象: `a5097ab^1..aaf1d5c`（Round 1 で実装ファイルは変えていない。aaf1d5c は design.md と review.md だけ）。実装モデル: opus-5.5（エンジニア2）。レビューモデル: cold reviewer = opus、確認 = fable-5.1（PM）
- cold reviewer に依頼した（完了判定の直前。1 サイクル 2 回目）。入力は design.md と変更ファイル 16 本のパス、未解消の Must fix「無し」、書き出し先だけ
- 前ラウンドの Must fix: 無し。Should fix 1（design.md）は aaf1d5c の design.md、Should fix 2（Splunk の healthcheck）は Round 1 の実測（121 秒で healthy）で解消したまま
- テスト: cold reviewer が worktree で 1 本ずつ実行（下に貼ったとおり `test_local_compose` 121 / `test_lab_debug` 84 / `test_stream` 75 / `test_oss` 171、失敗 0）

### cold reviewer の結果（review-r02.md をそのまま連結）

# 手元の compose の残り 2 件を直す（009）: cold reviewer 2 回目

## サマリ

- 範囲: `git diff a5097ab^1 HEAD` のうち指定の 16 ファイル（`.gitignore`、`app/containerlab/lab.sh`、`app/telegraf/telegraf.conf.in`、`app/telegraf/telegraf.sh`、`docker/compose/{.env.example,README.md,check.sh,compose.yaml,lab.sh,up.sh}`、`docs/development.md`、`ops/check.sh`、`tests/{test_lab_debug,test_local_compose,test_oss,test_stream}.py`）。突き合わせた先は `design.md`（11 項目、Round 1 のあとに現行の設計へ書き直したもの）
- 差分の大きさ: 16 files changed, 566 insertions(+), 143 deletions(-)
- 総評: design.md の 11 項目は実装とテストに反映されている。前ラウンドの Must は無く、Should 2 件は解消済み。design.md の 1 か所の書きぶりと実装が違う点と、`check.sh` の health の宛先の決め方に小さなずれがある（どちらも Nit）。Round 1 の Nit 7 件は意図して残したもの。新しく効いてくるものは無いので、ここには再掲しない

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合性。11 項目と「変更するファイル」表、検証方法、リスクを実装と突き合わせた
- Telegraf のテンプレートの置換。`telegraf.sh` がポートと `TELEGRAF_BIND` を検査してから sed で埋めることを確かめた
  - ECS は何も渡さないので、既定値のままで振る舞いは変わらない
  - `IaC/` と `ops/` を grep した。ECS のタスク定義（`*.tf`）に `LOG_PORT` / `TRAP_PORT` / `MDT_PORT` / `HEALTH_PORT` / `TELEGRAF_BIND` を渡す箇所は無い（コメント 1 件だけ）
  - `app/containerlab/lab.sh` の `TRAP_PORT` / `LOG_PORT` は export されておらず、Telegraf 側へは漏れない
- `.env` を compose 自身に読ませる方式。`check.sh` / `lab.sh` の `ENV_ALL` と `env_get` を見た
  - `docker compose --env-file .env.example config --environment` を `docker/compose` で実行した（compose v5.1.3）。rc=0、出力は stdout に 74 行、stderr は 0 行だった
  - `DOCKER_HOST` が届かない状態でも成功し、daemon は要らない
- `LAB_CMD` の決め方と `hint` の 3 つ目の引数（`fail-bgp` → `heal-bgp`）
  - EC2 は `setup.sh:29` が `/usr/local/bin/lab` に置き、`ops/up.sh:944` がそのパスで呼ぶ
  - ラッパーは `docker/compose/lab.sh` から `LAB_CMD="$0"` を渡す
- compose の `restart: on-failure:5` と healthcheck と `depends_on`
  - `docker compose -f docker/compose/compose.yaml --env-file docker/compose/.env.example config | grep -c 'on-failure'` は `4` だった（design.md の検証 10 と一致）
- `check.sh` の 11 項目（WARN が `ng` を増やさないこと、Spark 2 項目、Telegraf health）
- `ops/check.sh` の `bash -n` の段
- テストは自分で走らせた。worktree の根で 1 本ずつ実行した:
  - `uv run python tests/test_local_compose.py`: `通過 121 / 失敗 0`
  - `uv run python tests/test_lab_debug.py`: `通過 84 / 失敗 0`
  - `uv run python tests/test_stream.py`: `通過 75 / 失敗 0`
  - `uv run python tests/test_oss.py`: `通過 171 / 失敗 0`
  - 変更した 6 本の sh の `bash -n`: 全部通過

見ていない観点:

- WSL の実機での `up.sh` → `lab.sh up` → `check.sh` の通し（design.md のリスク 6、Splunk の healthcheck の WSL での所要時間）。手元では lab も Splunk も上げていない
- `/sbin/checkstate.sh` そのものの振る舞い
- `config --environment` を持つ compose の最低版（design.md のリスク 7。README にも未確認と書いてある）
- `.env` の実ファイル。読まない決まりなので、`.env.example` だけで確かめた
- `tests/test_oss.py` の差分のうち、Neo4j に関する部分（008 の範囲で、このサイクルの範囲外）

## Must fix

None

## Should fix

None

## Nit

- [design.md との整合性] 実装と design.md の書きぶりが違う。
  - 実装: `docker/compose/check.sh:95-100` で足した Telegraf の判定は「Telegraf: health が 200」で、`get - -o /dev/null -w '%{http_code}' "http://$tb:${hp:-8080}/"` を打つ。
  - design.md: `:34` は「`check.sh` の判定に『Telegraf: コンテナが running』を足す」と書く。`:66`（「`check.sh`（Telegraf の running、Spark の 2 項目）が NG にする」）、`:96`（変更するファイル表の「Telegraf の running」）、`:134`（リスク 3「コンテナが running か」）も同じ前提で書いている。
  - 起こること: 止まったコンテナは health が届かないので NG になり、目的（bind の失敗や restart の上限で止まった Telegraf を拾う）は果たせている。ただし design.md を読んでテストを足したり判定を探したりすると、`running` の判定は見つからない。逆に、コンテナは running でも health が落ちている場合（ポートの衝突など）も同じ NG に入る。この違いは design.md から読み取れない。
  - Nit にする理由: 振る舞いは設計の意図を満たしており、ずれているのは文書の言い回しだけだから。

- [runtime] Telegraf health の宛先を決める時点が、bind を決めた時点と違う。
  - 該当: `docker/compose/check.sh:95-96`。health の宛先 `tb` は「`check.sh` を打った時点で host に `203.0.113.1` があるか」で決まる。一方、Telegraf が待つアドレスは `docker/compose/up.sh:16-18` を打った時点で決まっている。
  - 起こること: lab を上げて `up.sh` を打つと、Telegraf は `203.0.113.1` だけで待つ。そのあと `lab.sh down` すると、`check.sh` は `127.0.0.1` に打って「繋がらない（docker compose ps -a telegraf が Exited なら …）」を出す。Telegraf は動いているので、案内どおり `ps -a` を見ても `Exited` は出ず、理由が分からない。
  - 補足: README:84 は「lab を作り直したら `restart telegraf`」としか書いていない。lab が無い状態で `check.sh` を打つこと自体が想定外の使い方なので、害は小さい。
  - Nit にする理由: lab を落としたままの `check.sh` は他の判定も NG になる状況で、誤るのは案内の文だけだから。

## 良かった点

- `.env` を自前で解釈するのをやめ、`docker compose config --environment` に読ませた。クォート、`$` の展開、シェル変数が勝つ順序が compose と必ず一致する。
  - 読めないときは compose のエラー（値のかけらを含みうる）を捨てて、汎用の文だけ出す。値が端末に漏れない作りになっている。
  - `test_local_compose.py` は偽の docker と本物の compose（`docker compose version` が通るとき）の両方でこれを確かめている。
- `telegraf.sh` は sed に渡す前に、ポート（1〜65535、先頭 0 なし）と `TELEGRAF_BIND`（空か IPv4 の形）を検査している。テンプレートへの注入を防ぎつつ、既定値では ECS の設定を 1 文字も変えない。ECS 側に新しい変数を渡していないことも grep で確かめられた。
- Spark を送り先の `service_healthy` まで待たせる設計で、ジョブが 5 回の上限を使い切る経路を塞いだ。
  - 根拠に restartCount の実測（build.md:205）がある。
  - `x-spark` のマージキーが `depends_on` を混ぜない罠を design.md に書き、Kafka 3 つと合わせて各 service に書いている。
- `LAB_CMD` で「戻すのは …」の案内を呼び方（`sudo lab` / ラッパー / 直のパス）に合わせた。障害を入れたあとの復旧コマンドを、そのまま貼れる形で出している。

## ユーザーへの質問

None

### PM の確認（Round 2）

- Must fix 0 / Should fix 0 / Nit 2
- Nit 1（design.md の「Telegraf: コンテナが running」と実装の「health が 200」のずれ）: 読んで確かめた。`check.sh:98` は `judge "Telegraf: health が 200"`、`test_local_compose.py:601` も `TH = "Telegraf: health が 200"`。design.md の 34・66・96・134 行目が「running」のままだった（Round 1 で PM が書き直したときの誤り）。**design.md の 4 か所を「health が 200」に直した**（このラウンドと一緒に commit）。コードとテストは変えていない
- Nit 2（health の宛先を `check.sh` の時点で決めるので、`lab.sh down` のあとは 127.0.0.1 に打って案内が合わない）: 読んで確かめた。`up.sh:16-18` と `check.sh:95-96` が同じ `ip -o -4 addr show` で別々に判定している。lab を落とした状態の `check.sh` は想定外の使い方で、誤るのは案内の文だけ。直さない。最終報告に載せる
- 未解消の Must fix / Should fix: 無し。cold reviewer 2 回目まで通ったので、サイクル完了（HTML と BACKLOG はこのあと）

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-009-local-compose-followups-review.html -->
