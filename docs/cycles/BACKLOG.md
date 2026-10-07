# サイクルをまたぐ候補

1 行 1 件、タイトルは動詞で書く。実装の手順と検証はここに書かず、サイクルの `design.md` に書く。

- [ ] Nautobot の Job を OSS 版の Neo4j につなぐ（`terraform/pipeline/nautobot` のタスク定義に `GRAPH_BACKEND` / `NEO4J_URI` / `NEO4J_PASSWORD`、イメージに Neo4j のドライバー）
- [ ] Neo4j の id 検索にラベルを付ける（`agent/graph.py` の `_BY_ID` がラベル無しの `MATCH (n)` で、頂点が増えると全走査になる）
- [ ] Kafka と OpenSearch のタスクを 1 台ずつ入れ替える手順を作る（いまは `terraform apply` で 3 つが同時に入れ替わる）
- [ ] OSS 版とマネージド版の時間あたりの費用を測って `docs/oss-variant.md` に書く
