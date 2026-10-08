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
