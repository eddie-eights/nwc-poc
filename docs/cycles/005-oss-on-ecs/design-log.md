# Cycle 005 oss-on-ecs 設計の経緯

## Round 0（2026-10-04）要件を詰めた

きっかけ: タスクの一覧の 6「OSS 環境（ECS）」。方針は docs/oss-variant.md にあったが、置き換え先は未定だった。

| 質問 | 推奨 | ユーザーの答え |
|---|---|---|
| OSS にする範囲 | 一覧を出して選んでもらう | MSK、EMR Serverless、OpenSearch Serverless、Managed Prometheus、Neptune Analytics の 5 つ。「これらだけでいい」 |
| 動かす場所 | ECS | 「ECS かな」 |
| 置き方 | `oss/` に terraform と ops を複製、アプリは共用 | 推奨どおり |
| 並べて立てるか | 立てられるようにする（接頭辞を分ける） | 推奨どおり |
| 状態の置き場 | タスクのディスク | 「Q3 は EFS にしよう」 |
| Neo4j の版 | Community | 推奨どおり。あとで「クラスター化の注意書きも含めて neo4j のコミュニティにする」 |
| メトリクスの置き場 | Prometheus | Prometheus はクラスターにできず EFS も非対応と分かり、「であれば victoria metrics のクラスターに変更したい」 |
| Kafka と OpenSearch のクラスター | 1 台でよい | 「kafka は kraft でのクラスター化は必須。open search もクラスター化する」 |
| VictoriaLogs | OpenSearch を第一の案 | 「まだ候補に残しておいて。vl-insert とか便利だから」 |
| VictoriaMetrics の台数 | vmstorage 2、複製なし | 「複製あり、分散じゃなくて複製したい」→ vmstorage 3、複製数 2 |
| Kafka の台数 | combined の 3 台 | 推奨どおり |
| OpenSearch の台数 | 全部の役を兼ねる 3 台、レプリカ 1 | いったん推奨どおり。プランの承認のときに「レプリカと合わせて 2 台じゃダメ？」→ 3 つの構成を比べて「データ 2 台 + まとめ役 1 台」 |
| Neo4j のデータの置き場 | 一時領域（NFS 非対応のため） | 推奨どおり |
| 認証 | Kafka と VictoriaMetrics は SG だけ、OpenSearch と Neo4j はパスワード | 推奨どおり |
| グラフのアルゴリズム | NetworkX | 「GDS を第 1 候補にする」 |
| サイクルの名前 | 「マネージドを OSS に置き換えた環境を作る」 | 「OK」 |

ユーザーの言葉: 「全部 OSS が足枷になってる。OSS にしたかったのは先のリソースだけで商用利用 OK のライセンスなら使っていい」。

この言葉から決めたこと:

- OSS にするのは置き換える 5 つだけ。周りの道具は、商用で使えるライセンスならよい。
- GDS を選ばない理由にしていた「プラグインに非公開のソースがある」を取り下げた。
- サイクルの名前を「全部 OSS の環境を作る」から変えた。

## 却下した案

| 案 | 却下した理由 |
|---|---|
| Prometheus を ECS に 1 台 | クラスターにできない。NFS（EFS）は非対応と公式に明記 |
| EKS | ユーザーが ECS を選んだ。EKS の Fargate は Arm が使えず、クラスターに時間課金がある |
| EBS | Fargate のサービスのタスクでは、タスクが終わると EBS が消える。既存のボリュームを付け直せない |
| Neo4j Enterprise のクラスター | 有償の契約が要る |
| アプリのコードも丸ごと複製 | 修正を 2 か所に入れ続けることになる |
| OpenSearch を 2 台だけ | 票が偶数だと 1 つ無視されるので、止まる台によってはクラスター全体が止まる |
| OpenSearch を 3 台とも全部の役 | 1 台の故障に耐えるだけなら、データを持つ台は 2 台で足りる。費用が 3 台分になる |
| Kafka に SCRAM | 公式イメージの format が `--add-scram` を渡さない。SG で足りる |

## こちら（設計の役）が決めたこと

ユーザーに聞かずに、技術的に決めたもの。異論があれば変える。

- 変えない terraform のルートは、ファイルをシンボリックリンクにする（複製より直し忘れが無い）。
- Kafka、OpenSearch、vmstorage は台ごとに ECS のサービスを分ける。
- Spark は 3.5 系。GDS の jar はイメージに焼き込む。
- 頂点の id はプロパティ `id` と一意制約で持つ。
- 接頭辞は `<owner>-nwc-oss`。

## 2026-10-05 Kafka の監視の画面に Kafbat UI を足す

- ユーザーの決定: 「oss の kafka では監視ツールの kafbat を入れたい」。
- 公式で確かめたこと（2026-10-05）: ライセンスは Apache 2.0。イメージは `ghcr.io/kafbat/kafka-ui`、ポートは 8080。`KAFKA_CLUSTERS_0_READONLY`（既定 false）、`GITHUB_RELEASE_INFO_ENABLED`（既定 true。GitHub の API へ新しい版を見にいく）、`AUTH_TYPE=LOGIN_FORM` と `SPRING_SECURITY_USER_NAME` / `SPRING_SECURITY_USER_PASSWORD`。
- 設計の役が決めたこと（異論があれば変える）: 1 タスク、見るだけ、ログインあり、GitHub への確認は止める、開き方は Grafana と同じポートフォワード、置き場は `oss/terraform/pipeline/stream/`。
- 未確認: 版、ARM64 のイメージ、KRaft の表示、`AUTH_TYPE` を書かないときの動き、タスクの大きさ。手元の compose で確かめる。
- やらないこと: JMX のメトリクスを VictoriaMetrics に入れて Grafana でグラフとアラートにする（Kafbat UI は時系列とアラートを持たない）。

## 2026-10-05 Kafbat UI は見るだけにしない

- ユーザーの決定: 「追加もしたい」。見るだけ（`KAFKA_CLUSTERS_0_READONLY=true`）をやめ、画面からトピックの追加などをできるようにする。
- 受け入れること: 画面でパイプラインの 5 つのトピックを消したり変えたりできてしまう。守るのはログインだけ。
- やらない案: 役割ごとの権限（RBAC）で「追加はできるが、5 つのトピックは触れない」にする。OAuth か LDAP が要り、PoC には重い（未確認: ログインフォームの認証で RBAC が使えるか）。

## 2026-10-05 マネージド版の MSK にも Kafbat UI を置く

- ユーザーの決定: 「msk にも入れたい」。OSS 版だけでなく、マネージド版の stream にも Kafbat UI を置く。
- 進め方: 005 を待たずに、ブランチ `feat/kafka-ui` でエンジニアに頼んだ（MSK へは IAM 認証、1 タスク、ログインあり、画面からトピックを追加できる）。
- 005 への影響: OSS 版の Kafbat UI は、マネージド版で作るタスク定義を使い回す。違いは認証（PLAINTEXT）だけ。
- スイッチは作らない（同日のユーザーの決定: 「スイッチなしで常に作る」）。stream を作る回は、いつも Kafbat UI を作る。`KAFKA_UI` というキーは置かない。

## Round 1（2026-10-06）手元の確認と実装に合わせて設計を直した

手元のコンテナ（`oss/compose/`）での確認と、`oss/` の実装が main に入った。design.md を現行の形に書き直した。AWS ではまだ立てていない。

### 手元の確認で変わった設計

| 項目 | 前の設計 | いまの設計 | 理由 |
|---|---|---|---|
| Kafka の voter | 動的（`controller.quorum.bootstrap.servers`） | 固定（`controller.quorum.voters`） | 公式イメージ `apache/kafka` が、動的な voter に必要な初期化をしない。組めなかった |
| OpenSearch のデータの 2 台の置き場 | EFS | タスクの一時領域 | 公式の文書がネットワークファイルシステムを避けるよう書いている（設計の役の決定）。2 台が同時に落ちるとデータは消える |
| vminsert の起動 | 順は決めていなかった | vmstorage の 3 台が受けるまで待つコンテナを付ける | つなぐ前に書いた行は 1 台にしか入らず、その台を止めると、欠けたことを示さずに値が抜けた |
| GDS | 動かなければ NetworkX | GDS だけ | Community Edition で動いた。中心性は定義どおりの値と一致、島の数は 1。「動くか」をリスクから外した |
| GDS のライセンス | 未確認 | GPLv3。自社の ECS で動かすだけなら配布に当たらない、という読み | jar の中の `NOTICE.txt` と `LICENSE.txt`、https://neo4j.com/licensing/ 。法的な助言ではない |
| Spark と Splunk | 書いていなかった | Splunk は変えない。Spark は同じ HEC に書く。token は ECS の secrets で受ける | 手元で Kafka から OpenSearch、vminsert、Iceberg、Splunk に書けた |

### 実装（コード）に合わせて直した記述

| 項目 | 前の設計の記述 | コード |
|---|---|---|
| Spark のタスクの数 | 格納先ごとに 1 タスク | サービスは 3 つ（`iceberg`、`splunk`、`http`）。`http` が OpenSearch と VictoriaMetrics の両方に書く。格納先は 4 つ |
| OpenSearch の Cloud Map の名前 | `opensearch-1`、`opensearch-2`、`opensearch-cm` | `opensearch`（データの 2 台）と `opensearch-cm`。ECS のサービスは Cloud Map のサービスを 1 つしか持てない |
| OpenSearch の REST | 書いていなかった | TLS なしの HTTP と Basic 認証。台どうしはデモの証明書の TLS |
| EFS の置き場 | `pipeline/analytics` | `terraform/base/core/oss.tf`（SG と通信の表と同じファイル）。アクセスポイントは使うルート |
| Kafka、OpenSearch の入れ替え | 1 台ずつ待って進める | `terraform apply` でタスク定義が変わると 3 台が同時に入れ替わる。1 台ずつの手順は、まだ無い |
| Neo4j のドライバ | レイヤーか、zip に同梱か（未定） | Lambda のレイヤー（`graph/requirements-oss.txt`） |
| GDS の入れ方 | jar をダウンロードして焼き込む | 公式イメージの `products/` にある jar を `plugins/` に写す |
| イメージの版 | Kafbat UI の版は未確認 | `oss/ops/oss-images.sh` に 1 か所（Kafka 4.3.1、Kafbat UI v1.5.0、OpenSearch 3.9.0、VictoriaMetrics v1.153.0、Spark 3.5.9、Neo4j 2026.09.0） |
| `oss/ops/up.sh` が作る範囲 | 全部のルート | `base/ecr`、`base/core`、`pipeline/lab`、`pipeline/stream`、`pipeline/analytics`、`pipeline/graph` の 6 つ。`pipeline/nautobot`、`agent`、`workflow`、Grafana、Web の部品、グラフの同期は、まだ |

### 未確認のまま

- OpenSearch が Fargate で起動するか（手元は `vm.max_map_count` が 262144 だった）。
- Kafka を EFS に置いてよいか（公式の文書に記述が無い）。
- Spark が S3 Tables に書けるか（AWS でしか確かめられない）。
- Kafbat UI の表示と、VictoriaMetrics の時刻が前後したサンプル（手順は `oss/compose/` にある。結果は docs に反映していない）。
