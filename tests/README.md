# tests/

模擬テスト。全部 AWS に触れずに動く（boto3 / terraform / docker は差し替えるか、設定とソースを文字列で突き合わせる）。

- **場所は層ではなく機能で切ってある。** 1 本が `app/` のソースと `IaC/terraform/` の定義と `ops/` のスクリプトを一緒に見張る（例: `test_stream` は Telegraf の設定と stream の Terraform と `ops/up.sh` の渡し方）。`app/` の下に置かないのは、`docker/images/<image>/Dockerfile` が `app/<name>/` をそのまま COPY するため（`.dockerignore` は無い）
- **全部回すのは `ops/check.sh`**（`tests/test_*.py` を glob で回すので、足したテストは名前を `test_*.py` にすれば入る）。1 本だけなら `uv run --group dev --group web python tests/test_<名前>.py`（`uv run pytest` は無い。素の `python3` は PyYAML が無くて落ちるものがある）
- 件数は `docs/development.md`「手元で確かめる」に書いてある

| テスト | 見張るもの | 読む場所 |
| :--- | :--- | :--- |
| `test_agentcore` | エージェント本体 `app.py` の流れ（Strands は本物、Bedrock のクライアントだけ差し替え）。evidence / proposals の列が `app/temporal/rules.py` と同じ順 | `app/agentcore/` `IaC/terraform/aws-managed/agent` |
| `test_graph` | `graph.py` / `topology.py` が Neptune へ送る openCypher とパラメータ、読み替え | `app/agentcore/` `ops/seed_graph.py` |
| `test_kb_index` | KB のベクトル索引を作る Lambda | `app/agentcore/kb_index.py` |
| `test_sync` | lab の定義 → 機器・回線・層のグラフ、SNS のアラート → `status_handler` → graph の status、`sync.tf` の配線 | `app/containerlab/` `app/graph/` `app/agentcore/data` `IaC/terraform/aws-managed/pipeline/graph` |
| `test_workflow` | Temporal の rules / awsio / worker、proposals.decide、mcp_client、`tools.json` と TOOL_SPECS の一致、Terraform と ops のつながり | `app/temporal/` `app/gateway/` `app/agentcore/` `IaC/terraform/aws-managed/workflow` `docker/images/` |
| `test_dashboard_config` | `app/dashboard/config.py` が読む `.env` と `DATA_DIR` の段数 | `app/dashboard/` |
| `test_stream` | 取り込みの経路（lab の機器 → Telegraf → MSK → Spark）。Spark が検知をしないこと | `app/telegraf/` `app/spark/` `IaC/terraform/aws-managed/pipeline/stream` `ops/up.sh` `ops/down.sh` `ops/deploy-env.sh` |
| `test_collectors` | syslog-ng の JSON のキーと型が Telegraf 時代の `device_log` と同じ、`syslog-ng.sh render`、GoFlow2、`ops/netflow_send.py` | `app/syslog-ng/` `docker/images/syslog-ng/` `ops/netflow_send.py` |
| `test_analytics` | analytics の Terraform（S3 Tables / EMR Serverless / 格納先）と `snmp_sinks.py` の列の一致、remote write の復号 | `app/spark/` `IaC/terraform/aws-managed/pipeline/analytics` `ops/up.sh` `docs/deploy.md` |
| `test_alerts` | Splunk の保存済みサーチと Grafana のアラートが同じ形の本文を出し、`rules.py` が読めること。SNS のトピックとイメージと ops の配線 | `app/splunk/` `app/grafana/` `app/temporal/rules.py` `IaC/terraform/aws-managed/base/core` `docker/images/splunk/` `ops/check-grafana.sh` `ops/grafana_rules_check.py` |
| `test_nautobot` | lab → seed_plan → Nautobot → graph / targets の一周、SSM と gnmic の作り直し、Web からの REST、配線 | `app/nautobot/` `app/dashboard/` `IaC/terraform/aws-managed/pipeline/nautobot` `ops/up.sh` `ops/down.sh` `docs/*.md` |
| `test_lab_debug` | デバッグ用の EC2（CloudFormation）が lab と stream の Telegraf からずれていないこと。版は `ops/lab-common.sh` が正 | `IaC/cloudformation/lab-debug.yaml` `IaC/terraform/aws-managed/pipeline/lab` `app/containerlab/` `app/telegraf/` `ops/lab-*.sh` |
| `test_local_compose` | 手元の docker compose（006）の版と契約が元の定義と同値 | `docker/compose/` `app/containerlab/lab.sh` `app/telegraf/` `app/spark/` `ops/oss/oss-images.sh` `ops/lab-common.sh` `ops/up-common.sh` |
| `test_oss` | OSS 版（005）の切り替え。環境変数が無ければ Cypher が 1 文字も変わらない（`golden/neptune_cypher.json`）、neo4j のとき Neptune 固有の構文が無い、認証の切り替え | `app/agentcore/` `app/temporal/` `app/graph/` `app/spark/` `app/grafana/` `app/neo4j/` `IaC/terraform/oss` `docker/images/` `ops/oss/` |
| `test_oss_ops` | `ops/oss/up.sh` / `down.sh` / `oss-images.sh`。接頭辞 `<owner>-nwc-oss` がマネージド版と混ざらない、`down.sh` が自分の分しか消さない | `ops/oss/` `ops/*.sh` `IaC/terraform/oss` `docker/` `app/` |
| `test_oss_roll` | Kafka と OpenSearch の台を 1 台ずつ入れ替える `roll-nodes.sh` / `roll_health.py` の判定 | `ops/oss/` `IaC/terraform/oss` |

`test_*.py` でないもの:

- `check_splunk_image.py` — Splunk のイメージの中の Python が持つ boto3 で SNS へ送れることを、イメージをビルドして確かめる。数分かかり docker が要るので `ops/check.sh` には入れない（Splunk の版を上げたときに手で回し、`CHECKED` を書き換える。`test_alerts` が `CHECKED` と Dockerfile の版を突き合わせる）
- `golden/neptune_cypher.json` — `test_oss` の 1 が比べる、切り替えを入れる前の Cypher の正解
