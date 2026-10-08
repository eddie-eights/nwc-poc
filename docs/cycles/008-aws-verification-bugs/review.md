# AWS 検証で見つけた不具合 3 件を直す（008）レビュー

## Round 1

- 対象: `3c99552..64b13b4`（docs/cycle-006-design に cde390f でマージ済み）。実装モデル: opus-5.5（エンジニア1）。レビューモデル: cold reviewer = opus、確認 = fable-5.1（PM）
- cold reviewer に依頼した（初回ビルド直後）。入力は design.md と変更ファイル 8 本のパスだけ
- マージ後の `bash ops/check.sh`: `すべて通過`（exit 0。test_alerts 138 / test_graph 78 / test_oss 171 / test_sync 100）

### cold reviewer の結果（review-r01.md をそのまま連結）

## サマリ
「AWS 検証で見つけた不具合 3 件を直す（008）」の `3c99552..64b13b4` をレビューした。対象のコードは 8 ファイル（app 3 / ops 1 / tests 4）で +305 −26 くらい。ほかに build.md が +365。

全体の評価: A（trap ルールの bucket budget）、B（ConflictException の打ち直し）、C（Neo4j の索引と、ラベルごとに分けた未登録の頂点の読み）は、どれも design.md の方針どおりに入っている。Neptune に送る openCypher は変わっておらず、golden の照合で縛られている。design から外れた所（size 10 × 10、test_oss.py の変更、seed はラベル 2 つだけ読む、A の確認方法）は全部 build.md に理由付きで書かれていて、コードを読んだ限り理由は成り立つ。Must fix は無い。Should fix は 2 件で、どちらも「A の直しを、プラグインの更新や lab の成長に対して壊れにくくする」もの。

### 見た観点 / 見ていない観点
- **design.md との整合性（見た）**
  - A: 3 点を確かめた。`interval: 30s` の固定、terms の size の引き下げ（10 × 10）、field の並び・`noDataState: OK`・`execErrState: KeepLast` がそのままか（yaml の diff を読んだ）
  - B: 4 点を確かめた。`ClientError` の `Error.Code == "ConflictException"` のときだけ打ち直すか、`CONFLICT_WAITS = (0.5, 1.0, 2.0)`、試すのが 4 回までか、それ以外の例外は今までどおり errors に積むか
  - C: 3 点を確かめた。`_INDEXES` の 9 個、Neo4j のときだけラベルごとに分けるか、`ops/seed_graph.py` が OSS のときだけ `db.prepareForReplanning()` を呼び、失敗は WARNING にするか
  - プラグインの式: build.md に写した grafana-opensearch-datasource v2.34.4 の `lucene_handler.go`（`hasAutoDateHistogram` / `termsBucketProduct` / `termsBucketEstimate`）を、自分でも `gh api repos/grafana/opensearch-datasource/contents/pkg/opensearch/lucene_handler.go?ref=v2.34.4` で取って突き合わせた。build.md の写しと `tests/test_alerts.py` の `terms_buckets` / `bucket_budget_ok` は、ソースの式と合っている。`pkg/tsdb/interval.go` は読んでいない（build.md の引用を信じた）
- **correctness（見た）**
  - B の打ち直しが冪等か: `apply()` → `graph.set_status` / `set_layer_status`（graph.py:660-723）を読んだ。打ち直してよいと判断した理由は 2 つ。中で使う書き込みは SET と MERGE だけで、Neptune の 1 クエリは 1 トランザクション（途中で ConflictException になっても半分だけ書かれた状態は残らない）
  - C の seed() がラベル 2 つだけ読むのは正しいか: graph.py:393-401 は device と interface 以外を捨てるので、結果は変わらない
  - C の count() が足し漏らさないか: 未登録の頂点を作るのは `_upsert_unregistered` だけ。そこで使うラベル（device / interface / bgp_session / isis_adjacency）は全部 `_LABELS` に入っているので、足し漏れは無い
- **runtime bugs（見た）**
  - botocore の service model で、neptune-graph の `ExecuteQuery` の error shape に `ConflictException`（HTTP 409、senderFault）があることを確かめた（`uv run --group dev python -c "...get_service_model('neptune-graph')..."`）
  - `graph.query()` が Neptune の例外を包み直さないことを確かめた（graph.py:128-134）
  - `_dialect()` が `CALL db.prepareForReplanning()` を書き換えないことを確かめた
- **data loss（見た）**: seed() が消すものは変わっていない。両方のバックエンドとも、置き換えるのは device / interface の未登録だけ
- **security（見た）**: 新しく文字列に埋め込むのは、`_ident()` を通したラベルと、定数の property 名だけ。外部の入力は入らない
- **API compatibility（見た）**: `count()` の戻り値の形は変わらない。Neptune に送る文字列は不変（test_oss.py の golden の照合）
- **missing tests（見た）**: B の 3 件、C の索引・分割・seed_graph.py の 3 件、A の red-green の式
- **テストの実行（自分で走らせた）**: 4 本とも rc 0
  - `uv run --group dev python tests/test_alerts.py` → `通過 138 / 失敗 0`
  - `tests/test_sync.py` → `通過 100 / 失敗 0`
  - `tests/test_graph.py` → `通過 78 / 失敗 0`
  - `tests/test_oss.py` → `通過 171 / 失敗 0`
- **見ていない観点**
  - `bash ops/check.sh` の全体は走らせていない（build.md の出力だけ）
  - 手元の Grafana / OpenSearch / Neo4j を上げての動的な確認はしていない（build.md の検証 4 と 7 の出力を読んだだけ）
  - AWS は触っていない。AOSS の index の shard 数が実際に 2 なのか、ConflictException の打ち直しが本番で効くのかは未確認
  - 認証ありの Neo4j で `db.prepareForReplanning()` に要る権限は、確かめていない
  - build.md の退行の注入（検証 3 / 5 / 6）は、自分では流し直していない

## Must fix
None

## Should fix
- **[correctness + missing tests] trap の発火元が 10 を超えると、件数の少ない機器の trap のアラートが黙って出なくなる。それを縛るテストが無い**
  - 場所: `app/grafana/provisioning/alerting/netops-opensearch.yaml:57`（`tags.sysName.keyword` の `size: '10'`）、yaml のコメントの 22 行目「lab は機器 6 台」
  - 何が起きるか: terms は件数の多い順に上位 10 件しか返さない。trap を送る機器（device map に無ければ送り元の IP）が 11 以上あると、件数の少ない機器は集計から落ち、その機器の trap はアラートにならない
  - 送り元はいま 7 つ: SR Linux の 6 台（`app/containerlab/gen_lab.py:20`）に、`tests/test_alerts.py` の最後の check にある lab.sh の trap-test（sysName は `ACC_VM`）が加わる。コメントの「6 台」より 1 つ多く、余裕は 3
  - 足すとよいもの: lab の trap の送り元の数（gen_lab の SR Linux の数 + ACC_VM）が sysName の size 以下であることを test_alerts.py で突き合わせる check。コメントの数も合わせて直す
  - Should にした理由: いまの lab では 7 ≤ 10 で壊れていない。ただ、lab に機器を足した人には、Grafana 側の trap が一部だけ消えることに気づく手段が無い（エラーも出ない）
- **[design.md との整合性（再発の防止）] A の直しは、プラグイン v2.34.4 の式に寄りかかっている。なのにビルドはプラグインの版を固定していない**
  - 場所: `docker/images/grafana/Dockerfile:12`（`grafana cli ... plugins install grafana-opensearch-datasource` に版の指定が無い）
  - 何が起きるか: `tests/test_alerts.py:223-232` の `terms_buckets` / `bucket_budget_ok` は 2.34.4 の写し。AWS のビルドは、その日の最新のプラグインを入れる。版が上がって、fixed interval も検査するようになったり、見積もりが大きくなったりすると、nwc-trap はまた評価エラーに戻る。そうなってもテストは落ちず、`execErrState: KeepLast` のため rules API の `health` も `ok` のまま
  - 足すとよいもの: Dockerfile でプラグインの版を固定する。design の変更対象外なので、このサイクルでは BACKLOG の候補に回すのでよい（build.md のセルフレビュー #1 でも PM への報告になっている）
  - Should にした理由: 今日ビルドすれば 2.34.4（build.md の `gh api .../releases/latest`）なので、今は壊れない。困るのは次にプラグインが上がったときで、AWS のビルドを打つ人が困る

## Nit
- **[correctness] 時間の予算に、打ち直す apply() の時間が入っていない**
  - 場所: `tests/test_sync.py:538-540`、`:557-558` の予算の式。`app/graph/status_handler.py:32-33` の docstring の「関数の時間は長くて 3.5 秒 × 通知の件数だけ延びる」
  - 中身: どちらも数えているのは待ちの和だけで、打ち直す apply()（Neptune の 1〜5 回の問い合わせ）にかかる時間は入っていない。1 回目が読みの timeout 近くまで延びてから 409 になると、2 AZ の 58.1 秒の見積もりを超えうる
  - design のリスク 4 で受け入れた範囲（超えれば Lambda の再試行で直る）なので、docstring の言い方を「待ちだけで 3.5 秒、打ち直しの問い合わせの時間は別」に直す程度でよい
- **[correctness] 打ち直しの待ちが固定で、揺らぎが無い**
  - 場所: `app/graph/status_handler.py:57` の `CONFLICT_WAITS`
  - 中身: 3 つ以上の呼び出しが同じ頂点を同時に書くと、負けた側同士が同じ時刻に打ち直して、またぶつかりうる
  - 観測された症状は「同じ秒に 2 件」で、2 件なら片方は勝つので問題は出ない
- **[design.md との整合性] yaml のコメントが、推測を事実のように書いている**
  - 場所: `netops-opensearch.yaml:23`「AOSS の index は shard 2 なので」
  - 中身: build.md:29 では、これはエラー文の 13600 から逆算したもので、AWS の shard 数そのものは見ていないとある。コメントにも「（エラー文から逆算。実物は未確認）」を足したほうが、次に読む人が誤解しない
- **[missing tests] 時間の区切りの数を決め打ちしている**
  - 場所: `tests/test_alerts.py:242` の `600 // 30 + 1`
  - 中身: 600 はルールの `relativeTimeRange from: 600` を読まずに書いた値。範囲を広げても、この check は落ちない（interval は `== "30s"` で縛られている）
- **[テストの後始末] `_seed_graph` が、変えた値を元に戻していない**
  - 場所: `tests/test_graph.py:307-332` の `_seed_graph`
  - 中身: 2 つある。`graph.BACKEND` を元の値ではなく `"neptune"` に戻している。`NAME_PREFIX` / `GRAPH_REPLACE` は元の値に戻さず pop している
  - 今は害が無い（このファイルの前提は neptune で、そのあとでこの 2 つの環境変数を読むものも無い）が、ファイルの並びが変わると効いてくる

## 良かった点
- 推測のまま残っていた bucket budget の式を、プラグインのソースの行番号つきで写していた。そのうえで AWS のエラー文の 13600 を再現する check（`terms_buckets([50, 20], 2) == 13600`）を置いた。テストの式と本物がずれていないことを、テスト自体が示している
- `execErrState: KeepLast` があると、rules API の `health` ではエラーが見えない。build.md はこれに気づいて、`/api/ds/query` で前のルールと新しいルールの違いを実測していた。design の確認方法の穴を、証拠つきで埋めている
- C は `_unregistered()` 1 か所で分けている。Neptune の文字列を一切変えないことは、test_oss.py の golden の照合と `_SPLIT4` で機械的に縛っている。Neo4j のスキーマの失敗の 2 種類（Neo4jError は WARNING で進む、DriverError は張ったことにしない）も、索引の側までテストで縛っている
- B は `ClientError` の Code で絞っている。AccessDeniedException を打ち直さないことと、待ちの列 `[0.5, 1.0, 2.0]` をテストで確かめていて、退行の注入でも落ちることを示している

## ユーザーへの質問
- AWS で A を確かめるときの合否の判定について。design.md の検証方法は「rules API の `health` が `error` でないこと」だが、KeepLast のもとでは直っていなくても `ok` になる（build.md の検証 4）。次の AWS の確認では、`alerts[].state` に `(Error` が無いことか、`/api/ds/query` が 200 を返すことを合格の条件にする、でよいか

### PM の確認（fable-5.1）

- Should fix 1（terms の size 10 と送り元の数）: 読んで確かめた。`gen_lab.py` の docstring「6 台とも SNMP の trap」、`lab.sh:49` の `ACC_VM=dc1-host-01`（trap-test の送り元）、yaml の `size: '10'`（57 行目）とコメント「lab は機器 6 台」（22 行目）。送り元 7 ≤ 10 なので今は壊れないが、縛るテストが無いのは事実 → **Should fix のまま（missing tests なので直す）**。直しはエンジニア1 の `fix/grafana-plugin-pin-error-state` の (a) に出した
- Should fix 2（プラグインの版の固定）: `docker/images/grafana/Dockerfile:12` に版の指定が無いことを読んで確かめた → **Should fix のまま**。同じブランチの (a) で Dockerfile の固定と test_alerts.py の版の突き合わせを足す（BACKLOG の候補と同じ行）
- Nit 5 件: 直さない。最終報告に載せる
- 質問への答え: はい。AWS の A の合否は `alerts[].state` に `(Error` が無いこと、または `/api/ds/query` が 200 を返すこと（PM が最後の AWS 検証でこの条件で見る）
- Round 2 は (a) のマージ後。実装が変わるので cold reviewer の 2 回目（完了判定の直前）をそこで呼ぶ
