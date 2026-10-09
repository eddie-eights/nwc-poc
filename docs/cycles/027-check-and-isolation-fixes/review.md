# check.sh の判定と heal-main の孤立の判定を直す（027）のレビュー

## Round 1

cold reviewer に依頼した: はい（opus-5.5 / high、detached worktree review-027、HEAD 062d254、文脈なし）。実装は build.md の Round 1（fable-5.1）と Round 2（Should fix 3 件の直し）。以下の「cold review」は reviewer の報告をそのまま連結したもの。

### cold review

## サマリ

check.sh の判定と heal-main の孤立の判定を直す（027）の cold review、Round 1。対象は `origin/main...062d254` の 12 ファイル（+388 / -72。うち build.md が +165）。中身は 3 件で、check.sh の Kafka の `messagesCount`（A）、check.sh の Telegraf と GoFlow2 の宛先（B）、`impact` の孤立の定義（C）。

全体として、3 件とも design.md のとおりに実装されている。C の新しい定義は、design.md の手順 2〜5 を自分で別に書き直した実装と突き合わせ、ランダムな 30000 例で `newly_isolated` と `reconnected` が全部一致した。上げるだけの変更（link_up / device_up）で danger や warn になる例も 0 件だった。テスト 3 本は手元で通った。Must fix と Should fix は無い。Nit は、テストで縛られていない細部と、範囲外の同じ形の潜在バグだけ。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - A の手順 1〜3、B の手順 1〜6、C の手順 1〜9 と、「やらないこと」を 1 つずつコードと照らした。
  - 変更対象の表に無い `docker/compose/.env.example:25` と `docker/compose/up.sh:13` はコメントだけの変更。B で古くなった説明を直したもので、build.md の「設計からずらした点 4」に書いてある。`up.sh` の bind の決め方は変わっていない。
  - build.md の頭のモデル名（設計では opus-5.5、実際は fable-5.1）と `test_workflow` の件数（設計 328 → 333）も、build.md に理由つきで書いてある。
- **correctness（C）**
  - design.md の定義（かたまり、W(K)、端の K\*、逆向きの reconnected、targets の除外、`i in deg1`）を、`rules.impact` とは別に union-find で書き直した。
  - 書き直した実装と `rules.impact` を、ランダムなグラフ 30000 例で突き合わせた。条件は 2〜7 台、端（trex）あり、機器と回線の DOWN が混ざる、変更は 1〜2 個の混在。
  - 結果: 不一致 0、上げるだけの変更で danger / warn になる例 0。使ったスクリプトは scratchpad に一時的に置き、確かめたあとで消した。
- **correctness（A）**
  - `num` を確かめた。`c.get(n, 0)` なので、トピックが無ければ 0 で「0 件」。`messagesCount` が無い・null・int でなければ文字列が返り、`judge` の NG になる。
  - bash のクォートも確かめた。`\"` が `"` になり、中の `'…'` は Python の文字列の中に収まる。`judge` の 2 番目の引数は展開したあと再解釈されない。
- **correctness（B）**
  - `cid` / `dest` / `probe` の分岐を追った。コンテナが無いときは curl を打たない。bind が空なら 127.0.0.1。bind が host に無ければ curl を打たない。それ以外はその bind に打つ。
  - `grep -F -- " $3/"` は `203.0.113.10/24` に当たらない。
  - GoFlow2 の sed は `-addr=:8081` で空、`-addr=203.0.113.1:8081` で `203.0.113.1` を取る（バックトラックで `:8081` を外す）。
  - 突き合わせた相手: `compose.yaml:87-88` と `:139-145`、`app/telegraf/telegraf.sh:43`（TELEGRAF_BIND を IPv4 か空に縛る）、`telegraf.conf.in:70`（`service_address = "http://__BIND__:__HEALTH_PORT__"`）。
- **security**
  - `docker inspect` で読むのは telegraf の `.Config.Env` だけ。抜き出すのは `TELEGRAF_BIND` と `HEALTH_PORT` で、表示しない。compose.yaml の telegraf の environment に秘密の値は無い。
  - bind は curl の URL と NG の echo にしか入らず、Python の式には入らない。式に入る `$2` は固定のサービス名。
- **runtime bugs**
  - `set -euo pipefail` のもとで、`cid` は `|| true`、inspect も `|| true`。`probe` の中の `judge` が立てる `ng=1` はグローバルに効く（サブシェルの中ではない）。
- **API compatibility**: `impact` の戻り値のキー、`verdict` の決め方、`summary` の組み立て、`isolated_after` と `deg` は変わっていない。check.sh の出力の行の形も変わっていない。
- **missing tests**: A の 2 条件、B の宛先の検査 5 本、C の総当たりと玩具 6 本（test_agentcore の 1 本と test_workflow の 5 本）を読んだ。
- **docs**
  - `grep -n "誤報" app/temporal/rules.py docs/workflow.md docs/architecture/resources/temporal.md app/agentcore/topology.py` は 0 行。
  - `grep -n "MGMT_GW" docker/compose/check.sh` は 0 行。
  - 古い説明（「いちばん大きいかたまり」「check.sh が 203.0.113.1 / .env の HEALTH_PORT に打つ」）は、docs・app・docker・ops・tests に残っていない（docs/cycles の記録を除く）。
- **実際に走らせたコマンド**（チェックアウトは `review-027`、venv は scratchpad）
  - `uv run --frozen --group dev --group web python tests/test_workflow.py` → `通過 333 / 失敗 0`
  - `… tests/test_agentcore.py` → `通過 162 / 失敗 0`
  - `… tests/test_local_compose.py` → `通過 142 / 失敗 0`
  - `bash -n docker/compose/check.sh` → 通った

見ていない観点:

- `shellcheck docker/compose/check.sh` は打っていない。このマシンに shellcheck が無く、`uvx` でパッケージを取ってくるのは控えた。build.md の「指摘なし」は再現していない。
- `bash ops/check.sh` は打っていない。terraform の init がチェックアウトに書き込むため。このサイクルは Terraform を変えていない。
- 赤→緑の「赤」側は再現していない。origin/main の check.sh / impact に新しいテストを当てて落ちることは、確かめるにはファイルを書き換える必要がある。build.md の記録を読んだだけ。
- 実機の確かめはしていない（WSL の docker compose、Kafbat UI の本物の応答、`docker inspect` の `.Config.Env` の実際の値）。design.md と build.md のとおり、未確認のまま。
- data loss と type safety は、変更が読むだけのスクリプトと純関数なので、対象外と判断した。

## Must fix

None

## Should fix

None

## Nit

- **[missing tests] A の `type(v) is int`（`isinstance` にしない理由は bool を弾くこと）を縛るテストが無い**
  - 場所: `docker/compose/check.sh:71`
  - design.md の A-2 はこの区別を明示している。それでも `isinstance(v, int)` に戻す退行は、今のテスト（nokey / null の 2 条件）では落ちない。戻すと `"messagesCount": true` は `True > 0` で `ok` になる。
  - 偽の curl に `FAKE_KAFKA_SHAPE=bool`（値が `true`）を足せば縛れる。
  - Nit にした理由: Kafbat UI が bool を返すことは確かめられておらず、design.md の検証方法もこの検査を求めていない。build.md の「残したもの 5」と同じ指摘。
- **[design.md との整合性 / 表示] 同点で割れていたかたまりが上げてつながると、`reconnected` に変化の無い機器まで全部載る**
  - 場所: `app/temporal/rules.py:155`（`topology.py` も同じ）
  - 例: 3–3 に割れた状態から heal-main を打つと、承認画面の precheck の文言が「つながり直す機器: <spine を含む全員>」になる。
  - design.md の手順 4（向きを逆にして同じことをする）のとおりで、verdict は ok なので壊れはしない。
  - Nit にした理由: 表示の問題だけで、設計どおり。build.md の「残したもの 6」と同じ指摘。運用者が誤読しうるので、気になるなら BACKLOG に載せる候補。
- **[runtime bugs（範囲外）] `docker/compose/up.sh:15` が、check.sh の新しいコメントが「やってはいけない」と書いた形のまま残っている**
  - check.sh の新しいコメントは `check.sh:108`、新しい書き方は `:112`。
  - `up.sh:15` の形: `set -euo pipefail` のもとで `ip -o -4 addr show | grep -q " $MGMT_GW/"`。
  - check.sh 側は、`grep -q` が早く抜けると `ip` が SIGPIPE で落ち、pipefail で外れになる、と書いて `>/dev/null` に替えた。`up.sh` 側だと、GW があるのに `TELEGRAF_BIND` が空になりうる。
  - `ip` の出力は小さく、パイプのバッファに収まるので、実際に起きる見込みは低い。
  - Nit にした理由: 「up.sh の bind の決め方は変えない」は設計の範囲外。同じリポジトリの中で判断が食い違っているので、BACKLOG に載せる候補。
- **[correctness] GoFlow2 の `/metrics` のポートは 8081 に固定している**
  - 場所: `check.sh:136`
  - `-addr=<bind>:<port>` から bind だけを読み、ポートは読んでいない。compose.yaml の `:145` が `:8081` に固定しているので、今はずれない。
  - Nit にした理由: design.md の B-1 が bind だけを読むと決めていて、ずれる経路が今は無い。
- **[missing tests] コンテナはあるが `docker inspect` が失敗する分岐が、テストで縛られていない**
  - 場所: `check.sh:126` / `:135`
  - たとえば ps と inspect のあいだでコンテナが消えると、bind が空扱いで 127.0.0.1 に打ち、「繋がらない」の NG になる。今の挙動は妥当。
  - Nit にした理由: NG には倒れて、終了コードは正しい。build.md の「残したもの 4」と同じ。
- **[design.md との整合性] build.md の頭の実装モデルが、design.md の実装ステップ 4 の指定（`opus-5.5`）と違う（`fable-5.1`）**
  - 場所: `docs/cycles/027-check-and-isolation-fixes/build.md:3`
  - 引き継いだことと PM の指示は、build.md の「設計からずらした点 1」に書いてある。
  - Nit にした理由: 記録の正確さの問題で、動きには関わらない。

## 良かった点

- C の `largest` / `lost` は、design.md の定義をそのまま短く写していて、別に書き直した実装とランダムな 30000 例で全部一致した。`view` の戻り値に `adj` と `comps` を足しただけで、`isolated_after` と `deg` の計算には触れていない。
- `rules.py` と `topology.py` の本文が一致している（`inspect.getsource` の検査が通る）。
- 総当たりの heal-main だけでは同点の規則を縛れない、と自分で気づいて、`rules.impact` を直接打つ玩具 5 本を足している。1 本道、2–2 の同点、端の 2 形、島の割れ。
- B のテストは、Telegraf と GoFlow2 の bind を別々に読むこと、片方だけコンテナが無いこと、`.env` やシェルの `HEALTH_PORT` を読まないこと（18081 / 18082 / 18083 の 3 値）、を全部縛っている。
- 偽の docker の `env -u FAKE_TG_ENV` は、bind を `.env` から読む退行をテストが見逃さないようにする手当てになっている。
- `grep -F -- " $3/"` の前後の区切りで、`203.0.113.10` のおとりを踏まない。偽の ip にもおとりが入っている。

## ユーザーへの質問

None

### PM の判定

- Must fix 0 / Should fix 0 なので、直すものは無い。Nit 6 件は直さない。
- Nit のうち、BACKLOG の候補に回すものは 2 件。BACKLOG への追記は PM がやる。
  - `docker/compose/up.sh:15` の `ip … | grep -q` を、check.sh と同じ `>/dev/null` の形に揃える（pipefail と SIGPIPE）。
  - 同点で割れたかたまりが上げてつながったとき、`reconnected` に変化の無い機器まで載る表示を絞る。
- 残りの Nit 4 件は記録だけにする。
  - `type(v) is int` を縛るテストが無い。
  - GoFlow2 の `/metrics` のポートを 8081 に固定している。
  - コンテナはあるが `docker inspect` が失敗する分岐のテストが無い。
  - build.md の頭の実装モデルの行が design.md の指定と違う。
- 未確認のもの。
  - WSL の実機での check.sh。
  - shellcheck（cold reviewer の環境に無かった）。

### エンジニアが確かめたこと

この worktree（HEAD 062d254）で、venv を scratchpad に置いて 3 本を走らせた。出力の最後の行をそのまま貼る。

- `uv run --frozen --group dev --group web python tests/test_workflow.py` → `通過 333 / 失敗 0`
- `uv run --frozen --group dev --group web python tests/test_agentcore.py` → `通過 162 / 失敗 0`
- `uv run --frozen --group dev --group web python tests/test_local_compose.py` → `通過 142 / 失敗 0`

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261010-cycle-027-check-and-isolation-fixes-review.html -->
