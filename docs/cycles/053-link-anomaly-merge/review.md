# 053 のレビュー（1 本の回線断で修復案を 1 件にまとめる）

## Round 1

- レビューした人: PM（opus-5.5 / effort: high）と cold reviewer（opus / effort: xhigh。初回ビルドの直後なので依頼した）
- 対象: 92c99ad（エンジニアの Round 1）+ 1defa7b（PM の修正）。ベースは 4f29031

### セルフレビューの Should fix の扱い（build.md の Round 1 から）

- S-1（HOLD が明けたあと、もう片方の端の繰り返しの通知で 2 本目が起きる）: Should fix → **設計どおり（受け入れ）に格下げ**。
  - 根拠: HOLD 明けに次の通知で調べ直すのは `app/temporal/worker.py` の `run` のコメント（「過ぎたら閉じて、次の通知で調べ直す」）どおり。Splunk の通知（starts_at が検索ごとに変わる）は 053 の前から同じ端でも 2 本目を起こす。
  - 実行: `.venv/bin/python scratchpad/s1_base.py <テスト>`（テストのファイルを runpy で読み込み、`latest_proposal` が leaf の T0 と spine の T0+60 の修復案だけ rejected を返す状態で handle_message に 3 通渡す）

    ```
    4f29031（053 の前）: tests: 457 checks passed
    grafana leaf repeat (starts_at T0) -> started []
    grafana spine repeat (starts_at T0+60) -> started []
    splunk leaf next search (latest(_time) after HOLD) -> started ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']
    92c99ad（053）: tests: 472 checks passed
    grafana leaf repeat (starts_at T0) -> started []
    grafana spine repeat (starts_at T0+60) -> started ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']
    splunk leaf next search (latest(_time) after HOLD) -> started ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']
    ```

  - 053 で増えるのは Grafana のもう片方の端の 1 回だけ（2 本目の修復案 `leaf#T0+60` ができると以後は飛ばす）。053 の前は回線断のたびに最初から 2 本だったので、本数は増えない。design.md の「未確定事項とリスク」に足した（1defa7b）
- S-2（Neptune を読めないとき、resolved も配り直しになり 5 回で DLQ）: 設計どおり（受け入れ）。b の端の resolved を a の端の id に読み替えるにはトポロジが要り、読まずに送ると b の端の解消が届かないまま消える。SQS の配り直しで一時的な停止をしのぐ。design.md の「未確定事項とリスク」に足した（1defa7b）
- 範囲外の報告:
  - 「Neptune を読めないときは起こす」の 3 行（`docs/workflow.md:65`、`docs/architecture/resources/temporal.md:96`、`docs/nautobot.md:230`）は 053 の前から誤り（4f29031 の `start_for` も `read_topology` の例外を投げる）。コードどおり「起こさず配り直させる（5 回で DLQ）」に直した（1defa7b）
  - 「既知の不具合」を指す 4 行は、f9a9498 / e645ecc で直して表から消した件を指したまま（`git log -S` で確認）。053 の範囲外なので QUEUE に積んだ
  - design.md の `app/graph/graph.py` → `app/agentcore/graph.py`、troubleshooting.md の空の表は直した（1defa7b）
- `bash ops/check.sh` → rc=0、「すべて通過」（1defa7b の前の作業ツリーで実行）

### cold reviewer（review-r01.md）

# 053 cold review Round 1（1 本の回線断で修復案を 1 件にまとめる）

対象: `4f29031..1defa7b`（18 files changed, 329 insertions(+), 25 deletions(-)）。レビュアーは design.md と実際のコードだけを根拠にした。

## サマリ

- 実装は design.md の手順 1〜6 と T-1〜T-8 に沿っている。
  - `rules.link_anomaly` は `app/temporal/rules.py:277-293`。
  - `handle_message` が 1 通につき 1 回トポロジを読んで読み替えるのは `app/temporal/worker.py:393-404`。
  - `start_for(client, alert, topology=None)` は `worker.py:345-370`。
- 自分で打ったテスト。
  - `uv run --directory <worktree> --group dev --group web python tests/test_workflow.py` は `通過 472 / 失敗 0` で、終了コードは 0。「053」で始まる check は 15 件とも ok だった。
  - `tests/test_alerts.py` は `通過 168 / 失敗 0`、`tests/test_oss.py` は `通過 177 / 失敗 0`。
- 赤 → 緑も自分で確かめた。053 の前の `handle_message`（`4f29031` の版）をメモリ上で `worker` に差し戻して 053 のテストを流すと、T-5 で次のように落ちる。
  ```
  RAISED AssertionError 053 T-5 handle_message: ... （いま: 起こした id ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1', 'investigate-dc1-spine-01#link_down#ethernet-1/3']、read_topology 2 回）
  ```
- 検証の対象（回線の辺の向き）。
  - 回線の辺を書くのは `app/agentcore/graph.py:366` の `_link_row` だけで、`a < b` に揃えている。
  - 静的トポロジ（`app/agentcore/data/topology.json`）の 12 本は、どれも IF が空でなく、端の重複も無く、`a < b` だった。
  - そのため「a の端（機器名の順で前）」という design.md の前提は、マネージド版でも OSS 版（`awsio._dialect` で `id(x)` → `x.id`）でも成り立つ。
- 結論。Must fix は 0、Should fix は 0、Nit は 9。
  - design.md の「未確定事項とリスク」で受け入れた件（閉じたあとに遅れて届くもう片方の firing、HOLD 明けの 2 本目、Neptune を読めないときの resolved の配り直し、入れ替えの間の古い id）は、指摘に上げていない。

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合
  - 手順 1〜6、T-1〜T-8、id の形と通知の本文を変えないこと。
  - 「未確定事項とリスク」がコードどおりに書かれているか（とくに `HOLD_MINUTES` と Neptune を読めないとき）。
- correctness
  - b の端の firing / resolved が a の端の id に読み替わること。
  - a の端が来たときに id・機器・IF が変わらないこと。
  - 空の IF の行を照合しないこと。
  - detail を 1000 文字で切ること。
  - 入力の dict を書き換えないこと。
- runtime
  - トポロジの読み取りは 1 通につき 1 回。link_down の無いメッセージでは読まない。
  - 読めないときは例外を投げ、`starter_queue` がメッセージを消さない。
- data loss
  - Neptune が止まっても、メッセージは SQS に残って配り直される（5 回で DLQ）。
- API の互換
  - `start_for` の第 3 引数は既定値付き。`app/` の中の呼び手は `worker.py:404` だけ。
- security
  - detail の頭に入る機器名と IF 名は、Neptune（Nautobot から同期、管理者が書く値）から来る。エージェントのプロンプトに新しく外部の文字列が入る経路は無い。
- missing tests
  - T-1〜T-8 の有無と中身。
- docs
  - design.md が挙げた 8 か所と、PM の修正で直した 3 か所（workflow.md、temporal.md:96、nautobot.md）。
  - QUEUE.md の追加行。

見ていない観点:

- AWS での動作。実物の Neptune Analytics / Neo4j にも、Grafana と Splunk の実際の通知にも当てていない。
- `bash ops/check.sh`。`terraform init` が worktree に書き込む恐れがあるので打っていない。「すべて通過」は build.md と commit の記載で、自分では確かめていない。
- SR Linux の実際の trap の ifName と、Neptune の IF 名の表記の一致。
- Nautobot の機器名の大文字・小文字の揺れ。照合は大文字・小文字を区別し、`graph.py` も正規化しない。揃っていなければ、053 の前と同じ端ごとの動きに戻るだけ（`maintenance_hold` も同じ前提）。
- `tests/` の残りの 13 本。`app/temporal/` を読むのは `test_workflow`・`test_alerts`・`test_oss` のほかに `test_agentcore`・`test_analytics`・`test_lab_debug`・`test_oss_ops`・`test_sync` があるが、これらは打っていない。

## Must fix

None

## Should fix

None

## Nit

- [docs] `docs/architecture/resources/temporal.md:35`
  - 「Neptune Analytics | トポロジ（事前チェックと、保守中の機器の判定に使う）」のままで、053 で starter が回線の両端のまとめにも読むようになったことが書かれていない。同じファイルの :96 と :125、`awsio.py:105` の docstring には書いてある。
  - 読む人が依存先の表だけ見ると、Neptune が止まったときに resolved まで配り直しになる理由を辿れない。動作には影響しないので Nit。
- [docs] `docs/troubleshooting.md:501-505`
  - 節の頭（:501）は「まだ直していないもの。直したら、その行を消す」のまま。:503 は「いまは無い … 直したもの:」と続き、:505 に直した件を残している。
  - 節の決まり（直したら消す）と中身（直したものを残す）が食い違う。読み手が迷う程度なので Nit。
- [correctness] `app/temporal/rules.py:290`
  - 回線の行の `b_if` が None でも、a の端の照合は通る（:289 は b の端の照合にだけ `b and b_if` を求める）。そのとき detail の頭が `None` の文字列になる。
  - 自分で打った結果は `回線 x e1 ↔ y None（x e1 側の通知）。d`。
  - id と機器・IF は正しく、表示だけの問題なので Nit。
- [correctness] `app/temporal/rules.py:285-292`
  - 同じ `(機器, IF)` が 2 本の回線の行に出ると、links の先頭から最初に当たった行に読み替える。そのため Neptune の返す順で id が変わる。
  - 自分で打った結果。行の順を入れ替えると、同じアラートが `p#link_down#e9` と `q#link_down#e8` に分かれた。
  - いまのトポロジ（12 本）には重複が無く、壊れたデータでしか起きないので Nit。build.md のセルフレビューの N-1 と同じ。
- [runtime] `app/temporal/worker.py:398`
  - link_down を含むメッセージでは毎回トポロジを読む。同じ `first_seen` の修復案が既にあるとき（`start_for` の :353 で飛ばす繰り返しの通知）も、resolved だけのメッセージでも読む。
  - 053 の前は、どちらも Neptune を読まなかった。Grafana の繰り返しの通知と Splunk の毎回の通知の数だけ、Neptune の問い合わせが増える。
  - design.md が 1 通 1 回の読み取りと resolved の配り直しを受け入れているので Nit。
- [missing tests] `tests/test_workflow.py:2111-2183`
  - 1 通の中に「leaf の firing」と「spine の resolved」が混ざるケースの check が無い。
  - いまのコードは同じ id に start → signal の順で送るので、起こしたワークフローがすぐ解消扱いになる。design.md の「どちらの端の resolved でも閉じる」に沿った動きだが、テストで固定されていない。
  - Grafana は端ごとに別の通知（group_by）なので、起きにくい。そのため Nit。
- [missing tests] `tests/test_workflow.py:216-248`
  - `b_if` が None で a の端が当たる行の detail の頭（上の rules.py:290 の件）を見る check が無い。T-3 が見ているのは「b の端を照合しない」ことだけ。Nit。
- [tests] `tests/test_workflow.py:2176`
  - 053 の塊のあと、`awsio.read_topology` は `_la_topo` を返す差し替えのまま残る。いまは後ろのテストが read_topology を使わないので害は無い。
  - 後ろに足したテストが知らずにこの差し替えを踏むと、原因を追いにくい。ほかの塊（:197、:2050）も同じ書き方なので Nit。
- [保守性] `app/temporal/worker.py:345`
  - `start_for` を単体で呼ぶと `link_anomaly` を通らない（読み替えは `handle_message` の :400 だけ）。いまの呼び手は :404 だけなので、動作には影響しない。
  - docstring は「topology を受け取る」とだけ書き、「読み替えは呼び手の責任」と書いていない。build.md のセルフレビューの N-4 と同じなので Nit。

053 の範囲外（053 の前から同じ）:

- [docs] `tests/test_workflow.py:5`
  - docstring は「実行は python3 tests/test_workflow.py（依存は無い）」のまま。実際は `app/agentcore/topology.py` の `load_static` が yaml を import するので、素の `python3` では `ModuleNotFoundError: No module named 'yaml'` で落ちる（自分で打って確かめた）。
  - ops/check.sh と同じ `uv run --group dev --group web` なら通る。053 が入れたものではないので、件数に数えていない。

## 良かった点

- `link_anomaly` は入力の dict を書き換えず（`{**alert, ...}`）、link_down 以外とトポロジに無い target は元の dict をそのまま返す。純粋な関数として T-1〜T-4 で固定されている。
- トポロジを 1 通につき 1 回だけ読み、読み替えと `maintenance_hold` に同じ値を渡す。そのため、読み替えたあとの a の端からも相手の端（spine）の保守中を数えられる（T-7）。どちらの端が保守中でも、起こさない判定が対称になった。
- Neptune を読めないときの扱いは、firing も resolved も例外にしてメッセージを残す形（T-8）で固定された。PM の修正で、053 の前から古かった docs の 3 行（「起こす」→「起こさず配り直させる」）もコードに揃った。
- id の形・`workflow_id`・`proposal_id`・通知の本文を変えずに、読み替えを starter の入口 1 か所に閉じ込めた。下流（ワークフロー、S3 Tables、Web）の変更が要らない。
- 赤 → 緑（T-5）が build.md に記録されている。退行の注入（M1〜M7）でテストが落ちることも記録されている。T-5 の赤はレビュアーも別の手順で再現できた。

## ユーザーへの質問

None

### 完了の判定（PM）

- cold reviewer は Must 0 / Should 0 / Nit 9。Must が 0 なので 2 回目は呼ばない
- Nit 9 件と範囲外 1 件は直さない。docs の 3 か所（`temporal.md:35`、`troubleshooting.md` の「既知の不具合」の節の頭、`tests/test_workflow.py:5`）は QUEUE に積んだ
- S-1 の再現を 1defa7b で取り直した（格下げの根拠が変わっていないこと）

  ```
  tests: 472 checks passed in .../pm-042/tests/test_workflow.py
  grafana leaf repeat (starts_at T0) -> started []
  grafana spine repeat (starts_at T0+60) -> started ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']
  splunk leaf next search (latest(_time) after HOLD) -> started ['investigate-dc1-a-leaf-01#link_down#ethernet-1/1']
  ```

- `bash ops/check.sh`（1defa7b）→ rc=0、最後の行「すべて通過」
- 見た観点: design 整合性 / correctness / security / runtime bugs / data loss / API compatibility / missing tests（cold reviewer の「見た観点」と PM の S-1 の再現）
- 見ていない観点: AWS での動作（実物の Neptune Analytics と Grafana / Splunk の通知）、SR Linux の trap の ifName と Neptune の IF 名の一致、type safety（型の検査は無い）
