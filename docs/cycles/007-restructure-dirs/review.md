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

## Round 2

- 対象: detached HEAD `866f585`（基点 `248db1b`）、作業ツリー `review-007-r2`。変更ファイルは `git diff -M --name-status 248db1b 866f585` の 498 行
- 前ラウンドの未解消 Must fix: なし

## サマリ

- design.md の移動表、Terraform の段数、`dir_tag` の引数追加と呼び元、compose の `context` / `dockerfile`、S3 の prefix（`lab/` `web/` `docs/`）を残すこと、gateway の zip 内のパス、版上げ（Grafana 13.2.3 / Telegraf 1.40.1 / Splunk 10.4.4）は design のとおりに入っている。
- Round 1 の Should fix（507ba75 が取り込まれていない）は、e60918b のマージで解消している。`tests/test_oss_ops.py:575,576,595,601` は `IaC/terraform/oss` / `IaC/terraform/aws-managed` に直っている。Nit 1〜5 は 5d0bf74 で直っていて、新しいテスト `tests/test_dashboard_config.py` は check.sh の中で 3 項目とも `OK` になった。
- レビュー側で `uv sync --group dev --group web` を打ち、`review-007-r2` で `bash ops/check.sh` を走らせた。結果は exit 0。最後の 2 行は `通過 325 / 失敗 0` と `すべて通過`。fmt は `差分なし`、validate は 18 ルートすべて `OK`。
- Must fix 0 件、Should fix 0 件、Nit 2 件。

### 見た観点 / 見ていない観点

- 見た: design 整合
  - 検証 2: `git diff -M --name-status --diff-filter=AD 248db1b 866f585` で増えたファイルを見た。build.md と `tests/test_dashboard_config.py` 以外の A/D は、rename の検出から漏れたシンボリックリンクと小さいファイルだけ。
  - 検証 3: `git log --follow --oneline 866f585 -- app/agentcore/app.py | wc -l` が 24。
  - 検証 4: `git ls-tree --name-only 866f585` で、`agent` `terraform` `local` `cloudformation` `kb-docs` が無い。
  - 検証 5: `git ls-tree -r --name-only 866f585 docker/images` で 8 本、どれも Dockerfile だけ。
  - 検証 13: `git grep` の残り。`web/` `lab/` は S3 の prefix、Nautobot のログストリーム、`docs/architecture/README.md` の `IaC/terraform/aws-managed/` 配下のツリー、`docs/deploy.md` の移し方の手順（前の配置を指すので正しい）だけ。
  - 3561bfe..866f585 の design.md / design-log.md の差分。
- 見た: correctness
  - `docker/compose/compose.yaml` の `context: ../../app/<名前>` と `dockerfile: ../../docker/images/<名前>/Dockerfile`。context から相対で解決される。
  - `docker/compose/up.sh` / `lab.sh` の `../../app/containerlab/...`。
  - `oss/compose/compose.yaml` の volume `../../app/spark`。
  - `oss/compose/check_neo4j.py` / `check_vm.py` の `HERE/../../app/...`。
  - 各 Dockerfile の `COPY` 元が `app/<名前>/` の中にあること。
  - `ops/lab-common.sh` の `dir_tag`・`telegraf_tag`・`build_telegraf`・`upload_lab`。
  - `app/dashboard/config.py` の段数。
- 見た: runtime bugs
  - `.gitignore` の新しいパス。`git check-ignore -v --no-index` で `docker/compose/.env`、`app/containerlab/splab.clab.yml`、`IaC/terraform/**/.build/`、`*.tfstate` が無視されることを確かめた。
  - `deploy.env.example`、`pyproject.toml`、`README.md`、`CLAUDE.md` のパス。
- 見た: data loss
  - `docs/deploy.md:123-154` の state の移し方。
    - `git ls-files` で「マージ前なら止まる」。
    - `mv_new` は移し先があれば上書きしない。
    - `rm -rf` の前に `git ls-files` と `find` で二重に確かめる。
    - zsh でも `$(echo $D)` が語に分かれる。
- 見た: missing tests
  - `tests/test_dashboard_config.py` の中身。import せずに正規表現で照合し、照合した式を評価して実在するパスになるかを見る。`.env` は開かず、パスの比較だけ。
  - check.sh の `tests/test_*.py` の glob に、このテストが入ること。
- 見た: e60918b（248db1b の取り込み）のマージの解き方。`git show --remerge-diff` で、BACKLOG は両側を残し、`test_oss_ops.py` は新しいパスにしてあることを見た。
- 見ていない: AWS（`terraform plan` / `apply`、`ops/up.sh`）。指示により何も打っていない。
- 見ていない: `docker build`（検証 8）と `docker compose config`（検証 10）。レビュー側では走らせていない。
- 見ていない: 検証 11 / 12 / 14。`lab_topology.py` の出力の diff、`import config`、`check_splunk_image.py` はレビュー側では打っていない。12 は新しいテストで段数だけ確かめた。
- 見ていない: `docs/deploy.md` の移し方を本物の state で走らせること（メインのチェックアウトに触らない指示のため）。
- 見ていない: `review-007-r2` の `git status`。このセッションは cycle-006-design の worktree に隔離されていて、`git -C` での実行を拒まれた。
- 開示 1: check.sh の出力を、worktree の外の scratchpad の `r2-check.log` へ `>` で書き出した。
- 開示 2: check.sh の validate は `review-007-r2` の中に gitignore 対象の `.terraform/` を作る（check.sh 本来の動き）。

## Must fix

None

## Should fix

None

## Nit

- [design 整合] `ops/lab-debug.sh:96`
  - 起きること: `sync` のログが `lab/ を s3://$BUCKET/lab/ に置き直して` のまま。前半の `lab/` は手元の写し元のことで、写し元はいま `app/containerlab/` になっている（同じ行の後半と `upload_lab` は `app/containerlab/` を写している）。打った人が手元の `lab/` を探して迷う。
  - Nit の理由: 動作は正しく、ログの文言だけの問題だから。
- [design 整合] `docs/cycles/007-restructure-dirs/design.md:192,216,217`
  - 起きること: design は「`tests/test_*.py` 14 本」「新規に増えるファイルは無し（build.md を除く）」のまま。実際は `tests/test_dashboard_config.py` が増えて 15 本になっている（`docs/development.md:37` も 15 本と書いている）。経緯は build.md:560 にだけあり、design-log.md には無い。design の検証 1 と 2 で突き合わせる人が食い違いに当たる。
  - Nit の理由: テストを足したのは Round 1 の Nit への対応で、正しい変更だから。記録の場所がずれているだけ。

## 良かった点

- Round 1 の Should fix を、マージ（e60918b）で正しく解いている。衝突しない側で壊れるはずだった `test_oss_ops.py` の 4 行も新しいパスに直してあり、レビュー側の check.sh で `通過 144 / 失敗 0` を確かめた。
- `tests/test_dashboard_config.py` は config.py を import しない（import すると `.env` を読み、`AWS_REGION` が無ければ止まる）。照合した式をそのまま評価して実在を見るので、文字列が一致するだけのテストになっていない。`.env` の中身にも触れない。
- compose の `dockerfile` を context から相対で書き、Telegraf / Splunk / Grafana の版を compose の build args でも同時に上げている（design の 4 か所の同時更新）。
- `.gitignore` の `IaC/terraform/**/.build/` は 1 行で managed と OSS の両方を受け、`!IaC/terraform/oss/**/oss.auto.tfvars` の例外も付いて行っている。
- `docs/deploy.md` の移し方は、マージ前に打つと止まる、上書きしない、消す前に二重に確かめる、の 3 つで、data loss に倒れない作りになっている。

## ユーザーへの質問

None

### PM の確認（Round 2、完了判定）

- レビューしたモデル: cold reviewer = opus（general-purpose、effort は既定）。PM = fable-5.1 / effort: high。cold reviewer に依頼した（完了判定の直前、2 回目＝最後）。対象 866f585（feat/restructure-dirs 5d0bf74 を docs/cycle-006-design に `--no-ff` でマージしたもの）。レビュー結果は scratchpad に書かせて上に連結した（review 用 worktree `review-007-r2` は `git status --short` が空のまま。確認後に `git worktree remove --force` で消した）
- 前ラウンドの Should fix（507ba75 の未取り込み）の取り直し: 866f585 で `git grep -n 'oss/terraform\|"terraform"' -- tests/test_oss_ops.py` → 0 件、`sed -n '575p' tests/test_oss_ops.py` → `IaC/terraform/oss`。マージ後の木で `uv sync --group dev --group web` のあと `bash ops/check.sh` → exit 0、`失敗 0` が 14 行、`68 項目すべて通過`、最後が `すべて通過`（scratchpad の `check-after-007.log`）。cold reviewer 側の check.sh も exit 0、`通過 325 / 失敗 0`、`すべて通過`（`r2-check.log`）
- Nit 1（`ops/lab-debug.sh:96` のログ文言）: `sed -n '96p' ops/lab-debug.sh` → `log "lab/ を s3://$BUCKET/lab/ に置き直して …（起動のたびに app/containerlab/setup.sh が置き直す）"` で再現。前半の `lab/` を `app/containerlab/` に直した（1 語。tests/ にこの文言の照合は無いことを `grep -rn '置き直して' tests/` で確認。test_lab_debug.py:209 が見るのは別の文）
- Nit 2（design.md の 14 本 / 新規ファイル無し）: `sed -n '192p;216,217p' docs/cycles/007-restructure-dirs/design.md` で再現。192 行に `tests/test_dashboard_config.py` を新規として追記、216 行を 15 本、217 行を「新規は build.md と test_dashboard_config.py だけ」に直した
- Round 1 の Nit 6（description の変更で state のあるスタックが作り直しになる）は直さない。deploy.md の「前の配置で立てた環境はマージの前に down」で覆う。最終報告に載せる
- 直したあとの取り直し: `bash ops/check.sh` → 下の「完了判定の取り直し」

#### 完了判定の取り直し

- 866f585 + 上の 3 ファイルの直し（`ops/lab-debug.sh:96`、design.md 192/216/217 行、BACKLOG）の木で `bash ops/check.sh` → 最後の 2 行が `すべて通過` / `exit=0`（scratchpad の `final-check.log`）。Must fix 0 / Should fix 0。Nit で残すのは Round 1 の Nit 6 だけ
- BACKLOG の行を `[x] … → 007-restructure-dirs（2026-10-08 完了）` にした。サイクル完了。state の移し方（`docs/deploy.md`）はユーザーが main にマージしたあとメインのチェックアウトで打つ

<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-007-restructure-dirs-review.html -->
