# Neptune Analytics

← [リソースごとの知見](README.md)

## ひとことで

ネットワークのトポロジと、その頂点・辺の「いまの状態」（`status`）を置くグラフ。グラフ DB はこの 1 つだけ。
メモリに載せて openCypher で問い合わせる。公開の口は閉じ、VPC からはインターフェース型エンドポイント越しに IAM（SigV4）で届く。壊れても lab の定義と Nautobot から作り直せる置き場で、正本ではない。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| グラフ | `<prefix>-graph`。1 つ | `terraform/pipeline/graph/neptune.tf` の `aws_neptunegraph_graph.graph` |
| 大きさ | 16 m-NCU（最小。16 / 32 / 64 / 128 / 256 から選ぶ） | 変数 `provisioned_memory` |
| 公開 | `public_connectivity = false`。サブネットグループも SG も持たない | `neptune.tf` |
| AZ | `NEPTUNE_AZ_NUM`（既定 1、1〜3）。値 − 1 個のレプリカを別の AZ に置く | `ops/up.sh`、`neptune.tf` の `replica_count` |
| グラフの ID | SSM の String `/<prefix>/neptune-graph-id`。Runtime、Web、Lambda、ワーカーがここから読む | `neptune.tf` の `aws_ssm_parameter.graph_id` |
| 問い合わせ | openCypher だけ。boto3 の `neptune-graph` クライアントの `execute_query` | `agent/graph.py` の `query()` |
| スイッチ | `PIPELINE=1`。`SKIP_GRAPH=1` で外す（外すと「トポロジ」が使えず、`status` を書く先が無い） | `deploy.env.example`、`ops/up.sh` |
| 費用 | 58 セント/時 × `NEPTUNE_AZ_NUM`（東京、16 m-NCU）。無料枠は無い | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`）、変数 `provisioned_memory` の説明 |

グラフに入っているもの（1 つのグラフをラベルで分けている）:

| 層 | ラベル | 中身 |
|---|---|---|
| 物理 | 頂点 `device`、`interface`、辺 `link` | 機器、IF、ケーブル |
| IP | `ip_interface`、`isis_adjacency` | IF のアドレス、IS-IS の隣接 |
| EVPN/BGP | `bgp_session`、`evpn_instance`、`ethernet_segment` | BGP のセッション、EVPN のインスタンス、ES |
| 状態 | 各頂点・辺のプロパティ `status` | UP / DOWN / ALARM。アラートが届くと Lambda `<prefix>-graph-status` が書き換える |
| 変更 | `change`（id は `change#<id>`） | Nautobot の変更の履歴の新しい 50 件 |

障害そのもの（アラートの 1 通ごと）も修復案も頂点にしていない。アラートの履歴は S3 Tables の `alert_events`、修復案は `proposal_events` にある（修復案の頂点 `proposal` は 2026-10-05 にやめた）。

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| Runtime（エージェント）、Web の EC2 | 読み書き | `neptune-graph-data` のエンドポイント（443、private DNS）、SigV4。ポリシー `<prefix>-graph-access` |
| Lambda `<prefix>-graph-status` | 書く（`status`） | 同じエンドポイント、SigV4 |
| tools の Lambda（Gateway のトポロジのツール） | 読むだけ | 同じエンドポイント、SigV4（`ReadDataViaQuery` と `GetQueryStatus` だけ） |
| ワーカー（Temporal） | 読むだけ（トポロジ。事前チェックと保守中の機器の判定） | 同じエンドポイント、SigV4（タスクロールには Write と Delete も残っているが、コードは使わない） |
| Nautobot の Job | 書く（物理層） | 同じエンドポイント、SigV4（タスクロール） |
| `ops/sync-graph.sh` | 書く（lab の定義の投入） | 利用者の PC → SSM → Web の EC2 → 上と同じ経路 |

## 知見

- **Neptune Database から置き換えた（2026-10-04）。**
  それまでは Gremlin で `db.t4g.medium` 1 台（約 $0.14/h と数えていた）。機器が増えたときに影響範囲や中心性をグラフの側で計算したい、がユーザーの理由。
  出典: `terraform/pipeline/graph/neptune.tf` の先頭のコメント、[data-stores.md](../../data-stores.md) の「11. Neptune とは」。
- **この PoC の使い方には Analytics のほうが合う。**
  データが小さく、書くのは `status` の更新だけ。正本を Neptune に置かない方針とも合う。Database に戻すのは、常時書き込む正本にする・複数 AZ のリードレプリカが要る・Gremlin や SPARQL が要る、のどれかになったとき。
  出典: FAQ「Neptune Database と Neptune Analytics の使い分けは？ いまの構成でも問題ない？」。
- **止めておく手段が無い。**
  料金は確保したメモリの時間課金。使わない日は `ops/down.sh` で消す。
  出典: `neptune.tf` の先頭のコメント。
- **口は AWS の API で、VPC の中のエンドポイントではない。**
  Neptune Database は VPC の中のクラスターエンドポイント（8182）だった。Analytics は `neptune-graph` の API で、VPC からは土台の `neptune-graph-data` のエンドポイントの private DNS で `<graph-id>.<region>.neptune-graph.amazonaws.com` が引ける前提。届かなければ `aws_neptunegraph_private_graph_endpoint` を足す。
  出典: `neptune.tf` の先頭のコメント、[troubleshooting.md](../../troubleshooting.md) の「閉域（`explicit deny`）」の Neptune の行。
- **閉域の Deny には入れていない。**
  `neptune-graph` のリクエストに `aws:SourceVpc` が付くか確かめていない。外から届かないことは、グラフの `public_connectivity = false` で守っている。
  出典: `terraform/base/core/perimeter.tf` のコメント、`terraform/pipeline/graph/sync.tf` のコメント。
- **グラフアルゴリズムは openCypher から呼ぶ。**
  `CALL neptune.algo.degree(...)` の形。使っているのは次数中心性・近接中心性・弱連結成分（`centrality` ツール）。媒介中心性は無い。
  出典: [data-stores.md](../../data-stores.md) の「11. Neptune とは」。
- **レプリカは 2 つまでなので、AZ は 3 まで。**
  `replicaCount` は 0〜2。レプリカ 1 つごとに同じ m-NCU の料金がかかる。既定の 1 では、障害が起きるとグラフが戻るまで止まる。
  出典: `neptune.tf` のコメント（Neptune Analytics API Reference「CreateGraph」、https://docs.aws.amazon.com/neptune-analytics/latest/apiref/API_CreateGraph.html 、2026-10-04 確認）、[data-stores.md](../../data-stores.md) の「12. AZ 冗長か」。
- **トポロジの最初の中身は lab の定義から入れる。物理層の正は Nautobot。**
  `ops/up.sh` の手順 7-3b と `ops/sync-graph.sh` が Web の EC2 越しに入れる。そのあとは Nautobot の Job が物理層を合わせる。`ops/sync-graph.sh --replace` は lab の定義で上書きする。
  出典: [pipeline.md](../../pipeline.md) の「Neptune のトポロジ」「Nautobot（機器の一覧とケーブルの正）」、`sync.tf` の先頭のコメント。
- **`--replace` で入れ直すと、Web で編集した内容とアラートで付いた `status` は消える。**
  lab の定義に戻る。機器の名前を変えたときも、その機器の `status` と上の層へのつながりは消える（名前が頂点の ID のため）。
  出典: `ops/sync-graph.sh` の先頭のコメント、[pipeline.md](../../pipeline.md) の「Nautobot（機器の一覧とケーブルの正）」。
- **登録の無い機器のアラートは、`registered=false` の頂点になる。**
  Lambda のログには `UNREGISTERED` の警告が出る。
  出典: [pipeline.md](../../pipeline.md) の「Neptune のトポロジ」。
- **Neptune が止まっても検知は続く。止まるのは `status` の更新、トポロジのツール、新しいワークフローの起動。**
  worker は起こす前に保守中の機器かどうかを Neptune で見るため。修復案の一覧と承認・却下は Neptune を使わないので動く。アラートは SQS で待ち、5 回受け取っても処理できなければ DLQ に行く。
  出典: [data-stores.md](../../data-stores.md) の「4. 気を付けること」、[workflow.md](../../workflow.md) の「流れ」。
- **IAM では頂点ごとに権限を分けられない。**
  許可はグラフ単位（`ReadDataViaQuery` / `WriteDataViaQuery` / `DeleteDataViaQuery`）。修復案を Neptune に置いていたあいだは、「承認できるのは人だけ」の線を IAM で引けなかった。2026-10-05 からは修復案が Neptune に無く、この線は決定のキューの `sqs:SendMessage`（Web の EC2 のロールだけ）で引いている。
  出典: `terraform/pipeline/graph/access.tf`、`terraform/workflow/proposals.tf` の先頭のコメント、[data-stores.md](../../data-stores.md) の「4. 気を付けること」。
- **修復案の頂点はやめた（2026-10-05）。**
  辺が 1 本も無く、id で引いて書き換えるだけで、グラフとして使っていなかった。同じ内容を S3 Tables の `proposal_events` にも書いていた（2 か所に書いていた）。いまは `proposal_events` だけ。
  出典: [修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)、FAQ「Neptune には修復案は書かないよね？ status 更新だけよね？」。
- **プロパティに向くのは「いまの値が 1 つ」のものだけ。**
  機器や回線の `status` がそれ。障害や修復案は 1 つの機器に何件も積み重なるので、分析で要るときに S3 Tables から「写し」として頂点で載せる。件数や期間の集計だけなら Athena で足りる。
  出典: FAQ「Neptune Analytics で分析するときに障害情報や修復案も必要になるなら、プロパティとして入れたほうがいい？」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| AWS の上での動作 | 2026-10-05 に AWS で確かめた。Lambda `<prefix>-graph-status` が `status` を DOWN / UP に書き換え、チャット（AgentCore Runtime）が「dc1-leaf-01 の接続先は」に正しく答えた。`private_graph_endpoint` は足していない（土台の `neptune-graph-data` のエンドポイントだけ）。`NEPTUNE_AZ_NUM` は既定の 1、エンドポイントは 2 AZ |
| `NEPTUNE_AZ_NUM` が 2 以上 | 未確認 |
| 16 m-NCU で足りるか | 2026-10-05 の動作確認は 16 m-NCU で通した（lab の 8 台）。メモリの使用量は見ていない |
| `neptune-graph` のリクエストに `aws:SourceVpc` が付くか | 未確認（それで Deny に入れていない） |
| サーバー側の問い合わせのタイムアウト | 付けていない（クライアント側は接続 3 秒・読み 10 秒・2 回。アラートの履歴を残す（001）の設計のリスクの 9） |
| `status` の Lambda が待つ時間 | エンドポイントが 3 AZ だと上限 66.6 秒で、Lambda の timeout 60 秒を超える（`ops/up.sh` は注意だけ出す） |
| アカウントあたりのグラフの数の上限 | リポジトリに数字が無い（Service Quotas で確かめる、と data-stores.md にある） |

2026-10-05 に、修復案（`proposal`）を S3 Tables の `proposal_events` だけに置き、Neptune をトポロジと `status` だけにした。[修復案を S3 Tables にまとめる（003）の設計](../../cycles/003-proposals-in-s3tables/design.md)。

OSS 版（`oss/terraform/pipeline/graph`）には Neptune Analytics が無く、代わりに `neo4j.tf` が Neo4j Community Edition 2026.09.0 と Graph Data Science を ECS（Fargate ARM、1 vCPU / 4 GB）に 1 台立てる。データはタスクの一時領域で、タスクが入れ替わるとグラフは空に戻る（`ops/sync-graph.sh --oss` で入れ直す）。アプリは `bolt://`（7687）でユーザー `neo4j` とパスワード（SSM の SecureString `/<prefix>/neo4j-password`）でつなぐ。[oss-variant.md](../../oss-variant.md)、[マネージドを OSS に置き換えた環境を作る（005）の設計](../../cycles/005-oss-on-ecs/design.md)。

## 関連

- [sns-sqs-lambda.md](sns-sqs-lambda.md)、[nautobot.md](nautobot.md)、[temporal.md](temporal.md)、[vpc-perimeter.md](vpc-perimeter.md)
- [data-stores.md](../../data-stores.md): 「Neptune の層」「11. Neptune とは」〜「14. トポロジをグラフにする意味」
- [pipeline.md](../../pipeline.md): 「Neptune のトポロジ」
- FAQ の 7 章「Neptune（グラフに置くもの）」: [faq-fukuda-nwc-poc.md](../../faq-fukuda-nwc-poc.md)
