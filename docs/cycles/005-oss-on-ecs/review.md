# Cycle 005 oss-on-ecs レビューの記録: マネージドを OSS に置き換えた環境を作る

## Round 1

cold reviewer: opus（Agent general-purpose、effort: high）/ 指摘の確認: PM の fable-5.1 / effort: セッションの既定
cold reviewer に依頼した（初回ビルド直後、review.md が無いラウンド）。対象は `d7616d6^..78be1e4`。

# レビュー r01: マネージドを OSS に置き換えた環境を作る（005）

対象: `d7616d6^..78be1e4`（worktree `cycle-005-review`）。正: `docs/cycles/005-oss-on-ecs/design.md`。

## サマリ

- Must fix 0 件、Should fix 0 件、Nit 7 件。
- 実装は design.md の構成と合っている。
  - Kafka は KRaft 3 台を EFS に、OpenSearch はデータ 2 台とまとめ役 1 台、VictoriaMetrics は cluster、Spark は ECS 上の `local[*]`、Neo4j は GDS 入り。
  - 「実装の状態」で「まだ」と書かれている項目（Nautobot の Job → Neo4j）は、実装にも無い。design.md と BACKLOG.md:5 の両方に残っており、黙って抜けているわけではない。
- マネージド版への影響を見た。
  - 環境変数が無いときの挙動はマネージド版と同じ。`tests/test_oss.py` の 1 節がこれを検査している。
  - workflow の output は、OSS の分岐が空ならマネージド版と同じ値になる。
- シークレットの通し方を見た。
  - 値は SSM の SecureString から ECS の `secrets` へ渡し、environment と state には入れていない。
  - `ops/up-common.sh` は 077 の一時ファイルを `file://` で渡している。
- `ops/check.sh` をこの worktree で走らせた（`bash ops/check.sh 2>&1 | tail -40`）。
  - exit code は 0。
  - 末尾の出力は「通過 324 / 失敗 0」と「すべて通過」。
  - fmt と validate はどちらも途中で `die` しなかった。`tail -40` のため、それより前のテストファイルの件数は見ていない。
- check.sh を走らせたあとの `git status --short` では、追跡しているファイルのうち `docs/cycles/005-oss-on-ecs/design.md` が変わっていた（` M`）。
  - 中身は 333 行目の「8. AWS での確認。（まだ）→（済み …）」と、リスク 11・12 の番号の入れ替え。
  - 更新時刻は 20:46。このレビューは design.md を書き換えておらず、check.sh も docs を書かない。レビューと並行して誰かが直したもの。
  - 未コミットなので、下の Nit 7 に入れた。
  - `.terraform.lock.hcl` のシンボリックリンクは、check.sh のあとも変わっていない（git status に出ていない）。
- `build.md` は、始めたときから未追跡で置かれていた。このレビューが作ったものではない。

### 見た観点 / 見ていない観点

見た観点:

- design.md との整合を見た。
  - 「構成」「アプリのコードの切り替え」「実装の状態」「手順」「検証方法」「未確定事項とリスク」と、実装を照らした。
- 正しさを見た。
  - `agent/graph.py` の `_dialect` / `_neo4j_schema` / `count` / `seed`、`workflow/awsio.py`、`graph/status_handler.py`、`ops/seed_graph.py`、`ops/sync-graph.sh`。
- terraform を見た。
  - `terraform/base/core/oss.tf`、`terraform/workflow/*` の差分、`terraform/pipeline/stream` の `msk.tf` の切り出し。
  - `oss/terraform/pipeline/{stream/kafka.tf,analytics/opensearch.tf,analytics/spark.tf,graph/neo4j.tf}` と、シンボリックリンクのルート。
- 運用スクリプトを見た。
  - `ops/common.sh` の `tf_init_root`、`ops/check.sh`、`ops/up-common.sh`、`oss/ops/up.sh`、`oss/ops/down.sh`。
- セキュリティを見た。
  - SSM と ECS secrets、EFS の TLS 強制とマウントターゲット経由の制限、SG の表、OpenSearch の Basic 認証。
  - サプライチェーン（イメージと jar の取得）。
- データの消失を見た。
  - EFS とタスクの一時領域の使い分け、`down.sh` の SSM の消し方（ManagedBy タグで絞る）。
- 足りないテストを見た。
  - `tests/test_oss.py` と `tests/test_oss_ops.py` の範囲。
  - SG の表との完全一致、使ってはいけない `aws_*` のリソース、compose とのイメージの版の一致、`-lockfile=readonly`。

見ていない観点:

- `agent/requirements.txt` の `strands-agents==1.57.2` と `agent/app.py` の差分は見ていない。
  - これは 005 の外の `feat/strands-agent-v2` を merge したもの（ea6cbbd 経由の 01f6046）で、design.md の範囲に無い。別にレビューすべき。
- 一部は流し読みだけ。
  - VictoriaMetrics と Grafana の terraform、`oss/terraform/base/network` まわり、`oss/compose/` のスクリプト。
- AWS では動かしていない。
  - 動作は design.md の「AWS で確かめたこと」（2026-10-07）の記録を読んだだけで、再現はしていない。
- Nautobot の Job → Neo4j は見ていない。design.md で「まだ」と明記されているため。

## Must fix

None

## Should fix

None

## Nit

1. [正しさ] `agent/graph.py:166-173`
   - 何が起きるか。
     - `_neo4j_schema` は「制約を作った」をプロセスごとのフラグ `_cache["schema"]` で覚えている。
     - Neo4j のタスクが入れ替わると、データは一時領域なので消える（design.md 検証 6）。そのあとも、生きている Web、Runtime、温まった Lambda は制約を作り直さない。
     - `ops/sync-graph.sh --oss` を打つまでのあいだ、status の Lambda の `_upsert_unregistered`（`agent/graph.py:580`）は、一意制約の無い状態で MERGE する。
     - 同じ id のアラートが同時に 2 本来ると、未登録の頂点が 2 つできる。その状態で seed の別プロセスが `CREATE CONSTRAINT` を打つと失敗し、`ops/seed_graph.py` の再試行を使い切って同期が止まる。
   - Nit にした理由: 同じ id のアラートが同時に届く場合に限られ、design.md の検証 6 は AWS で通っているため。
   - 直し方の例: `ServiceUnavailable` のあとや、ドライバを作り直したときにフラグを落とす。
2. [正しさ] `workflow/awsio.py:83-91`
   - 何が起きるか。
     - `_neo4j_cypher` は `agent/graph.py:145-151` の `_dialect` のうち `id(x)` の書き換えしか写していない。
     - いま awsio が打つ 2 本のクエリ（`workflow/awsio.py:99`、`101`）は `id()` しか使わないので動く。
     - あとから `` `~id` `` や `AS from` を含むクエリを足すと、Neo4j の構文エラーになる。
   - Nit にした理由: いまのクエリでは起きない、将来の食い違いの話のため。
   - 直し方の例: `_dialect` を共用するか、awsio のクエリが `id()` 以外の Neptune の書き方を含まないことをテストで押さえる。
3. [設計整合] `docs/cycles/005-oss-on-ecs/design.md:213`
   - 何がずれているか。
     - 「格納先の選択」は「無い。いつも 4 つ」と書いている。
     - 一方で `oss/terraform/pipeline/analytics/spark.tf:4`、`55` は `var.sinks` に無い格納先を外し、空になったジョブを作らない。
   - Nit にした理由: 既定値が 4 つ全部なので挙動は design.md どおりで、文書の記述だけがずれているため。
   - 直し方の例: 「既定は 4 つ。`sinks` で絞れる」に直すか、変数を外す。
4. [セキュリティ] `spark/Dockerfile:20-27`
   - 何が起きるか。
     - Maven Central から `curl -fsSLO` で jar を取るとき、sha1 や sha512 を照合していない。
     - HTTPS なので経路での改ざんには強い。一方で、ミラーやキャッシュが壊れた jar を返しても、ビルドは通ってしまう。
   - Nit にした理由: PoC で HTTPS 取得のため、すぐ壊れる経路が無い。
   - 直し方の例: `.sha1` も取って `sha1sum -c` する。
5. [実行時の不具合] `oss/ops/up.sh:266`
   - 何が起きるか。
     - `wheels-oss/` に `.whl` が 1 つでもあると、ダウンロードをまるごと飛ばす。
     - `web/requirements-oss.txt` か `requirements.txt` の版を上げても、古い wheel のまま進み、Web の EC2 でインストールが食い違う。
   - Nit にした理由: マネージド版の `ops/up.sh:781` と同じ作りで、005 が新しく持ち込んだ癖ではないため。
   - 直し方の例: requirements のハッシュを置き場に書き、一致しなければ取り直す。
6. [実行時の不具合] `oss/ops/up.sh:536`、`ops/check.sh:36`
   - `oss/ops/up.sh:536` で起きること。
     - workflow の `aws ecs wait services-stable` は、待ちの上限（既定 10 分）を超えると `set -e` で up.sh 全体が止まり、やり直す手当てが無い。
     - マネージド版の `ops/up.sh:1223` と同じ作り。
   - `ops/check.sh:36` で起きること。
     - `oss/terraform` のルートでも `init` に `-lockfile=readonly` を付けていない。
     - いまは `TF_BASES=(terraform oss/terraform)` の順で、先にマネージド版のルートがこの PC のハッシュを足すので、シンボリックリンクの lock は書き換わらない。今回の実行でも書き換わらなかった。
     - 順番を入れ替えると、`ops/common.sh:36-46` が防いでいる「lock が実ファイルに置き換わる」が check.sh 側で起きうる。
   - Nit にした理由: どちらも、いまの手順では起きないため。
7. [設計整合] `docs/cycles/005-oss-on-ecs/design.md:333`、`424-427`
   - コミット `78be1e4` の時点でずれていること。
     - 手順 8 が「AWS での確認。（まだ）」のまま。13 行目、274 行目、355 行目の「2026-10-07 に 1 を除いて通った」と食い違う。
     - リスクの番号が 12 → 11 の順に並んでいる。
   - いまの状態。
     - この worktree の作業ツリーでは、レビュー中に両方とも直っていた（未コミット）。
   - Nit にした理由: 文書だけの話で、作業ツリーではもう直っているため。
   - 直し方の例: その変更をコミットする。

## 良かった点

- 環境変数が無いときはマネージド版と同じ挙動で、それをテストで押さえている（`tests/test_oss.py` の 1 節）。
  - workflow の output も、OSS の分岐が空ならマネージド版と同じ値。
  - 共用しているコードを壊していないことが機械で確かめられる。
- シークレットが environment にも terraform の state にも入らない。
  - SSM の SecureString を ECS の `secrets` で受けている。
  - Neo4j は entrypoint で `GRAPH_PASSWORD` を `NEO4J_AUTH` に直している。
  - `tests/test_oss.py` が「environment にパスワードが無い」を検査している。
  - `ops/up-common.sh` の 077 の一時ファイルと `file://` で、値がコマンドラインに出ない。
- 守りの仕掛けが具体的。
  - terraform の precondition は、何を打てば直るかを文で返す。
  - `oss/ops/down.sh` は SSM を `ManagedBy` タグで絞って消し、最後に残りを報告する。
  - `terraform/base/core/oss.tf` の EFS は、TLS でない接続とマウントターゲット経由でない接続を拒む。
- テストが構成の取り違えを狙い撃ちしている。
  - SG の表 31 行との完全一致。
  - OSS の木にマネージドのリソース（`aws_msk_*` など）が入らないこと。
  - compose と ECS のイメージの版の一致。
  - `-lockfile=readonly` の付け方。
  - `LAYER_PYVER` と `sync.tf` の runtime の突き合わせ。
- design.md の「未確定事項とリスク」が正直。
  - 公式の記述が無いもの（Fargate 上の OpenSearch、EFS 上の Kafka）と、未確認のもの（並べて立てる、vminsert だけの起き直し）を分けて書いている。
  - AWS で確かめた結果を数値つきで残し、down.sh のあとの残りを名指しで確かめている。
- `ops/seed_graph.py` は、Neo4j が Bolt を開くまで `graph.count()` を 30 秒おきに 10 回待つ。
  - healthCheck が無いこと（リスク 11、BACKLOG.md:10）を、運用の側で吸収している。

## ユーザーへの質問

- レビュー中に作業ツリーの `docs/cycles/005-oss-on-ecs/design.md` が書き換わった（手順 8 を「済み」に、リスクの番号を 11・12 に）。
  - このレビューと check.sh のどちらにもその操作は無い。
  - 意図した並行の編集か確認したい。意図したものなら、コミットすれば Nit 7 は閉じる。

### 指摘の確認（PM、fable-5.1）

- Must fix / Should fix は 0 件なので、再現して直すものは無い。Nit 7 件は直さず一覧に残す（下の例外 2 件は docs だけなので直した）。
- Nit 3（`sinks`）: 読んで確かめた。`oss/terraform/pipeline/analytics/variables.tf:116-119`（マネージド版への symlink）に `sinks` があり既定は 3 つ、`oss/ops/up.sh:448` が 4 つを渡し、`spark.tf:55` が空のジョブを作らない。design.md「実装の状態」の行を「`up.sh` はいつも 4 つを渡す。変数は共用で、手で絞ればその分のジョブは作られない」に直した。
- Nit 7（手順 8 と番号）: cold reviewer が見た未コミットの変更は PM が並行して直したもの（意図した編集）。このラウンドで commit する。
- Nit 1（Neo4j の入れ替え後に一意制約を作り直さない）: 読んだだけで、同時に同じ id のアラートが 2 本来る状況は再現していない。`docs/cycles/BACKLOG.md` に足す。
- Nit 2・4・5・6: 読んだだけ。cold reviewer の「いまの手順では起きない」の判断を据え置き、BACKLOG に足す（2・4・5・6）。

### 完了判定（2026-10-07）

- `git diff --stat`（78be1e4 から）: `docs/cycles/005-oss-on-ecs/design.md | 8 ++++----` だけ。実装ファイルは 1 バイトも変わっていないので、cold reviewer の 2 回目は呼ばない（規定: 実装が不変のラウンドに外部レビューは掛けない。1 回目がこの完了判定のラウンドと同じコードを見ている）。
- `bash ops/check.sh`（この worktree、design.md の修正後）: rc=0、末尾は次のとおり。

```
通過 324 / 失敗 0

すべて通過
```

- AWS の検証 7/8 の結果は design.md「検証方法」の AWS の表と「down.sh のあと」（2026-10-07 に実行。この完了判定では再実行していない）。

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261007-cycle-005-oss-on-ecs-review.html -->
