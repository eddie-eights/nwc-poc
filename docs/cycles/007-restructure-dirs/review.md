# 「ディレクトリを app/ と docker/ と IaC/ に並べ直す」（007）レビュー

## Round 1


- 対象: detached HEAD `3561bfe`（基点 `66929ba`）、作業ツリー `review-007-r1`
- 前ラウンドの未解消 Must fix: なし（初回）

## サマリ

- design.md の 3 つの書き換え表（コード / tests / docs）、Terraform の相対パスの段数、`dir_tag` の引数追加と呼び元 8 か所、版上げ（Grafana 13.2.3 / Telegraf 1.40.1 / Splunk 10.4.4）は、表のとおりに入っている。design との差分 27 行は design-log.md に理由付きで残っている。
- レビュー側で `uv sync --group dev --group web` のあと `bash ops/check.sh` を `review-007-r1` で走らせ、最後が `通過 325 / 失敗 0`、`すべて通過`、exit code 0 だった（fmt 差分なし、aws-managed の 9 ルート validate `OK` を出力で確認）。
- Must fix は無し。Should fix は 1 件で、マージ先 `docs/cycle-006-design` に既に入っている 507ba75 との統合がブランチに無い点。いまのまま入れると衝突 2 件と、衝突しないで壊れるテスト 1 本が出る。

### 見た観点 / 見ていない観点

- 見た: design 整合（3 つの書き換え表、変更対象ファイル、実装ステップ、検証方法 1〜14 と build.md の結果の突き合わせ）
- 見た: correctness（Terraform の `path.module` 段数: agent / workflow の `repo_root` は 4 段、managed と OSS の `pipeline/graph/sync.tf` は 5 段。`app/dashboard/config.py` の `.env` / `DATA_DIR` / `sys.path` の段数。compose の `dockerfile` が `context` からの相対で解決されること。`docker/compose/up.sh` と `lab.sh` の段数）
- 見た: runtime bugs（`ops/up.sh` / `ops/up-common.sh` / `ops/lab-common.sh` / `oss/ops/*.sh` の `-f docker/images/<名前>/Dockerfile app/<名前>/`、`nautobot_context` の写し元、`TF_DIR` を通した tfstate のパス、S3 の prefix `lab/` `web/` `docs/` を残したこと、`.gitignore` の新パス）
- 見た: data loss（docs/deploy.md の state の移し方の手順。`git ls-files` の確認、`mv_new`、`find -prune`、`rm -rf` の前の確認）
- 見た: missing tests（`tests/test_lab_debug.py` に足された `_dir_tag` と `_tag_calls` の 2 検査、`config.py` のパス解決のテストの有無）
- 見た: マージの可否（`git merge-tree --write-tree 3561bfe docs/cycle-006-design` の結果）
- 見ていない: AWS（`terraform plan` / `apply`、`ops/up.sh`、ECR への push）。指示により何も打っていない
- 見ていない: `docker build`（検証 8）と `docker compose config`（検証 10）をレビュー側では走らせていない。build.md の記録を読んだだけ
- 見ていない: docs/deploy.md の state の移し方を、本物の state を持つメインのチェックアウトで走らせること（build.md でも偽の残骸での試験だけ）
- 見ていない: WSL での compose の実起動、`tests/check_splunk_image.py` の再実行（build.md の 11/11 を読んだだけ）
- 見ていない: 001〜006 と docs/verification の文書（design のとおり書き換え対象外）

## Must fix

None

## Should fix

- [design 整合 / missing tests] `docs/cycle-006-design` に入っている 507ba75（fix/oss-ops-vpc-tty）がこのブランチに無く、`git merge-tree --write-tree 3561bfe docs/cycle-006-design` で `docs/cycles/BACKLOG.md` と `tests/test_oss_roll.py` が CONFLICT になる。さらに衝突しない側で、マージ後の `tests/test_oss_ops.py:575,576,595,601` が `"oss/terraform"` / `"terraform"` / `"oss/terraform/base/core"` のまま残る（507ba75 が足した down の VPC の検査。007 の `ops/down-common.sh` は `$TF_DIR`＝`IaC/terraform/...` を渡すので、この 4 行の照合は外れて test_oss_ops が落ちる）。design.md 237 行（リスク 5）は「この 2 本を先に docs/cycle-006-design に入れてから」マージする順序を求めているが、その解消はエンジニアの scratchpad の patch にしか無く、ブランチに残っていない。直し方: このブランチに `docs/cycle-006-design` をマージして衝突を解き、`test_oss_ops.py` の 4 行を `IaC/terraform/oss` / `IaC/terraform/aws-managed` に直して commit し、`bash ops/check.sh` を `すべて通過` で通してから PM がマージする。そうしないと、マージする人が衝突をその場で解き、test_oss_ops の失敗に気づかないまま入る。

## Nit

- [design 整合] `app/agentcore/requirements-oss.txt:2` と `app/temporal/requirements-oss.txt:2` のコメントのビルド例が `docker buildx build … app/agentcore/` のままで `-f docker/images/<名前>/Dockerfile` が無い。Dockerfile は context の外へ移ったので、コメントを写して打つ人はビルドに失敗する。
- [design 整合] `ops/up-common.sh:178` と `:230` のコメントが「`app/splunk/` の Dockerfile の ARG」「`app/grafana/` の Dockerfile の ARG」のまま。Dockerfile はいま `docker/images/splunk/` と `docker/images/grafana/` にあるので、版を上げる人が探す場所を誤る。
- [design 整合] design.md の 80、82、189、205、221 行はシンボリックリンクを 90 本としているが、実数は 92 本（design-log.md の Round 1 に理由付きで記録済み）。検証 6 の期待値が design.md 上は `90` のままなので、次にこの表で検証する人が食い違いに当たる。design.md 側の数字を直すか、表に「実数 92、design-log 参照」と添える。
- [design 整合] `docs/development.md` の「変えたもの」の表に `docker/images/<名前>/Dockerfile` への移動が載っていない（ディレクトリの移動と `DATA_DIR` は載っている）。Dockerfile を元の場所で探す人向けの 1 行が無い。
- [missing tests] `app/dashboard/config.py` の `.env` の位置（`HERE/../../.env`）、相対 `DATA_DIR` の解決（`HERE/../..` 起点）、`sys.path` の `HERE/../agentcore` を検査するテストが無い（`tests/` を `DATA_DIR` / `ENV_FILE` / `config` で grep して該当なし）。検証 12 は build.md での手動確認だけなので、次に段数が変わったときに check.sh では気づけない。
- [runtime bugs] SSM パラメータ・OpenSearch のセキュリティポリシー・Cloud Map の namespace・Lambda レイヤーの description と user_data 内のパスが変わるため、state が残っているスタックに `apply` すると作り直しや in-place 更新が出る。いまは AWS のリソースが全部消えていて、docs/deploy.md に「マージ前に down」とあるので実害は無いが、deploy.md の手順を飛ばした人には予期しない作り直しになる。

## 良かった点

- design-log.md の Round 1 に、design からずれた 27 行を全部理由付きで残している（リンク 92 本、OSS の `repo_root` が 5 段、`neo4j/` `web/` `splunk/` の置換しすぎを戻したこと、作り直しになる description など）。レビューで「なぜ表と違うか」を追い直す必要が無かった。
- `dir_tag` を、ディレクトリが無いときに失敗させるよう固くした。これで test の写しの木に `app/grafana` が無いことが表に出ている。`tests/test_lab_debug.py` に、Dockerfile を変えるとタグが変わる・同じ中身なら同じタグ・ディレクトリが無ければ失敗、の 3 点と、呼び元がちょうど 8 か所で各々が対応する `-f` の Dockerfile を渡す検査が入った。
- S3 の prefix（`lab/` `web/` `docs/`）を変えずに残し、置換表の機械置換で入りすぎた箇所を戻している。gateway の zip 内のパス名も変えていない。
- tfstate の直書きを `$TF_DIR` に寄せ、Terraform の段数を managed と OSS の両方で合わせた。check.sh の validate と fmt が通ることをレビュー側の実行でも確認した。
- docs/deploy.md の state の移し方が、`git ls-files` で追跡中のファイルを消さない確認、移し先が既にあれば上書きしない `mv_new`、確認つきの `rm -rf` を持ち、bash と zsh の両方で偽の残骸を使って試してある。
- Splunk 10.4.4 を `tests/check_splunk_image.py` の結果（build.md で 11/11）で上げており、design の「条件付き」を守っている。

## ユーザーへの質問

- [data loss] docs/deploy.md の state の移し方は、本物の state（メインのチェックアウトの `terraform/**/terraform.tfstate`）では試されていない。メインのチェックアウトに state や `.terraform` が実際に残っているかをレビュー側から確認できない（メインのチェックアウトには触らない指示のため）。確認すべきは、マージ前に PM がユーザーへ出す手順で `ls terraform/*/terraform.tfstate terraform/*/*/terraform.tfstate` の結果を先に見ること。何も無ければ移す手順そのものが要らない。

### PM の確認（Round 1）

- レビューしたモデル: cold reviewer = opus（general-purpose、effort は既定）。PM = fable-5.1 / effort: high。cold reviewer に依頼した（初回ビルド直後、1 回目）。対象 3561bfe。レビュー結果は hook の都合で scratchpad に書かれたものを上に連結した（review 用 worktree には新規ファイルが増えていないことを `ls` で確認）
- Should fix（507ba75 の未取り込み）: ded9cbb（エンジニア2 が docs/cycle-006-design 248db1b を merge commit e60918b で取り込み）で解消。確認: `git merge-tree --write-tree ded9cbb docs/cycle-006-design` → ツリー f08c726、exit 0（衝突なし）。`git show ded9cbb:tests/test_oss_ops.py | grep -n tf_dir` → 575 行が `"IaC/terraform/oss"`、ほか `IaC/terraform/aws-managed`。check.sh はエンジニア2 の報告（exit 0、通過 325 / 144 / 66 ほか 13 行、失敗 0）。PM はマージ後に取り直す
- Nit 1〜5 を ded9cbb で再現（`git show ded9cbb:<path>` と `git grep ... ded9cbb -- tests/`）: requirements-oss.txt の例に `-f` 無し、up-common.sh:178/230 のコメントが `app/splunk/` `app/grafana/`、design.md 23/80/82/189 行が 90 本、development.md の表に Dockerfile の行なし、tests/ に config.py の段数の検査なし → 直しをエンジニア2 に依頼（Nit だが同じブランチで安く直せるため）。Nit 6（description 変更での作り直し）は deploy.md の「マージ前に down」で足りるので直さない
- ユーザーへの質問（state の有無）: メインのチェックアウトに `terraform/{agent,workflow,base/core,base/ecr,pipeline/{analytics,graph,lab,nautobot,stream}}/terraform.tfstate` の 9 本と `.terraform/` 9 本、`.build/` 3 本（agent / workflow / pipeline/graph）がある（`find` で確認、中身は読んでいない）。`oss/terraform/` には無い。よって deploy.md の state の移し方はマージ後に必要
