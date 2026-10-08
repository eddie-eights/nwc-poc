# Cycle 007 restructure-dirs 設計: ディレクトリを app/ と docker/ と IaC/ に並べ直す

PM(fable-5.1) / effort: high

この文書は現行の設計だけを書く。変えた経緯は [design-log.md](design-log.md)。

## 背景

いまのリポジトリの直下には、アプリのソース（`agent/ web/ workflow/ lab/ nautobot/ spark/ telegraf/ grafana/ splunk/ neo4j/ graph/ kb-docs/`）、IaC（`terraform/ oss/terraform/ cloudformation/`）、手元用の compose（`local/compose/`）、運用スクリプト（`ops/ oss/ops/`）が同じ階層に 20 個ほど並んでいて、何が何の入口か分からない。Dockerfile は各ソースディレクトリの中に埋まっている。

このサイクルは**中身を変えずにパスだけ動かす**。3 つの入口に分ける: `app/`（ソース）、`docker/`（Dockerfile と手元の compose）、`IaC/`（Terraform と CloudFormation）。`ops/ oss/ops/ oss/compose/ tests/ tools/ docs/` はアプリではないので動かさない。

### 合意した決定（ユーザー確認済み 2026-10-08。PM が置いた決定と却下した案は design-log.md）

| いま | あと |
|---|---|
| `agent/` `workflow/` `web/` `lab/` | `app/agentcore/` `app/temporal/` `app/dashboard/` `app/containerlab/`（改名。ユーザー決定） |
| `nautobot/ spark/ telegraf/ grafana/ splunk/ neo4j/ graph/` | `app/<同名>/` |
| `kb-docs/` | `app/resources/` |
| 各ディレクトリの `Dockerfile`（8 本） | `docker/images/<app の名前>/Dockerfile`（build の context は `app/<名前>/` のまま） |
| `local/compose/`（006） | `docker/compose/`（compose.yaml・`.env.example`・up / down / check / lab.sh・README） |
| `terraform/` | `IaC/terraform/aws-managed/` |
| `oss/terraform/` | `IaC/terraform/oss/`（シンボリックリンク 90 本を貼り直す） |
| `cloudformation/` | `IaC/cloudformation/` |
| `ops/ oss/ops/ oss/compose/ tests/ tools/ docs/ jars/ wheels/ pyproject.toml .env.example` | そのまま |

- 表記は `IaC`（大文字混じり）。`docker/images/<名前>/` には Dockerfile だけ置き、ソースの写しは置かない
- イメージの版は「LTS があるものは LTS 系列の最新、無いものは最新の安定版」。このサイクルで上げるのは **Grafana 13.2.3 と Telegraf 1.40.1**（Docker Hub にタグがあることを 2026-10-08 に API で確認: どちらも HTTP 200）。**Splunk 10.4.4** はタグはある（HTTP 200）が amd64 で boto3 のアラート送信が通るかを `tests/check_splunk_image.py` で確かめてから。通らなければ 10.4.3 のまま
- AWS のリソース名（ECR のリポジトリ名 `<prefix>-grafana` など、ECS / Lambda / SSM の名前）は変えない。このサイクルで AWS には何も立てない

## 設計方針

### 動かし方の原則

1. **`git mv` で動かす**（`git log --follow` で履歴が追えるように）。中身の書き換えは参照の修正だけ
2. **commit を 3 つに分ける**。(1) `git mv` とシンボリックリンクの貼り直し、(2) 参照の書き換え、(3) `dir_tag` の変更と版上げ。(1) の直後は参照が壊れていてテストが通らない（織り込み済み。(2) で通す）
3. **既存の名前を温存する**: ECR のイメージ名、`dir_tag` の版の環境変数名（`GRAFANA_VERSION` 等）、S3 の prefix `lab/`（下の落とし穴 7）、EC2 の中の置き場 `/opt/<prefix>-web`

### Dockerfile の置き場と build の形

| `docker/images/<名前>/Dockerfile` | いま | build の呼び元 | context |
|---|---|---|---|
| `agentcore` | `agent/Dockerfile` | `ops/up-common.sh` `build_agent` | `app/agentcore/` |
| `temporal` | `workflow/Dockerfile` | `ops/up-common.sh` `build_worker` | `app/temporal/` |
| `grafana` | `grafana/Dockerfile` | `ops/up-common.sh` `build_grafana` | `app/grafana/` |
| `splunk` | `splunk/Dockerfile` | `ops/up-common.sh` `build_splunk` | `app/splunk/` |
| `nautobot` | `nautobot/Dockerfile` | `ops/up-common.sh` `build_nautobot` | 一時ディレクトリ（`nautobot_context` が `app/nautobot/.` と `app/agentcore/{graph,toolkit}.py` と lab の seed を集める。いまと同じ） |
| `telegraf` | `telegraf/Dockerfile` | `ops/lab-common.sh` `build_telegraf` | `app/telegraf/` |
| `spark` | `spark/Dockerfile` | `oss/ops/oss-images.sh` | `app/spark/` |
| `neo4j` | `neo4j/Dockerfile` | `oss/ops/oss-images.sh` | `app/neo4j/` |

build は全部 `docker buildx build … -f docker/images/<名前>/Dockerfile <context>` にする（いまは 8 本とも `-f` 無し。context の中に Dockerfile がある前提）。Dockerfile の `COPY` は context からの相対なので中身は変えない。`.dockerignore` はいま 1 本も無く、このサイクルでも作らない。

`oss/compose/spark/Dockerfile` と `oss/compose/neo4j/Dockerfile` は **OSS 版の部品の検査用で ECR に push しない**ので `docker/images/` には入れず、`oss/compose/` に残す（`oss/compose/` 自体の扱いは design-log.md Q1）。

### `dir_tag` に Dockerfile を混ぜる

`ops/lab-common.sh` の `dir_tag <版> <ディレクトリ>` は、ディレクトリの中の全ファイル（相対パス + 中身）の sha256 で ECR のタグを作る。いまは Dockerfile がディレクトリの中にあるのでハッシュに入っているが、外に出すと **Dockerfile を変えてもタグが変わらず、ECR の古いイメージを使い続ける**。

変更: `dir_tag <版> <ディレクトリ> [ファイル...]` にして、3 つ目以降のファイルを「リポジトリの根からの相対パス + 中身」としてディレクトリの後ろにハッシュへ足す。呼び元 8 か所（`ops/up-common.sh:182` splunk、`ops/up.sh:637,646` grafana / nautobot、`oss/ops/up.sh:183,193`、`oss/ops/oss-images.sh:28,29`、`ops/lab-common.sh:73` telegraf）は全部 `docker/images/<名前>/Dockerfile` を渡す。agent / worker は固定タグ `v1` のままで `dir_tag` を使っていない（いまと同じ）。

この変更で**全イメージのタグが 1 回変わり、次の `up.sh` で全部ビルドし直し**になる（時間がかかるだけ。ECR のタグは上書きできないので、古いタグは `KEEP_ECR` の運用のまま残る）。

### Terraform の相対パス

Terraform は `path.module` からの相対で根のファイルを読む。深さが変わるのは次の 3 か所。

| ファイル | いま | あと |
|---|---|---|
| `terraform/agent/locals.tf:19`、`terraform/workflow/locals.tf:22`（oss 側はシンボリックリンクで同じファイル） | `fileexists("${path.module}/../../pyproject.toml") ? "${path.module}/../.." : "${path.module}/../../.."`（`terraform/` なら 2 段、`oss/terraform/` なら 3 段） | **`"${path.module}/../../../.."` 固定**（`IaC/terraform/aws-managed/agent` も `IaC/terraform/oss/agent` も根から 4 段。判定式は要らなくなる） |
| `terraform/pipeline/graph/sync.tf:24,29,36,42` | `${path.module}/../../../graph/…` 等（3 段） | `${path.module}/../../../../app/graph/…`（5 段 + `app/`） |
| `oss/terraform/pipeline/graph/sync.tf:12`（実ファイル） | `repo_root = "${path.module}/../../../.."`（4 段） | `"${path.module}/../../../../.."`（5 段） |

読み先に `app/` と新しい名前を足す: `agent/kb_index.py` → `app/agentcore/kb_index.py`（`terraform/agent/kb.tf:126`）、`terraform/workflow/gateway.tf:16-23,44` の `tools_files` のキー（`agent/toolkit.py` 等 → `app/agentcore/…`。`tools/tools.json` と `tools/handler.py` は `tools/` のままなので変えない）、`workflow/rules.py` → `app/temporal/rules.py`、`graph/status_handler.py` → `app/graph/status_handler.py`、`graph/requirements-oss.txt` → `app/graph/requirements-oss.txt`（oss の sync.tf:56 のエラー文と `oss/ops/up.sh:383,392`）。

`gateway.tf` の `tools_files` のキーは Lambda の zip の中のパスにもなっているか確かめる（`terraform/workflow/gateway.tf:37` が `${local.repo_root}/${source.key}` で読んでいる）。zip の中の置き場（`handler.py` から import する名前）が変わると Lambda が壊れるので、**キーを変えるなら読み元のパスだけを変え、zip の中のファイル名は変えない**（`source.key` を zip の中のパスにも使っているなら、`{ src = "app/agentcore/toolkit.py", dst = "toolkit.py" }` の形に分ける）。

`.build/*.zip` の出力先は `path.module` からの相対なので変わらない。`terraform_remote_state` は隣のルートを相対で読むので、木ごと動けば変わらない。

### シンボリックリンク 90 本

`oss/terraform/` の 90 本は全部 `terraform/` の中を指す（`../../../terraform/…` 21 本、`../../../../terraform/…` 67 本、`../../../../../terraform/…` 2 本）。移動後、リンク元 `IaC/terraform/oss/<root>/X` から見て `../../../` は `IaC/` なので、**リンクの文字列は `terraform/` の直後に `aws-managed/` を挟むだけ**（段数は変えない）。例: `../../../terraform/agent/main.tf` → `../../../terraform/aws-managed/agent/main.tf`。

`git mv` はリンクのファイルを動かすだけで文字列は変えないので、動かしたあとに貼り直す:

```bash
git ls-files -s IaC/terraform/oss | awk '$1=="120000"{print $4}' | while read -r l; do
  t=$(readlink "$l"); ln -sfn "${t/\/terraform\//\/terraform\/aws-managed\/}" "$l"; git add "$l"
done
```

`tests/test_oss.py:868-885` の `links_to_managed` が realpath でマネージド版の同じファイルを指すことを確かめる（`ROOT/terraform/<root>/<n>` → `ROOT/IaC/terraform/aws-managed/<root>/<n>` に直す）。

### Terraform の state は git では動かない（落とし穴 1）

メインのチェックアウトの `terraform/<root>/` には gitignore 対象の `.terraform/`（provider のキャッシュ）と `terraform.tfstate` が残る。`git mv` では付いて行かない。2026-10-08 の時点で AWS のリソースは全部消えている（`docs/verification/20261008-*.md`）ので state の中身は空に近く、失っても困らないが、`.terraform/` を置き直せば `init` のダウンロードが省ける。main にマージしたあと、メインのチェックアウトで:

```bash
for r in base/ecr base/core agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot workflow; do
  for f in .terraform terraform.tfstate terraform.tfstate.backup .build; do
    [ -e "terraform/$r/$f" ] && mv "terraform/$r/$f" "IaC/terraform/aws-managed/$r/$f"
  done
done
rmdir -p terraform/* 2>/dev/null; rm -rf terraform
```

OSS 版の state（`oss/terraform/base/core` の tfstate）は PM の worktree `verify-oss-20261008` にしか無く、リソースは消えているので捨てる。この手順は `docs/deploy.md` にも「007 で並べ直したとき」として 1 段落で書く。**実行は PM がユーザーに手順を出す**（エンジニアはメインのチェックアウトに触らない）。

### 参照の書き換え（コード）

棚卸し（2026-10-08、`git ls-files` + grep）で見つかった、`TF_DIR` を通らない直書きと相対パス。全部直す。

| 場所 | いま | あと |
|---|---|---|
| `ops/common.sh:6` | `TF_DIR="${TF_DIR:-terraform}"`、`:42` の案内 `terraform/$1` | `IaC/terraform/aws-managed` |
| `ops/up.sh:596,604,709,719,733,845,1036` | `terraform/…/terraform.tfstate` | `$TF_DIR/…`（直書きを `TF_DIR` に寄せる） |
| `ops/up.sh:148` | `SPARK_SCRIPT=spark/snmp_sinks.py` | `app/spark/snmp_sinks.py` |
| `ops/up.sh:786-798` | `web/requirements.txt`、`web/*.py`、`agent/$f.py`、`agent/data/`、`kb-docs/` | `app/dashboard/…`、`app/agentcore/…`、`app/resources/` |
| `ops/up.sh:883,896,897,977,1016`、`ops/sync-graph.sh:43`、`ops/up-common.sh:240`、`oss/ops/up.sh:298,305,306,409,447` | `lab/splab.clab.yml.in`、`lab/lab_topology.py lab`（最後の `lab` は引数のディレクトリ） | `app/containerlab/splab.clab.yml.in`、`app/containerlab/lab_topology.py app/containerlab` |
| `ops/up-common.sh:233-259` | `cp -R nautobot/.`、`cp agent/graph.py agent/toolkit.py`、`--push agent/`、`--push grafana/`、`--push workflow/`、`--push "$2"`、`:189 --push splunk/` | `app/nautobot/.`、`app/agentcore/…`、`-f docker/images/<名前>/Dockerfile app/<名前>/` |
| `ops/lab-common.sh:73,76,81` | `dir_tag "$TELEGRAF_VERSION" telegraf`、`--push telegraf`、`aws s3 sync lab/ "s3://$1/lab/"` | `dir_tag … app/telegraf docker/images/telegraf/Dockerfile`、`-f … app/telegraf`、`aws s3 sync app/containerlab/ "s3://$1/lab/"`（**S3 側の prefix `lab/` は変えない**。落とし穴 7） |
| `ops/lab-debug.sh:25` | `TEMPLATE=cloudformation/lab-debug.yaml` | `IaC/cloudformation/lab-debug.yaml` |
| `ops/sync-graph.sh:37`、`oss/ops/up.sh:40`、`oss/ops/down.sh:26` | `TF_DIR=oss/terraform` / `TF_DIR=terraform` | `IaC/terraform/oss` / `IaC/terraform/aws-managed` |
| `oss/ops/up.sh:270-277` | `web/requirements-oss.txt web/requirements.txt`、web と agent の写し | `app/dashboard/…`、`app/agentcore/…` |
| `oss/ops/up.sh:383-394` | `graph/requirements-oss.txt`、`oss/terraform/pipeline/graph/.build/neo4j-layer` | `app/graph/…`、`IaC/terraform/oss/pipeline/graph/.build/neo4j-layer` |
| `oss/ops/oss-images.sh:28,29,55,56` | `dir_tag … spark` / `neo4j`、`--push spark` / `neo4j` | `app/spark` + Dockerfile、`-f docker/images/spark/Dockerfile app/spark` |
| `ops/seed_graph.py:40` | 案内文 `oss/terraform/pipeline/graph`、`terraform/pipeline/graph` | 新しいパス |
| `web/config.py:19` | `os.path.join(HERE, "..", ".env")` | `HERE, "..", "..", ".env"`（`app/dashboard/` は根から 2 段。`ENV_FILE` で上書きできるのは同じ） |
| `web/config.py:48` と `.env.example:49` | `DATA_DIR=agent/data` を `HERE/..` から解決 | `DATA_DIR=app/agentcore/data` を `HERE/../..` から解決。`.env` を持っている人は `DATA_DIR` を書き換える（`docs/development.md` に 1 行） |
| `web/config.py:55` | `sys.path.append(HERE/../agent)` | `HERE/../agentcore`（兄弟のまま） |
| `oss/compose/check_neo4j.py:16`、`check_vm.py:18`、`oss/compose/compose.yaml:168` | `../../agent/data`、`../../spark`、`../../spark:/opt/check:ro` | `../../app/agentcore/data`、`../../app/spark`、`../../app/spark:/opt/check:ro` |
| `local/compose/compose.yaml:43,58,171,192` | `build: ../../spark`、`context: ../../telegraf` 等（Dockerfile が context の中にある前提） | `context: ../../app/spark` + `dockerfile: ../../docker/images/spark/Dockerfile`（compose の `dockerfile` は context からの相対。`../../docker/images/…` は context `app/spark` から見て `docker/images/…` になるので `../../docker/images/spark/Dockerfile`） |
| `local/compose/up.sh:10-12`、`lab.sh:12` | `../../lab/lab_topology.py ../../lab`、`"$PWD/../../lab/lab.sh"` | `../../app/containerlab/…`（`docker/compose/` も根から 2 段なので段数は同じ） |
| `.gitignore:14,27,28,29` | `lab/splab.clab.yml`、`!oss/terraform/**/oss.auto.tfvars`、`terraform/**/.build/`、`oss/terraform/**/.build/` | `app/containerlab/splab.clab.yml`、`!IaC/terraform/oss/**/oss.auto.tfvars`、`IaC/terraform/**/.build/`（1 行で両方） |
| `pyproject.toml:1,2,12,14,17` | コメントの `agent/requirements.txt` 等 | 新しいパス（`pyproject.toml` は根のまま。Terraform の判定式を消すので「根の印」の役目は終わる） |
| `ops/check.sh:14-15,25,35,38,45,48` | `TF_BASES=(terraform oss/terraform)`、`[ "$base" = oss/terraform ]`、`bash -n` の `lab/lab.sh lab/setup.sh local/compose/*.sh`、`find agent graph lab local nautobot ops oss spark splunk tests tools web workflow` | `TF_BASES=(IaC/terraform/aws-managed IaC/terraform/oss)`、`IaC/terraform/oss`、`app/containerlab/lab.sh app/containerlab/setup.sh docker/compose/*.sh`、`find app docker oss ops tests tools` |
| `terraform/base/core/outputs.tf:105`、`agent/outputs.tf:49`、`pipeline/lab/outputs.tf:23`、`stream/variables.tf:94,104` | 出力の案内文の `web/` `agent/` `kb-docs/` `lab/` | 新しいパス（S3 の prefix `lab/` は残す） |
| `cloudformation/lab-debug.yaml` | コメントと説明の `lab/` `telegraf/`（`:454,456` の `lab/` は S3 の prefix） | コメントだけ直す。S3 の prefix は残す |
| `CLAUDE.md:5`、`README.md:70,98-103,140,150,169` | `terraform/`、`terraform -chdir=terraform/…`、`agent/ や workflow/`、`local/compose/README.md` | 新しいパス |

### tests の書き換え

`ROOT` は `tests/` の 1 つ上で変わらない。変えるのは部品。

- `os.path.join(ROOT, "<dir>", …)` と `read("<dir>/…")` の `<dir>` を新しい名前に（14 ファイル。棚卸しの件数: test_workflow 50、test_alerts 31、test_stream 23、test_sync 18、test_lab_debug 13、test_local_compose 11、test_nautobot 10 の `read()` と、`os.path.join` 多数）。機械的に `sed` で置換してから、置換したパターンを `grep` で数えて 0 件を確かめる
- `tests/test_oss_ops.py:353` の `UP_DIRS`、`:357` の `("terraform", "oss/terraform")`、`tests/test_oss_roll.py:289-293` の `TF_DIR=oss/terraform`
- `tests/test_lab_debug.py:414-416` の `bash -n` 対象、`:77` の `telegraf/Dockerfile`
- `tests/test_oss.py:921-926` は `repo_root` の判定式を文字列で照合している。式を固定にするので、**照合も固定の式に変える**（`"${path.module}/../../../.."` が managed と oss の両方の locals.tf にあること）。`tests/test_workflow.py:527,530` の `${local.repo_root}/…` の照合も新しいキーに
- `tests/test_alerts.py:792-794`、`test_oss_ops.py:710,715`、`test_workflow.py:749` は build の文字列（`--push splunk/` 等）を照合している。`-f docker/images/splunk/Dockerfile app/splunk/` に合わせる
- `tests/test_local_compose.py:207-229` は `ops/check.sh` の `bash -n` と `find` の行を正規表現で照合し、写しの木で走らせて 19 本以上を数える。`docker/compose/*.sh` と `find app docker …` に合わせる
- `tests/test_alerts.py:964-973`（`find` の対象 ⊇ 追跡中の `*.py` の最上位ディレクトリ）は、`find app docker oss ops tests tools` で自動的に満たす
- `tests/test_oss.py:1290,1380,1384`、`test_analytics.py:1191`、`test_alerts.py:634,905`、`test_nautobot.py:217`、`test_stream.py:180`、`test_workflow.py:625-628` は Dockerfile をパスで読む。`docker/images/<名前>/Dockerfile`（`oss/compose/*/Dockerfile` はそのまま）
- `tests/test_app.py:12,509`、`test_graph.py:5`、`test_kb_index.py:5` は `../agent/…` `../workflow/…` で直接 import する。`../app/agentcore/…` `../app/temporal/…`

### docs の書き換え

- 直すのは**いまの木を説明している文書**だけ: `README.md`、`CLAUDE.md`、`docs/*.md`（pipeline、data-stores、deploy、troubleshooting、workflow、nautobot、development、setup、collection、oss-variant、faq-fukuda-nwc-poc、alert-comparison、hearing）、`docs/architecture/**`、`docs/verification/*.md`、`docs/cycles/BACKLOG.md`、`local/compose/README.md`（→ `docker/compose/README.md`）
- **001〜006 のサイクルの文書（design / design-log / build / review）は当時の木の記録なので書き換えない。** `docs/development.md` に「007 より前のサイクルの文書は当時のパス（`agent/` `terraform/` 等）で書かれている」と 1 行入れる
- 手順: 置換表（下）を `sed` で当ててから、`grep -n` で残りを目視。コードのコメント（各 app の docstring、terraform のコメント）も同じ表で置換する

置換表（単語境界に注意。`lab/` は S3 の prefix と `docs/architecture/resources/lab-ec2.md` の文脈があるので、`app/containerlab/` に置き換えてよいのはリポジトリのパスとして使っている箇所だけ）:

| いま | あと |
|---|---|
| `agent/` | `app/agentcore/` |
| `workflow/` | `app/temporal/` |
| `web/` | `app/dashboard/` |
| `lab/`（リポジトリのパス） | `app/containerlab/` |
| `nautobot/ spark/ telegraf/ grafana/ splunk/ neo4j/ graph/` | `app/<同名>/` |
| `kb-docs/` | `app/resources/` |
| `<名前>/Dockerfile` | `docker/images/<名前>/Dockerfile` |
| `local/compose/` | `docker/compose/` |
| `oss/terraform/` | `IaC/terraform/oss/` |
| `terraform/`（`oss/terraform/` を先に置換してから） | `IaC/terraform/aws-managed/` |
| `cloudformation/` | `IaC/cloudformation/` |

### 版上げ（commit 3）

| 部品 | いま | あと | 変える場所 |
|---|---|---|---|
| Grafana | 13.2.2 | 13.2.3 | `docker/images/grafana/Dockerfile` の ARG、`ops/up-common.sh:230`、`IaC/terraform/aws-managed/pipeline/analytics/variables.tf:243`、`docker/compose/compose.yaml`（4 か所を同時に。`test_oss_ops.py:710` が `GRAFANA_VERSION` を照合） |
| Telegraf | 1.40.0 | 1.40.1 | `docker/images/telegraf/Dockerfile:7`、`ops/lab-common.sh:11`、`…/stream/variables.tf:80`、`docker/compose/compose.yaml`、`IaC/cloudformation/lab-debug.yaml` の既定値（`tests/test_lab_debug.py:77` が lab-common と Dockerfile の一致を照合。`memory: srlinux-tag-sync` のとおり variables.tf と CFn も同値） |
| Splunk | 10.4.3 | 10.4.4（条件付き） | `tests/check_splunk_image.py` を 10.4.4 で走らせ、boto3 のアラート送信が通ったときだけ `docker/images/splunk/Dockerfile:8`、`ops/up-common.sh:180`、`…/analytics/variables.tf:181`、`docker/compose/compose.yaml`。通らなければ据え置き、理由を build.md に |

他（Redis 8 系、EMR 7.14 + jar、Iceberg 1.12）は 2026-10-08 に別ブランチで済んでいる（BACKLOG の `[x]`）。PostgreSQL 18 / Spark 4 / Python 3.14 は据え置き。

## 変更対象ファイル

- 動かす（`git mv`）: 上の表の全ディレクトリ、Dockerfile 8 本、`local/compose/` 一式、`oss/terraform/` のシンボリックリンク 90 本（貼り直し）
- 書き換える（コード）: `ops/common.sh` `ops/up.sh` `ops/up-common.sh` `ops/lab-common.sh` `ops/lab-debug.sh` `ops/sync-graph.sh` `ops/check.sh` `ops/seed_graph.py` `oss/ops/up.sh` `oss/ops/down.sh` `oss/ops/oss-images.sh` `oss/compose/compose.yaml` `oss/compose/check_neo4j.py` `oss/compose/check_vm.py` `docker/compose/compose.yaml` `docker/compose/up.sh` `docker/compose/lab.sh` `app/dashboard/config.py` `.env.example` `.gitignore` `pyproject.toml`（コメント）
- 書き換える（Terraform）: `IaC/terraform/aws-managed/agent/locals.tf` `agent/kb.tf` `agent/outputs.tf` `workflow/locals.tf` `workflow/gateway.tf` `pipeline/graph/sync.tf` `base/core/outputs.tf` `pipeline/lab/outputs.tf` `pipeline/stream/variables.tf`、`IaC/terraform/oss/pipeline/graph/sync.tf`、`IaC/cloudformation/lab-debug.yaml`（コメント）
- 書き換える（tests）: `tests/test_*.py` 14 本と `tests/check_splunk_image.py`
- 書き換える（docs）: 上の「docs の書き換え」の一覧。`docs/deploy.md` に state の移し方、`docs/development.md` に古いサイクルの文書の注記と `DATA_DIR` の変更

## 再利用するもの

- `ops/lab-common.sh` の `dir_tag`（引数を足すだけ）。`fetch` / `mirror_image` はそのまま
- `ops/common.sh` の `TF_DIR` と `tf_init_root`（既定値を変えるだけ。`-chdir="$TF_DIR/$root"` の形はそのまま）
- `tests/test_oss.py` の `links_to_managed`（比較先のパスを変えるだけ）
- `tests/check_splunk_image.py`（Splunk 10.4.4 の判定に使う。設計の変更なし）
- `ops/check.sh` の構造（`ROOTS` × `TF_BASES`、`bash -n`、`find … ast.parse`、`tests/test_*.py` の順次実行）

## 実装ステップ

0. ブランチは `docs/cycle-006-design` から切る（006 の `local/compose/` が要る）。`bash ops/check.sh` が「すべて通過」で始まることを確かめ、`git ls-files | wc -l`（434）と `git ls-files -s | awk '$1=="120000"' | wc -l`（90）を build.md に書く
1. **commit 1（git mv）**: ディレクトリと Dockerfile と `local/compose/` を `git mv`。シンボリックリンクを上のループで貼り直す。`git status` に untracked が無いこと、`find IaC/terraform/oss -type l ! -exec test -e {} \; -print` が空（壊れたリンクが無い）を確かめる
2. **commit 2（参照）**: 上の 3 つの表のとおり書き換える。置換したあと `git grep -n -E '(^|[^/a-zA-Z0-9_])(agent|workflow|web|lab|kb-docs|local/compose|oss/terraform|cloudformation)/' -- ':!docs/cycles/00[1-6]-*' ':!docs/verification'` で残りを数え、S3 の prefix と「当時の記録」以外が 0 になるまで直す。`bash ops/check.sh` を通す
3. **commit 3（dir_tag と版上げ）**: `dir_tag` の引数を足し、呼び元 8 か所に Dockerfile を渡す。Grafana と Telegraf を上げる。Splunk は `tests/check_splunk_image.py` の結果で決める。`bash ops/check.sh` を通す
4. `docs/deploy.md` と `docs/development.md` の追記は commit 2 に入れる
5. セルフレビュー（`/robust`）は `/cycle-build` 手順6 のとおり

## 検証方法（期待出力まで）

| # | コマンド | 期待 |
|---|---|---|
| 1 | `bash ops/check.sh` | 最後に `すべて通過`、exit 0（af6be54 と同じ。fmt 差分なし、18 ルートの validate が `OK`、`bash -n` と ast.parse が通り、`tests/test_*.py` 14 本が全部通る） |
| 2 | `git diff --stat docs/cycle-006-design -M` | rename 行（`=>`）と参照の書き換えだけ。新規に増えるファイルは無し（`docs/cycles/007-*/build.md` を除く） |
| 3 | `git log --follow --oneline app/agentcore/app.py \| wc -l` | 2 以上（移動前の履歴が追える。1 なら `git mv` でなくコピーになっている） |
| 4 | `ls` | `app docker IaC ops oss tests tools docs`（+ gitignore 対象の `jars` `wheels`、`pyproject.toml` `README.md` `CLAUDE.md` `.env.example`）。`agent terraform local cloudformation kb-docs` が無い |
| 5 | `ls docker/images` | `agentcore grafana nautobot neo4j spark splunk telegraf temporal` の 8 つ。各ディレクトリに `Dockerfile` だけ |
| 6 | `git ls-files -s IaC/terraform/oss \| awk '$1=="120000"' \| wc -l` と `find IaC/terraform/oss -type l ! -exec test -e {} \; -print` | `90` と空 |
| 7 | `for r in …9 ルート; do terraform -chdir=IaC/terraform/aws-managed/$r validate; done`（oss も） | 18 回 `Success!`（check.sh の 2 と同じ。state は無いので `plan` は確かめない。理由は下のリスク 1） |
| 8 | `docker build -f docker/images/telegraf/Dockerfile app/telegraf` と grafana、spark | 3 つとも exit 0（push しない。arm64 のビルドが出来ない PC なら `--platform` 無しでよい） |
| 9 | `. ops/lab-common.sh; a=$(dir_tag 1 app/telegraf docker/images/telegraf/Dockerfile); echo '# x' >> docker/images/telegraf/Dockerfile; b=$(dir_tag 1 app/telegraf docker/images/telegraf/Dockerfile); git checkout docker/images/telegraf/Dockerfile; [ "$a" != "$b" ] && echo 変わる` | `変わる`（Dockerfile だけ変えてもタグが変わる） |
| 10 | `cd docker/compose && docker compose config >/dev/null && echo OK` | `OK`（`context` と `dockerfile` の解決が通る。`.env` は `.env.example` を写す） |
| 11 | `python3 app/containerlab/lab_topology.py app/containerlab --snmp-agents` | いまの `python3 lab/lab_topology.py lab --snmp-agents` と同じ出力（移動前に取っておいて diff） |
| 12 | `cd app/dashboard && python3 -c 'import config'`（`AWS_REGION=ap-northeast-1` を環境に） | 例外なし。`config.DATA_DIR` が `<根>/app/agentcore/data` の絶対パス |
| 13 | `git grep -n -E '(^\|[^/a-zA-Z0-9_])(agent\|workflow\|web\|kb-docs\|local/compose\|oss/terraform\|cloudformation)/' -- ':!docs/cycles/00[1-6]-*' ':!docs/verification' ':!docs/cycles/007-*'` | 0 行（`lab/` は S3 の prefix があるので別に目視） |
| 14 | `tests/check_splunk_image.py`（10.4.4） | 通れば 10.4.4、通らなければ 10.4.3 のまま。どちらでも結果を build.md に |

## 未確定事項とリスク

1. **`terraform plan` で state を失っていないことは確かめられない。** AWS のリソースが全部消えていて state が空なので、`plan` は「全部作る」になる。validate と、state を `mv` する手順（docs/deploy.md）で代える。次に `up.sh` を打ったときに「`init` が provider を落とし直すだけで apply は通る」ことが事後の確認になる
2. **`gateway.tf` の `tools_files` のキーが zip の中のパスを兼ねているか**は、実装で `gateway.tf:16-44` を読んで確かめる（上の「Terraform の相対パス」の末尾）。兼ねていれば src / dst に分ける。分けずにキーを変えると Lambda の import が壊れる
3. **compose の `dockerfile:` の相対の起点。** compose v2 では `dockerfile` は `context` からの相対（Docker の仕様）。検証 10 の `docker compose config` で解決されたパスを見て確かめる。通らなければ `dockerfile` を絶対パス風（`../../docker/images/…` を `build.context` と同じ起点で）に直す
4. **`dir_tag` の変更で全イメージのタグが変わる。** 次の `up.sh` で 8 イメージ全部をビルドし直す（Nautobot 約 1 GB、Splunk 2〜3 GB で 30 分前後）。壊れはしない
5. **他の worktree（`fix/oss-ops-vpc-tty`、`fix/oss-ecr-outputs-nautobot-job`）が古いパスで作業中。** 007 のマージは、この 2 本を先に `docs/cycle-006-design` に入れてから。順序は PM が持つ。逆にすると rename との衝突になる
6. **Splunk 10.4.4 の amd64 イメージで boto3 のアラート送信が通るか**は未確認（検証 14 で決める）
7. **S3 の prefix `lab/` と CloudFormation の `lab/`**（`lab-debug.yaml:454,456`、`pipeline/lab/outputs.tf:23`、`lab-common.sh:81` の `s3://…/lab/`）は**リポジトリのパスではない**。機械的な置換で `app/containerlab/` にすると lab の EC2 が `setup.sh` を見つけられなくなる。置換のあと `git grep 's3://.*lab/'` で残っていることを確かめる
8. **`docs/cycles/00[1-6]` を書き換えない判断**は、古い文書を読む人が「`agent/` って何」となるのを `docs/development.md` の 1 行で受ける。読み替え表はこの design.md の「docs の書き換え」にある
<!-- artifact: /Users/eight/Documents/repo/artifacts/nwc-poc/20261008-cycle-007-restructure-dirs-design.html -->
