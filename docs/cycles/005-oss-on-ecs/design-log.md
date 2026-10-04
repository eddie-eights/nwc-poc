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
