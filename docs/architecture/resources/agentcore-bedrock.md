# AgentCore と Bedrock

← [リソースごとの知見](README.md)

## ひとことで

チャットに答えるエージェントを動かすところ。AgentCore Runtime（VPC モードのコンテナ）が、Bedrock のモデル（Nova 2 Lite）、ガードレール、Knowledge Base、ツールを使って答える。
`WORKFLOW=1` のときは、ツールを AgentCore Gateway（MCP）越しに VPC の中の Lambda で動かす。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| Runtime | `<owner>_nwc_poc_agent`（名前にハイフンが使えないので `-` を `_` にする）。VPC モード、`RUNTIME_AZ_NUM`（既定 1、1〜3） | `terraform/agent/runtime.tf`、`terraform/agent/locals.tf` |
| セッション | 放置は 300 秒で畳む。寿命は最大 3600 秒 | `runtime.tf` |
| コンテナ | `agent/app.py`。イメージは ECR の `<prefix>-agent`（arm64） | `agent/Dockerfile` |
| モデル | `jp.amazon.nova-2-lite-v1:0`（東京と大阪に振り分ける推論プロファイル） | 変数 `model_id` |
| ガードレール | Standard 階層、フィルタの強さは MEDIUM | `terraform/agent/kb.tf` の `aws_bedrock_guardrail.this` |
| Knowledge Base | `CREATE_KB=1` のときだけ。S3 の `docs/` の md → Titan Embeddings v2（1024 次元）→ OpenSearch Serverless の `kb-index`（faiss） | `kb.tf` |
| 検索 | HYBRID で 20 件取り、`amazon.rerank-v1:0` で 5 件に絞る | 変数 `number_of_results`、`rerank_model_id`、`number_of_reranked_results`、[agent.md](../agent.md) |
| Runtime の ARN | SSM の String `/<prefix>/runtime-arn`。Web の EC2 が読む（60 秒キャッシュ） | `runtime.tf` |
| Gateway | `<prefix>-tools`。MCP（`2025-06-18`）、認証は AWS_IAM。URL は SSM の String `/<prefix>/gateway-url` | `terraform/workflow/gateway.tf` |
| tools の Lambda | `<prefix>-tools`。VPC の中（`LAMBDA_AZ_NUM`、既定 1）。`agent/topology.py`、`agent/evidence.py`、`agent/proposals.py` を動かす | `gateway.tf` |
| KB の index を作る Lambda | `<prefix>-kb-index`（`agent/kb_index.py`）。apply のときに 1 回呼ぶ | `kb.tf` |
| スイッチ | `AGENT=1`（既定 0）。KB は `CREATE_KB=1`。Gateway と tools の Lambda は `WORKFLOW=1` | `deploy.env.example`、`ops/up.sh` |
| 費用 | KB は +$0.35/h（OCU 0.33 + エンドポイント 2 本）。エンドポイントは 1 本 1.4 セント/時 × `ENDPOINTS_AZ_NUM` | `ops/up.sh` の先頭のコメント |

ツールと読む先:

| ツール | 読む先 |
|---|---|
| `list_devices`、`neighbors`、`blast_radius`、`root_cause`、`what_if`、`topology_graph`、`layers`、`centrality` | Neptune Analytics のトポロジ（無ければ `agent/data/` の静的な 8 台） |
| `recent_changes` | Neptune Analytics の `change` |
| `list_proposals` | S3 Tables の `proposal_events`（Athena のワークグループ `<prefix>-history`。`proposal_id` ごとに `seq` が最大の行。既定は全部の状態を新しい順に 20 件、最大 100 件） |
| `search_logs` | OpenSearch Serverless の `snmp-logs`（trap と syslog） |
| `query_metrics` | Amazon Managed Prometheus（PromQL） |
| `query_history` | S3 Tables の `alert_events`（Athena のワークグループ `<prefix>-history`。`event_id` で重複を落とし、新しい順に最大 50 件） |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Web の EC2 | Web → Runtime | `bedrock-agentcore` のエンドポイント、`invoke_agent_runtime`、インスタンスロール。待つのは 150 秒まで |
| worker（Temporal） | worker → Runtime | 同じエンドポイント、タスクロール（原因と修復案を聞く） |
| Bedrock のモデルとガードレール | Runtime → Bedrock | `bedrock-runtime` のエンドポイント、Converse + Guardrail |
| Knowledge Base | Runtime → Bedrock（Retrieve） | `bedrock-agent-runtime` のエンドポイント。コレクションは Bedrock がサービス側から検索する |
| Gateway | Runtime → Gateway → tools の Lambda | `bedrock-agentcore.gateway` のエンドポイント、SigV4。ツールは最大 5 往復 |
| Neptune、OpenSearch、Prometheus、Athena | tools の Lambda（か Runtime のコンテナ）→ 各サービス | 各エンドポイント、SigV4。Neptune は読むだけ |
| SSM | Runtime、Web → パラメータ | `ssm` のエンドポイント |

## 知見

- **Runtime の実行ロールは土台が持つ。**
  stream と graph のルートがロール名を読んでポリシーを付けるため。agent のルートは、Runtime が動くのに要るポリシーを足して Runtime を作る。
  出典: `terraform/agent/runtime.tf` の先頭のコメント。
- **ARN を SSM で渡すので、agent を後から作っても消しても Web の EC2 を作り直さずに済む。**
  Web は環境変数でなく SSM のパラメータを読む。
  出典: `runtime.tf` のコメント。
- **`jp.` の推論プロファイルは、東京と大阪のモデルへ振り分ける。**
  IAM のリージョンは `*` にしてある。SCP や Permissions boundary が大阪を止めていると、`ap-northeast-3` の `AccessDeniedException` になる。
  出典: `runtime.tf` のコメント、[troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」。
- **日本語を判定させるには、ガードレールを Standard 階層にする。**
  Classic 階層は英語・フランス語・スペイン語だけ。Standard はクロスリージョン推論が必須で、判定は APAC のほかのリージョンで行われることがある。
  出典: `terraform/agent/kb.tf` のコメント。
- **フィルタは MEDIUM にしてある。**
  ネットワーク運用の語（攻撃・遮断・kill など）で誤検知しにくくするため。プロンプト攻撃のフィルタは入力だけに効く（出力側は NONE にする決まり）。
  出典: `kb.tf` のコメント。
- **ガードレールを変えたら、版を作り直す。**
  版は作成時点の内容を固定する。`description` の `r1` を `r2` に上げる。
  出典: `kb.tf` のコメント。
- **Runtime は KB のコレクションを直接呼ばない。**
  Retrieve を呼ぶと、Bedrock がサービス側から検索する。取り込みも Bedrock がその経路で書く。リランクは呼び出し側でなく KB のサービスロールの権限で動く。
  出典: `kb.tf` のコメント。
- **KB の index は、VPC の中の Lambda が作る。**
  コレクションは VPC エンドポイントからしか届かないので、Terraform を打つ PC からは作れない。index がもうあれば作らない（mappings が違っても直さない）。2026-09-17 までの opensearch provider では、Bedrock が足したフィールドの差分で毎回作り直しになり、ベクトルが消えた。
  出典: `kb.tf` のコメント。
- **ハイブリッド検索には、faiss エンジンと `index: true` の text フィールドが要る。**
  出典: `kb.tf` のコメント。
- **KB のデータソースは RETAIN で消す。**
  DELETE だと destroy のときにベクトルの削除が走り、コレクションが先に消えると失敗する。
  出典: `kb.tf` のコメント。
- **Runtime も Gateway も、VPC のエンドポイントを通らない呼び出しを拒む。**
  AgentCore の文書の DenyAllExceptVPC と同じ形のリソースポリシー。デプロイする人は外れるので、Runtime だけを PC から CLI で確かめられる。
  出典: `runtime.tf` と `terraform/workflow/gateway.tf` のコメント。
- **承認・却下はツールに出していない。**
  承認・却下は Web が決定のキュー `<prefix>-decisions` に送る。このキューへの `sqs:SendMessage` は Web の EC2 のロールにだけ付け、Runtime と tools の Lambda のロールには付けない。tools の Lambda の権限は Neptune を読むだけ（Write は付けない）で、Athena で読めるテーブルは `alert_events` と `proposal_events` だけ。
  出典: `gateway.tf` と `terraform/workflow/proposals.tf` のコメント、[workflow.md](../../workflow.md) の「流れ」。
- **Gateway に届かなければ、Runtime はコンテナの中のツールで答える。**
  出典: [workflow.md](../../workflow.md) の「流れ」。
- **`agent/` のモジュールを増やしたら、3 か所に足す。**
  tools の Lambda の zip の一覧（`gateway.tf`）、`agent/Dockerfile`、土台の `upload_web_command`。Lambda には PyYAML が無いので `devices.yaml` は JSON にして入れる。
  出典: `gateway.tf` のコメント。
- **`agent/app.py` は Runtime のコンテナで動き、EC2 には置かない。**
  置くと `KeyError: 'MODEL_ID'` や Web のロールの `AccessDenied` になる。Web のロールに権限を足して直さない。
  出典: [agent.md](../agent.md) の「チャットの経路」、[troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」。
- **Runtime の ENI は、消したあと最大 8 時間残る。**
  そのあいだは VPC、サブネット、Runtime の SG を残してほかを消す。時間をおいて `ops/down.sh` を打ち直す。Lambda の ENI は 20〜40 分。
  出典: [deploy.md](../../deploy.md) の「`ops/down.sh` がすること」、[troubleshooting.md](../../troubleshooting.md) の「消すとき」。
- **答えがおかしいときの見方。**
  [troubleshooting.md](../../troubleshooting.md) の「チャットの答えがおかしい」（150 秒で失敗、`tools=0`、`参照:` が付かない、ガードレールの誤検知）。
  出典: 同じファイル。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| Runtime をサブネット 1 つで作る | API は受け付ける（`VpcConfig` の subnets は 1〜16 個、https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_VpcConfig.html 、2026-10-05 確認）。手引きは 2 つ以上を勧める。1 つ（既定の `RUNTIME_AZ_NUM=1`）で作って動くことは 2026-10-05 に AWS で確かめた。2 つ以上は未確認 |
| `query_history` が閉域の Deny に当たらないか | Athena のワークグループ `<prefix>-history` で `alert_events` を読めることは 2026-10-05 に AWS で確かめた。チャットから `query_history` を呼んだ結果は未確認 |
| チャットの通し | 2026-10-05 に AWS で、チャットが「dc1-leaf-01 の接続先は」に正しく答えた（`AGENT=1 PIPELINE=1 WORKFLOW=1`、`RUNTIME_AZ_NUM` は既定の 1） |
| ツールの回数の上限に当たったとき | `MAX_TOOL_ROUNDS`（既定 5）に当たると、何も返さずに終わる。既知（2026-10-05。[troubleshooting.md](../../troubleshooting.md) の「既知の不具合」） |
| 異常の一覧を返すツール | 無い（2026-10-02 にやめた。いまのアラートは Grafana と Splunk の画面で見る） |
| タグ | Runtime のロググループには Terraform で付かない（`ops/up.sh` の手順 9 で付ける）。KB のデータソースとガードレールの版には付かない |

## 関連

- [opensearch-serverless.md](opensearch-serverless.md)、[neptune-analytics.md](neptune-analytics.md)、[s3-tables-athena.md](s3-tables-athena.md)、[web-ec2.md](web-ec2.md)、[temporal.md](temporal.md)
- [agent.md](../agent.md): 「チャットの経路」
- [workflow.md](../workflow.md): Gateway と tools の Lambda
- [troubleshooting.md](../../troubleshooting.md): 「チャットの答えがおかしい」
