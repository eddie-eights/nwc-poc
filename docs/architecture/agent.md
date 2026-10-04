# 構成: エージェント（agent）

← [構成](README.md)

`terraform/agent`（`AGENT=1`。既定）。AgentCore Runtime、ガードレール、KB（`CREATE_KB=1` のときだけ。`KB_GRAPHRAG=1` なら GraphRAG）。

## チャットの経路

```mermaid
flowchart LR
  PC["利用者の PC<br/>localhost:8080"] -->|"SSM（ssmmessages、TLS）"| WEB["Web の EC2<br/>Gradio 127.0.0.1:8080<br/>チャット / トポロジ / 承認"]
  WEB -->|"invoke_agent_runtime"| RT["AgentCore Runtime<br/>agent/app.py"]
  RT -->|"Retrieve（HYBRID + Rerank、20 件 → 5 件）"| KB["ナレッジベース<br/>CREATE_KB=1 のときだけ"]
  RT -->|"Converse + Guardrail"| LLM["Nova 2 Lite（jp.）"]
  RT -->|"ツール（最大 5 往復）"| TOOLS["list_devices / neighbors / blast_radius / root_cause / what_if / topology_graph / layers<br/>search_logs / query_metrics / query_history / list_proposals / recent_changes"]
  RT -.->|"WORKFLOW=1"| GW["Gateway（MCP）→ tools Lambda"]
```

- Web の EC2 は土台（[core.md](core.md)）。EC2 にパブリック IP も受信ルールも無い。SSM Agent が内側から ssmmessages へつなぐ。
- Runtime を呼ぶのは EC2 のインスタンスロール。ブラウザに AWS の認証情報は置かない。
- KB のベクトルの置き場は 2 通り。既定は OpenSearch Serverless（ハイブリッド検索）。`KB_GRAPHRAG=1` なら Neptune Analytics のグラフ（[kb_graph.tf](../../terraform/agent/kb_graph.tf)）で、取り込みのときに Bedrock がモデル（既定は回答と同じ Nova 2 Lite）でチャンクから実体と関係を抜き出し、Retrieve はベクトルで当たったチャンクから関係を辿って関連するチャンクも返す。ハイブリッド検索は使えないので Runtime は `KB_SEARCH_TYPE=DEFAULT` を受けて検索の種類を指定しない。このグラフはトポロジの Neptune（データベース）とは別物で、Bedrock だけが読み書きする。GraphRAG のデータソースは aws provider に実体の抜き出しの項目が無いので、Terraform の中から AWS CLI で作る
- ツールは Neptune（トポロジ 3 層と修復案。無ければトポロジは `agent/data/` の静的な 8 台と層）、OpenSearch Serverless、Prometheus を読む。異常の一覧を返すツールは無い（2026-10-02 にやめた。アラートは Grafana と Splunk の画面で見る）。
- Gateway（MCP）と tools Lambda は WORKFLOW で作る（[workflow.md](workflow.md)）。
- `agent/app.py` は Runtime のコンテナで動き、EC2 には置かない（[README.md](README.md) の「どのファイルがどこで動くか」）。
