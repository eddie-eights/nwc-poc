# マネージド版と OSS 版

## 方針

- **いまの構成は、できる限り AWS のマネージドサービスで作る。**
  自分で立てるのは、マネージドに相当するものが無いか、このアカウントで使えないものだけ（下の表の「自分で立てているもの」）。
- **最終的には、いまの構成とは別に、マネージドの部分を OSS に置き換えた版も作る。**
  いまの構成を書き換えるのではなく、並べて持つ。
- OSS 版は、手元のコンテナでの確認と、terraform と ops の実装（`IaC/terraform/oss/` と `ops/oss/`）が済み、2026-10-07 と 10-08 に AWS で 1 回ずつ立てて動作を確かめた（結果は下の「AWS で確かめたこと」）。
  - 手元で動かすのは [`docker/compose/`](../docker/compose/)。
  - 置き換え先は下の表。設計は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md)。
- **OSS にするのは、下の表で置き換え先を書いた 5 つだけ。**
  ほかはマネージドのまま使う。
- **5 つ以外の道具は、商用で使えるライセンスなら OSS でなくてよい。**

## OSS 版を作る目的

マネージド版と OSS 版を同じ用途で並べて、違いを確かめる。

1. **AWS マネージドでできて、OSS ではできないことを調べる。**
   逆（OSS でできて、マネージドではできないこと）も記録する。
2. **費用の違いを検証する。**
   待機の時間課金と、使った分の課金、機器が増えたときの伸び方。
3. **メンテナンス性の違いを検証する。**
   版上げ、パッチ、バックアップ、障害時の復旧、監視、権限の管理、作る・消すの手間。

## いまの構成での分け方

| 役割 | いま（AWS マネージド） | OSS 版 |
|---|---|---|
| エージェントの実行と入口 | Bedrock AgentCore（Runtime、Gateway） | 変えない（マネージドのまま） |
| モデルとガードレール | Bedrock（Amazon Nova 2 Lite、ガードレール） | 変えない（マネージドのまま） |
| 手順書の検索 | Bedrock のナレッジベース + OpenSearch Serverless | 変えない（マネージドのまま）。ただし `ops/oss/up.sh` はナレッジベースを作らない（OpenSearch Serverless を使うので OSS 版には入れない。`CREATE_KB` も読まない） |
| Kafka | MSK | Apache Kafka（KRaft）を ECS に 3 台。データは EFS。内部トピック（`__consumer_offsets` 等）は 1 パーティション（MSK は既定の 50。トピックが最初に作られたときに決まるので、50 で出来ている EFS では消して作り直すまで 50 のまま） |
| Kafka の監視の画面 | MSK のコンソールと CloudWatch | Kafbat UI（Apache 2.0）を Web の EC2 の Docker に 1 つ。ブローカー、トピック、メッセージを見る。トピックの追加とメッセージの送信も画面からできる。コンシューマーの遅れは出ない（Spark は consumer group を作らず、offset を checkpoint に持つ） |
| ストリーム処理 | EMR Serverless（Spark） | Apache Spark 3.5 を ECS に（ジョブごとに 1 タスクで 3 つ。`iceberg`、`splunk`、OpenSearch と VictoriaMetrics に書く `http`）。Splunk へはマネージド版と同じ HEC に書く |
| 生データの表 | S3 Tables（Iceberg） | 変えない（マネージドのまま） |
| ログの検索 | OpenSearch Serverless | OpenSearch を ECS に 3 台（データ 2 台でレプリカ 1、まとめ役だけの小さい 1 台）。データは 3 台ともタスクの一時領域（公式の文書がネットワークファイルシステムを避けるよう書いている）。データの 2 台が同時に落ちると消える。候補として VictoriaLogs を残す |
| メトリクス | Amazon Managed Service for Prometheus | VictoriaMetrics のクラスターを ECS に（vminsert 1、vmselect 1、vmstorage 3、複製数 2）。データは EFS |
| トポロジのグラフ | Neptune Analytics（openCypher。中心性と連結成分は `neptune.algo.*`） | Neo4j Community Edition を ECS に 1 台。アルゴリズムは GDS（Community Edition で動くことを手元で確かめた） |
| Nautobot の DB | RDS | 変えない（マネージドのまま） |
| コンテナの実行 | ECS Fargate | 変えない（マネージドのまま） |
| アラートの配送 | SNS、SQS、Lambda | 変えない（マネージドのまま） |
| ログ | CloudWatch Logs | 変えない（マネージドのまま） |
| 設定とシークレット、踏み台 | SSM（パラメータ、Session Manager） | 変えない（マネージドのまま） |
| 閉域 | VPC エンドポイントと `aws:SourceVpc` の Deny | 変えない（マネージドのまま） |

**Neo4j のクラスターについての注意**

- クラスターは Neo4j Enterprise Edition だけの機能で、Community Edition では組めない。OSS 版の中で、Neo4j だけは 1 台で動く。
- 止まっているあいだは、トポロジの表示、status の更新、エージェントのトポロジの検索ができない。
- データはタスクの一時領域にある（Neo4j は NFS を非対応と明記している）。タスクが入れ替わると消えるので、下の「Neo4j を起こし直したあとの戻し方」の 2 段で lab の定義と Nautobot から同期し直す。

**Neo4j を起こし直したあとの戻し方**

タスクが HEALTHY になったら（止めてから起こし直すと 1 分ほど）、次の順に打つ。2026-10-08 に AWS で確かめた手順（[verification/20261008-oss-aws.md](verification/20261008-oss-aws.md) の「Neo4j を止める」）。

1. `OWNER=<owner> ops/sync-graph.sh --oss`
   - lab の定義から、物理層（機器・インタフェース・回線）と IP 層、EVPN・BGP 層を入れる。中心性もこれで答えるようになる。
   - 変更履歴（`change` の頂点）は入らず、0 件のまま。
2. Nautobot の Job「gnmic とグラフ DB に同期」を手で打つ
   - Nautobot の変更履歴と、Nautobot で足した機器と回線を Neo4j に書く。10-08 は変更履歴が 0 件から 19 件に戻った。

順番はこの順にする。先に Job を打つと機器が入ってグラフが空でなくなり、`--replace` なしの `ops/sync-graph.sh` は何もしない。`--replace` を付けると lab の定義で上書きするので、Job で入れた Nautobot の機器と回線が消える。

自分で立てているもの（マネージド版でも OSS か自前のコンテナ）:

- Telegraf、gnmic、syslog-ng（AxoSyslog）、GoFlow2、Grafana、Temporal、Nautobot（Redis と一緒）、Kafbat UI、containerlab（lab）
- Splunk（OSS ではないが、VPC の中の ECS に自分で立てている）

Amazon Managed Grafana は、このアカウントに IAM Identity Center が無くて使えないので Grafana OSS にしている（[deploy.md](deploy.md)）。

## OSS 版の実装の置き場と、いまの状態

| 置き場 | 中身 |
|---|---|
| `IaC/terraform/oss/` | OSS 版の terraform。ルートごとのディレクトリに、変えないファイルは `IaC/terraform/aws-managed/` のファイルへのシンボリックリンク、OSS 版だけのファイル（`kafka.tf`、`opensearch.tf` など）と `oss.auto.tfvars`（`project = "nwc-oss"`）を置く。state は別 |
| `ops/oss/up.sh`、`ops/oss/down.sh` | OSS 版の作る・消す。接頭辞は `<owner>-nwc-oss` で、マネージド版と並べて立てられる。SSM のパラメータのタグは `ManagedBy=ops/oss/up.sh`（2026-10-09 より前に `oss/ops/up.sh` で立てたものは、そのときの commit の `oss/ops/down.sh` で消す） |
| `ops/oss/oss-images.sh` | イメージの名前と版の正（1 か所） |
| `ops/oss/roll-nodes.sh`、`ops/oss/roll_health.py` | Kafka と OpenSearch を 1 台ずつ入れ替える。`ops/oss/up.sh` が stream と analytics の apply の前に打つ |
| `docker/images/spark/Dockerfile`、`docker/images/neo4j/Dockerfile` | ECS 向けの Spark と Neo4j（GDS 入り）のイメージ |
| `ops/common.sh`、`ops/up-common.sh`、`ops/down-common.sh` | マネージド版と OSS 版の共通の関数 |

- **`ops/oss/up.sh` が作るルートは、マネージド版の `ops/up.sh` と同じ 9 つ。**
  `base/ecr`、`base/core`、`agent`、`pipeline/lab`、`pipeline/stream`、`pipeline/graph`、`pipeline/nautobot`、`pipeline/analytics`、`workflow`。
  Grafana、Web の部品、エージェント、workflow、Neo4j への同期までつないである（何がどう動くかは [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md) の「実装の状態」）。
- **機能と格納先は選ばない。**
  `AGENT` / `PIPELINE` / `WORKFLOW` / `STORES` などのキーは読まず、ルートはいつも全部、格納先はいつも `iceberg` / `opensearch` / `prometheus` / `splunk` の 4 つ、Grafana もいつも作る。
- **Nautobot の Job は Neo4j に書く（2026-10-08 に AWS で確かめた。手で打つ Job と JobHook の両方。[verification/20261008-oss-aws.md](verification/20261008-oss-aws.md)）。**
  - Neo4j のタスクが入れ替わると変更履歴も消え、`ops/sync-graph.sh --oss` では戻らないので、そのあと Job「gnmic とグラフ DB に同期」を打ち直す（上の「Neo4j を起こし直したあとの戻し方」）。
  - Job の名前はマネージド版と同じで、説明に Neo4j と出る。エージェントの `root_cause` と `topology_graph` の `source` は `neo4j`（空なら `neo4j-empty`）
  - graph の state に `neo4j_uri` があるので、`IaC/terraform/aws-managed/pipeline/nautobot` が `GRAPH_BACKEND=neo4j`・`NEO4J_URI` と secrets の `NEO4J_PASSWORD` を渡す。
  - `ops/oss/up.sh` が Neo4j のドライバー入りのイメージ（`app/nautobot/requirements-oss.txt`）を作る。
- **打ち直しで Kafka か OpenSearch のタスク定義が変わると、1 台ずつ入れ替える。**
  terraform だけで apply すると、変わった台が同時に入れ替わる（Kafka は controller の過半数を、OpenSearch はインデックスを失う）。
  - `ops/oss/up.sh` は変わる台を plan で拾い、リーダーでない台から 1 台ずつ `-target` で apply する。
    - 間でクラスターが健全に戻るのを ECS Exec で待つ（手元に Session Manager plugin が要る）。
  - 止まったら `ops/oss/up.sh` を打ち直せば残りの台だけ入れ替える。`OSS_ROLL=0` で一度に入れ替える。
  - 2026-10-08 に AWS で打った:
    - 端末の無いシェルからは ECS Exec が `Cannot perform start session: EOF` で切れて止まった（何も入れ替えない）。
    - `script -q /dev/null` で疑似端末を付けた 2 回目は Kafka の 3 台をリーダーでない 1 → 2 → 3 の順に入れ替えて rc=0（13 分 57 秒）。
  - そのため標準入力が端末でないときは、`ops/oss/roll-nodes.sh` が ECS Exec を `script` で包んで疑似端末を付ける。
    - Linux の util-linux の `script -q -c` と macOS の `script -q /dev/null` を見分ける。Linux の形は AWS では未確認。
  - `script` も打てなければ、何も入れ替えずに「端末から打つか `OSS_ROLL=0`」と出して止まる（手順は [cycles/005-oss-on-ecs/design.md](cycles/005-oss-on-ecs/design.md) の「Kafka と OpenSearch を 1 台ずつ入れ替える」）。
- **Splunk は OSS 版でも変えない。**
  マネージド版と同じ Splunk を立てる。Spark は Splunk の token を、ECS の secrets（SSM の SecureString）から環境変数 `SPLUNK_HEC_TOKEN` で受ける（マネージド版は、ジョブが SSM から読む）。

## 手元のコンテナで確かめたこと

| OSS | 確かめたこと |
|---|---|
| Kafka | 固定の voter（`controller.quorum.voters`）で 3 台が組めた。1 台止めても、書いた 1000 件を全部読めて、書けた。動的な voter は、公式イメージが必要な初期化をしないので組めなかった |
| OpenSearch | 3 台のどれを止めても検索できた |
| VictoriaMetrics | vminsert が vmstorage の 3 台につないだあとに書いたデータは、1 台止めても全部読めた。つなぐ前に書いた行は 1 台にしか入らず、その台を止めると、欠けたことを示さずに値が抜ける。だから vmstorage が上がってから vminsert を起動する |
| Neo4j + GDS | Community Edition で GDS が動いた。中心性は定義どおりの値と一致、島の数は 1 |
| Spark | EMR なしの Spark 3.5 で、Kafka から OpenSearch、vminsert、Iceberg、Splunk（HEC）に書けた |

## AWS で確かめたこと（2026-10-07 と 10-08。どちらも OSS 版だけを立てた）

10-08 の記録は [verification/20261008-oss-aws.md](verification/20261008-oss-aws.md)。

| OSS | 確かめたこと | 残っている未確認 |
|---|---|---|
| Kafka | EFS に置いた 3 台が組めて、1 時間流して 5 つのトピックに入った。1 台止めても残り 2 台で受け続け、戻ると 3 分以内に under-replicated が 0 に戻った。10-08 は、タスク定義を変えて打ち直した `ops/oss/up.sh` が 3 台を 1 台ずつ入れ替え、最後に under-replicated が 0 だった（記録の「1 台ずつ入れ替える」） | 日単位で流したときの遅さやロック。内部トピックのパーティション数の設定が 1 になっていること（下） |
| OpenSearch | 3.9.0 の 3 台が Fargate で `node.store.allow_mmap=false` で起動し、green。trap が入り、1 台止めても yellow で検索できた。まとめ役（1 GB）は OOM で落ちなかった | なし |
| VictoriaMetrics | 6 台分 396 系列が入り、Grafana に出た。vmstorage を 1 台止めて戻しても値は抜けなかった | vminsert だけが起き直したとき |
| Neo4j + GDS | status の Lambda とエージェントの `centrality` が Neo4j を読み書きした。タスクを止めると Web は静的データに落ちて 200 のまま、起こし直して `ops/sync-graph.sh --oss`（物理層と IP 層）と Nautobot の Job（変更履歴）の 2 段で戻った | Neptune の結果と同じ並びになるか（マネージド版と並べて立てる必要がある） |
| Spark | EMR なしで S3 Tables に書けた（1 時間で 115,719 行）。OpenSearch、vminsert、Splunk にも入った | なし |
| 全体 | lab でリンクを落とすと、Grafana のアラート → SNS → Lambda → Neo4j の status → Web のトポロジまでつながった。エージェントの `centrality`、`search_logs`、`query_metrics` が答えた。`ops/oss/down.sh` で消した（下） | マネージド版と並べて立つか（Fargate の vCPU の上限 30 に OSS 版だけで 21.5） |

- Kafka の内部トピックのパーティション数（「OSS 版の Kafka の内部トピックのパーティションを絞る（022）」）:
  - consumer group が無いと `__consumer_offsets` は作られないので、トピックではなく設定を見る。
  - Kafbat UI の Brokers のブローカー設定か `kafka-configs.sh --bootstrap-server … --describe --entity-type brokers --entity-name 1 --all` で、`offsets.topic.num.partitions` が STATIC_BROKER_CONFIG の 1 になっていること。
- 全体の `ops/oss/down.sh`:
  - 接頭辞 `efukuda-nwc-oss` のリソースが消えた。
  - 設計どおり残るのは、Runtime の ENI が消えるまでの VPC・サブネット・runtime の SG と、`KEEP_ECR=1` の ECR。どちらも時間課金は無い。

## GDS のライセンス（法的な助言ではない）

- **Neo4j が配る GDS の jar は GPLv3。**
  jar の中の `NOTICE.txt` と `LICENSE.txt` で確かめた。https://neo4j.com/licensing/ も Community Edition を GPL v3 としている。
- **GPLv3 は、商用利用そのものを制限しない。**
  義務が生じるのは、イメージを第三者に配るとき。自社の ECS と private の ECR で動かすだけなら配布に当たらない、という読み。本番で使う前に、法務か契約の担当に確かめる。
- **Community 版の GDS の制限は、並列 4 コアまで、モデル 3 つまで。**
  出典は https://neo4j.com/docs/graph-data-science/current/introduction/ 。

## いまの構成を作るときに気をつけること

- **マネージドに固有の機能は避けずに使う。**
  「マネージドでできること」を調べるのが目的なので、OSS へ移しやすいように機能を削ることはしない。
- **固有の機能に頼った箇所は、どこで何に頼ったかを docs に書く。**
  OSS 版で同じことができるか、何で代えるかを比べる材料になる。
- **費用と手間は、分かった時点で記録する。**
  時間課金は [README](../README.md) の「作るもの」と [deploy.md](deploy.md)、はまった点は [troubleshooting.md](troubleshooting.md)。

## マネージドに固有の機能に頼った箇所

| 箇所 | 頼ったもの | OSS 版で比べること |
|---|---|---|
| エージェントのツール `centrality`（[app/agentcore/graph.py](../app/agentcore/graph.py) の `centrality()`） | Neptune Analytics のグラフアルゴリズム `neptune.algo.degree` / `closenessCentrality` / `wcc`（openCypher の `CALL`） | GDS の `gds.degree` / `gds.closeness` / `gds.wcc` で計算する（手元では定義どおりの値と一致し、AWS でもエージェントから答えが返った）。Neptune の結果と同じ並びになるかと、機器が増えたときの計算時間は未確認 |
| グラフへの問い合わせ全部（[app/agentcore/graph.py](../app/agentcore/graph.py) の `query()`、[app/temporal/awsio.py](../app/temporal/awsio.py) の `cypher()`） | boto3 の `neptune-graph` の `execute_query`（SigV4、VPC エンドポイント `neptune-graph-data`）。頂点の id は Neptune の `~id` | Neo4j の Python ドライバ（Bolt）とパスワードに差し替えた（`GRAPH_BACKEND=neo4j`）。id はプロパティ `id` と一意制約で持つ。一意制約とその索引はラベルごとなので、id で頂点を引くクエリには Neo4j でだけラベルを付ける（`_lbl`。下） |

- `_lbl`: 無いと全部の頂点を読む。Neptune の `~id` はグラフ全体で一意なので、送る openCypher は変えない。
