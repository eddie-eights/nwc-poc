# netops の名前を nwc に揃える（019）

設計: PM(fable-5.1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

AWS の資源の名前は 2026-10-04 に `nwc-*`（接頭辞 `<owner>-nwc-poc`）に揃えたが、アプリの中の識別子には `netops` が残っている（Splunk の app `netops_alerts`、Grafana の `netops*.yaml`、Nautobot の `app/nautobot/netops/`、S3 Tables の名前空間 `netops` など）。ユーザーの決定（2026-10-09）: **名前は `nwc` に揃える。** 対象は AWS の名前ではなく、リポジトリの中に残る `netops` / `NetOps` 全部。

**動作は変えない。名前だけ変える。** 名前の変更に伴って作り直しになる AWS の資源（S3 Tables の名前空間とテーブル、Nautobot の Job の行）は、この PoC が環境をその日に消す前提なので移行の仕組みを作らない。2026-10-09 時点で AWS の環境は消えている（`ops/down.sh` 済み）。

### 調査で分かった事実（2026-10-09、`docs/cycle-006-design` 06a1e97）

`grep -rli netops`（`.git` / `.terraform` を除く）は 100 ファイル。`docs/cycles/` と `docs/verification/` は記録なので**触らない**（下の方針 4）。それ以外で `netops` を持つのは次のとおり。

- **Splunk**: `app/splunk/netops_alerts/`（`default/app.conf` の `id = netops_alerts` と `label = NetOps alerts`、`default/alert_actions.conf` の `[netops_sns]`、`default/savedsearches.conf` の `[netops_gnmi]` `[netops_trap]` `[netops_trap_clear]` と `action.netops_sns = 1`、`README/*.conf.spec`、`bin/netops_sns.py` の `SUBJECT = "netops alert"`）、`app/splunk/entrypoint.sh:11` `NETOPS_ALERTS_ENV`（既定値は既に `nwc-alerts.env`）と `:22` の `rm -rf …/apps/netops_alerts`、`docker/images/splunk/Dockerfile:11`、`IaC/terraform/aws-managed/pipeline/analytics/splunk.tf:5,102,417` と `variables.tf:179`、`base/ecr/outputs.tf:64`、`ops/up-common.sh:206`、`ops/up.sh:53`、`deploy.env.example:66`、`tests/check_splunk_image.py`（16 行）、`tests/test_alerts.py`（66 行）。
- **Spark の sourcetype**: `app/spark/snmp_sinks.py:113` `SPLUNK_SOURCETYPE_PREFIX = "netops"`（`netops:metrics` / `netops:traps` / `netops:logs` …）。読む側は Splunk の保存済みサーチと `docker/compose/check.sh:87,93`、docs。
- **Grafana**: `app/grafana/provisioning/alerting/netops.yaml`（連絡先。`subject: netops alert`、テンプレート `netops.sns`）、`netops-prometheus.yaml`、`netops-opensearch.yaml`、`dashboards/netops.yaml`（provider `name: netops` / `folder: netops`）、`dashboards/metrics.json:3,5` と `logs.json:3,5`（title `netops / …` とタグ）、`datasources-oss/*.yaml` のコメント、`app/grafana/start.sh:16` `SRC=/etc/grafana/netops` と `:32-48` の cp、`docker/images/grafana/Dockerfile:17`、`IaC/terraform/oss/pipeline/analytics/grafana.tf:10`、`app/temporal/rules.py:175`。
- **Nautobot**: `app/nautobot/netops/`（`bootstrap.py`、`nb_sync.py`、`nb_map.py`、…）、`app/nautobot/jobs/netops_jobs.py`（`name = "NetOps"` のグループ名、Job のクラスパス `netops_jobs.SyncTopology` / `SyncOnChange`）、`bootstrap.py` の `NETOPS_SEED`、`JOBS = ("netops_jobs.…")`、`HOOK = "netops-sync"`、ユーザー `netops-web`（`NAUTOBOT_API_USER` の既定値）、logger `netops.bootstrap`、`context_detail="netops-bootstrap"`、`nb_sync.py:26` `LOCK = "netops-nautobot-sync"`、`docker/images/nautobot/Dockerfile:16-25`（`/tmp/netops-requirements/`、`/opt/nautobot/netops/`、`PYTHONPATH`）、`IaC/terraform/aws-managed/pipeline/nautobot/{nautobot.tf:3,5,58,119, locals.tf:3,110, outputs.tf:22}`、`base/ecr/outputs.tf:69`、`ops/up.sh:692`、`ops/up-common.sh:340`、`app/dashboard/nautobot_api.py:4,6,54`、`tests/test_nautobot.py`（14 行）。
- **S3 Tables**: `IaC/terraform/aws-managed/pipeline/analytics/variables.tf:44`（`namespace` の既定値 `netops`）、`terraform.tfvars.example:9`、`tables.tf:42-55`（`resource "aws_s3tables_namespace" "netops"` と `moved { from = …netops[0] to = …netops }`）、`tables.tf:70,137,297`、`history.tf:130`、`outputs.tf:207`、`IaC/terraform/oss/pipeline/analytics/outputs.tf:97`、`tests/test_analytics.py`（22 行）、docs の Athena の例（`netops.<table>`）。
- **そのほかの参照**: `app/gnmic/gnmic.yaml.in:25`、`app/telegraf/telegraf.conf.in:15`、`app/containerlab/{lab.sh:64, lab_topology.py:34, trex/kafka_load.sh:52, trex/stl/udp_trap.py:14}`、`IaC/terraform/aws-managed/agent/variables.tf:50`、`docker/compose/{README.md:60,68, compose.yaml:255}`、`pyproject.toml:6`（`description = "NetOps PoC …"`）、`README.md:1`（`# nwc-poc — NetOps PoC（Terraform）`）、`tests/{test_app.py 7, test_oss.py 10, test_stream.py 3, test_workflow.py 3, test_local_compose.py 4}`。
- **docs**: `docs/{alert-comparison.md 12, pipeline.md 23, faq-fukuda-nwc-poc.md 18, nautobot.md 8, data-stores.md 4, troubleshooting.md 4, collection.md 2, deploy.md 1}`、`docs/architecture/{README.md 3, resources/splunk.md 12, grafana.md 3, nautobot.md 2, s3-tables-athena.md 1, firehose.md 1}`。
- Nautobot の Job は `bootstrap.py` がクラスパス（`<module>.<Class>`）で登録する。モジュール名を変えると、既存の DB には古い Job の行が残るが、DB（RDS）は `ops/down.sh` で消えるので移行は要らない。
- S3 Tables の `namespace` の値を変えると名前空間とテーブルが作り直しになる（2026-10-04 の `snmp_metrics` からの改名のときと同じ。`tables.tf:58-60` のコメント）。AWS の環境は消えているので中身は無い。

## 設計方針

1. **置換の表。** 識別子は `netops` → `nwc`、`NETOPS` → `NWC`、`NetOps` → `NWC`。ファイル名とディレクトリ名は `git mv`。

   | いま | あと |
   |---|---|
   | `app/splunk/netops_alerts/` | `app/splunk/nwc_alerts/`（app の `id = nwc_alerts`、`label = NWC alerts`） |
   | アラートアクション `netops_sns`、`bin/netops_sns.py` | `nwc_sns`、`bin/nwc_sns.py` |
   | 保存済みサーチ `netops_gnmi` / `netops_trap` / `netops_trap_clear` | `nwc_gnmi` / `nwc_trap` / `nwc_trap_clear` |
   | `NETOPS_ALERTS_ENV` | `NWC_ALERTS_ENV` |
   | SNS の subject `netops alert`（Splunk と Grafana） | `nwc alert` |
   | sourcetype `netops:<topic>` | `nwc:<topic>` |
   | `app/grafana/provisioning/alerting/netops*.yaml`、`dashboards/netops.yaml` | `nwc*.yaml`、`dashboards/nwc.yaml`（provider / folder `nwc`、テンプレート `nwc.sns`） |
   | ダッシュボードの title `netops / …` とタグ `netops` | `nwc / …`、`nwc` |
   | `/etc/grafana/netops`（イメージの中） | `/etc/grafana/nwc` |
   | `app/nautobot/netops/`、`/opt/nautobot/netops/`（イメージの中） | `app/nautobot/nwc/`、`/opt/nautobot/nwc/` |
   | `app/nautobot/jobs/netops_jobs.py`、Job のグループ名 `NetOps` | `nwc_jobs.py`、`NWC` |
   | JobHook `netops-sync`、ロック `netops-nautobot-sync`、ユーザー `netops-web`、`NETOPS_SEED`、logger `netops.bootstrap`、`netops-bootstrap` | `nwc-sync`、`nwc-nautobot-sync`、`nwc-web`、`NWC_SEED`、`nwc.bootstrap`、`nwc-bootstrap` |
   | `/tmp/netops-requirements/`（Dockerfile） | `/tmp/nwc-requirements/` |
   | S3 Tables の `namespace` の既定値 `netops`、資源の名前 `aws_s3tables_namespace.netops` | `nwc`、`aws_s3tables_namespace.nwc`（**`moved` を足す**: `from = aws_s3tables_namespace.netops` `to = aws_s3tables_namespace.nwc`。既存の `netops[0] → netops` の `moved` は残す。値が変わるので apply では作り直しになる） |
   | `pyproject.toml` の `description`、`README.md:1` の見出し | 「NetOps」を使わない書き方（例: `nwc-poc — ネットワーク運用の PoC（Terraform）`） |

2. **散文の「NetOps」も残さない。** コメント・docs の中で「NetOps の Job」「NetOps PoC」のように一般名詞として使っている箇所も `nwc` か日本語（「ネットワーク運用」）に書き換える。合格条件を「grep が 0」の 1 つにするため。
3. **テストは名前を追って直す。** `tests/test_alerts.py` / `test_analytics.py` / `test_nautobot.py` / `check_splunk_image.py` 等が固定している文字列を新しい名前に置き換える。**検査の内容と件数は変えない**（通っていた検査が消えたら名前の変更ではない）。
4. **`docs/cycles/` と `docs/verification/` は触らない**（当時の記録）。`docs/cycles/BACKLOG.md` は PM だけが書く。
5. **compose と ECS のどちらでも同じ名前になる。** `docker/compose/compose.yaml` / `check.sh` と `IaC/terraform/{aws-managed,oss}` の両方を直す。`IaC/terraform/oss/` の symlink は `aws-managed` を指しているので、実体を直せば済む（`tests/test_oss.py` の `links_to_managed` が確かめる）。
6. **AWS の動作確認（エンジニア3 の `verify/aws-managed-20261009`）とは独立に進める。** そちらは古い名前のイメージで検証する。このサイクルのマージ後に次の `ops/up.sh` がイメージを作り直す（`dir_tag` が変わる）。

## 変更対象ファイル

上の「調査で分かった事実」の一覧の全部（`docs/cycles/` と `docs/verification/` を除く）。`git mv` するもの:

- `app/splunk/netops_alerts/` → `app/splunk/nwc_alerts/`（中の `bin/netops_sns.py` → `bin/nwc_sns.py`）
- `app/grafana/provisioning/alerting/netops.yaml` / `netops-prometheus.yaml` / `netops-opensearch.yaml` → `nwc.yaml` / `nwc-prometheus.yaml` / `nwc-opensearch.yaml`
- `app/grafana/provisioning/dashboards/netops.yaml` → `nwc.yaml`
- `app/nautobot/netops/` → `app/nautobot/nwc/`
- `app/nautobot/jobs/netops_jobs.py` → `nwc_jobs.py`

## 再利用するもの

- 2026-10-04 の `snmp_metrics` → `metrics` の改名のやり方（`tables.tf` の `moved` とコメント）
- 007 / 017 の「`git mv` してから参照を `grep` で全部直す」手順

## 実装ステップ

1. `git mv`（1 commit）
2. 参照の書き換え: コード・設定・Dockerfile・Terraform（1 commit）。`grep -rn -i netops app docker IaC ops deploy.env.example pyproject.toml README.md` が 0 になるまで
3. tests と docs（1 commit）。`grep -rli netops . | grep -v -e '^./docs/cycles/' -e '^./docs/verification/' -e '^./.git/'` が `moved` の 2 本だけになるまで（検証方法の 1 つ目）
4. セルフレビュー（`/cycle-build` 手順 6）

## 検証方法

- `grep -rli netops --exclude-dir=.git --exclude-dir=.terraform --exclude-dir=.venv . | grep -v -e '^./docs/cycles/' -e '^./docs/verification/'` の出力が、`IaC/terraform/aws-managed/pipeline/analytics/tables.tf` と `tests/test_analytics.py` の 2 本だけ。この 2 本で残ってよいのは `moved` ブロックの中に残る旧名（`from = aws_s3tables_namespace.netops[0]` / `from = aws_s3tables_namespace.netops` と、連鎖の中継の `to = aws_s3tables_namespace.netops`）と、それを見る検査だけ（`moved` は古い state のアドレスを字面で書くしかない。2026-10-09 にエンジニア1 の質問で PM が決めた。案 B の「moved を 2 本とも消す」は、`namespace` を tfvars で上書きしている環境で同じ名前の destroy と create がぶつかるので採らない）。`grep -rn -i netops` でこの 2 本の中身を見て、ほかの行が無いことを build.md に貼る
- `uv run pytest` が全部通り、通る検査の件数が実装前と同じ（build.md の冒頭で実測して書く。2026-10-09 の 013 のレビューでは test_alerts 168 / test_analytics 513 / test_nautobot 68 / test_stream 106 ほか）
- `terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics validate` と `IaC/terraform/aws-managed/pipeline/nautobot`、`IaC/terraform/oss/pipeline/analytics` が `Success`
- `docker build -f docker/images/splunk/Dockerfile app/splunk` が通り、`python tests/check_splunk_image.py` が通る（Splunk の app の名前とアクションの名前はイメージの中のパスで決まるため）。amd64 のイメージ（Mac では `--platform linux/amd64`）。ビルドできない環境なら「未確認」と build.md に書く
- `docker build -f docker/images/grafana/Dockerfile app/grafana` と `docker/images/nautobot/Dockerfile`（`ops/up.sh` の `build_nautobot` と同じ一時ディレクトリの作り方）が通る
- `git log --follow app/nautobot/nwc/nb_sync.py` で改名前の履歴が追える

## 未確定事項とリスク

1. **Splunk のアラートアクションの名前の変更で、`alert_actions.conf` と `bin/<name>.py` と `savedsearches.conf` の `action.<name>` の 3 か所が一致していないと Splunk が黙って発火しない。** `tests/check_splunk_image.py` と `test_alerts.py` がこの一致を見ているはずなので、名前を変えた検査がそのまま守る。AWS での発火の実測は次の動作確認（エンジニア3 の検証のあと）に回す。未確認。
2. **Grafana の連絡先のテンプレート名 `nwc.sns` と `message: '{{ template "nwc.sns" . }}'` の一致**も同じ。`tests/test_alerts.py` が見ているはず。
3. **Nautobot の `PYTHONPATH=/opt/nautobot/nwc` と Job の import（`import nb_sync` 等）**: ディレクトリ名を変えてもモジュール名は変わらないが、`bootstrap.py` の `JOBS` のクラスパスは `nwc_jobs.…` に変わる。`tests/test_nautobot.py` が `JOBS` と `netops_jobs.py` のクラス名の一致を見ているなら、そこを直す。
4. **`IaC/terraform/aws-managed/agent/variables.tf:50` の `netops`** は AgentCore Runtime の名前の説明の中。名前そのものは接頭辞から導くので、説明の文言だけ直す。実装時に該当行を読んで確かめる。
