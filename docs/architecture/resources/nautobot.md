# Nautobot（ECS + RDS）

← [リソースごとの知見](README.md)

## ひとことで

機器の一覧とケーブルの正（台帳）。人が編集する場所をここ 1 か所にし、機械が読む場所（Telegraf の取りにいく先と Neptune の物理層）へは Nautobot の中の Job が写す。
Fargate の 1 タスク（web・worker・redis の 3 コンテナ）と、RDS の PostgreSQL 1 台で動かしている。`ops/down.sh` で DB ごと消える。

## このプロジェクトでの使い方

| 項目 | 値 | 定義している場所 |
|---|---|---|
| サービス | `<prefix>-nautobot`。1 タスクに 3 コンテナ。Fargate ARM、2 vCPU / 4 GB。AZ を選ぶキーは無い | `IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf` |
| イメージ | 公式の `networktocode/nautobot:3.2.6-py3.12` に boto3、`app/nautobot/` の Job、`app/agentcore/graph.py`、`lab_seed.json` を足したもの。ECR の `<prefix>-nautobot`。redis は `8.10.2-alpine` を ECR の `<prefix>-redis` に写したもの | `docker/images/nautobot/Dockerfile`、`ops/up-common.sh` の `NAUTOBOT_VERSION`、`REDIS_TAG` |
| DB | RDS の PostgreSQL 17、`db.t4g.micro`、gp3 20 GB。バックアップ無し、最後のスナップショット無し。`NAUTOBOT_DB_AZ_NUM`（既定 1、1〜2。2 は Multi-AZ） | `IaC/terraform/aws-managed/pipeline/nautobot/database.tf` |
| 名前 | Cloud Map `nautobot.<prefix>-nautobot.internal:8080`。SSM の String `/<prefix>/nautobot/url` にも書く | `nautobot.tf` |
| シークレット | SSM の SecureString `/<prefix>/nautobot/{secret-key,admin-password,db-password,api-token}` の 4 つ（`ops/up.sh` が apply の前に乱数で作る） | `ops/up-common.sh` の `ensure_nautobot_secrets`、`nautobot.tf` の `secrets` |
| ログ | `/ecs/<prefix>-nautobot`。ストリームは `web/`、`worker/`、`redis/` | `nautobot.tf` |
| スイッチ | `PIPELINE=1` ならいつも立つ。`SKIP_STREAM` と `SKIP_GRAPH` の両方があるときだけ作らない | `ops/up.sh` |
| 費用 | 13 セント/時（Fargate 9.9 + RDS 2.5 + gp3 0.4）。Multi-AZ で 16 セント/時 | `ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`） |

台帳のどこが、どこに映るか:

| Nautobot | 反映先 |
|---|---|
| Device の Service `gnmi`（tcp） | Telegraf の gNMI の購読先（SSM `/<prefix>/telegraf-dialin/nautobot/gnmi-targets`） |
| Device の Service `snmp`（udp） | Telegraf の SNMP のポーリング先（同 `snmp-agents`。`SNMP_POLL=0` では使わない） |
| Device（名前、Location、Role、primary IPv4、`asn`）と Interface | Neptune の `device` / `interface` |
| Cable（`link_role`、`bandwidth_mbps`） | Neptune の回線。種類（fabric / l2 / lag）は両端の Role と LAG から決める |
| Device の Status `Maintenance` | Neptune の `device` の `maintenance`。その機器の `link_down` ではワークフローを起こさない |
| 変更の履歴（ObjectChange） | Neptune の頂点 `change`（新しい順に 50 件） |

## つながり

| 相手 | 向き | ポートと認証 |
|---|---|---|
| 利用者の PC | PC → Web の EC2 → Nautobot | 8080/tcp（手元は `localhost:8081`）。SSM のポートフォワーディング。ユーザー admin |
| Web の「トポロジ」タブ | Web の EC2 → Nautobot の REST API | 8080/tcp、API のトークン |
| RDS | タスク → DB | 5432/tcp。SG `<prefix>-nautobot-db` はタスクからだけ受ける |
| SSM と ECS | Job → パラメータ、`ecs:UpdateService` | `ssm`、`ecs` のエンドポイント、タスクロール |
| Neptune Analytics | Job → グラフ | `neptune-graph-data` のエンドポイント、SigV4、openCypher（差分） |

## 知見

- **web・DB・Job・Celery・Redis は、Nautobot にもともとある組み合わせ。**
  PostgreSQL と Redis は使う側が用意する決まり。ここでは DB を RDS、Redis をタスクの中のコンテナにした。足したのは `app/nautobot/` の Job と起動時の用意（`bootstrap.py`）。
  出典: [nautobot.md](../../nautobot.md) の「3. 部品ごとの役割」、FAQ「Nautobot はもともと Web・データベース・Job・Celery・Redis がセットになったもの？…」。
- **タスクは 1 つだけ。2 つにするとキャッシュ・ロック・キューが別々になる。**
  Redis と Celery の worker が同じタスクにあるため。入れ替えのときも、古いほうを止めてから新しいほうを起こす。コードから確かめた理由で、AWS では試していない（2026-10-04）。
  出典: `IaC/terraform/aws-managed/pipeline/nautobot/nautobot.tf` のコメント。
- **Redis は 8 系（`8.10.2-alpine`）。**
  8 系からライセンスに AGPLv3 を選べる（7.4 は RSALv2 / SSPL だけで、OSS のライセンスではなかった）。公式のイメージは Search・JSON・Bloom・TimeSeries のモジュールを読み込んで起きる（使っていない。起きた直後の使用メモリは約 1.4 MB）。持ち続けるデータは無い（`--save "" --appendonly no`）ので、版を上げても移すものは無い。
  2026-10-08 に手元のコンテナで、同じ command と healthCheck（`redis-cli ping`）で起き、Nautobot 3.2.6 のイメージ（redis-py 8.1.0、kombu 5.6.2、Celery 5.6.3、django-redis 7.0.0）からキャッシュの読み書きと Celery のブローカーの送受信ができた。AWS では未確認。
  出典: https://redis.io/legal/licenses/ （ライセンス）、https://redis.io/docs/latest/operate/oss_and_stack/stack-with-enterprise/release-notes/redisce/redisos-8.0-release-notes/ （8.0 の変更は ACL の分類と `GETRANGE` で、ここでは使っていない。2026-10-08 確認）。
- **DB を 3 AZ にはできない。**
  2 は Multi-AZ の DB インスタンス（待機系 1 台。読めない）。3 AZ は Multi-AZ DB クラスターという別のリソースで、`db.t4g.micro` が使えない。
  出典: `IaC/terraform/aws-managed/pipeline/nautobot/database.tf` のコメント（Amazon RDS User Guide、https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html と https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/multi-az-db-clusters-concepts.html 、2026-10-04 確認）。
- **DB のパスワードは plan にも state にも残らない。**
  `ops/up.sh` が SSM の SecureString に作り、Terraform は ephemeral で読んで write-only の引数（`password_wo`）に渡す。タスクは同じパラメータを ECS の secrets で受ける。
  出典: `database.tf` のコメント。
- **最初の起動は migrate に 5〜10 分かかる。**
  ヘルスチェックは合わせて 15 分まで待つ。`bootstrap.py` が落ちても画面は上げる（ログに理由が出る）。
  出典: `nautobot.tf` のコメント。
- **最初の中身は lab の定義から入れる。2 回目からは Nautobot の中身が正。**
  機器が 0 台のときだけ、イメージに入れた `lab_seed.json`（8 台 / 12 本）から seed する。
  出典: [nautobot.md](../../nautobot.md) の「4. 起動してから同期するまで」。
- **同期は Redis のロックの中で「読む → 書く」をする。**
  Job が重なっても順に走る。Telegraf とグラフ DB（Neptune。OSS 版は Neo4j）の片方が失敗しても、もう片方はやる。
  出典: 同上。
- **Job が触るのは物理層と変更履歴だけ。**
  `status`（アラートが書く）と IP 層・EVPN/BGP 層は触らない。IP 層から上は lab の定義から `ops/sync-graph.sh` が入れる（Nautobot には無い）。
  出典: [nautobot.md](../../nautobot.md) の「7. 役割の分担（Nautobot と Neptune）」、[pipeline.md](../../pipeline.md) の「Nautobot（機器の一覧とケーブルの正）」。
- **Telegraf の一覧は、変わったときだけ書き換えてサービスを作り直す。**
  作り直すと購読が数十秒切れる。
  出典: [pipeline.md](../../pipeline.md) の「Nautobot（機器の一覧とケーブルの正）」。
- **機器が 1 台も無いときは Neptune を触らない。**
  空で合わせると物理層が全部消えるため。
  出典: [nautobot.md](../../nautobot.md) の「8. 押さえておくこと」。
- **機器の名前を変えると、Neptune では別の機器になる。**
  名前が頂点の ID。その機器の `status` と上の層へのつながりは消える。
  出典: 同上。
- **一括で変えるときは、JobHook `netops-sync` を止めてから。**
  変更 1 件ごとに Job が走り、一覧が変わるたびに Telegraf の取りにいく側が作り直される。最後に手で Job を打つ。
  出典: 同上。
- **JobHook は、変更した人に Job を実行する権限が無いと出ない。**
  管理者は出る。権限を絞ったユーザーを作るなら、Job `netops_jobs.SyncOnChange` の実行も許す。
  出典: [pipeline.md](../../pipeline.md) の「Nautobot（機器の一覧とケーブルの正）」。
- **Web の「トポロジ」タブのリンクの編集は、Neptune でなく Nautobot に書く。**
  SSM の `/<prefix>/nautobot/url` があるあいだ。Web が Neptune の物理層を直接書くことはない。
  出典: [nautobot.md](../../nautobot.md) の「7. 役割の分担（Nautobot と Neptune）」。
- **`ops/sync-graph.sh --replace` は lab の定義で上書きする。**
  Nautobot で足したものは、Job を打つまで Neptune から消える。
  出典: [nautobot.md](../../nautobot.md) の「8. 押さえておくこと」。

## 制約と未確認

| 項目 | 状態 |
|---|---|
| 編集した内容 | `ops/down.sh` で DB ごと消える。作り直すと lab の定義から入り直す |
| AWS で確かめた範囲 | 起動・seed・Job と JobHook の登録・起動時の同期まで |
| Nautobot での変更 → JobHook → SSM / dialin / Neptune | AWS では未確認（手元のテスト `tests/test_nautobot.py` と、手元の Docker の Nautobot 3.2.6 への REST API だけ） |
| Web からの Nautobot への書き込み | AWS では未確認 |
| 本番の機器の一覧を外から入れる | PoC には未実装（[nautobot.md](../../nautobot.md) の 6 章の (7)） |
| デバッグ用の EC2（`ops/lab-debug.sh`） | Nautobot を使わない |
| OSS 版（`IaC/terraform/oss/pipeline/nautobot`。同じファイルをシンボリックリンクで使う） | Job は Neo4j に書けない（タスク定義が `GRAPH_BACKEND` などを渡さず、イメージに Neo4j のドライバーが無い）。トポロジは lab の定義から `ops/sync-graph.sh --oss` で入れる（[005 の設計](../../cycles/005-oss-on-ecs/design.md)の「実装の状態」） |

## 関連

- [neptune-analytics.md](neptune-analytics.md)、[telegraf.md](telegraf.md)、[ssm-parameter-store.md](ssm-parameter-store.md)、[lab-ec2.md](lab-ec2.md)
- [nautobot.md](../../nautobot.md): 構成・部品・使い方・Neptune と組み合わせた使いどころ
- [pipeline.md](../../pipeline.md): 「Nautobot（機器の一覧とケーブルの正）」
- FAQ の 6 章: [faq-fukuda-nwc-poc.md](../../faq-fukuda-nwc-poc.md)
