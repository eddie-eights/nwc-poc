# check.sh の判定と heal-main の孤立の判定を直す（027）

設計: PM(opus-5.5) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

009 の cold review と 011 のセルフレビューで出て BACKLOG に残っていた、判定の誤りを 3 件束ねる。2 件は手元の `docker/compose/check.sh`、1 件は事前チェック（`impact`）。3 件は互いに独立で、1 件ずつ 1 commit にする。

| # | BACKLOG の行 | 出所 |
|---|---|---|
| A | 手元の `check.sh` の Kafka のメッセージ数の判定で `messagesCount` が無い・null の応答を NG の理由つきで扱う | 009 の cold review Round 1 の Nit 3 |
| B | 手元の `check.sh` の Telegraf の health の宛先を `up.sh` が決めた bind に合わせる | 009 の cold review Round 2 の Nit 2 |
| C | heal-main と孤立の同点を解く | 011 のセルフレビュー S1 |

### 調査で分かった事実（2026-10-09、origin/main 3e06e4c）

**A. messagesCount が無い・null**

- `docker/compose/check.sh:25-37` の `judge` は、Python の式が例外を出すと「読めない応答: …」の NG になる。`ok` / `注意:` で始まればそのまま、それ以外は NG で `ng=1`。
- `:61` が `kafka=$(get - 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' || true)`。
- `:68` が `cnt="{t['name']: t['messagesCount'] for t in json.loads(s)['topics']}"`。`messagesCount` が無ければ `KeyError`、null なら `None` と `0` の比較で `TypeError` になり、4 行とも「読めない応答」の NG になる（理由が読めない）。
- `:69` の metrics は `'ok' if $cnt.get('metrics', 0) > 0 else '0 件'`。`:70-72` の gnmi の 0 件は NG（「0 件（gnmic の on-change…docker compose logs gnmic）」）。`:73-78` の traps / logs の 0 件は「注意: 0 件（…）」。
- テストの偽の curl（`tests/test_local_compose.py:461`）は `{"topics":[{"name":"metrics","messagesCount":%s},…]}` を返し、件数は `FAKE_METRICS` / `FAKE_GNMI` / `FAKE_TRAPS` / `FAKE_LOGS` で決める。`:707-725` が 4 つの 0 件を見る。

**B. Telegraf の health の宛先**

- `docker/compose/up.sh:12-20` は `MGMT_GW=203.0.113.1` が host にあれば `TELEGRAF_BIND=$MGMT_GW`、無ければ空にして WARNING（「docker/compose/lab.sh up のあとに docker/compose/up.sh telegraf syslog-ng goflow2 で ${MGMT_GW} だけに直す」）を出し、`export GNMI_TARGETS DEVICE_MAP TELEGRAF_BIND` する。
- `docker/compose/compose.yaml`
  - telegraf の environment は `:87` が `TELEGRAF_BIND: ${TELEGRAF_BIND:-}`、`:88` が `HEALTH_PORT: ${HEALTH_PORT:-8080}`。
  - syslog-ng は `:128` が `SYSLOG_BIND: ${TELEGRAF_BIND:-}`。
  - goflow2 の command は `:140` が `-listen=netflow://${TELEGRAF_BIND:-}:2055,sflow://${TELEGRAF_BIND:-}:6343`、`:145` が `-addr=${TELEGRAF_BIND:-}:8081`。
- `docker/compose/check.sh:100-108` は、`check.sh` を打った時点で 203.0.113.1 が host にあるかを見直して宛先を決める。
  ```
  MGMT_GW=203.0.113.1
  tb=127.0.0.1; ip -o -4 addr show 2>/dev/null | grep -q " $MGMT_GW/" && tb=$MGMT_GW
  hp=$(env_get HEALTH_PORT)
  judge "Telegraf: health が 200" "…" <<<"$(get - -o /dev/null -w '%{http_code}' "http://$tb:${hp:-8080}/" || true)"
  ```
  `up.sh` のあとに `lab.sh down` すると、コンテナは 203.0.113.1 で待ったままなのに `check.sh` は 127.0.0.1 に打ち、「繋がらない」と出て `up.sh telegraf` を案内する（直し方が合わない）。逆に、lab が無いときに `up.sh` してから `lab.sh up` すると、コンテナは全部の IF で待っているのに 203.0.113.1 に打つ（これは届くので害は無い）。
- `:114-116` の GoFlow2 も同じ `$tb` で `http://$tb:8081/metrics` を打つ。`:111-113` の syslog-ng は `ss -Hlun` の `:5140` を見るので bind に依らない。
- `HEALTH_PORT` は `env_get`（シェル、`.env` の順）で読む。コンテナが上がったあとに `.env` だけ変えると、`check.sh` は新しい値に打つ。
- テスト（`tests/test_local_compose.py`）
  - `:252-253` が `MGMT_GW` を `up.sh`、`check.sh`、`lab.sh` で比べる。
  - `:396` からの `fake(name, body)` で偽のコマンドを作る。偽の docker（〜`:424`）は `ps` に `FAKE_PS` を返し、それ以外は ENV の行を `FAKE_LOG` に書く。偽の ip（`:433-436`）は `FAKE_GW=1` なら 203.0.113.1/24 を持つ。偽の curl（`:450-470`）の `:469` が `http://*:8081/metrics` に `FAKE_GF_METRICS`、`:470` が `http://*:*/` に `FAKE_TG_HEALTH` を返す。
  - `:475-477` が消す環境変数の一覧（`FAKE_*` を足したらここにも足す）。
  - `:689-706` が宛先を見る。GW が無ければ `127.0.0.1:8080` と `127.0.0.1:8081/metrics`、`FAKE_GW=1` なら `203.0.113.1:8080` と `203.0.113.1:8081`、`FAKE_TG_HEALTH` が 000 / 503 のとき、`HEALTH_PORT` はシェル、`.env` の順（18081 / 18082）。
  - `:739-742` は `OS_COUNT=0` のとき `ok  ` の行がちょうど 14 本であることを見る。
- docs は `docker/compose/README.md:117`（「Telegraf の health（`up.sh` と同じく `203.0.113.1` があればそこ、無ければ `127.0.0.1` の `HEALTH_PORT`）」）と `:192`（「`check.sh` も `HEALTH_PORT` に打つ」）。

**C. heal-main と孤立の同点**

- `app/temporal/rules.py:85-159` の `impact(devices, links, changes)`（`app/agentcore/topology.py` に同じものがあり、`tests/test_workflow.py:126-127` が `inspect.getsource` の一致を見る）。
  - `:99-119` の `view(dd, ld)` は、端（`END_ROLES = ("trex",)`）でない機器だけで成分を作り、**いちばん大きい成分を本流（main）**にする。端は main に隣接していれば main に入る。戻り値は `(set(adj) - main, deg)`。
  - `:136-143` で `newly_isolated = sorted(iso1 - iso0 - targets)`、`reconnected = sorted(i for i in iso0 - iso1 if i in deg1)`、`redundancy_lost`（`deg1` が 1 で `deg0` が 2 以上、`iso1` に無い）、`redundancy_restored`、`isolated_after = sorted(iso1)`。`:144` で verdict（unknown / danger / warn / ok）。
- 問題は、変更前と変更後で本流を**別々に**選ぶこと。トポロジが割れているとき、変更で成分の大きさの順が入れ替わると、何も切れていない機器が「孤立する」に出る。上げるだけの `heal-main`（`ACTION_CHANGES` = `link_up dc1-a-leaf-01#ethernet-1/1`）でも danger が出る。同点のときは `adj` の並び（名前順）で先に出た成分が本流になる。
- 実測（静的データ 7 台・12 本。spine 2 台の状態 4 通り × 回線の DOWN 2^12 = 16384 通りで `heal-main` の `impact` を回した。2026-10-09、scratchpad の試作）: いまの `impact` は **144 通りで danger か warn** を出す。下の定義では **0 通り**。
- spine を 2 台とも落とす（spine-01 が DOWN のまま `device_down dc1-spine-02`）と、leaf 4 台が 1 台ずつの成分になり、いまは名前順で先頭の `dc1-a-leaf-01` が本流に選ばれて `newly_isolated` に 3 台だけ出る（実測 `['dc1-a-leaf-02', 'dc1-s-leaf-01', 'dc1-s-leaf-02']`）。`tests/test_agentcore.py:242-246` はこれに合わせて `len == 3 and set < {leaf 4 台}` を見ている。
- コメントと docs が誤報を書いている。`app/temporal/rules.py:77-78`（「トポロジが割れているときは「危険」と出ることがある…BACKLOG の「heal-main と孤立の同点を解く」」）、`:89` と `app/agentcore/topology.py:342` の docstring（「いちばん大きいかたまりに入っていない機器を「孤立」とする」）、`docs/workflow.md:48`、`docs/architecture/resources/temporal.md:106` の「処置の種類」の行。
- `app/agentcore/tools.json:70` と `app/agentcore/topology.py:513` の what_if の説明は「孤立」の定義に触れていないので直さない。

## 設計方針

**A. messagesCount が無い・null を理由つきの NG にする**

1. `cnt` を `{t['name']: t.get('messagesCount') for t in json.loads(s)['topics']}` にする（キーが無ければ `None`）。トピックが無いときは今までどおり `.get(name, 0)` で 0 件として扱う。
2. 4 行（metrics / gnmi / traps / logs）の式を、値が `type(v) is int`（`bool` を弾くため `isinstance` にしない）でなければ NG で「messagesCount が無い（Kafbat UI の応答の形が違う。curl -s 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' で中身を見る）」を出す形にする。traps / logs もこの場合は `注意` でなく NG（件数が読めないのは 0 件とは別の問題）。
   - 4 行で同じ式を繰り返さないよう、`cnt` の隣に「数か、理由の文字列か」を返す小さな式（`num`）を置いて使い回す。書き方は実装に任せるが、`judge` の 2 番目の引数（Python の式）の形は変えない。
3. 0 件のとき、トピックが無いときの文言と判定（metrics / gnmi は NG、traps / logs は注意）は今のまま。
4. テスト（`tests/test_local_compose.py`）
   - 偽の curl の Kafka の応答に、`messagesCount` を落とす（`FAKE_KAFKA_SHAPE=nokey`）と null にする（`=null`）を足す。`:475-477` の消す一覧にも足す。
   - 足す検査は 2 つ。`nokey` と `null` のそれぞれで、4 行とも `NG` に「messagesCount が無い」が出て、「読めない応答」が出ず、終了コードが 1。
   - 赤→緑: 先にテストだけ足し、いまの `check.sh` で「読めない応答」になって落ちることを確かめてから直す。

**B. health の宛先を、動いているコンテナの設定から読む**

1. `check.sh` の Telegraf と GoFlow2 の判定の前で、動いているコンテナから bind とポートを読む。
   - telegraf は `docker compose … ps -a -q telegraf` でコンテナを引き、`docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' <id>` の `TELEGRAF_BIND=` と `HEALTH_PORT=` を読む。
   - goflow2 は `docker inspect --format '{{json .Config.Cmd}}' <id>` の `-addr=<bind>:8081` から bind を読む。
   - `docker compose` の呼び方（`-f` / `--env-file` / プロジェクト名）は `check.sh` が Spark の `ps`（`:48`）で使っているものに揃える。
2. 決め方は 3 通り。
   - コンテナが無い → curl を打たずに NG。理由は「コンテナが無い（docker/compose/up.sh telegraf で上げる）」（goflow2 も同じ形）。
   - bind が空 → `127.0.0.1` に打つ（全部の IF で待っている）。
   - bind が host に無い（`ip -o -4 addr show | grep -q " $bind/"` が外れる）→ curl を打たずに NG。理由は「<bind> が host に無い（lab.sh down のあとなら docker/compose/lab.sh up で戻すか、docker/compose/up.sh telegraf syslog-ng goflow2 で上げ直す）」。
   - それ以外は `<bind>` に打つ。
3. ポートはコンテナの `HEALTH_PORT` を使う（読めなければ 8080）。`.env` の `HEALTH_PORT` はもう読まない（コンテナが上がったあとに `.env` だけ変えたときに、待っていないポートへ打たないため）。
4. `check.sh` の `MGMT_GW` は使わなくなるので消す。`tests/test_local_compose.py:252-253` は `up.sh` と `lab.sh` だけを比べる形に直す。
5. テスト（`tests/test_local_compose.py`）
   - 偽の docker に `ps -a -q <svc>`（`FAKE_TG_ID` / `FAKE_GF_ID`。空ならコンテナ無し）と `inspect`（`FAKE_TG_ENV` に `TELEGRAF_BIND=…` と `HEALTH_PORT=…` の行、`FAKE_GF_CMD` に JSON の配列）の分岐を足す。既存の `ps`（Spark）と ENV を書く分岐は変えない。`:475-477` の消す一覧にも足す。
   - 既定（`FAKE_*` を何も付けない）は、コンテナがあり bind が空、`HEALTH_PORT=8080` にする。これで `:739-742` の `ok  ` 14 本はそのまま。
   - `:689-706` の検査を書き直す。
     - bind が空なら `127.0.0.1:8080` と `127.0.0.1:8081/metrics` に打つ。
     - bind が 203.0.113.1 で `FAKE_GW=1` なら `203.0.113.1:8080` と `203.0.113.1:8081` に打つ。
     - **bind が 203.0.113.1 で `FAKE_GW` が無い（`lab.sh down` のあと）なら curl を打たずに NG、理由に「host に無い」と「lab.sh up」が出る。**（このサイクルの本題）
     - コンテナが無いなら NG で「up.sh telegraf」が出る。
     - `HEALTH_PORT` はコンテナの値（例 18081）に打ち、`.env` の値（例 18082）には打たない。
     - `FAKE_TG_HEALTH` が 000 / 503 のときの文言は今のまま。
   - 赤→緑: 太字の検査をいまの `check.sh` で落ちることを確かめてから直す。
6. `docker/compose/README.md:117` と `:192` を新しい決め方に直す。

**C. 本流を「変更前のかたまりがどこへ行ったか」で選ぶ**

`impact` の孤立の定義を次に変える。`view` の `deg` と `isolated_after`（いまの見え方）は変えない。

1. **かたまり**は、生きていて端でない機器を、端を中継にせずにつないだもの（今の `view` の成分と同じ）。変更前を comps0、変更後を comps1 とする。
2. **W(K)**: 変更前のかたまり K の生き残り（K ∩ 変更後に生きている機器）を comps1 で分け、**唯一いちばん大きい断片**を W(K) とする。同点なら W(K) = ∅。
3. **孤立する機器（newly_isolated）**: 各 K の「生き残り − W(K)」を集め、targets（変更で落とした機器）を除く。
4. **つながり直す機器（reconnected）**: 向きを逆にして同じことをする。comps1 の各 K' について、変更前に生きていた機器を comps0 で分け、唯一いちばん大きい断片を除いた残り。`i in deg1` の条件は残す。
5. **端（TRex）**: 変更前に隣接するかたまりのうち唯一いちばん大きい K\* があり、変更後に W(K\*) のどれにも隣接していなければ、孤立する機器に入る。K\* が無い（同点、または隣接なし）なら入れない。つながり直す機器は逆の向きで同じことをする。
6. 補助の関数は `impact` の中に入れ子で書く（`tests/test_workflow.py:126-127` の `inspect.getsource` の一致を保ち、`rules.py` と `topology.py` に同じ本文を置くため）。
7. 性質: 上げる変更（`link_up` / `device_up`）は変更前のかたまりを割らないので、W(K) = 生き残り全部で孤立は出ない。回線の本数も減らないので `redundancy_lost` も出ない。`heal-main` で danger と warn は原理的に出ない。
8. テスト
   - `tests/test_agentcore.py:242-246` を、`newly_isolated` が leaf 4 台と `dc1-trex-01` の 5 台に一致する形に直す（いまは 3 台と部分集合）。spine を両方落とせば leaf は全部ばらばらになり、どれも本流ではない。
   - `tests/test_workflow.py` の事前チェックの節に、総当たりの検査を 1 つ足す: 静的データ（`app/agentcore/topology.py` の `load_static()`）で、spine 2 台の状態 4 通り × 回線 12 本の DOWN の組み合わせ 2^12 = 16384 通りのどれでも、`rules.precheck("heal-main", …)` が danger にも warn にもならない。
   - 順位が入れ替わる玩具を 1 つ足す（`tests/test_agentcore.py:251-260` の a / b / c の並び）: 機器 a〜e、回線 a–b（UP）、c–d（UP）、d–e（DOWN）。`link_up e#1` で c–d–e が 3 台になって a–b（2 台）より大きくなっても、孤立は出ず ok、つながり直す機器は `["e"]`。いまの `impact` は danger で `newly_isolated == ["a", "b"]`（試作で実測）。
   - 既存の what_if の検査 5 つ（`:230-241`）と玩具の 4 つ（`:251-260`）は、新しい定義でも同じ結果になる（試作で確かめた: 冗長切れ warn、Leaf 1 台 ok、TRex ok、片系 DOWN で残りを落とすと danger `[dc1-a-leaf-01]`）。
   - 赤→緑: 総当たりの検査と順位が入れ替わる玩具を先に足し、いまの `impact` で落ちること（総当たりは 144 通り）を確かめてから直す。
9. コメントと docs を直す。
   - `app/temporal/rules.py:77-78` の誤報の注を消す。
   - `app/temporal/rules.py:89` と `app/agentcore/topology.py:342` の docstring を新しい定義（「変更前のかたまりのうち、変更後にいちばん大きい断片から外れた機器を「孤立」とする。同点ならどれも本流にしない」）に直す。
   - `docs/workflow.md:48` の誤報の行を消すか、新しい定義の 1 行に替える。
   - `docs/architecture/resources/temporal.md:106` の「処置の種類」の行から「`heal-main` でも「危険」の誤報が出ることがある」を消す。

**やらないこと。**

- syslog-ng の判定（`ss` で見ているので bind に依らない）は変えない。
- `docker/compose/up.sh` の bind の決め方は変えない。
- `impact` の戻り値のキー、verdict の決め方、`summary` の文言、`isolated_after` と `deg` は変えない。
- `tools.json` と `what_if` の説明は直さない。
- マネージドと OSS の Web の承認画面は触らない（`precheck` の結果を出すだけ）。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `docker/compose/check.sh` | A / B | `cnt` と 4 行の式、Telegraf と GoFlow2 の宛先をコンテナから読む、`MGMT_GW` を消す |
| `tests/test_local_compose.py` | A / B | 偽の curl と docker の分岐、消す一覧、`:252-253`、`:689-706` の書き直し、足す検査 |
| `docker/compose/README.md` | B | `:117` と `:192` |
| `app/temporal/rules.py` | C | `impact` の孤立とつながり直しの決め方、`:77-78` のコメント、`:89` の docstring |
| `app/agentcore/topology.py` | C | `impact`（rules.py と同じ本文）、`:342` の docstring |
| `tests/test_agentcore.py` | C | `:242-246` の直し、順位が入れ替わる玩具 |
| `tests/test_workflow.py` | C | 総当たりの検査 |
| `docs/workflow.md` | C | `:48` |
| `docs/architecture/resources/temporal.md` | C | `:106` |

## 再利用するもの

- `check.sh` の `judge` / `get` と、Spark の `docker compose ps -a` の呼び方（`:48`）。
- `tests/test_local_compose.py` の `fake()` と、偽の docker / ip / curl の分岐の書き方。
- `impact` の中の `view`（`deg` と `isolated_after` に使い続ける）と、changes を当てる部分（`:121-135`）。
- `app/agentcore/topology.py` の `load_static()`（総当たりの検査の入力）。

## 実装ステップ

1. A: テストを足して赤を確かめ、`check.sh` を直して緑にする。1 commit。
2. B: テストを足して（書き直して）赤を確かめ、`check.sh` と README を直して緑にする。1 commit。
3. C: テストを足して赤を確かめ、`rules.py` と `topology.py` の `impact` を同じ本文で直して緑にする。コメントと docs も同じ commit。1 commit。
4. `build.md` を書く（頭は `実装モデル: opus-5.5 / effort: high`。各ステップの赤と緑の出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `uv run --group dev --group web python tests/test_local_compose.py` が「通過 N / 失敗 0」（2026-10-09 は 138）。A で 2、B で 2 以上増える。`:739-742` の `ok  ` 14 本の検査が通ったまま。
- `uv run --group dev --group web python tests/test_agentcore.py` が「通過 162 / 失敗 0」（2026-10-09 は 161。`:242-246` は直すだけで件数は変わらず、順位が入れ替わる玩具で 1 増える）。
- `uv run --group dev --group web python tests/test_workflow.py` が「通過 328 / 失敗 0」（2026-10-09 は 327。総当たりで 1 増える）。`impact` の本文の一致の検査（`:126-127`）が通ったまま。
- `bash ops/check.sh` が全部通る（ほかのテストの件数は変わらない）。
- `grep -n "誤報" app/temporal/rules.py docs/workflow.md docs/architecture/resources/temporal.md` が 0 行。
- `grep -n "MGMT_GW" docker/compose/check.sh` が 0 行。
- `bash -n docker/compose/check.sh` と `shellcheck docker/compose/check.sh` が通る。
- 実機（WSL の docker compose）での確かめは未確認のまま残す（このサイクルでは偽のコマンドのテストだけ）。

## 未確定事項とリスク

1. Kafbat UI が `messagesCount` を落とす・null にする応答を実際に返すかは未確認（009 の cold review の指摘は推測）。直しは応答の形が変わったときに理由を出すためのもので、今の応答では挙動が変わらない。
2. `docker inspect` の `.Config.Env` は compose が渡した値で、コンテナの中で書き換えた値ではない。telegraf は環境変数をそのまま読むので一致するはずだが、実機では未確認。
3. `docker compose ps -a -q` が止まったコンテナ（Exited）も返すので、Exited のときは bind を読んで curl を打ち、「繋がらない」と出る。今の文言（`ps -a telegraf が Exited なら logs telegraf`）がそのまま当たるので、分けない。
4. C の新しい定義は、変更前に割れていたかたまりが変更後に同点で割れると、どちらも「孤立」に出す（本流を選ばない）。spine を 2 台とも落とすと孤立が 3 台から 5 台（leaf 4 台と TRex）に増える。運用者には大げさに見えうるが、どの leaf も本流ではないので正しい。
5. 総当たりの検査は 16384 通りで、試作では 2 つの `impact` を回して数秒だった。テスト全体の時間への影響は小さいはずだが、実装時に測る。
