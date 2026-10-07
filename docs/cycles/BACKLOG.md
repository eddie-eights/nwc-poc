# サイクルをまたぐ候補

1 行 1 件、タイトルは動詞で書く。実装の手順と検証はここに書かず、サイクルの `design.md` に書く。

- [x] Nautobot の Job を OSS 版の Neo4j につなぐ（2026-10-08 完了。コードとテストまでで、AWS では未確認。005 の design.md「実装の状態」）
- [ ] Neo4j の id 検索にラベルを付ける（頂点が増えると全走査になる）
- [ ] Kafka と OpenSearch のタスクを 1 台ずつ入れ替える手順を作る（いまは `terraform apply` で 3 つが同時に入れ替わる）
- [ ] OSS 版とマネージド版の時間あたりの費用を測って `docs/oss-variant.md` に書く
- [ ] OSS 版をマネージド版と並べて立てる（Fargate の vCPU の上限 30 を上げてから。GDS と `neptune.algo.*` の並びの比較もここで）
- [ ] Neo4j の ECS のサービスに healthCheck を付ける（いまは healthStatus が UNKNOWN のままで、Bolt が開くまでの時間が ECS から見えない）
- [ ] status の Lambda のメモリを上げる（AWS で `Max Memory Used` 111 MB / 128 MB。`terraform/pipeline/graph/sync.tf` はマネージド版と共用）
- [ ] `agent/topology.py` の「neptune read failed」のログを、Neo4j のときは graph の名前で出す（OSS 版でも neptune と出る）
- [x] マネージドを OSS に置き換えた環境を作る → 005-oss-on-ecs（2026-10-07 完了）
- [ ] Neo4j のタスクが入れ替わったあとに一意制約を作り直す（`agent/graph.py` の制約済みフラグを ServiceUnavailable のあとに落とす。005 のレビュー Nit 1）
- [ ] `workflow/awsio.py` の Cypher の方言の変換を `agent/graph.py` と共用にするかテストで突き合わせる（いまは `id()` しか写していない。005 のレビュー Nit 2）
- [ ] `spark/Dockerfile` が取る Maven Central の jar のハッシュを照合する（005 のレビュー Nit 4）
- [ ] `oss/ops/up.sh` の wheel の取り直しを requirements のハッシュで判定する（いまは `.whl` が 1 つでもあれば取り直さない。005 のレビュー Nit 5）
- [ ] `oss/ops/up.sh` の services-stable の待ちに再試行を付け、`ops/check.sh` の OSS のルートにも `-lockfile=readonly` を付ける（005 のレビュー Nit 6）
