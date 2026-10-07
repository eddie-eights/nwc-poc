# サイクルをまたぐ候補

1 行 1 件、タイトルは動詞で書く。実装の手順と検証はここに書かず、サイクルの `design.md` に書く。

- [x] Nautobot の Job を OSS 版の Neo4j につなぐ（2026-10-08 完了。コードとテストまでで、AWS では未確認。005 の design.md「実装の状態」）
- [x] Neo4j の id 検索にラベルを付ける（頂点が増えると全走査になる）（2026-10-08 完了。fix/neo4j-id-labels。`_lbl` で Neo4j のときだけ付け、Neptune に送る openCypher は不変。手元の Neo4j で 2000 機器の set_status の dbHits 15014 → 13。AWS の Neo4j / Neptune では未確認）
- [ ] Neo4j に残る全走査を減らす（`count()` と seed の `MATCH (n) WHERE n.registered = false`、remove_device の `MATCH (n:interface) WHERE n.device_id = $id` は device_id に索引が無い。大量投入の直後は索引の統計が古いので `CALL db.prepareForReplanning()` も検討。2026-10-08 の fix/neo4j-id-labels で見つけた）
- [x] Kafka と OpenSearch のタスクを 1 台ずつ入れ替える手順を作る（いまは `terraform apply` で 3 つが同時に入れ替わる）（2026-10-08 完了。feat/oss-redis8-rolling の `oss/ops/roll-nodes.sh`。AWS では未確認）
- [ ] OSS 版とマネージド版の時間あたりの費用を測って `docs/oss-variant.md` に書く
- [ ] OSS 版をマネージド版と並べて立てる（Fargate の vCPU の上限 30 を上げてから。GDS と `neptune.algo.*` の並びの比較もここで）
- [x] Neo4j の ECS のサービスに healthCheck を付ける（いまは healthStatus が UNKNOWN のままで、Bolt が開くまでの時間が ECS から見えない）（2026-10-08 完了。fix/oss-review-nits、AWS では未確認）
- [x] status の Lambda のメモリを上げる（AWS で `Max Memory Used` 111 MB / 128 MB。`terraform/pipeline/graph/sync.tf` はマネージド版と共用）（2026-10-08 完了。256 MB。fix/oss-review-nits）
- [x] `agent/topology.py` の「neptune read failed」のログを、Neo4j のときは graph の名前で出す（OSS 版でも neptune と出る）（2026-10-08 完了。fix/oss-review-nits）
- [x] マネージドを OSS に置き換えた環境を作る → 005-oss-on-ecs（2026-10-07 完了）
- [x] Neo4j のタスクが入れ替わったあとに一意制約を作り直す（`agent/graph.py` の制約済みフラグを ServiceUnavailable のあとに落とす。005 のレビュー Nit 1）（2026-10-08 完了。fix/oss-review-nits）
- [x] `workflow/awsio.py` の Cypher の方言の変換を `agent/graph.py` と共用にするかテストで突き合わせる（いまは `id()` しか写していない。005 のレビュー Nit 2）（2026-10-08 完了。fix/oss-review-nits）
- [x] `spark/Dockerfile` が取る Maven Central の jar のハッシュを照合する（005 のレビュー Nit 4）（2026-10-08 完了。feat/spark-bump）
- [x] `oss/ops/up.sh` の wheel の取り直しを requirements のハッシュで判定する（いまは `.whl` が 1 つでもあれば取り直さない。005 のレビュー Nit 5）（2026-10-08 完了。fix/oss-review-nits）
- [x] `oss/ops/up.sh` の services-stable の待ちに再試行を付け、`ops/check.sh` の OSS のルートにも `-lockfile=readonly` を付ける（005 のレビュー Nit 6）（2026-10-08 完了。fix/oss-review-nits）
- [ ] 手元の docker compose で動く構成を作る（WSL 用。lab から Splunk と Grafana まで届くこと） → 006-local-compose
- [ ] ディレクトリを app/ と docker/ と IaC/ に並べ直す（006 のあと。compose は docker/compose.yml へ動かす。app/ の下は agent→agentcore、workflow→temporal、web→dashboard、lab→containerlab に改名。2026-10-08 に順番を入れ替えた）
- [ ] Kafbat UI を Web の EC2 に同居させ、lab の EC2 で `containerlab graph` のトポロジ図を見られるようにする（Fargate のタスクと Cloud Map をやめる。EC2 は t4g.medium に上げ、Docker と MSK の IAM 権限と SG の web→MSK 9098 を足す。graph は 50080 を SSM のポートフォワードで。2026-10-08 の決定）
- [x] Redis を 8 系に上げる（OSS 版）（2026-10-08 完了。feat/oss-redis8-rolling。`REDIS_TAG` は共通なのでマネージド版の Nautobot の Redis も 8.10.2 になる。AWS では未確認）
- [x] EMR を 7.14.0 に上げ、Spark の jar を合わせる（2026-10-08 完了。feat/spark-bump。AWS では未確認）
- [x] Iceberg を 1.12.0 に上げる（2026-10-08 完了。feat/spark-bump。OSS 版だけ。マネージド版は EMR 同梱の 1.10.1 のまま。AWS では未確認）
- [ ] lab を IS-IS の spine 2 + a-leaf 2 + s-leaf 2 と各 leaf につなぐ TRex にする（SR-MPLS はライセンスが届いたら `ixr-6e` に差し替え。2026-10-08 の決定）
- [ ] コレクターを gNMI / SNMP trap / syslog-ng / GoFlow2 の 4 種にする（telegraf-dialin を外し、ifTable の代わりに gNMI の oper-state を使う）
- [x] `ops/up.sh` が取る jar（`JAR_URLS` 6 本）のハッシュを照合する（`spark/Dockerfile` と compose の分は 2026-10-08 に済んだ）（2026-10-08 完了。fix/up-jar-hash。前の版の jar は jars/ と S3 から消す。S3 の `--delete` は AWS で未確認）
- [x] `.env.example` と `ops/up.sh` と terraform のコメントの古い記述を直す（2026-10-08 完了。fix/stale-comments。コメントと description だけで動作は変えていない。2026-10-08 の docs 同期で見つけた、コードの側の食い違い: `.env.example:140` の SNMP_POLL の既定、`ops/up.sh:445` の docker の要る先に kafka-ui が無い、`ops/up.sh` の mdt・NEED_AOSS・SINK_*/GRAFANA の古いコメント、`terraform/base/core/endpoints.tf:27`・`perimeter.tf:4-6`・`outputs.tf:58`、`terraform/pipeline/stream/variables.tf:109`、`terraform/pipeline/analytics/locals.tf:1-10`、`agent/evidence.py:3,5`）
- [x] `terraform/agent/kb.tf` の kb_index のロールにネットワークの境界の条件を付ける（ほかのロールにはあって、これだけ無い。2026-10-08 の docs 同期で見つけた）（2026-10-08 完了。fix/kb-index-firehose。KB のロールはサービス側の例外なので付けない。AWS では未確認）
- [x] status の Lambda の Firehose の待ちを AZ の数に合わせる（`terraform/pipeline/graph/sync.tf:125` と `graph/status_handler.py:23-24` のコメントは 1 AZ = 15.6 秒の `ops/up.sh:421-423` と合っていない）（2026-10-08 完了。fix/kb-index-firehose。timeout と CONFIG は変えずコメントを 1 / 2 / 3 AZ の秒数に直し、test_sync が up.sh の式と突き合わせる）
- [ ] 手元の compose の構成のスライドを作る（006 のあと。マネージド版・OSS 版は 2026-10-08 に作った）
