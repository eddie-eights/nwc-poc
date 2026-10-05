## Round 1

- cold reviewer に依頼した（初回ビルド直後で review.md が無いため）。モデル: opus（general-purpose）。指摘の再現と分類: Opus 5.5 / effort xhigh
- 渡したのは 4 つ（design.md のパス、変更ファイルのパス一覧、未解消 Must fix = なし、書き出し先）。ただし次の 2 点は一覧から外した
  - `agent/proposals.py` と `web/incident_view.py`: ユーザーの許可待ち。「どのツールでも開かない」とだけ足した（実装の説明ではなくユーザーの制約）
  - `docs/cycles/003-…/build.md` とログ 4 本: セルフレビューの結果を含むため
- 返ったあとの `git status --porcelain -uall` は `?? docs/cycles/003-proposals-in-s3tables/review-r01.md` の 1 本だけ

### cold reviewer の結果（review-r01.md をそのまま連結）

# レビュー r01: 修復案を S3 Tables にまとめる（Cycle 003 proposals-in-s3tables）

- 対象: ブランチ `feat/proposals-s3tables`、範囲 `bfdd398..HEAD`（a599c75 まで）
- 基準: `docs/cycles/003-proposals-in-s3tables/design.md` と実際のコードだけ
- 前回の未解決 Must fix: なし（初回）

## サマリ

- 変更は 33 ファイル（+7518 / -637）。うち 5 本は docs/cycles 配下の build.md とログ。
- `agent/proposals.py` と `web/incident_view.py` の 2 本は、指示によりどのツールでも開いていない（diff も取っていない）。この 2 本は、テストを走らせたときに import されただけ。
- 残りのコード、Terraform、テストは読んだ。
- 結論: Must fix は無い。
  - 設計の中心は設計どおりに入っている。
    - 修復案は proposal_events だけ（28 列、seq）。
    - 決定は Web → 決定のキュー → starter → シグナル。
    - ignored の行を残す。
    - NOT_FOUND のときは pending だけを expired にする。
    - put_proposal は、同じ 1 回の追記で前の pending を expired にする。
    - Runtime から Neptune の書き込みを外した。
    - SendMessage は Web のロールだけ。
  - 自分で走らせたテスト 5 本、terraform fmt、validate はすべて通った。
- 残る点は次のとおり。
  - 確かめられなかったもの（Should 2 件）。
    - 除外した 2 本に依存する画面の文言。
    - 設計の実装ステップ 4（`terraform plan`）が未実行。
  - 小さな指摘（Nit 6 件）。

### 見た観点 / 見ていない観点

- 見た観点（自分で実行したもの。出力を根拠にする）
  - テストは worktree の `.venv/bin/python` で走らせた。システムの `python3` は `yaml` / `botocore` が無く import で落ちるため。temporalio は入っておらず、テストが差し替えている。

    | コマンド | 結果 | 終了コード |
    |---|---|---|
    | `.venv/bin/python tests/test_workflow.py` | 通過 318 / 失敗 0 | 0 |
    | `.venv/bin/python tests/test_app.py` | 通過 142 / 失敗 0 | 0 |
    | `.venv/bin/python tests/test_graph.py` | 通過 72 / 失敗 0 | 0 |
    | `.venv/bin/python tests/test_sync.py` | 通過 95 / 失敗 0 | 0 |
    | `.venv/bin/python tests/test_analytics.py` | 通過 473 / 失敗 0 | 0 |
    | `terraform fmt -check -recursive terraform` | 差分なし | 0 |
    | `terraform validate`（workflow、pipeline/analytics、pipeline/graph） | 3 つとも "Success! The configuration is valid." | 0 |

    validate は既存の `.terraform` を使った。init は打っていない。
  - `grep -rn "n:proposal\|update_record" workflow agent web --exclude=proposals.py --exclude=incident_view.py` は 0 件。
  - 走らせたあとも `git status` は clean のまま。
  - 設計との突き合わせ
    - §1〜§7 の表と、検証方法（L259-285）のうちローカルで見られる項目。
    - リスク 1〜13。
    - 変更対象ファイル（L231-237）。
  - correctness（`workflow/worker.py`）
    - 決定のシグナル `decide` と、時間切れ・解消のあとの扱い。
    - `_wait_resolved` / `_flush`: HOLD のあいだの ignored、残り時間の計算、書けなかったときに止めないこと。
    - `put_proposal`: 別の実行のときは再試行しない、自分の再試行なら同じ行を使う、前の pending は同じ追記で expired。
    - `handle_message`: アラートのキューに来た決定を捨てる。
    - `handle_decision`: NOT_FOUND で pending のときだけ expired。ほかの RPC エラーは投げてメッセージを残す。
    - `main` の必須の環境変数。
  - security / IAM
    - 決定のキューと DLQ: SNS の購読が無い。perimeter の DenyOutsideVpc。
    - `web_access`（SendMessage と Athena）が付くのは `local.web_role_name` だけ。
    - Runtime の Neptune は読み取りだけ（graph_writer_role）。
    - ワーカーのタスクロール（DecisionQueue、AuditTable）。
  - 型と互換
    - `audit_rows` の int / None。
    - `decision_from_message` の検査。
    - `rules.PROPOSAL_EVENT_COLUMNS` と tables.tf の列の一致（test_analytics と test_app が検査している）。
    - `.env.example` の差分。
  - 配備
    - `ops/up.sh`（今回は変更なし）の apply の順: analytics は L1253、workflow は L1384。Web の再起動は L1001。
    - Web の設定は `toolkit.Param` が SSM `<PARAM_PREFIX>/<名前>` から読む。値が無いあいだは TTL ごとに引き直す。
    - Web に上げるモジュール（up.sh L964 の `toolkit topology graph proposals`）と、それだけで import できるかのテスト。
- 見ていない観点
  - `agent/proposals.py` と `web/incident_view.py` の中身。テストを通した外から見える振る舞いでしか判断していない。
  - AWS の上での動作（設計の検証方法の AWS 1〜6、リスク 1）。AWS には触っていない。
  - `terraform plan`（リスク 2、実装ステップ 4）。state と AWS が要るため打っていない。
  - Temporal の本物（temporalio が入っていない）。ワークフローはテストの差し替えの上でしか見ていない。

## Must fix

None

## Should fix

- [design][未確認] 承認・却下を押したあとに「承認を送った。反映まで少し待つ」と出るかを確かめられない
  - 設計: design.md L34（合意した決定 6「画面に『送った。反映まで少し待つ』と出す」）、L208（§6「承認を送った。反映まで少し待つ（数秒〜20 秒。更新を押す）」）、検証方法の AWS 2（L281「承認を押すと『送った』の文言が出る」）。
  - 根拠: 文言を出すのは `web/incident_view.py` の `decide_proposal` で、指示によりこのファイルを読んでいない。テストは `tests/test_workflow.py` L1297-1338 だけ。そこで確かめているのは次の 2 つで、`decide_proposal` が返す 1 つ目の値（ボタンの下に出す文言）を見る検査は無い（`grep -rn "反映まで" tests` は 0 件）。
    - `proposals.decide` に渡す引数（`decided == [("p1", "approved", "山田 太郎 (web)")]`）。
    - 選択を空に戻すこと（`r[2]`）。
  - 壊れる条件: 文言が入っていない、または「承認した」のように言い切る文言だったとする。押した人は、反映までの数秒〜20 秒のあいだ一覧が「承認待ち」のままなのを見て、失敗したと思う。もう一度押せば、別の秒の決定として ignored の行が増える（リスク 12）。
  - 分類の理由: 設計の合意済みの決定で、それを守るテストが無い。外れていれば Web で承認する人全員が戸惑う。ただし害は行が増えることまでなので Must ではない。
  - 確認すべきこと:
    - `decide_proposal` が `decide` の戻り値 `status == "sent"` のときに L208 の文言を返すか。
    - テストに `"反映まで" in r[0]` のような検査を足すか。

- [design][未確認] 実装ステップ 4 の `terraform plan`（proposal_events が作り直しになるか）が打たれていない
  - 設計: design.md L253（ステップ 4「`terraform plan` で作り直しになることを確かめる」）、L290（リスク 2「作り直しにならず更新もできない場合は、`ops/up.sh` で消してから作る手順が要る」）。
  - 根拠:
    - build.md L11、L138、L391、L589 に「未実行（AWS を触らない）」とある。
    - `ops/up.sh` はこのサイクルで変わっておらず、消してから作る手順は無い。
    - 自分も plan は打っていない（validate だけ）。
  - 壊れる条件: 12 列の `proposal_events` が残っている既存の環境で up.sh を打つとする。provider がスキーマの変更を作り直しにせず、更新もできなければ、up.sh は 7-4（L1253 の `tf_apply pipeline/analytics`）で止まる。更新が「通るが列が増えない」形なら、28 列で書くワーカーの追記が失敗し、修復案が作れない。
  - 分類の理由: 新しく作る環境には効かない。既存の環境があれば、承認の流れ全体が止まる。設計が「確かめる」と書いた手順が抜けている。
  - 確認すべきこと: 既存の環境があるうちに `terraform -chdir=terraform/pipeline/analytics plan` を打ち、`aws_s3tables_table.proposal_events`（相当のリソース）に `must be replaced` が出るか。出なければ、up.sh に消してから作る手順を足す。

## Nit

- [design][未確認] 設計の grep（L278）のうち、`agent/proposals.py` の分は確かめられない
  - 設計: design.md L278「`grep -rn "n:proposal\|update_record" workflow agent web` が 0 件」。
  - 根拠:
    - 2 本を除いた grep は 0 件（上の表）。
    - `agent/proposals.py` は読めない。
    - 間接的な裏付けがある。`tests/test_graph.py` は `graph` に `get_record` / `update_record` が無いことを検査している。`tests/test_app.py` の `decide` / `list_proposals` / `get_proposal` の検査は、Athena と SQS の差し替えで通っている（通過 142）。
    - ただし `graph.query` に `n:proposal` の Cypher を直接渡す呼び出しが残っていても、これらの検査では落ちない。
  - 分類の理由: テストが外から振る舞いを押さえているので、残っている見込みは低い。設計の検証項目なので、未確認として残す。
  - 確認すべきこと: 許可のある人が、除外なしで L278 の grep を打つ。

- [security] ワーカーのタスクロールに Neptune の書き込み・削除の権限が残っている
  - 根拠: `terraform/workflow/iam.tf` L66-77 の sid `Neptune` に `neptune-graph:WriteDataViaQuery` と `DeleteDataViaQuery` がある。コメントは「書き込みはもう使わないが、外すことは cycle 003 の設計に無いので残している」。ワーカーが Neptune を使うのは `awsio.read_topology`（事前チェックと保守中の判定）の読み取りだけ。
  - 壊れる条件: ワーカーのコンテナや、エージェントの応答から組んだコマンドの経路が乗っ取られたとする。その場合、トポロジと status の頂点を書き換え・削除できる。
  - 分類の理由: 設計 §7 の表（L211-223）は Runtime の書き込みを外すとだけ書いていて、ワーカーは対象外なので設計違反ではない。最小権限の観点の改善。
  - 確認すべきこと: 次のサイクルで Read と GetQueryStatus だけにするか。

- [runtime] リスク 9（analytics を作り直したら workflow も apply し直す）を、up.sh は WORKFLOW を指定しない回では守らない
  - 設計: design.md L297。
  - 根拠:
    - WORKFLOW を指定した回は、同じ up.sh の中で analytics（L1253）→ workflow（L1384）の順に apply するので、設計の「up.sh の 1 回の中で両方が替わる」は満たす。
    - WORKFLOW を指定しない回では、`terraform/workflow` の state が残っていても up.sh は L881-890 でエンドポイントを残すだけで、apply も警告もしない。
    - Web とツールの読み取りの IAM（`terraform/workflow/locals.tf` L131 の `history_table_arns`）はテーブルの ARN を名指ししている。
    - 一方、ワーカーの `AuditTable` は `${audit_bucket_arn}/table/*`（iam.tf L91-93）。古いワーカーは新しいテーブルに 12 列の行を書けてしまう（リスク 9 の前半）。
  - 壊れる条件: スキーマ変更でテーブルが作り直される回に、workflow の state が残ったまま WORKFLOW を指定せずに up.sh を打つ。承認タブと `query_history` が古い ARN を指して AccessDenied になる。古いワーカーは 12 列で書き続ける。
  - 分類の理由: テーブルが作り直されるのはこのスキーマ変更の 1 回だけで、しかも WORKFLOW を指定しない組み合わせのときだけ起きる。設計も手順として書いている。
  - 確認すべきこと: up.sh で analytics の `proposal_events_table_arn` が変わったときに、workflow の apply を促す（または止める）一文を出すか。

- [design] 一覧のクエリの LIMIT が §5 の例と違う（既定 50、上限 100）
  - 設計: design.md §5 の SQL の例（L191）は `ORDER BY event_time DESC LIMIT 100`。
  - 根拠:
    - `tests/test_app.py` L475-477 は既定で `WHERE rn = 1 AND status = ? ORDER BY event_time DESC LIMIT 50` を期待している。
    - 全件では L515 が `LIMIT 100`。
    - `device_id` は内側の WHERE（L476）にある。修復案のどの行も同じ `device_id` を持つ（全項目の行）ので、結果は同じ。
  - 分類の理由: 意味は同じで、件数の既定値だけが例と違う。承認待ちが 50 件を超える PoC は考えにくい。
  - 確認すべきこと: design.md の例を実装に合わせるか、既定を 100 にそろえるか。

- [design] 変更対象ファイルの `web/config.py`（L234）が変わっていない
  - 根拠: `git diff --stat bfdd398..HEAD` に `web/config.py` が無い。Web の承認タブの設定は `toolkit.Param` が SSM（`terraform/workflow/proposals.tf` の `decision_queue_url` と `proposals_read`）から読む。EC2 の user_data は `PARAM_PREFIX=/${name_prefix}` を書く（`terraform/base/core/templates/web_user_data.sh.tftpl` L45）。だから config.py を変えなくても動く。
  - 分類の理由: 機能の抜けは無く、設計の一覧と実際の差だけ。
  - 確認すべきこと: design.md の変更対象から外すか、「設定は SSM から読むので変えない」と注記するか。

- [style] テストの書式の崩れ
  - 根拠: `tests/test_workflow.py` L33 `fake ={"execute_query": …}`（`=` のあとに空白が無い）。
  - 分類の理由: 動作には関係しない。

## 良かった点

- ワークフローの判断の場所がはっきりしている。starter（`handle_decision`）は届けるだけで、効くかどうかはワークフローが決める（`decide` の docstring と実装が一致）。決定を受ける `proposal_id` を調査の前に決めているので、調査中に届いた決定も取りこぼさない（worker.py L271）。
- 時間切れ・解消（`_closed`）のあとに届いた決定を、効いた決定として控えずにログだけにしている。設計の検証項目「expired のあとに承認と却下を送っても行は増えない」をコードとテストの両方で守っている。
- `_flush` は ignored の行を書けなくてもワークフローを止めない。`_wait_resolved` は、HOLD の 24 時間のあいだに届いた決定をその場で行にし、残り時間を `deadline - workflow.now()` で計算し直している。待ち時間が延びない。
- `put_proposal` は前の pending の expired と新しい created を 1 回の追記に入れている。expired だけが入って created が無い、という半端な状態にならない。
- 決定のキューをアラートのキューと分けた理由（SNS に publish できる Grafana / Splunk のロールが決定を流せる）が、events.tf のコメントとテストの両方にある。アラートのキューに来た決定を捨てる検査もある。
- Web に上げるモジュールだけで `proposals` を import でき、import 時に boto3 のクライアントを作らないことをテストしている。EC2 の起動時の ModuleNotFoundError（過去の事故）を前もって防いでいる。

## ユーザーへの質問

None

### 指摘の再現と分類（Opus 5.5 / effort xhigh）

Must fix は 0 件。Should fix の 2 件はどちらも再現を試み、片付けた。

- Should 1「押したあとに『送った。反映まで少し待つ』と出るかを確かめられない」
  - 再現した: `grep -rn "反映まで" tests` が 0 件。`tests/test_workflow.py` の decide_proposal のテストは `decided` と `r[2]` しか見ていなかった。
  - 根本原因: テストが決定の副作用（`proposals.decide` に渡す引数、選択を空に戻すこと）だけを見て、画面に出す文言（戻り値の 1 つ目）を検査していなかった。
  - 直した: `tests/test_workflow.py` L1336-1339。承認（`r`）と却下（`rj`）の両方の戻り値の 1 つ目に「送った」と「反映まで」が入ることを検査する。`incident_view.py` は開いていない（テストから import して実行しただけ）。
  - 確かめたこと:
    - `.venv/bin/python tests/test_workflow.py` → `ok 承認も却下も、押したら「送った。反映まで少し待つ」と出す（…）`、`通過 319 / 失敗 0`
    - 退行の注入（scratchpad の `inject_rv1.py`。import したあとで `decide_proposal` の 1 つ目の戻り値を「承認した」に差し替える）→ `rc 1`、`AssertionError: 承認も却下も、押したら「送った。反映まで少し待つ」と出す（…）`
- Should 2「`terraform plan`（proposal_events が作り直しになるか）が打たれていない」
  - 壊れる条件（作り直しにも更新にもならず up.sh が止まる、または列が増えない）は再現しない。AWS に触らずに plan を打って確かめた。
  - 打ち方（scratchpad の `plan_offline.py`）:
    - provider は worktree に入っている 6.64.0 を `-plugin-dir` で使う（lock も 6.64.0）。
    - 鍵は偽物にし、STS とメタデータは呼ばない設定にした（`skip_*`）。AWS_ の環境変数は渡していない。
    - state には bfdd398 の 12 列の `proposal_events` を置き、いまの tables.tf の 28 列に `plan -refresh=false` を打つ。
  - 出力（抜粋）:
    ```
    old(bfdd398) 12 列 / new(worktree) 28 列
    plan rc 0
    -/+ destroy and then create replacement
      # aws_s3tables_table.proposal_events must be replaced
          ~ metadata { # forces replacement
    Plan: 1 to add, 0 to change, 1 to destroy.
    ```
  - provider のソース（v6.64.0 の `internal/service/s3tables/table.go`）でも、metadata / iceberg / schema / field の各ブロック（L250 / L241 / L231 / L221）と field の name・type・required が `RequiresReplace`。`tables.tf` の `proposal_events` に `lifecycle`（`ignore_changes` / `prevent_destroy`）は無い（`grep -n -E "lifecycle|ignore_changes|prevent_destroy"` が 0 件）。
  - 片付け: 解消。`tables.tf` L131-132 の「作り直しになることは terraform plan では未確認」を、確かめた内容に書き換えた（コメントだけ）。`terraform fmt -check` は差分なし、`tests/test_analytics.py` は `通過 473 / 失敗 0`。
  - 残るもの: AWS 上の plan は打っていない。AWS の検証項目（1〜6）と一緒に未実行のまま最終報告に載せる。作り直しでいまの proposal_events の行が消えることは、設計（リスク 2）と tables.tf の注記のとおり。
- Nit 6 件は直さない。最終報告に一覧で載せる。
  1. 設計の grep（L278）のうち `agent/proposals.py` の分が未確認（ユーザーの許可待ち）
  2. ワーカーのタスクロールに Neptune の書き込み・削除の権限が残っている（iam.tf L66-77）
  3. WORKFLOW を指定しない up.sh ではリスク 9（analytics を作り直したら workflow も apply し直す）が守られない
  4. 一覧のクエリの LIMIT が §5 の例（100）と違う（既定 50、上限 100）
  5. 変更対象の `web/config.py`（design.md L234）が変わっていない（設定は SSM から読むので機能の抜けは無い）
  6. `tests/test_workflow.py` L33 `fake ={` の書式

### テスト（tables.tf のコメントを直したあとに取り直したもの）

- `.venv/bin/python tests/<file>` を 1 本ずつ:

  | テスト | 結果 |
  | :--- | :--- |
  | test_workflow | 通過 319 / 失敗 0 |
  | test_analytics | 通過 473 / 失敗 0 |
  | test_app | 通過 142 / 失敗 0 |
  | test_graph | 通過 72 / 失敗 0 |
  | test_stream | 通過 75 / 失敗 0 |
  | test_sync | 通過 95 / 失敗 0 |
  | test_alerts | 通過 113 / 失敗 0 |
  | test_kb_index | 通過 7 / 失敗 0 |
  | test_lab_debug | 通過 75 / 失敗 0 |
  | test_nautobot | 58 項目すべて通過 |

- `terraform -chdir=terraform/pipeline/analytics fmt -check -diff` は差分なし。`ops/check.sh` 全体（3 つの root の validate を含む）はこのラウンドでは打っていない（実行の許可が 1 回ぶんで、Round 3 で使った）

## Round 2

cold reviewer に依頼した（完了判定の直前の 2 回目。opus、general-purpose。渡したのは design.md のパス、変更ファイルのパス一覧、前ラウンドの未解消 Must fix（無し）、書き出し先の 4 つだけ）。戻ったあと `git status --porcelain -uall` は `?? docs/cycles/003-proposals-in-s3tables/review-r02.md` の 1 本だけ。以下はその本文（review-r02.md を連結した）。

# Cycle 003 修復案を S3 Tables にまとめる（proposals-in-s3tables） cold review Round 2

対象: worktree `feat/proposals-s3tables`（HEAD d8c994e）を `bfdd398` と比べた差分。判断の根拠は design.md と実コードだけ。前回の未解決の Must fix は無し。

## サマリ

Must fix 0 件、Should fix 0 件、Nit 1 件。

design.md の変更一覧（L230-238）、行の作り（L104-124）、`ignored` の行の規則（L165-176）、検証（L266-284）に照らして、実装に食い違いは見つからなかった。Nit の 1 件は、workflow の worker が Neptune の書き込み権限を持ったままになっていること（最小権限。設計の範囲外）。

### 見た観点 / 見ていない観点

見た観点

- design.md への適合
  - 変更一覧の各ファイル: workflow/worker.py、workflow/awsio.py、workflow/rules.py、agent/graph.py、agent/toolkit.py、terraform/workflow（events.tf、proposals.tf、locals.tf、iam.tf）、terraform/pipeline/graph（access.tf、locals.tf）。
  - 変更一覧にあるが差分の無い web/config.py、ops/up.sh、tests/test_analytics.py は、変更が要らないことを確かめた。
    - web/config.py は `toolkit.Param` で SSM から読む。
    - up.sh は analytics を workflow より先に apply する。
    - test_analytics.py:330 は列の順を一般的に検査している。
- 正しさ
  - 「いま」は proposal_id ごとに (seq, event_time) の最大（rules.py:346-355）。design.md L124 と L139 に合う。
  - `ignored` の event_id の形は `<pid>#ignored#<decision>#<decided_at>#<decided_by>` で、design.md L104 に合う。
  - `_closed`（決定なしで終わったあと）に届いた決定は控えず、行にしない。design.md L175 に合う。
  - `ignored` の行は効いた決定があるときだけ書く。書けなければ warning を出して捨てる（worker.py:245-255）。design.md L169-171 に合う。
  - 決定のキュー: NOT_FOUND 以外の RPC の失敗では消さない。読めない本文や決定以外の値は、シグナルを送らずに消す。
- セキュリティ
  - SendMessage は Web のロールだけ（proposals.tf の `web_access`）。キューのポリシーは閉域の Deny だけ。
  - Runtime の Neptune は読み取りだけ（graph/access.tf）。
  - `brief_error` が ARN とアカウント ID を伏せる。
  - Athena のクエリの値は ExecutionParameters で渡す。`ATHENA_TEXT_RE` で幅の広い proposal_id も通す。
- 実行時の不具合・データ損失
  - put_proposal の自分の再試行と古い修復案の expired を 1 回の append にまとめている。
  - `append_proposal_events` は CommitFailedException を 5 回まで読み直す。
  - `_flush` で `record_ignored` がコミット後に再試行を使い切ると、`_row` が進まず、次の行と seq が重なる。design.md L139 が「再試行で同じ seq が重なる」ことを受け入れ、読む側の (seq, event_time) の並べ方で吸収している。「いま」は後から書いた行（event_time が遅い）になるので壊れない。指摘にはしない。
- API 互換・型
  - `audit_rows` の int と timestamptz の変換。`from_table_rows` の戻し。28 列の順。
- テストの不足
  - design.md の検証の項目（L266-271）に対応するテストがあることを、テスト名で確かめた。
- テストの実行（自分で実行した。出力の最終行）
  - `tests/test_workflow.py`: 通過 319 / 失敗 0
  - `tests/test_app.py`: 通過 142 / 失敗 0
  - `tests/test_graph.py`: 通過 72 / 失敗 0
  - `tests/test_sync.py`: 通過 95 / 失敗 0
  - `tests/test_analytics.py`: 通過 473 / 失敗 0
  - まとめて走らせたシェルは exit code 0。zsh の PIPESTATUS が空で、ファイルごとの exit code は取れていない。根拠は上の「通過 / 失敗」の行。
- Terraform
  - `terraform -chdir=terraform/workflow`、`terraform/pipeline/graph`、`terraform/pipeline/analytics` で `fmt -check -diff` と `validate` が通った。
  - 実行の前後で `git status --porcelain` は空のまま。

見ていない観点

- agent/proposals.py と web/incident_view.py の中身
  - ユーザーの許可待ちのため開いていない。
  - これらを import する test_app.py と test_workflow.py の結果でだけ、間接的に見た。
  - Athena のクエリ（design.md L186-193）と画面の表示は、コードで確かめていない。
- AWS の上での動き
  - Athena、SQS、VPC エンドポイント経由の到達。
  - Neptune Analytics の `neptune.algo.*` が ReadDataViaQuery だけで動くか。
  - PyIceberg の S3 Tables への実際の append。
- `terraform plan`（テーブルの作り直しが起きるか）。
- docs/*.md の文言。
- Web のロールのインラインポリシーの合計サイズ
  - IAM のロールごとの上限は 10,240 文字。
  - 載っているポリシー: base/core/web.tf:32 と :61、agent/runtime.tf:202、pipeline/stream/access.tf、pipeline/graph/access.tf、workflow/proposals.tf:32 と :60。
  - `web_access` が増えた分で上限に届くかは測っていない。

## Must fix

None

## Should fix

None

## Nit

- [security] terraform/workflow/iam.tf:66-77: workflow の worker のロールが `neptune-graph:WriteDataViaQuery` と `DeleteDataViaQuery` を持ったままになっている。いまの worker が Neptune に打つクエリは workflow/awsio.py:68-75 の `read_topology` の 2 本（MATCH ... RETURN）だけで、書き込みは使わない。worker の権限で Neptune のトポロジや status を消せる状態が残っている。design.md L74 と L221 で外すと決めているのは Runtime の書き込みだけで、worker については何も書いていない。設計に食い違うわけではなく、壊れる入力も無いので Nit（コメントにも「設計に無いので残している」とある）。

## 良かった点

- design.md の検証の項目ごとに、テスト名で対応が追える。
  - 時間切れのあとの決定は行を増やさない。
  - 承認のあとに別の名前で却下すると `ignored` が 1 行。
  - 同じ承認の重複配達では行を増やさない。
- 決定のキューは SNS につながない独立のキューで、SendMessage は Web のロールだけに付いている。「チャットから承認できない」が IAM でも守られている（チャットのツールに decide が無いこともテストで押さえている）。
- エラー文の ARN とアカウント ID を伏せる `brief_error` を、Athena と SQS の両方の失敗に通している。
- `handle_decision` は、Temporal に届かない（NOT_FOUND 以外）ときに決定のメッセージを消さない。直らなければ DLQ に回るので、決定が黙って消えない。
- `ignored` の行を「打つ・確かめる」のあとに書く順にしていて、lab への適用を待たせない（design.md L169）。
- Runtime の Neptune は読み取りの権限だけになり、agent/graph.py も読み取りの関数しか残していない（「修復案の頂点を読み書きする関数は無い」をテストで確かめている）。

## ユーザーへの質問

- workflow の worker のロールから Neptune の書き込み（WriteDataViaQuery / DeleteDataViaQuery）を外すのは、このサイクルで入れるか、次のサイクルに回すか。いまのコードは読み取りしか使っていない。

### 指摘の再現と分類（Opus 5.5 / effort xhigh）

Must fix 0 件、Should fix 0 件。Nit の 1 件は Round 1 の Nit 2 と同じもの。

- Nit「worker のロールに Neptune の書き込み・削除の権限が残っている」
  - 再現した: `grep -n -E "WriteDataViaQuery|DeleteDataViaQuery|設計に無い" terraform/workflow/iam.tf` で L67（「外すことは cycle 003 の設計に無いので残している」）、L72、L73。
  - 直さない。外すかどうかは設計の範囲外なので、勉強用に回す。cold reviewer の「ユーザーへの質問」も同じ件。
- 「見ていない観点」のうち `agent/proposals.py` と `web/incident_view.py` は、ユーザーの許可を得て自分で読んだ。結果は下の項目 6・9 と Round 1 の Nit 1。

### 未確認だった項目

- Round 1 の Nit 1「design.md L278 の grep のうち agent/proposals.py の分が未確認」
  - 解消した: `grep -rn "n:proposal\|update_record" workflow agent web; echo "rc=$?"` → `rc=1`（0 件）。
- 項目 6「ignored の行が『いま』になったとき、画面に何が出るか」（design.md L168 で未確認）
  - 一覧は proposal_id ごとに最新の 1 行だけを出す（agent/proposals.py L60-62 の `row_number() … WHERE rn = 1`）。ignored の行が付いても、画面の行は増えない。
  - 一覧（web/incident_view.py L48 まわり）と詳細（L64-70）には event と detail の欄が無い。`grep -n -E "event_detail|\"event\"|'event'|detail" web/incident_view.py` でヒットしたのは L56 の関数名 `proposal_detail` だけ。
  - そのため、ignored の行が最後に来ても、状態と決めた人は効いた決定のまま。変わるのは「更新」の時刻だけで、効かなかった決定の中身は画面に出ない。見られるのはエージェントの `list_proposals`（COLUMNS に event がある）からだけ。
  - design.md §6 の「表と詳細の項目は変えない」とは合う。ただし L168 の「画面の履歴には 1 行増える」は実装と違う。design.md の文言を直すよう勉強用に回す（Nit。壊れる入力は無い）。
- 項目 9「名前に `'` が入っていても読めるか」
  - 読む側: Athena に渡す値は proposal_id・device_id・status の 3 つだけ（agent/proposals.py L97 `params = [v for v in (by_id, device_id, status) if v]`）。decided_by は条件に使わない。詳細の画面は `html.escape` を通す（web/incident_view.py L64、L70）。
  - 書く側: tests/test_workflow.py L306-315 が `O'Brien (web)` で event_id と detail を検査している（check.sh の test_workflow は 319 / 0）。
  - 問題なし。

### Round 1 の Should の再確認（main e02648a を取り込んだあと）

- Should 1: `.venv/bin/python scratchpad/inject_rv1.py` → `rc 1`、`AssertionError: 承認も却下も、押したら「送った。反映まで少し待つ」と出す（…）`。注入しないときは check.sh の test_workflow が `通過 319 / 失敗 0`。
- Should 2: `.venv/bin/python scratchpad/plan_offline.py` → `old(bfdd398) 12 列 / new(worktree) 28 列`、`plan rc 0`、`# aws_s3tables_table.proposal_events must be replaced`、`Plan: 1 to add, 0 to change, 1 to destroy.`

### main の取り込みと check.sh

- `git merge-tree --write-tree --name-only HEAD e02648a` → 衝突なし。`git merge --no-ff e02648a` で取り込んだ（ユーザーの許可による。push はしていない）。
- `ops/check.sh`（取り込んだあと。最後の行は `すべて通過`）:

  | 項目 | 結果 |
  | :--- | :--- |
  | terraform fmt -check -recursive | 差分なし |
  | 9 つのルートの validate | すべて OK |
  | ops スクリプトの構文 | 構文エラーなし |
  | test_app | 通過 142 / 失敗 0 |
  | test_graph | 通過 72 / 失敗 0 |
  | test_stream | 通過 75 / 失敗 0 |
  | test_sync | 通過 95 / 失敗 0 |
  | test_analytics | 通過 484 / 失敗 0（main の splunk-cluster のテストを含む） |
  | test_workflow | 通過 319 / 失敗 0 |
  | test_alerts | 通過 128 / 失敗 0 |
  | test_kb_index | 通過 7 / 失敗 0 |
  | test_lab_debug | 通過 75 / 失敗 0 |
  | test_nautobot | 58 項目すべて通過 |

### 残るもの（最終報告に載せる）

- AWS 上の検証（design.md L279-284 の 1〜6）と、AWS 上の `terraform plan` は未実行。
- Nit: Round 1 の 2〜6、Round 2 の項目 6 の design.md の文言。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261005-cycle-003-proposals-in-s3tables-review.html -->
