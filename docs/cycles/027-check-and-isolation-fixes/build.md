# check.sh の判定と heal-main の孤立の判定を直す（027）の実装記録

実装モデル: fable-5.1 / effort: high

A〜C の実装（下の表の 4 commit、2026-10-09）は前の実装者 opus-5.5 が書いた。使用量の上限で止まったので、fable-5.1 が 2026-10-10 に引き継ぎ、前の worktree に残っていた未 commit のテストを取り込み、赤→緑を打ち直し、この build.md とセルフレビューを書いた。

ブランチ `feat/027-check-and-isolation-fixes`（origin/main 3e06e4c から）。順序は設計どおり A → B → C。

| # | commit | 件名 |
|---|---|---|
| A | 9d87933 | check.sh の Kafka のメッセージ数で messagesCount が無い・null の応答を理由つきの NG にする |
| B | 8d773d1 | check.sh の Telegraf の health と GoFlow2 の /metrics の宛先を、動いているコンテナの設定から読む |
| C | 74ce9d6 | impact の孤立を「変更前のかたまりがどこへ行ったか」で決め、上げるだけの heal-main で危険が出ないようにする |
| B' | 33f7411 | 偽の docker の config --environment から FAKE_TG_ENV を外し、bind を .env から読む退行がテストで落ちるようにする（opus-5.5 のセルフレビュー） |
| B'' | 86b2caf | 前の実装者が commit していなかった B のテストの追加を取り込む（bind を別々に読む 2 条件、コンテナが片方だけ無い 2 条件） |
| S | fe12c2c | セルフレビューの Should fix 3 件: 孤立の規則を rules.impact で直接縛るテスト 5 本と、.env.example / up.sh の古いコメント 2 行 |

## 引き継ぎで確かめたこと

- 前の worktree（`agent-a12415732ada12c15`、ロック済み・読むだけ）と 33f7411 を、`git diff --stat origin/main...HEAD` の 9 ファイルと `docs/cycles/027-check-and-isolation-fixes/` で 1 つずつ `diff -q` した。違ったのは `tests/test_local_compose.py` だけ。
- その差分は B のテストの強化（設計 B-5 の「bind をコンテナから読む」「コンテナが無い」を、Telegraf と GoFlow2 で別々に見る条件）で、check.sh の変更は無い。設計に沿うのでそのまま取り込み、86b2caf にした。
- `cp /Users/eight/Documents/Dev/sandbox/nwc-poc/.env .env` は元のチェックアウトに `.env` が無く（`.env.example` だけ）失敗した。このサイクルのテストは `.env` を読まない（B-3 で check.sh も読まなくなった）ので、影響は無い。

## ステップごと

テストの件数は、HEAD（86b2caf）の時点で打った `uv run --group dev --group web python tests/<名前>.py` の最後の行。赤は、対象のファイルだけを `git show origin/main:<ファイル> > <ファイル>` で origin/main の形に戻して打ち、あとで `git checkout -- <ファイル>` で戻した。テストは最初の失敗で止まるので、赤の一覧は check を止まらない形に差し替えた scratchpad の `run_nonfatal.py`（リポジトリには入れない）で取った。

### A. Kafka の messagesCount が無い・null

- `cnt` を `t.get('messagesCount')` にし、4 行（metrics / gnmi / traps / logs）の式を `type(v) is int` でなければ NG「messagesCount が無い（Kafbat UI の応答の形が違う。curl -s 'http://127.0.0.1:18080/api/clusters/nwc/topics?perPage=100' で中身を見る）」にした。traps / logs もこの場合は注意でなく NG。0 件・トピックが無いときの文言と判定は変えていない。
- テストは偽の curl に `FAKE_KAFKA_SHAPE=nokey|null` を足し、2 条件（無い / null）で 4 行とも NG で「読めない応答」にしないことと終了コード 1 を見る。
- 赤→緑。
  - 直す前（origin/main の check.sh）: `NG check.sh: Kafbat UI の応答で messagesCount が無い…`、`NG check.sh: … messagesCount が null …` の 2 本（B の 7 本と合わせて `通過 133`）。
  - 直した後: `通過 142 / 失敗 0`。
- 境界を手で打った: 値が `True` / `"3"` / `3.0` は全部「messagesCount が無い」、トピック自体が無ければ今までどおり「0 件」。

### B. Telegraf の health と GoFlow2 の /metrics の宛先

- `cid` / `dest` / `probe` の 3 つの関数を足した。`docker compose ps -a -q <svc>` でコンテナを引き、Telegraf は `docker inspect` の `.Config.Env` から `TELEGRAF_BIND` と `HEALTH_PORT`、GoFlow2 は `.Config.Cmd` の `-addr=<bind>:<port>` から bind を読む。
- 決め方は設計の 3 通り。コンテナが無い → NG「コンテナが無い（docker/compose/up.sh <svc> で上げる）」。bind が空 → 127.0.0.1。bind が `ip -o -4 addr show` に `" <bind>/"` で載っていればそこ、無ければ NG「<bind> が host に無い（lab.sh down のあとなら …）」。
- `.env` の `HEALTH_PORT` と `MGMT_GW` は check.sh から消した。`tests/test_local_compose.py:252` は `"MGMT_GW" not in read(check.sh)` にした。
- README `:117` の箇条書きと `:192` の「check.sh は .env でなく動いている Telegraf のコンテナの HEALTH_PORT に打つ」を新しい決め方にした。
- テストは偽の docker に `ps -a -q`（`FAKE_TG_ID` / `FAKE_GF_ID`）と `inspect`（`FAKE_TG_ENV` / `FAKE_GF_CMD`）を足し、7 条件を書き直した。
- 赤→緑。
  - 直す前（origin/main の check.sh）: B の 7 本が NG（bind が空、bind が host にある、bind が host に無い、コンテナが無い、繋がらない、HTTP 503、HEALTH_PORT はコンテナの値）。A の 2 本と合わせて `通過 133`。
  - 直した後: `通過 142 / 失敗 0`。
- 退行を入れて、テストが縛っていることを確かめた（確かめたあと戻した）。
  - `grep -F -- " $3/"` を `grep -F -- "$3"` にする（前方一致で `203.0.113.10` を拾う）→「bind が host に無い」の check が落ちる（偽の ip に `203.0.113.10` のおとりがある）。
  - 33f7411 で opus-5.5 が入れた `env -u FAKE_TG_ENV` は、bind を `.env` から読む退行を落とすためのもの（その commit の件名のとおり）。

### C. impact の孤立の判定

- `view` が `adj, comps, set(adj) - main, deg` を返すようにし（`main` は `max(comps, key=len)` のまま。`isolated_after` の意味は変えない）、入れ子の `largest`（唯一いちばん大きい断片。同点か空なら空）と `lost`（a のかたまりごとに、b で W(K) から外れた機器。端は隣接する唯一最大のかたまり K\* の W(K\*) に隣接しなければ外れる）を足した。
- `newly_isolated` は `lost(adj0, comps0, adj1, comps1) - targets`、`reconnected` は向きを逆にして `i in deg1` の条件を残した。
- `rules.py` と `topology.py` に同じ本文を置いた（`tests/test_workflow.py:126-127` の `inspect.getsource` の一致はそのまま通る）。
- docstring を「同点ならどれも本流にしない」にし、`rules.py` の「誤報」のコメント、`docs/workflow.md:48`、`docs/architecture/resources/temporal.md:106` を新しい定義に直した。
- テスト。
  - `tests/test_agentcore.py`: spine 2 台を落とす check の期待を leaf 4 台と TRex の 5 台にした。順位が入れ替わる玩具（a–b と c–d と、DOWN の d–e。d–e を上げると c–d–e の 3 台が a–b の 2 台を抜くが、何も切れていない a と b は孤立に出ず ok、つながり直すのは e）を 1 本足した。
  - `tests/test_workflow.py`: spine 2 台の状態 4 通り × 回線 12 本の DOWN 2^12 通りの `rules.precheck("heal-main", …)` が全部 ok の総当たりを 1 本足した。
- 赤→緑。
  - 直す前（origin/main の rules.py と topology.py）: `test_agentcore` は spine の check と玩具の check の 2 本が NG で `通過 160`。`test_workflow` は総当たりが NG で `通過 327`。origin/main の impact で総当たりの verdict を数えると `{'ok': 16240, 'danger': 144}`（設計の「調査で分かった事実」の 144 と一致）。
  - 直した後: `通過 162 / 失敗 0`、`通過 328 / 失敗 0`。`test_workflow.py` 全体で 1.0 秒（総当たりの時間は目立たない。設計のリスク 5）。
- 退行を入れて、テストが縛っていることを確かめた（確かめたあと戻した）。
  - `topology.py` の `largest` を `parts[0] if parts else set()`（同点でも先頭を取る）にする → `test_agentcore` の spine の check が落ちる。
  - 同じ退行を `rules.py` だけに入れる → 落ちるのは `inspect.getsource` の一致の check だけで、総当たりは通る。heal-main（上げるだけ）は同点を作らないので、総当たりは同点の規則を縛っていない（下のセルフレビューの「直したもの」1 で、`rules.impact` を直接打つ check を足した）。
- 境界を手で打った: a–b–c–d の 1 本道で `device_down b` → 孤立は `['a']`（c–d の 2 台が唯一最大）で danger。2–2 に割れる形 → 4 台とも孤立（設計のリスク 4 のとおり）。端しか無いトポロジ → ok。

## 設計からずらした点

1. build.md の頭の実装モデルは、設計の実装ステップ 4 では `opus-5.5` だが、引き継いだ fable-5.1 にした（PM の指示）。A〜C を opus-5.5 が書いたことは冒頭に残した。
2. 設計の「1 commit」の A / B / C に加えて、B のテストの commit が 2 つ（33f7411、86b2caf）と、セルフレビューの Should fix の commit が 1 つある。check.sh の変更は無い。
3. `.env` の cp は元のチェックアウトに無くてできなかった（上の「引き継ぎで確かめたこと」）。
4. 設計の変更対象に無い `docker/compose/.env.example:25` と `docker/compose/up.sh:13` のコメント 2 行を直した（check.sh が `.env` の `HEALTH_PORT` / `MGMT_GW` に打つと書いてあり、B でやめた挙動の説明が残っていた。反対弁護人の Should fix 1）。
5. `tests/test_workflow.py` は設計の 328 でなく 333（孤立の規則を `rules.impact` で直接縛る 5 本を足した。下のセルフレビューの Should fix 2・3）。

## 検証方法の結果

設計の「検証方法」の順。86b2caf で一度打ち、セルフレビューの Should fix を入れたあと（fe12c2c の内容）でもう一度打った。下は後の値（違うのは `test_workflow` の 328 → 333 だけ）。

- `uv run --group dev --group web python tests/test_local_compose.py` → `通過 142 / 失敗 0`（設計の 138 + A で 2 + B で 2 以上 = 142。`:739-742` 相当の `ok  ` 14 本の検査も通ったまま）。
- `uv run --group dev --group web python tests/test_agentcore.py` → `通過 162 / 失敗 0`。
- `uv run --group dev --group web python tests/test_workflow.py` → `通過 333 / 失敗 0`（設計の 328 に、セルフレビューで 5 本足した）。
- `bash ops/check.sh` → 最後の行は「すべて通過」。テストの件数は順に 162、168、519、79、3、78、7、110、142、174、200、66、108、103、333（027 で変わったのは 142 と 162 と 333 だけ）。
- `grep -n "誤報" app/temporal/rules.py docs/workflow.md docs/architecture/resources/temporal.md` → 0 行。
- `grep -n "MGMT_GW" docker/compose/check.sh` → 0 行。
- `bash -n docker/compose/check.sh` → 通る。`shellcheck docker/compose/check.sh` → 指摘なし（この Mac に shellcheck が無いので `/opt/homebrew/bin/uvx --from shellcheck-py shellcheck` で打った）。

### 未確認（実機が要るもの）

- WSL の docker compose での check.sh の実行（A / B とも偽のコマンドのテストだけ）。
- Kafbat UI が `messagesCount` を落とす・null にする応答を本当に返すか（設計のリスク 1）。
- `docker inspect` の `.Config.Env` が telegraf の読む値と一致するか（設計のリスク 2）。
- Exited のコンテナのとき「繋がらない」と `ps -a telegraf が Exited なら` の案内が出ること（設計のリスク 3）。

### セルフレビュー

実装モデル fable-5.1 / effort high。反対弁護人は `Agent`（general-purpose、model fable）に、設計と 5 commit の diff と赤→緑の記録を渡して読むだけで見てもらった。

観点ごとに見た。

- **correctness**
  - A: `type(v) is int` は `bool` を弾く（手で `True` を打って「messagesCount が無い」）。`json.loads` が失敗する応答は今までどおり `judge` の「読めない応答」。
  - B: bind の照合は `" <bind>/"` の前方一致でなく完全一致（退行で確かめた）。`HEALTH_PORT` はコンテナの値で、`.env` とシェルの値は読まない（33f7411 の check）。
  - C: 2 つの `impact` の本文は `inspect.getsource` で一致。総当たり 16384 通りは全部 ok。origin/main では 144 通りが danger で、設計の調査と一致。
- **security**: 権限は広げていない。`.env` は読んでいない（check.sh もテストも）。`docker inspect` の出力に資格情報が載りうるが、check.sh は `TELEGRAF_BIND` と `HEALTH_PORT` と `-addr=` だけを sed で抜き、表示しない。
- **runtime bugs**
  - `cid` は `docker compose ps -a -q` が失敗しても `|| true` で空になり、「コンテナが無い」の NG に落ちる（`set -e` で途中で止まらない。`:817` の 13 項目 NG の check が通る）。
  - `docker inspect` が失敗したときは bind が空扱いで 127.0.0.1 に打ち、「繋がらない」で捕まる（下の Nit 5）。
- **data loss**: 無し（check.sh は読むだけ。impact は純関数）。
- **API compatibility**: `impact` の返す辞書のキーは変えていない。`isolated_after` の意味も変えていない。check.sh の出力の行の形（`ok  名前` / `NG  名前: 理由`）も変えていない。
- **type safety**: `largest` / `lost` に型注釈を付けた。対象外に近い。
- **missing tests**: 同点の規則と島の割れを縛るテストが無かった。下の「直したもの」1・2 で足した。残りは「残したもの」5（bool の退行）。

#### 直したもの（Must fix 0。Should fix 3 件は PM の判断でこのサイクルで直した）

1. **Should fix（missing tests）: 同点の規則（`largest` の「同点なら空」）を縛っているのが `test_agentcore` の spine の check 1 本と、`rules.py` と `topology.py` の本文の一致だけだった**（自分の指摘。反対弁護人の 3 も同じ）。
   - `rules.py` の `largest` を「同点でも先頭を取る」にしても、`test_workflow` で落ちるのは本文の一致の check だけで、総当たりは通っていた（heal-main は上げるだけで同点を作らない）。
   - `tests/test_workflow.py` に `rules.impact` を直接打つ check を 5 本足した（`_toy` 補助）。1 本道 a–b–c–d で `device_down b` → `['a']` だけ孤立。真ん中の回線を落として 2–2 → 4 台とも孤立。端 t が a と c につながる形で `device_down a` → t は孤立に出ない（W(K\*) の c につながったまま）。端 t が a にだけつながる形で `device_down a` → t が孤立で danger。
   - 直したあと同じ退行を `rules.py` に入れると、本文の一致に加えて「2–2 に割れる」と「島が割れる」（下の 2）の 2 本が落ちる（確かめたあと戻した）。
2. **Should fix（missing tests）: 本流から切れていた島が割れると danger になる、という意味の変更をテストが縛っていなかった**（反対弁護人の 2）。
   - 旧 impact は「すでに孤立している機器」を `newly_isolated` に二度と出さなかった。新 impact はかたまりごとに W(K) を見るので、島 f–g の中の回線を落とすと f と g が孤立で danger（設計の手順 3 どおり）。再現: a–b–c–d–e と f–g で `link_down f#1` → `danger ['f', 'g']`。
   - 上の 5 本のうち 1 本（島 f–g）がこれを縛る。
3. **Should fix（docs）: `docker/compose/.env.example:25` と `docker/compose/up.sh:13` のコメントが、check.sh が `.env` の `HEALTH_PORT` / `MGMT_GW` に打つという B でやめた挙動のままだった**（反対弁護人の 1）。
   - 2 行を「check.sh は動いているコンテナの値（docker inspect）に打つ」に直した。設計の変更対象に無いので「設計からずらした点 4」にも書いた。

#### 残したもの（直していない。全部 Nit）

1. **Nit: `cid` の `head -n 1`。** 同じサービスのコンテナが 2 つある（scale、`docker compose run` の一回限りの残骸）とき先頭を取り、順序は保証されない。compose の通常の使い方では 1 つなので、そのまま（反対弁護人の 6 も同じ）。
2. **Nit: WSL 以外で `ip` が無い host では、bind が入っているときに必ず「host に無い」になる。** check.sh は WSL 前提（README）で、bind が空なら `ip` を打たないので、素の Mac で up.sh した形では出ない。
3. **Nit: `num` の 3 段の lambda は読みにくい。** `judge` が python の式 1 つを受ける形なので、関数定義を置けない。式の形は A の前からの踏襲。
4. **Nit: `docker inspect` が失敗したとき・docker デーモンが落ちているときの文言。** inspect が失敗すると bind が空扱いで 127.0.0.1 に打って「繋がらない」。デーモンが落ちていると `cid` が空で「コンテナが無い（up.sh telegraf で上げる）」と出て、原因が docker だとは出ない。前段の Spark の check で docker 不達は分かる（反対弁護人の 9）。
5. **Nit: 「messagesCount が無い」の文言は、値が `true` / `12.0` / `"12"` のときにも出る。** キーが無い・null と同じ案内（応答の形を curl で見る）が当たるので、分けていない。`isinstance` に戻す退行（bool を通す）を落とすテストも無い（反対弁護人の 5）。
6. **Nit（反対弁護人の 4）: 3–3 の同点に割れた状態から heal-main を打つと、`reconnected` に 7 台全部（切れていない spine も）が載る。** 同点では誰も本流でないので「全員が外れていて全員つながり直す」になる。verdict は ok で設計どおり。表示の問題だけなので残した。
7. **Nit（反対弁護人の 7・8）: `TELEGRAF_BIND=0.0.0.0` や `-addr=[::]:8081` の形は「host に無い」NG になる。** up.sh は host のアドレスか空しか渡さず、telegraf.sh は IPv4 に縛るので、今は到達しない。
8. **Nit（反対弁護人の 10）: `view` の `main = main | {...}` を `|=` に短くすると `comps` の要素を壊す。** コメントもテストも無い。
9. **Nit（反対弁護人の 11）: 33f7411 の `env -u FAKE_TG_ENV` は、設計 B-5 の「既存の分岐は変えない」から少しずれる。** bind を `.env` から読む退行を落とすためで、残す。

#### 反対弁護人

`Agent`（general-purpose、model fable、読むだけ）。結論は Must fix 0 / Should fix 3 / Nit 9。自分でもテストを走らせ（142 / 162 / 328）、`rules.py` と `topology.py` の `impact` のバイト一致と `bash -n` を確かめていた。shellcheck は再現していない（この Mac に無い。上の「検証方法の結果」のとおり shellcheck-py で打った）。

- Should fix 1（`.env.example:25` / `up.sh:13` の古いコメント）: `grep -n "check.sh" docker/compose/.env.example docker/compose/up.sh` で再現。直した（上の 3）。
- Should fix 2（島が割れると danger、テスト無し）: 玩具で `danger ['f', 'g']` を再現。直した（上の 2）。反対弁護人が 196608 件を掃いて旧→新で verdict が変わる件数（danger→ok 6462、ok→danger 5254、danger→warn 36）を出し、見本はどれも新の方が正しかったと報告した。この掃引は自分では打ち直していない。
- Should fix 3（同点の規則のテスト）: 自分の指摘と同じ。直した（上の 1）。
- Nit 4（3–3 同点からの heal-main で `reconnected` が全員）: 玩具 6 台で `ok ['a1', 'a2', 'b1', 'b2', 's1', 's2']` を再現。残した（上の 6）。
- Nit 5〜11: 読んで妥当。残した（上の 1、4、5、7、8、9）。
- Nit 12（build.md が未追跡、頭のモデル名）: この commit で解消。モデル名は「設計からずらした点 1」。
- 不成立とされたもの（`load_static` の共有、`set -e`、`grep -q` の SIGPIPE、総当たりの時間、`HEALTH_PORT=` 空、`ip -o -4` の peer 表記）は、自分の確認とも一致する。

#### /robust（1 回。Pre-Mortem 3 つ）

1. **「実機で check.sh が Telegraf を NG にする」としたら。**
   - 考えられる原因: `docker inspect` の `.Config.Env` に `TELEGRAF_BIND` が無い（compose が環境変数を渡していない）か、値が空。
   - 確かめたこと: `docker/compose/compose.yaml:87-88` の telegraf は `TELEGRAF_BIND: ${TELEGRAF_BIND:-}` と `HEALTH_PORT: ${HEALTH_PORT:-8080}` を environment で渡している（up.sh を通さず compose を直に打っても、キーは空の値で入る）。読めなければ空扱いで 127.0.0.1 の 8080 に打つ（bind が空のときの Telegraf は全部のインターフェースで待つので、届く）。
   - 結論: 新たに NG になる形は無い。実機は未確認の項目に入れた。
2. **「GoFlow2 の `-addr=` の sed が bind を取れない」としたら。**
   - 考えられる原因: `.Config.Cmd` の JSON で `-addr=` の値に `"` や `:` が無い形（`-addr=:8081`）。
   - 確かめたこと: `-addr=:8081` は `\([^"]*\)` が空に当たって bind が空 → 127.0.0.1（偽の docker の既定がこの形で、「bind が空」の check が通る）。bind がある形は `FAKE_GF_CMD` で見ている。
   - 結論: 取れない形は、空として 127.0.0.1 に落ちるので、今までの挙動（127.0.0.1 に打つ）と同じ。
3. **「新しい孤立の定義で、fail-main / 保守の事前チェックが今まで ok だったものを danger にする」としたら。**
   - 考えられる原因: 変更前に割れていたかたまりを、変更後に同点で割ると全員が孤立に出る（設計のリスク 4）。
   - 確かめたこと: `test_agentcore` の `what_if` / `impact` の既存の check（spine 1 台、回線 1 本、保守中）は期待を変えずに通った。変わったのは spine 2 台を落とす check だけで、それは設計で決めた変更。
   - 結論: 既存の運用の形では変わらない。

Pre-Mortem から足した直しは無い。
