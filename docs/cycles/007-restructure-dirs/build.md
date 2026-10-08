# Cycle 007 restructure-dirs 実装の記録

## Round 1

実装モデル: claude-opus-5-5 / effort: xhigh

エンジニア2（PM [37a4d0] の指示）。ブランチ `feat/restructure-dirs`（`docs/cycle-006-design` の 66929ba から）。AWS には何も立てていない。

### commit

| commit | 内容 | 規模 |
|---|---|---|
| c46cd7e | 1: `git mv` とシンボリックリンクの貼り直し | 402 files（R 218、リンクの A/D 92 組） |
| 48683dd | 2: 参照の書き換え | 243 files（IaC/terraform 93、docs 40、tests 15、ops 11、app/agentcore 11、app/grafana 9、oss 8、docker/images 7、docker/compose 7、app/dashboard 7、ほか） |
| e4d45c3 | 3: `dir_tag` に Dockerfile を混ぜ、Grafana 13.2.3・Telegraf 1.40.1・Splunk 10.4.4 に上げる | 33 files |
| d25da17 | セルフレビューの直し（state の移し方の見張り、置換しすぎた `web/` `splunk/` を戻す、前の配置の環境はマージ前に消す注記） | 5 files |

移したもの（design.md の表どおり）:

- `agent/`→`app/agentcore/`、`workflow/`→`app/temporal/`、`web/`→`app/dashboard/`、`lab/`→`app/containerlab/`、`kb-docs/`→`app/resources/`、`nautobot/ spark/ telegraf/ grafana/ splunk/ neo4j/ graph/`→`app/<同じ名前>/`
- 各 `Dockerfile`→`docker/images/<app の名前>/Dockerfile`（8 つ）、`local/compose/`→`docker/compose/`
- `terraform/`→`IaC/terraform/aws-managed/`、`oss/terraform/`→`IaC/terraform/oss/`、`cloudformation/`→`IaC/cloudformation/`

commit 3 の変更ファイル: `ops/lab-common.sh`（`dir_tag <版> <ディレクトリ> [ファイル...]`、ディレクトリが無ければ失敗）、`ops/up-common.sh`・`ops/up.sh`・`oss/ops/oss-images.sh`・`oss/ops/up.sh`（呼び元 8 か所に `docker/images/<名前>/Dockerfile` を渡す、版）、`docker/images/{grafana,telegraf,splunk,spark,neo4j}/Dockerfile`、`docker/compose/compose.yaml`、`IaC/terraform/aws-managed/{base/ecr/outputs.tf,pipeline/analytics/{variables.tf,terraform.tfvars.example},pipeline/stream/{variables.tf,terraform.tfvars.example}}`、`IaC/terraform/oss/pipeline/analytics/{spark.tf,neo4j.tf}`（リンク先の実体はマネージド版のもの）、`IaC/cloudformation/lab-debug.yaml`（説明文だけ）、`app/splunk/.../{netops_sns.py,alert_actions.conf}`（コメント）、`tests/{check_splunk_image.py,test_alerts.py,test_lab_debug.py,test_oss_ops.py}`、`docs/{architecture/resources/{grafana,splunk,telegraf}.md,data-stores.md,deploy.md,faq-fukuda-nwc-poc.md,pipeline.md}`、`docs/cycles/007-restructure-dirs/design-log.md`。

### 設計から逸脱した点

`design-log.md` の「Round 1」の表に全部書いた（数の違い、`repo_root` の段数、`docs/verification/` を書き換えない、検証 13 の例外、`dir_tag` の引数を commit 2 で先に変えた、`neo4j/` の置換しすぎを戻した、`dir_tag` のディレクトリ検査、検証 2 の A/D 12 組、セルフレビューで見つけた 3 つ、など 27 行）。

### 着手前の確認（66929ba）

```
$ bash ops/check.sh   # 移動前（terraform/ と oss/terraform/）
通過 137 / 失敗 0 通過 489 / 失敗 0 通過 158 / 失敗 0 通過 72 / 失敗 0 通過 7 / 失敗 0 通過 82 / 失敗 0 通過 77 / 失敗 0 通過 167 / 失敗 0 通過 139 / 失敗 0 通過 56 / 失敗 0 通過 75 / 失敗 0 通過 96 / 失敗 0 通過 325 / 失敗 0 すべて通過
exit=0
$ git ls-tree -r --name-only 66929ba | wc -l
     436
$ git ls-tree -r 66929ba -- oss/terraform | awk '$1=="120000"' | wc -l
      92
```

（`test_lab_debug` は移動前 82、いまは `dir_tag` の 2 項目を足して 84）

### 検証（d25da17 のあと、未コミットの変更が無い状態で取り直した）

検証用の台本は scratchpad の `verify.sh`（design.md の 1〜13 をそのまま打つ）。14 は e4d45c3 のあとに別に打った（d25da17 は Splunk のイメージと `app/splunk/` に触っていない）。

#### 1. `bash ops/check.sh`

```
exit=0
== 1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss
== 2. 9 つのルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）
== 3. ops スクリプトの構文
== 4. 模擬テスト
通過 137 / 失敗 0
通過 489 / 失敗 0
通過 158 / 失敗 0
通過 72 / 失敗 0
通過 7 / 失敗 0
通過 84 / 失敗 0
通過 77 / 失敗 0
通過 167 / 失敗 0
通過 139 / 失敗 0
通過 56 / 失敗 0
通過 75 / 失敗 0
通過 96 / 失敗 0
通過 325 / 失敗 0
すべて通過
```

（14 本のうち 1 本は「68 項目すべて通過」の形で出る。fmt は差分なし、validate は 7 の 18 行）

#### 2. `git diff --stat docs/cycle-006-design -M`

書いてあるとおりのコマンドは、ブランチを切ったあとに `docs/cycle-006-design` へ 507ba75（`fix/oss-ops-vpc-tty`、10 files）が入ったので、その逆向きの差分を含む。分岐点（66929ba）からも取った。

```
$ git diff --stat docs/cycle-006-design -M | tail -1
 498 files changed, 2825 insertions(+), 2944 deletions(-)
   104 A / 104 D / 84 M / 206 R
$ git diff --stat 66929ba HEAD -M | tail -1
 495 files changed, 2791 insertions(+), 2662 deletions(-)
   104 A / 104 D / 81 M / 206 R
-- A のうち通常のファイル（120000 = シンボリックリンク以外）:
100644 IaC/terraform/aws-managed/base/core/outputs.tf
100644 IaC/terraform/oss/pipeline/graph/oss.auto.tfvars
100644 IaC/terraform/oss/pipeline/lab/oss.auto.tfvars
100644 IaC/terraform/oss/pipeline/nautobot/oss.auto.tfvars
100644 IaC/terraform/oss/workflow/oss.auto.tfvars
100644 app/agentcore/requirements-oss.txt
100644 app/dashboard/requirements-oss.txt
100644 app/grafana/provisioning/dashboards/netops.yaml
100644 app/graph/requirements-oss.txt
100644 app/nautobot/requirements-oss.txt
100644 app/nautobot/requirements.txt
100644 app/temporal/requirements-oss.txt
（D の通常のファイルはこの 12 の移動前のパスと 1 対 1。残る A/D 92 組はリンク）
-- commit ごとの状態と mode:
c46cd7e   92 A:120000   92 D:120000  209 R:100644    9 R:100755
48683dd  227 M:100644   16 M:100755
e4d45c3   31 M:100644    2 M:100755
d25da17    5 M:100644
-- 12 組の git log --follow（commit 数、最古のパス）:
IaC/terraform/aws-managed/base/core/outputs.tf          26 commits, 最古 terraform/main/outputs.tf
IaC/terraform/oss/pipeline/graph/oss.auto.tfvars        3 commits, 最古 oss/terraform/agent/oss.auto.tfvars
IaC/terraform/oss/pipeline/lab/oss.auto.tfvars          3 commits, 最古 oss/terraform/agent/oss.auto.tfvars
IaC/terraform/oss/pipeline/nautobot/oss.auto.tfvars     3 commits, 最古 oss/terraform/agent/oss.auto.tfvars
IaC/terraform/oss/workflow/oss.auto.tfvars              3 commits, 最古 oss/terraform/agent/oss.auto.tfvars
app/agentcore/requirements-oss.txt                      3 commits, 最古 agent/requirements-oss.txt
app/dashboard/requirements-oss.txt                      3 commits, 最古 web/requirements-oss.txt
app/grafana/provisioning/dashboards/netops.yaml         3 commits, 最古 grafana/provisioning/dashboards/netops.yaml
app/graph/requirements-oss.txt                          3 commits, 最古 graph/requirements-oss.txt
app/nautobot/requirements-oss.txt                       3 commits, 最古 nautobot/requirements-oss.txt
app/nautobot/requirements.txt                           3 commits, 最古 nautobot/requirements.txt
app/temporal/requirements-oss.txt                       3 commits, 最古 workflow/requirements-oss.txt
```

新しいファイルは増えていない（commit 1 は R とリンクの A/D だけ、2 以降は M だけ）。`oss.auto.tfvars` の 4 つは、作ったときから中身が同じ兄弟（`agent/` など 7 つが同じ blob `835a11a9`）なので、`--follow` は `agent/` の方へ辿る。commit 1 単独の diff では 4 つとも自分のパスから R100 で組になる（`git show -M c46cd7e`）。

#### 3. `git log --follow --oneline app/agentcore/app.py | wc -l`

```
      24
```

#### 4. `ls`

```
CLAUDE.md GLOSSARY.md IaC README.md app deploy.env.example docker docs ops oss pyproject.toml tests tools uv.lock
-- 無いはずのもの（agent workflow web lab terraform local cloudformation kb-docs nautobot spark telegraf grafana splunk neo4j graph）:
(ここまで)
```

#### 5. `ls docker/images`

```
agentcore grafana nautobot neo4j spark splunk telegraf temporal
docker/images/agentcore/: Dockerfile
docker/images/grafana/: Dockerfile
docker/images/nautobot/: Dockerfile
docker/images/neo4j/: Dockerfile
docker/images/spark/: Dockerfile
docker/images/splunk/: Dockerfile
docker/images/telegraf/: Dockerfile
docker/images/temporal/: Dockerfile
```

#### 6. シンボリックリンク

```
$ git ls-files -s IaC/terraform/oss | awk '$1=="120000"' | wc -l
      92
-- 壊れたリンク（find IaC -type l ! -exec test -e {} \; -print）:
(ここまで)
```

期待の 90 との差は design-log の 1 行目。

#### 7. terraform validate（check.sh の 2）

```
IaC/terraform/aws-managed/base/ecr  OK
IaC/terraform/aws-managed/base/core  OK
IaC/terraform/aws-managed/agent  OK
IaC/terraform/aws-managed/pipeline/lab  OK
IaC/terraform/aws-managed/pipeline/stream  OK
IaC/terraform/aws-managed/pipeline/analytics  OK
IaC/terraform/aws-managed/pipeline/graph  OK
IaC/terraform/aws-managed/pipeline/nautobot  OK
IaC/terraform/aws-managed/workflow  OK
IaC/terraform/oss/base/ecr  OK
IaC/terraform/oss/base/core  OK
IaC/terraform/oss/agent  OK
IaC/terraform/oss/pipeline/lab  OK
IaC/terraform/oss/pipeline/stream  OK
IaC/terraform/oss/pipeline/analytics  OK
IaC/terraform/oss/pipeline/graph  OK
IaC/terraform/oss/pipeline/nautobot  OK
IaC/terraform/oss/workflow  OK
```

`plan` は打っていない（state が無い。design.md のリスク 1）。

#### 8. `docker build -q -t nwc-007-verify-<名前>:local -f docker/images/<名前>/Dockerfile app/<名前>`（push しない、`--platform` 無し）

```
telegraf exit=0 sha256:fa2541006f8a9e122a522add10c2827e45cc1ab9d65b95685e835275047a2206
grafana exit=0 sha256:7b03bddb68cbd73bce6ef49a08a562dbaee044aef819256ab03e6e45d9730d3d
spark exit=0 sha256:4e86e591ae053963e5c1843f478bc7475e68162aa798aa4f3a8e2c8f54651907
```

#### 9. `dir_tag` は Dockerfile だけ変えてもタグが変わる

```
Updated 1 path from the index
a=1-302f76441bb4 b=1-332bd45f76f8
変わる
（そのあとの git status --porcelain --untracked-files=no は空）
```

#### 10. `cd docker/compose && docker compose config >/dev/null && echo OK`（`.env` は `.env.example` を写し、終わったら消した）

```
OK
-- build の解決（docker compose config --format json の services.*.build）:
grafana /…/restructure-dirs/app/grafana ../../docker/images/grafana/Dockerfile
spark-http /…/restructure-dirs/app/spark ../../docker/images/spark/Dockerfile
spark-splunk /…/restructure-dirs/app/spark ../../docker/images/spark/Dockerfile
splunk /…/restructure-dirs/app/splunk ../../docker/images/splunk/Dockerfile
telegraf /…/restructure-dirs/app/telegraf ../../docker/images/telegraf/Dockerfile
```

`dockerfile` は `context` からの相対で `docker/images/<名前>/Dockerfile` に届く（リスク 3）。build の解決は d25da17 のあとに打ち直して同じ（`.env` は消した）。

#### 11. `lab_topology.py`（移動前に `lab/` で取った出力との diff）

```
snmp-agents 同じ
--device-map 同じ
--gnmi-targets 同じ
--layers 同じ
```

#### 12. dashboard の config

`cd app/dashboard && ENV_FILE=/nonexistent DATA_DIR=app/agentcore/data AWS_REGION=ap-northeast-1 uv run --quiet python -c 'import config, os; print(config.DATA_DIR); print(os.path.isdir(config.DATA_DIR))'`（`.env` を読まないよう `ENV_FILE` を無いパスにし、`DATA_DIR` は `.env.example` の値を渡した）

```
/Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/restructure-dirs/app/agentcore/data
True
```

#### 13. grep

0 行にはならない。残る行は全部リポジトリのパスでない（design-log の例外の行）。d25da17 で戻した S3 の prefix（`.env.example:84`、`outputs.tf:104`）とログのストリーム（`troubleshooting.md:88`）、`docs/deploy.md` の移し方の節（007 より前のパスを名指しする）が前回から増えた。

```
$ git grep -n -E '(^|[^/a-zA-Z0-9_])(agent|workflow|web|kb-docs|local/compose|oss/terraform|cloudformation)/' -- ':!docs/cycles/00[1-6]-*' ':!docs/verification' ':!docs/cycles/007-*' | cut -c1-220
.env.example:47:# **既定は app/dashboard/data**（EC2 が S3 の web/data/ を取る先。user_data は絶対パスで書く）。
.env.example:80:#   PYTHONPATH=/opt/<接頭辞>-web/lib            pip install --target で入れた依存の場所
.env.example:81:#   GRADIO_TEMP_DIR=/var/lib/<接頭辞>-web/tmp   systemd の StateDirectory（DynamicUser なので他は書けない）
.env.example:84:# EC2 では使わない。EC2 の journald に KeyError: 'MODEL_ID' が出たら、S3 の web/app.py に app/agentcore/app.py を置いている
IaC/terraform/aws-managed/agent/kb.tf:2:# create_knowledge_base = true のときだけ作る（count）。バケットは IaC/terraform/aws-managed/base/core のもの（web/ lab/ stream/ と共用）。
IaC/terraform/aws-managed/base/core/bucket.tf:2:# web/（画面のコードと wheel）、docs/（KB の取り込み元。IaC/terraform/aws-managed/agent の create_knowledge_base = true のとき）、lab/ telegraf/ ana
IaC/terraform/aws-managed/base/core/bucket.tf:4:# force_destroy = true なので、docs/ web/ lab/ telegraf/ analytics/ が残っていても terraform destroy で消える
IaC/terraform/aws-managed/base/core/outputs.tf:104:  description = "Run in this repository after \"pip download\" into wheels/ (step 4 of ops/up.sh). Copies every app/dashboard/*.py (app / config / chat / topology_view /
IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:25:# バケットは IaC/terraform/aws-managed/base/core が作るので、初回の apply の時点では web/ が空。置いてから reboot-instanc
IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:27:  echo "web/ is not in s3://${bucket} yet. Upload it (output upload_web_command) and reboot the instance." >&2
IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:49:GRADIO_TEMP_DIR=/var/lib/${name_prefix}-web/tmp
IaC/terraform/aws-managed/base/core/web.tf:27:# 画面のコード・静的データ・wheel は同じバケットの web/ に置く（ops/up.sh の手順 4）。docs/ は読ませない。
IaC/terraform/aws-managed/base/core/web.tf:47:          StringLike = { "s3:prefix" = "web/*" }
IaC/terraform/aws-managed/base/core/web.tf:65:# 先頭の cloud-config で起動のたびにスクリプトを流すので、S3 の web/ を置き直して再起動すれば画面も更新される
IaC/terraform/aws-managed/base/core/web.tf:111:  # 起動スクリプトが S3 gateway（web/ と dnf）と ssm / ssmmessages のエンドポイント（SSM Agent の登録）を使うので、エンドポイントと SG
IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf:12:  description = "Log group of the task. Streams: web/ (migration, bootstrap, uwsgi), worker/ (the jobs), redis/"
docs/architecture/README.md:63:├── agent/         AGENT=1     Runtime / ガードレール / KB
docs/architecture/README.md:70:└── workflow/      WORKFLOW=1  Temporal on ECS / Gateway（MCP）/ SQS（SNS の購読と、承認・却下の decisions）
docs/architecture/resources/nautobot.md:19:| ログ | `/ecs/<prefix>-nautobot`。ストリームは `web/`、`worker/`、`redis/` | `nautobot.tf` |
docs/architecture/resources/web-ec2.md:18:| 画面のコード | 共有バケット `<prefix>-kb-<アカウント>` の `web/` から起動時に取る | `ops/up.sh` の手順 4、`IaC/terraform/aws-managed/base/core/buc
docs/architecture/resources/web-ec2.md:19:| ロール | `<prefix>-web`。SSM の管理、`web/*` の読み取り、`/<prefix>/*` の `ssm:GetParameter`。Runtime と Gateway を呼ぶ許可は agent と workflow のル
docs/architecture/resources/web-ec2.md:35:| S3 | Web → バケット | gateway 型エンドポイント（`web/` の取得と dnf） |
docs/deploy.md:125:2026-10-08 の cycle 007 で Terraform のルートを `terraform/` から `IaC/terraform/aws-managed/` へ、`oss/terraform/` を `IaC/terraform/oss/` へ移した。gitignore 対象の `.terraform/`
docs/deploy.md:129:消したあとも state は残るので、マージのあと `ops/check.sh` や `ops/up.sh` を打つ前に、前のチェックアウトの直下で一度だけ移す（手元の docker compose の `
docs/deploy.md:138:        oss/terraform/*) mv_new "$p" "IaC/terraform/oss/${p#oss/terraform/}" ;;
docs/deploy.md:141:  mv_new local/compose/.env docker/compose/.env
docs/development.md:7:2026-10-08 の cycle 007 でディレクトリを `app/`（コード）・`docker/`（Dockerfile と compose）・`IaC/`（Terraform と CloudFormation）に並べ直した。**007 より前のサ
docs/nautobot.md:58:| ログ | `/ecs/<prefix>-nautobot` | ストリームは `web/`（migrate・bootstrap・画面）、`worker/`（Job）、`redis/` |
docs/pipeline.md:393:aws logs tail /ecs/<prefix>-nautobot --follow                                            # web/ が起動と bootstrap、worker/ が Job
docs/troubleshooting.md:61:| ブラウザが「接続できない」 | Web が落ちている。上のログを見る。`web/ is not in s3://` なら Web の部品が S3 に無いので `ops/up.sh` を打ち直す |
docs/troubleshooting.md:88:| 手順 7-3c で「Nautobot のサービスが 20 分たっても安定しない」 | 止まらずに先へ進み、最後にもう一度同じ注意が出る。初回は DB の migrate のあ
ops/seed_graph.py:4:Web と同じ環境変数（/etc/<prefix>-web.env）と依存（/opt/<prefix>-web/lib）で動かす。GUI の「静的データを投入」と同じ関数（graph.seed）を呼ぶ。
ops/up.sh:998:    echo "Nautobot は動いている（ログ: aws logs tail --region $REGION $(tf pipeline/nautobot output -raw log_group_name) --follow。起動時の seed と同期は web/ のストリームの bootstr
tests/test_app.py:671:    return (f"User: arn:aws:sts::{_ACCT}:assumed-role/nwc-web/i-0abc1234 is not authorized to perform: {action} "
-- 末尾に / の無い書き方（git grep -n -E '(^|[^/A-Za-z0-9_.-])(oss/terraform|local/compose|local/)' -- ':!docs/cycles/00[1-7]-*' ':!docs/verification'）:
docs/deploy.md:125:（上と同じ行）
docs/deploy.md:129:（上と同じ行）
docs/deploy.md:132:if [ -n "$(git ls-files terraform oss/terraform local)" ]; then echo "まだ 007 をマージしていない（前の配置のファイルが git にある）。マージしてから打つ"; else
docs/deploy.md:134:  find terraform oss/terraform \( -name .terraform -o -name 'terraform.tfstate*' -o -name .terraform.tfstate.lock.info -o -name '*.tfvars' -o -name .build \) -prune -print 2>/dev/null |
docs/deploy.md:138:（上と同じ行）
docs/deploy.md:141:（上と同じ行）
docs/deploy.md:142:  find agent cloudformation grafana graph kb-docs lab local nautobot neo4j spark splunk telegraf terraform web workflow oss/terraform \
docs/deploy.md:152:D="agent cloudformation grafana graph kb-docs lab local nautobot neo4j spark splunk telegraf terraform web workflow oss/terraform"
docs/development.md:7:（上と同じ行）
tests/test_alerts.py:629:check("app の中に認証情報や local/ は無い（公開リポジトリ）",
-- S3 の prefix lab/ が残っていること（git grep -n -E 's3://[^ ]*/lab/' -- ops oss IaC app）:
IaC/cloudformation/lab-debug.yaml:454:          aws s3 sync --delete --region ${AWS::Region} s3://${Bucket}/lab/ $LAB/src/
IaC/cloudformation/lab-debug.yaml:456:            echo "lab/ is not in s3://${Bucket}/lab/ yet. Run ops/lab-debug.sh up and reboot." >&2
IaC/cloudformation/lab-debug.yaml:464:    Description: lab/ goes to s3://<this>/lab/ (ops/lab-debug.sh)
IaC/terraform/aws-managed/pipeline/lab/outputs.tf:23:  value       = "aws s3 sync app/containerlab/ s3://${local.bucket}/lab/ --exclude \"splab.clab.yml\" && aws s3 cp containerlab_${var.containerlab_
IaC/terraform/aws-managed/pipeline/lab/templates/lab_user_data.sh.tftpl:33:aws s3 sync --delete --region ${region} s3://${bucket}/lab/ $LAB/src/
IaC/terraform/aws-managed/pipeline/lab/templates/lab_user_data.sh.tftpl:35:  echo "lab/ is not in s3://${bucket}/lab/ yet. Run ops/up.sh and reboot." >&2
ops/lab-common.sh:86:upload_lab() {  # upload_lab <バケット>  lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む（app/containerlab/setup.sh）。リポジトリの直下で呼
ops/lab-common.sh:89:  aws s3 sync --only-show-errors app/containerlab/ "s3://$1/lab/" --exclude "splab.clab.yml" --exclude "__pycache__/*" --exclude "*.DS_Store" || return 1
ops/lab-common.sh:90:  aws s3 cp --only-show-errors "$CONTAINERLAB_RPM" "s3://$1/lab/"
ops/lab-debug.sh:96:    log "lab/ を s3://$BUCKET/lab/ に置き直して EC2 を再起動する（起動のたびに app/containerlab/setup.sh が置き直す）"
ops/lab-debug.sh:97:    upload_lab "$BUCKET" || die "lab の材料を s3://$BUCKET/lab/ に置けなかった"
ops/lab-debug.sh:147:    log "3. lab の材料（containerlab の rpm とトポロジ）を s3://$BUCKET/lab/ に置く"
ops/lab-debug.sh:148:    upload_lab "$BUCKET" || die "lab の材料を s3://$BUCKET/lab/ に置けなかった"
ops/up.sh:839:# lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む。apply より前に置けば、最初の起動で入る（再起動が要らない）
ops/up.sh:852:  log "5-1. lab の材料（containerlab の rpm とトポロジ）を s3://$KB_BUCKET/lab/ に置く"
ops/up.sh:853:  upload_lab "$KB_BUCKET" || die "lab の材料を s3://$KB_BUCKET/lab/ に置けなかった"
oss/ops/up.sh:292:# lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む。apply より前に置けば、最初の起動で入る
oss/ops/up.sh:293:log "5-1. lab の材料（containerlab の rpm とトポロジ）を s3://$KB_BUCKET/lab/ に置く"
oss/ops/up.sh:294:upload_lab "$KB_BUCKET" || die "lab の材料を s3://$KB_BUCKET/lab/ に置けなかった"
```

#### 14. `tests/check_splunk_image.py`（10.4.4）

1 回目は CHECKED の行だけ NG（版を上げる前の記録のまま）。上の 10 件が通っているので CHECKED を 10.4.4 に書き換えて打ち直した。

```
-- 1 回目（抜粋）
NG 見えた版が CHECKED と同じ（{'splunk': '10.4.4', 'python': '3.13.11', 'boto3': '1.37.14'}）。違うなら、上が通っているので CHECKED を書き換える（Splunk の版を変えたら Dockerfile・ops/up.sh も）
    CHECKED={'splunk': '10.4.3', 'python': '3.13.11', 'boto3': '1.37.14'}
exit=1
-- 2 回目（全文）
-- docker/images/splunk/Dockerfile と app/splunk/ を linux/amd64 でビルドして nwc-splunk-check:local にする
-- nwc-splunk-check:local を nwc-splunk-check-93517 で起こした。healthy になるのを待つ（数分）
ok Splunk が入口（app/splunk/entrypoint.sh）から起きて healthy になる（80 秒）
ok splunkd が読む設定でも python.required = 3.13（btool）
ok 偽の認証情報の口と偽の SNS がコンテナの中で動く
ok 直に: Splunk の Python 3.13 で boto3 / botocore が読め、SNS のクライアントを作れる（app に同梱していない = Splunk の site-packages のもの）
   python 3.13.11（/opt/splunk/bin/python3.13）boto3 1.37.14 botocore 1.37.14 /opt/splunk/lib/python3.13/site-packages/boto3/__init__.py
ok 直に: netops_sns.send で偽の SNS へ 1 通届く（認証情報は偽の口から、署名つき、Query API の Publish）
-- HEC に link down（dc1-leaf-01 ethernet-1/1）を入れた。netops_poll（毎分）が送るのを待つ
ok 本物の流れ: アラートアクションが偽の SNS へ 1 通だけ送る（次の回で重ねて送らない）
ok 本物の流れ: 本文は Grafana と同じ形の JSON（source=splunk、link_down の firing 1 件）、件名は netops alert、form は Publish
ok 本物の流れ: splunkd は Python 3.13 で起こし、Splunk の boto3 で送っている（User-Agent: Python 3.13.11、boto3 1.37.14）
ok 本物の流れ: splunkd.log に件数（published=1/1）と exit code=0 が残る
-- bin/boto3.py を置いて、HEC に link down（ethernet-1/2）を入れた。splunkd.log に理由が出るのを待つ
ok boto3 が読めないとき: splunkd.log に理由（boto3 を読めない・Splunk の Python の版・確かめ方）が 1 行で出て、exit code=3。送らない
   etops_sns STDERR -  boto3 を読めない（ModuleNotFoundError: No module named boto3 (check_splunk_image が置いた偽物)）。Splunk の Python 3.13.11（/opt/splunk/bin/python3.13）に boto3 が無い。Splunk の版を変えたなら tests/check_splunk_image.py で確かめ、無ければ boto3 を app の lib/ に同梱する（git の ffba169）
ok 見えた版が CHECKED と同じ（{'splunk': '10.4.4', 'python': '3.13.11', 'boto3': '1.37.14'}）。違うなら、上が通っているので CHECKED を書き換える（Splunk の版を変えたら Dockerfile・ops/up.sh も）
すべて通過（11 件）
exit=0
```

`alert_actions.conf` の「3.13 と 3.9 の両方に boto3 1.37.14」を直すために、素の `splunk/splunk:10.4.4` を起こして両方の Python を見た（コンテナは消した）:

```
$ docker exec -u splunk <c> /opt/splunk/bin/splunk cmd python3.9|python3.13 -c "import sys, boto3; print(sys.version.split()[0], boto3.__version__, boto3.__file__)"
3.9.25 1.37.14 /opt/splunk/lib/python3.9/site-packages/boto3/__init__.py
3.13.11 1.37.14 /opt/splunk/lib/python3.13/site-packages/boto3/__init__.py
（docker ps -a --filter name=<c> の行数）0
```

→ **Splunk は 10.4.4 にした。**

### docs/cycle-006-design へのマージの見込み（PM 向け）

`git merge-tree` で HEAD と `docs/cycle-006-design`（248db1b）を試した。テキストの衝突は 2 つ、衝突しないが壊れるのが 1 つ。

- 衝突 `docs/cycles/BACKLOG.md`: こちらの `app/containerlab/lab.sh` の行を残し、向こうの [x] 2 行（完了の注記）を取る
- 衝突 `tests/test_oss_roll.py` の docstring: 向こうを取り、パスを `IaC/terraform/oss/` にする
- 衝突しないが落ちる `tests/test_oss_ops.py`（507ba75 が足した行）: 575・576・595・601 行の `oss/terraform` `terraform` `oss/terraform/base/core` を `IaC/terraform/oss` `IaC/terraform/aws-managed` `IaC/terraform/oss/base/core` にする

この 3 つを当てた写し（scratchpad の `mergeprobe/`、`git init` して `git add -A`）で `tests/test_*.py` 14 本が全部通り（`test_oss_ops` 144、`test_oss_roll` 66）、`bash -n` も通った。当てた差分は scratchpad の `merge_resolution.patch`（76 行）。

### セルフレビュー

- 自分: claude-opus-5-5 / effort xhigh（`/robust`、要望リスト = design.md の実装ステップ 0〜5 と検証 1〜14）
- 反対弁護人: Agent general-purpose、model opus、id a6695f9433aec7c5c、文脈を渡して読み取り専用で依頼。返ってきたあと `git status --porcelain -uall` は依頼前と同じ。反対弁護人は main のチェックアウトで `find` と `ls` だけを打ち（書き込みなし）、test_lab_debug（84/0）と test_local_compose（77/0）を走らせた

#### 指摘

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 確かめたこと | 片付け |
|---|---|---|---|---|---|---|
| 1 | Should fix | data loss | `docs/deploy.md` の state の移し方（直す前 128-136 行） | 移し先に `.terraform` があると入れ子になる・tfvars と state を上書きする・`terraform.tfstate.<時刻>.backup` を拾わない・`oss/terraform/*/.terraform` を移さない・`.DS_Store` と `__pycache__` で最後の find が空にならない・無条件の `rm -rf`・マージ前に打つと追跡中のファイルまで動く（反対弁護人 2 に自分の分を足した） | 下の「移し方の試験」 | 直した（d25da17）。`git ls-files` の見張り、`mv_new`（移し先があれば移さない）、`find -prune` で 2 つの根、`.env`、最後の find、見張り付きの `rm -rf`。bash と zsh で同じ結果 |
| 2 | Should fix | correctness（文書） | `.env.example:84`、`IaC/terraform/aws-managed/base/core/outputs.tf:104`、`docs/troubleshooting.md:88`・`:95` | S3 の prefix `web/` とログのストリーム `web/` `splunk/` をリポジトリのパスに置換しすぎた。KeyError: 'MODEL_ID' やログを追う人が S3 に無い `app/dashboard/` を探す（反対弁護人 3。troubleshooting の 2 行は自分で見つけた） | 検証 13 の grep に戻した 3 行が出る。`token_exist.py` と `old_token.py` で 007 より前の名前と新しい名前の残りを全部洗い、残りはどれも意図したもの | 直した（d25da17） |
| 3 | Should fix | runtime / 運用 | SG の description（`security_groups.tf` 12 行、`oss.tf` 6 行。ForceNew）、`web_user_data.sh.tftpl` 4 行と `lab_user_data.sh.tftpl` 1 行（`user_data_replace_on_change = true`、`web.tf:88`・`instance.tf:27`）、Lambda レイヤーの description（`IaC/terraform/oss/pipeline/graph/sync.tf:62`、新しい版になる）、`lab-debug.yaml:439`（UserData の中のコメント。EBS ルートなので止めて起こし直す） | 007 より前に立てた環境が生きたまま state を移して `up.sh` を打つと、SG と web・lab の EC2 が作り直され、OSS 版のレイヤーが新しい版になり、lab-debug が再起動する（反対弁護人 1。SG・レイヤー・lab-debug は自分で足した） | 下の「作り直しを起こす差分」。いまは環境が無い（2026-10-08 に全部消した） | deploy.md に「前の配置で立てた環境はマージの前に `ops/down.sh`・`oss/ops/down.sh`・`ops/lab-debug.sh down` で消す」と書き、design-log に 1 行。description から説明を外す案は、それ自体が作り直しを起こし「中身を変えない」にも反するので取らなかった |
| 4 | Nit | missing tests | `tests/test_lab_debug.py:443` | `-f docker/images/<x>/Dockerfile` が build の行にあるかだけを見て、context との組は見ない（反対弁護人 4） | 下の「-f と context の組」で 8 か所とも組が合っている。退行の注入 1・4・5・6 はこのテストで落ちる | 残す |
| 5 | Nit | 保守性 | `docker/compose/compose.yaml:45,62,176,198` | `dockerfile` が合っているのは `app/<x>` と `docker/compose` が同じ深さだから（反対弁護人 5） | 検証 10 の build の解決で 5 つとも正しいパス | 残す |
| 6 | Nit | 記録 | この build.md の 14 の 3.9/3.13 の行 | 整形した 1 行で、生ログではない（反対弁護人 6） | `alert_actions.conf` は `python.required = 3.13` で、check_splunk_image は 3.13 で通っている | 残す |
| 7 | Nit | missing tests | `tests/test_lab_debug.py` の `_tag_calls` | 版を引用符なしの `$V` で渡す呼び元を拾わない（いまは 8 か所とも `"$…"`） | 読んだだけ | 残す |
| 8 | Nit | 既存 | `outputs.tf` の `upload_web_command` | `app/dashboard/` に `.venv` があると S3 に上げる（007 より前からの挙動） | 読んだだけ | 残す（範囲外） |

#### 退行を注入してテストが落ちること（d25da17 のあと）

```
$ bash $S/inj_r2.sh   # test_lab_debug.py。1 つ注入して走らせ、git checkout で戻す
--- 注入0（何もしない）
exit=0
通過 84 / 失敗 0
--- 注入1: telegraf_tag から Dockerfile を外す
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag の呼び元 8 か所は、どれも docker build の -f と同じ docker/images/<名前>/Dockerfile を渡す（context が app/<名前>/ ならその名前と同じ）
--- 注入2: 3 つ目からのファイルをハッシュに入れない
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag は 3 つ目からのファイルもハッシュに入れる（Dockerfile だけ変えてもタグが変わり、同じ中身なら同じタグ。ディレクトリが無けれ
--- 注入3: isdir の確認を外す
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag は 3 つ目からのファイルもハッシュに入れる（Dockerfile だけ変えてもタグが変わり、同じ中身なら同じタグ。ディレクトリが無けれ
--- 注入4: oss-images の spark に neo4j の Dockerfile を渡す
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag の呼び元 8 か所は、どれも docker build の -f と同じ docker/images/<名前>/Dockerfile を渡す（context が app/<名前>/ ならその名前と同じ）
--- 注入5: build_grafana の -f を別の Dockerfile に
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag の呼び元 8 か所は、どれも docker build の -f と同じ docker/images/<名前>/Dockerfile を渡す（context が app/<名前>/ ならその名前と同じ）
--- 注入6: ops/up.sh の nautobot の dir_tag から Dockerfile を外す
 1 file changed, 1 insertion(+), 1 deletion(-)
exit=1
AssertionError: dir_tag の呼び元 8 か所は、どれも docker build の -f と同じ docker/images/<名前>/Dockerfile を渡す（context が app/<名前>/ ならその名前と同じ）
--- 後
?? docs/cycles/007-restructure-dirs/build.md
```

#### 移し方の試験（deploy.md の 2 つの断片を `mig1.sh`・`mig2.sh` に写し、作り物の残り物で bash と zsh の両方で打った）

```
$ bash mig_test.sh bash $S/migrun_bash > mig_bash2.log; bash mig_test.sh zsh $S/migrun_zsh > mig_zsh2.log
$ diff <(tail -n +2 mig_bash2.log) <(tail -n +2 mig_zsh2.log) && echo "1 行目（シェル名）以外は同じ"
1 行目（シェル名）以外は同じ
$ cat mig_bash2.log
== 1 回目 (bash)
移し先にもうある（移していない）: IaC/terraform/aws-managed/base/ecr/.terraform
lab/clab-splab/topology-data.json
== 移したあと
./IaC/terraform/aws-managed/base/core/.terraform/providers/p
./IaC/terraform/aws-managed/base/core/main.tf
./IaC/terraform/aws-managed/base/core/templates/a.tftpl
./IaC/terraform/aws-managed/base/core/terraform.tfstate
./IaC/terraform/aws-managed/base/core/terraform.tfstate.1789821564.backup
./IaC/terraform/aws-managed/base/core/terraform.tfstate.backup
./IaC/terraform/aws-managed/base/core/terraform.tfvars
./IaC/terraform/aws-managed/base/ecr/.terraform/x
./IaC/terraform/aws-managed/base/ecr/main.tf
./IaC/terraform/aws-managed/base/ecr/terraform.tfstate
./IaC/terraform/oss/pipeline/graph/.build/neo4j-layer/python/neo4j/__init__.py
./IaC/terraform/oss/pipeline/graph/.terraform/y
./IaC/terraform/oss/pipeline/graph/main.tf
./app/dashboard/app.py
./docker/compose/.env
./docker/compose/compose.yaml
./lab/clab-splab/topology-data.json
./ops/up.sh
./terraform/base/core/.DS_Store
./terraform/base/core/templates/.DS_Store
./terraform/base/ecr/.terraform/x
./web/.DS_Store
./web/__pycache__/a.pyc
== 2 回目の消す前の検査 (lab/clab-splab が残る)
消さなかった（マージの前か、上の find がまだ何か出している）
lab terraform web 
== clab-splab を片付けてから
ls: agent: No such file or directory ls: lab: No such file or directory ls: local: No such file or directory ls: oss/terraform: No such file or directory ls: terraform: No such file or directory ls: web: No such file or directory oss 
== 移した中身
tsbk-core
tfvars-core
prov-new
st-ecr
dummy
x
== マージ前の検査
まだ 007 をマージしていない（前の配置のファイルが git にある）。マージしてから打つ
消さなかった（マージの前か、上の find がまだ何か出している）
./terraform/base/core/main.tf
./terraform/base/core/terraform.tfstate
```

移し先に先にあった `.terraform`（check.sh の init が作る）は上書きせず元に残し、`lab/clab-splab` のような移す先の無い中身が残っていれば消さない。マージ前に打つと何も動かさない。実物の state では打っていない（未確認）。

#### 作り直しを起こす差分（66929ba → HEAD）

```
$ git diff -U0 66929ba:terraform/base/core/security_groups.tf HEAD:IaC/terraform/aws-managed/base/core/security_groups.tf | grep -c '^+ .*= ".*(IaC/terraform/aws-managed/'
12
$ git diff -U0 66929ba:terraform/base/core/oss.tf HEAD:IaC/terraform/aws-managed/base/core/oss.tf | grep -c '^+ .*= ".*(IaC/terraform/oss/'
6
$ git diff -U0 66929ba:terraform/base/core/templates/web_user_data.sh.tftpl HEAD:IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl | grep -c '^+[^+]'
4
$ git diff -U0 66929ba:terraform/pipeline/lab/templates/lab_user_data.sh.tftpl HEAD:IaC/terraform/aws-managed/pipeline/lab/templates/lab_user_data.sh.tftpl | grep -c '^+[^+]'
1
$ grep -n user_data_replace_on_change IaC/terraform/aws-managed/base/core/web.tf IaC/terraform/aws-managed/pipeline/lab/instance.tf
IaC/terraform/aws-managed/base/core/web.tf:64:# user_data を変えると Terraform はインスタンスを作り直す（user_data_replace_on_change）。
IaC/terraform/aws-managed/base/core/web.tf:88:  user_data_replace_on_change = true
IaC/terraform/aws-managed/pipeline/lab/instance.tf:27:  user_data_replace_on_change = true
$ git diff 66929ba:oss/terraform/pipeline/graph/sync.tf HEAD:IaC/terraform/oss/pipeline/graph/sync.tf | grep '^[-+] .*description'
-  description              = "Neo4j Python driver for the status Lambda (graph/requirements-oss.txt)"
+  description              = "Neo4j Python driver for the status Lambda (app/graph/requirements-oss.txt)"
-  description        = "Status Lambda of oss/terraform/pipeline/graph - writes the dynamic status of devices and links into Neo4j"
+  description        = "Status Lambda of IaC/terraform/oss/pipeline/graph - writes the dynamic status of devices and links into Neo4j"
$ git diff -U0 66929ba:cloudformation/lab-debug.yaml HEAD:IaC/cloudformation/lab-debug.yaml | grep '^@@' | tail -1
@@ -439 +439 @@ Resources:
```

（`plan` は打っていないので、作り直しは Terraform と CloudFormation の挙動からの見込み。Cloud Map の namespace と output の description はその場で変わる）

#### -f と context の組（Nit 4 を落とした根拠）

```
$ git grep -n -E "docker (buildx )?build .*-f docker/images/" -- ops oss | perl -ne '/^([^:]+:\d+):.*-f docker\/images\/([^\/]+)\/Dockerfile\s+(\S+)/ and print "$1  -f $2  context $3\n"'
ops/lab-common.sh:84  -f telegraf  context app/telegraf/
ops/up-common.sh:189  -f splunk  context app/splunk/
ops/up-common.sh:242  -f agentcore  context app/agentcore/
ops/up-common.sh:246  -f grafana  context app/grafana/
ops/up-common.sh:249  -f temporal  context app/temporal/
oss/ops/oss-images.sh:55  -f spark  context app/spark/
oss/ops/oss-images.sh:56  -f neo4j  context app/neo4j/
（nautobot は ops/up-common.sh:258-259 で行が分かれていて上の抜き出しに出ない。-f docker/images/nautobot/Dockerfile "$2" で、$2 は app/nautobot/ の写し。読んだ）
```

#### 「問題なし」とした観点と根拠

- Terraform のパス参照: 18 ルートの validate が通る（検証 7）。`repo_root` は aws-managed 4 段・oss 5 段で、`file()` の先が実在する（validate が読む）
- シンボリックリンク: 92 本、壊れたもの 0（検証 6）
- `tools_files`（`IaC/terraform/aws-managed/workflow/gateway.tf:15`）: 66929ba の `terraform/workflow/gateway.tf` と diff を取り、左（リポジトリのパス）だけが `agent/` → `app/agentcore/` に変わり、右（zip の中の名前）は 8 行とも同じ
- EC2 の web の配置: user_data が `DATA_DIR` を絶対パスで渡す（`.env.example:47` の注記と `web_user_data.sh.tftpl`。読んだだけ）。手元の解決は検証 12
- Markdown のリンク: scratchpad の `mdlinks.py`（`git ls-files '*.md'` の相対リンクの先が在るか。cycles の 001〜007 と verification は除く）が `リンク 569 件, 先が無い 0`
- A/D の 12 組: 中身の変更だけで、`--follow` で前の名前まで辿れる（検証 2）
- gitignore: `git check-ignore -v` で新しいパスの `.terraform`（`.gitignore:20`）・`terraform.tfvars`（:24）・`terraform.tfstate`（:21）・`docker/compose/.env`（:4）・`IaC/terraform/oss/pipeline/graph/.build/x`（:28 `IaC/terraform/**/.build/`）・`app/dashboard/.venv/x`（:17）・`__pycache__`（:1）がどれも無視される
- compose の `../../`: 検証 10
- `dir_tag` が cwd で変わらない: 呼び元は根へ cd してから呼ぶ。注入 2・3 が落ちるのでハッシュの中身はテストで縛られている
- Telegraf の版: `ops/lab-common.sh` と compose の args が 1.40.1 で同じ（`test_local_compose` 77/0）
- Splunk 10.4.4: 検証 14（3.13 の boto3 1.37.14）
- マージの見込み: 自分の `merge-tree` と反対弁護人の `merge-tree` が同じ 3 か所を出した
- 置換しすぎ: `token_exist.py`（66929ba..HEAD で足した行の `app/`・`docker/`・`IaC/` のパスのうち、手元に無いもの）は 29 行。どれも生成物（`.build/`・`tfstate`・`clab-splab`）、プレースホルダー（`<タグ>` 付き・glob）、テストの偽名（`app/x`・`app/none`）、無いことを確かめる側（`app/agentcore/anomalies.py`）、`.env.example` の手元の既定（`app/dashboard/data`）で、S3 の prefix やログのストリームの位置に出ているものは無い（指摘 2 を直したあと、全行を目で見た）

#### ジンテーゼ

- 部分的真実
  - 「中身を変えない」はコードの動きについては成り立つが、インフラには中立でない。説明文の変更でも、前の配置の環境が生きていれば作り直しが起きる（指摘 3）
  - state の移し方は 1 回だけの手作業で、作り物の残り物でしか確かめていない
  - 検証 13 の grep は 0 行にならない。残りは例外として 1 行ずつ説明するしかない
  - テストは `-f` と context の組を縛っていない（いまは合っている）
- 結論の言い直し: 実装は design.md の 14 項目を満たし、Must fix は無い。ただしマージしてよいのは、前の配置で立てた環境（`ops/down.sh`・`oss/ops/down.sh`・`ops/lab-debug.sh down`）を消したあとで、マージしたら `ops/check.sh` より前に state を移すことが条件。`terraform plan` は未確認
- 差分: deploy.md に注記と見張りを足し、置換しすぎた 4 か所を戻した。コードの動きは変えていない

### docs/cycle-006-design（248db1b）の取り込み（e60918b、merge commit）

PM の指示で `git merge docs/cycle-006-design` を打った（squash・rebase はしていない）。ぶつかった BACKLOG.md と `tests/test_oss_roll.py` は上の「マージの見込み」のパッチで解き、`tests/test_oss_ops.py:575/576/595/601` の `tf_dir` を `IaC/terraform/oss`・`IaC/terraform/aws-managed` にした。直す前の自動マージのままの `tests/test_oss_ops.py` は落ちる:

```
直す前 exit=1
AssertionError: oss/ops/down.sh（VPC が 2 つ、state から読めない）: タグで当たった 2 つを両方見て、新しい方の Runtime の ENI で base/core を残す
```

e60918b で、未コミットの変更が無い状態で取り直した:

```
$ bash ops/check.sh
exit=0
通過 137 / 失敗 0 通過 489 / 失敗 0 通過 158 / 失敗 0 通過 72 / 失敗 0 通過 7 / 失敗 0 通過 84 / 失敗 0 通過 77 / 失敗 0 通過 167 / 失敗 0 通過 144 / 失敗 0 通過 66 / 失敗 0 通過 75 / 失敗 0 通過 96 / 失敗 0 通過 325 / 失敗 0 すべて通過
$ git log --follow --oneline app/agentcore/app.py | wc -l
      24
$ git diff --stat=200 docs/cycle-006-design -M | tail -1
 496 files changed, 3326 insertions(+), 2666 deletions(-)
$ git diff --name-status -M docs/cycle-006-design | awk '{print substr($1,1,1)}' | sort | uniq -c
 105 A  104 D   81 M  206 R
```

（A が検証 2 より 1 つ多いのは、この build.md。検証 13 の grep 2 本は取り込む前の HEAD と同じ出力）
