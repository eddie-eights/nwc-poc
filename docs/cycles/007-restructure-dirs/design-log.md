# Cycle 007 restructure-dirs 設計の経緯

## Round 0（2026-10-08）要件を詰めた

### ユーザーと決めたこと（会話。プランモードの `purring-prancing-hamster.md` で確認済み）

- 並べ直しは独立したサイクルでやる（移動と新規作成の diff を混ぜない）。順番は 006（手元の compose）→ 007（この並べ直し）に入れ替えた（2026-10-08 午後）
- 改名: `web/` → `dashboard/`（「web は dashboard の方が分かりやすい」）、`lab/` → `containerlab/`、`agent/` → `agentcore/`、`workflow/` → `temporal/`
- `ops/ tools/ tests/` はアプリではないので `app/` に入れない
- `docker/images/<名前>/` には Dockerfile だけ。表記は `IaC`
- 版は「LTS があるものは LTS 系列の最新、無いものは最新の安定版」。このサイクルで上げるのは Grafana と Telegraf（Splunk はイメージがあれば）。Redis 8 / EMR 7.14 / Iceberg 1.12 は別で済んだ。PostgreSQL 18 / Spark 4 / Python 3.14 は据え置き
- 残りの判断は「一旦君に任せる。違うところが出てきたらまた修正依頼する」

### PM が置いた決定

| 質問 | 推奨 | 決定 |
|---|---|---|
| Q1 `oss/compose/`（OSS 版の部品を 1 つずつ確かめる使い捨て。Kafka / OpenSearch / VM / Neo4j / Spark の jar）をどうするか | 消すか `docker/` に寄せるか迷った | **`oss/compose/` に残し、相対パス 3 か所だけ直す。** 理由: `tests/test_oss.py` が `oss/compose/*/Dockerfile` と compose.yaml を読んでいて、消すとテストごと消す diff になり「パスだけ動かす」から外れる。`docker/compose/` と役目が重なる（手元で OSS の部品を確かめる）ので、消すかどうかは BACKLOG に出して別に決める |
| Q2 `docker/images/<名前>` の名前 | ECR のイメージ名（`agent` `worker` …）か app の名前か | **app の名前**（`agentcore` `temporal` `grafana` …）。`app/<名前>/` と 1 対 1 で対応し、エンジニアが context を間違えない。ECR のイメージ名はいまのまま（`<prefix>-agent` `<prefix>-worker`） |
| Q3 compose のファイル名 | BACKLOG の行は `docker/compose.yml` | **`docker/compose/compose.yaml`**（006 のまま。Docker の推奨名で、`.env.example` と up / down / check / lab.sh と README を同じディレクトリに持てる） |
| Q4 `kb-docs/` の行き先 | `app/resources/` | **`app/resources/`**（プランの表のとおり。`docs/architecture/resources/` と名前が重なるが、片方は KB の資料、片方は設計の資料で、階層が違う） |
| Q5 Terraform の `repo_root` | 判定式を残して段数だけ変える | **固定の `"${path.module}/../../../.."`。** マネージド版も OSS 版も根から 4 段になり、`pyproject.toml` の有無で見分ける理由が消える。式が消えるので `tests/test_oss.py:923-926` の照合も固定の式に |
| Q6 シンボリックリンクの貼り直し | プランでは「2 段 / 3 段 / 4 段に貼り直す」 | **段数は変えず、`terraform/` の直後に `aws-managed/` を挟むだけ。** 棚卸しで、リンク元とリンク先が両方 `IaC/terraform/` の下に来るので深さが変わらないと分かった（プランの段数の記述は誤り） |
| Q7 `dir_tag` に Dockerfile を混ぜる方法 | 呼び元で Dockerfile を一時ディレクトリへ写す案 | **`dir_tag <版> <ディレクトリ> [ファイル...]` と引数を足す。** 写す案は 8 か所に同じ処理が散る |
| Q8 001〜006 のサイクルの文書 | 置換表でまとめて書き換える | **書き換えない。** 当時の木の記録で、`git log` と同じ扱い。`docs/development.md` に 1 行の注記。書き換える対象を「いまの木を説明している文書」に限ると、docs の作業が 400 行から 250 行ほどに減る |
| Q9 Terraform の state の移し方 | 自動化のスクリプトを足す | **手順を `docs/deploy.md` に書き、実行は PM がユーザーに出す。** リソースが全部消えていて state は空に近く、1 回きりの作業にスクリプトを足さない |
| Q10 版上げを別サイクルにするか | 別サイクル | **このサイクルの commit 3 に入れる**（ユーザー確認済みの範囲。`dir_tag` の変更で全イメージのタグが変わるので、版上げを同時にしてもビルドし直しの回数は増えない） |

### 却下した案

- `ops/` を `IaC/` の下へ: `ops/` は Terraform だけでなく Docker のビルドと lab の S3 同期と SSM Run Command を束ねるので、IaC ではない
- `oss/ops/` を `IaC/terraform/oss/ops/` へ: 上と同じ。`oss/` は「OSS 版の運用」のままにする
- `.dockerignore` を足す: いま無くても動いており、「中身を変えない」から外れる
- `jars/ wheels/` を `docker/` の下へ: gitignore 対象のビルド成果物で、`ops/up.sh` と `oss/ops/up.sh` の置き場の既定値を変えるだけの価値が無い

## Round 1（2026-10-08）実装（エンジニア2）で design.md と現物が食い違ったところ

止めずに次のとおりにして進めた。

| design.md | 現物 | どうしたか |
|---|---|---|
| `git ls-files` 434、シンボリックリンク 90 | 436、92（`IaC/terraform/oss/` のリンクが 2 本多い） | 数だけの違い。92 本を全部貼り直し、壊れたリンク 0 を確かめた |
| 根に残すファイルの一覧（手順 4 の `ls`） | `deploy.env.example` `GLOSSARY.md` `uv.lock` `.python-version` もある | そのまま根に残した |
| `relink` の例（`${link/terraform\//…}` の置換） | macOS の bash 3.2 / zsh で置換が効かない | `sed` でパスを作って貼り直した |
| 落とし穴 2（zip に入れる名前が変わる） | `workflow/gateway.tf` の `tools_files` はキーがパス、値が zip の中の名前 | キー（`app/agentcore/toolkit.py` など）だけ変え、値は変えていない。zip の中身は同じ |
| OSS の `pipeline/graph/sync.tf` の `repo_root` の例が `../` 4 つ | `IaC/terraform/oss/pipeline/graph` は根から 5 段 | `"${path.module}/../../../../.."`（5 つ）にした。マネージド版の `sync.tf` はもともと 5 段で直書き。`tests/test_oss.py` で両方の段数を実パスで確かめる |
| OSS の `sync.tf` のコメント「マネージド版より 1 つ深い」 | 並べ直しで深さがそろった | コメントを「深さはどちらも同じ」に直した |
| 書き換える docs に `docs/verification/*.md` | 日付付きの実行記録で、当時のコマンドの出力（例: `20261008-oss-aws.md` の「NG: oss/terraform/base/core が消えなかった」）をそのまま載せている | **書き換えない**（001〜006 のサイクル文書と同じ扱い）。`docs/development.md` の注記に `docs/verification/` も入れた |
| 検証 13 の grep が 0 件 | 0 にならない。残るのはリポジトリのパスでないもの: S3 の prefix `web/` と `web/data/`、Nautobot のロググループのストリーム `web/`、EC2 の `/opt/<prefix>-web/` と `/var/lib/${name_prefix}-web/tmp`、`tests/test_app.py` のロール名 `nwc-web/i-0abc` | 例外として残した（build.md に grep の生ログ） |
| 検証 13 の grep | 名前の直後に `/` が要るので、末尾に `/` の無い書き方（`` `oss/terraform` `` や「`local/compose` の README」）を拾わない | 別の grep（`(^|[^/A-Za-z0-9_.-])(oss/terraform|local/compose|local/)`）も打って直した。残るのは `tests/test_alerts.py` の Splunk のアプリの `local/`（リポジトリのパスでない）だけ |
| `dir_tag` の引数を変えるのは commit 3 | commit 2 で `splunk/` などが無くなるので、`dir_tag "$V" splunk` のままだとタグが作れない | commit 2 でディレクトリの引数を `app/<名前>` に、ビルドを `-f docker/images/<名前>/Dockerfile app/<名前>/` に変えた。Dockerfile をハッシュに混ぜる（追加の引数）のは commit 3 |
| check.sh の中身を見るテスト（`test_alerts` `test_analytics` `test_workflow`） | ディレクトリごとの名前（`agent` `web` …）を期待していた | `app` を期待するように直した（check.sh の `find` の対象が `app/` にまとまったため） |
| compose の `build.context` を見るテスト（`tests/test_local_compose.py`） | `"../../telegraf"`（末尾の `/` 無し）と比べていて、機械的な置換で拾えなかった | `context` が `../../app/<名前>`、`dockerfile` が `../../docker/images/<名前>/Dockerfile` で、その Dockerfile が実在することを見る関数にした |
| 一時ディレクトリで `ops/up.sh` の断片を動かすテスト（`test_sync` `test_analytics`） | `up.sh` が `terraform/<root>/terraform.tfstate` を直に見ていた | `up.sh` を `$TF_DIR/<root>/terraform.tfstate` にしたので、テストの一時ディレクトリに `IaC/terraform/aws-managed/` を作り、`TF_DIR` を渡す |
| state の移し方（`rm -rf terraform`） | gitignore 対象の `*.tfvars` も `terraform/<root>/` に残る。`rm -rf` だと手で書いた `terraform.tfvars` を失う | `docs/deploy.md` の手順は `terraform.tfvars` も移し、`local/compose/.env` を `docker/compose/.env` へ移し、最後に `find` で残りを見せてから消すようにした |
| `.gitignore` の `.build/` | — | design どおり `IaC/terraform/**/.build/` の 1 行にした |
| 検証 13 の grep と「`<旧名>/` を `app/<新名>/` に置換」 | パスでない `neo4j/` を置換しすぎた: `app/neo4j/entrypoint.sh` の `NEO4J_AUTH="neo4j/${GRAPH_PASSWORD}"`（ユーザー名 `neo4j` とパスワード）が `app/neo4j/…` になり、そのまま出すと Neo4j が起動しない | 3 か所（entrypoint.sh・`neo4j.tf` のコメント・`tests/test_oss.py` の check 名）を `neo4j/` に戻した。`tests/test_oss_ops.py` のレイヤーの `neo4j/`（`python/neo4j/`）も戻した。追加した行に出てくる `app/` `docker/` `IaC/` のパスが実在するかを全部突き合わせ、残りはプレースホルダー（`<名前>` など）と gitignore 対象だけだった |
| 検証 13 の grep | テストがパスを `os.path.join(ROOT, "grafana", …)`・`("agent", m + ".py")`・`git_files("grafana")`・辞書のキー（`_pins["workflow"]`）で組み立てている所は `/` が無いので拾わない | check.sh を流して落ちた所を 1 つずつ直した（`test_alerts` `test_oss` `test_nautobot` `test_graph` `test_workflow` `test_lab_debug`）。`test_stream` の「up.sh に `/telegraf/` が無い」（S3 に置かないことの確かめ）は `app/telegraf/` を除いて見るようにした |
| `.gitignore` の 2 行を 1 行にする | `tests/test_oss_ops.py` が `^oss/terraform/**/.build/$` の行そのものを見ていた | `^IaC/terraform/**/.build/$` の行に加えて、`git check-ignore` で `oss/pipeline/graph`・`aws-managed/workflow`・`aws-managed/agent` の `.build/` が無視されることを見るようにした |
| Dockerfile のコメント「build の context はこのディレクトリ」（spark・neo4j） | context は `app/<名前>/` になり、Dockerfile のある `docker/images/<名前>/` ではない | 「context は `app/<名前>/`」に直した |
