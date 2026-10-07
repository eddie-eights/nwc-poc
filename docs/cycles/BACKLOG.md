# サイクルをまたぐ候補

1 行 1 件、タイトルは動詞で書く。実装の手順と検証はここに書かず、サイクルの `design.md` に書く。

- [ ] Nautobot の Job を OSS 版の Neo4j につなぐ（いまは Neptune にしか書けない。何が足りないかは 005 の design.md「実装の状態」）
- [ ] Neo4j の id 検索にラベルを付ける（頂点が増えると全走査になる）
- [ ] Kafka と OpenSearch のタスクを 1 台ずつ入れ替える手順を作る（いまは `terraform apply` で 3 つが同時に入れ替わる）
- [ ] OSS 版とマネージド版の時間あたりの費用を測って `docs/oss-variant.md` に書く
