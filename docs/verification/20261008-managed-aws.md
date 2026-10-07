# マネージド版を AWS で一度立てて、障害と復旧を通した記録（2026-10-08）

AWS アカウント 493116771193、ap-northeast-1、`OWNER=efukuda`（接頭辞 `efukuda-nwc-poc`）。時刻は UTC（日本時間は 2026-10-08 の朝）。

ここに書いた「通った」は、出力を見て確かめたものだけ。見ていないものは「未確認」、動かなかったものは「失敗」と書く。

## まとめ

- `ops/up.sh` は 1 回目で通った（rc=0、66 分 42 秒）。
- 障害注入から、アラート、Temporal のワークフロー、修復案、承認、`sudo lab heal-main` での修復、解消の通知、verified までが一続きで通った。
  - 異常の検知は約 2 分、承認から verified までは 52 秒。
- 落とした 2 本のうち承認しなかった方は、解消が先に届いて obsolete になった。
- `ops/down.sh` は rc=0（34 分 52 秒）。サービスごとに API で見て、実体が消えたことを確かめた。
- 残ったものは 2 つで、どちらも down.sh が意図して残すもの。
  - VPC `vpc-02ec7950cb98632a0` と、そのサブネット 3 つ、SG `efukuda-nwc-poc-runtime`。Runtime の ENI を外し終わるのを待っている。
  - ECR のリポジトリ 11 個（`KEEP_ECR=1`、7.39 GB）。
- 見つかった不具合。
  - Grafana の trap ルール（OpenSearch）は、評価のたびにエラーになる。Grafana 側からは trap のアラートが出ない。Splunk 側からは出た。
  - graph-status の Lambda が、Neptune への書き込みが重なったときに `ConflictException` で失敗した。Lambda のやり直しで 54 秒後に反映された。

## 事前確認（up.sh の前）

使ったのは `scratchpad/precheck.sh`。AWS の読み取りだけで確かめた。

- `efukuda-nwc-poc` の ECS クラスター、EMR Serverless のアプリ、MSK は無かった。
- VPC `vpc-02ec7950cb98632a0`（Name `efukuda-nwc-poc-vpc`）は残っていた。2026-10-05 の `ops/down.sh` で ENI を待って残したもの。中身は次のとおり。
  - サブネット 3 つ
  - SG `efukuda-nwc-poc-runtime`（sg-0bf4c62efe1fc4973）
  - ルートテーブル rtb-02a9ba99df70c6d1c
  - ENI、インスタンス、エンドポイントは 0
- ENI はとっくに外れていた。それでも、docs が言う「数時間おいて打ち直す」は誰も実行しておらず、3 日残っていた（下の「docs のずれ」）。
- タグ API（`Project=efukuda-nwc-poc`）は、消えたリソースも返していた。
- OSS 版の検証で残った `efukuda-nwc-oss` の VPC と SG はユーザーの判断で残しているので、触っていない。

## state の扱い

- Terraform の state は 9 つのルートとも local backend で、`ops/up.sh` を打ったチェックアウトにしか無い。
  - メインのチェックアウトの `deploy.env` と tfstate は、`ls` で有無だけ確かめた。中身は読んでいない。
  - worktree で up.sh を打つと state がその worktree に分かれる。そこで、up.sh と down.sh はメインのチェックアウト `/Users/eight/Documents/Dev/sandbox/nwc-poc` で打った（PM の指示の案 2）。
- 追跡しているファイルが変わっていないことは、git status の代わりに shasum で確かめた。
  - このセッションのハーネスが、別のチェックアウトへの `git -C` を拒むため。
  - 追跡対象で ignore されていない 321 ファイルの shasum を、up の前と down の後で比べ、差分 0 だった。
- 穴 1: state が 1 つのチェックアウトにしか無いので、worktree で作業するエージェントは、そのままでは立てたものを消せない。
  - worktree を消すと state も消える。今回は auto で作られた最初の worktree が途中で消えたが、state をメインのチェックアウトに置いていたので影響しなかった。
- 穴 2: 残った VPC を up.sh が使い回せるのは、state が残っている同じチェックアウトから打ったとき。
  - 今回は使い回せた（VPC ID が前後で同じ）。

## 立てたもの（ops/up.sh）

- 開始は 2026-10-07T18:34:57Z、終了は 19:41:39Z（rc=0）。かかった時間は 66 分 42 秒で、やり直しは無い。
- deploy.env の既定値のまま打った（down.sh の表示によると、ファイルから入ったキーは AGENT / PIPELINE / WORKFLOW / IMAGE_TAG / KEEP_ECR）。
- 環境変数は `OWNER=efukuda` と `NO_DASHBOARD_PORTFORWARD=1`。
- 作ったルート。
  - base/ecr
  - base/core
  - agent
  - pipeline/lab
  - stream
  - analytics
  - graph
  - nautobot
  - workflow
- 主なリソース。
  - Web の EC2: i-0836c92298988a378（10.0.0.34）。踏み台として、SSM Run Command で検査のスクリプトを打った。
  - lab の EC2: i-05a7c282915577428
  - ECS クラスター
    - workflow（Temporal と worker。タスクの IP は 10.0.0.164）
    - telegraf（dialin / dialout / kafka-ui）
    - analytics（splunk / grafana）
    - nautobot
  - EMR Serverless のアプリ 00g9bna3is0vf22l。ジョブ sinks-grafana / sinks-splunk / sinks-s3iceberg が RUNNING。
  - そのほか
    - MSK
    - OpenSearch Serverless（コレクション `efukuda-nwc-poc-logs`、インデックス `snmp-logs`）
    - AMP
    - Neptune Analytics
    - Lambda（graph-status / tools など）
    - SNS `efukuda-nwc-poc-alerts`
    - SQS（anomalies / decisions）
    - S3 / S3 Tables
    - AgentCore Runtime
    - SSM のパラメータ

## 検証の手筋

- AWS の外から VPC の中へは入れない（閉域）。Web の EC2 の上で `scratchpad/ssm-run.sh <instance> <script>` を使い、base64 で送ったスクリプトを AWS-RunShellScript で打った。
- パスワードは EC2 の上で `aws ssm get-parameter --with-decryption` で取り、シェル変数に入れた。出力には出していない。
- Grafana は `admin` で `/api/health`、`/api/datasources`、`/api/prometheus/grafana/api/v1/rules` を見た。
- AMP と OpenSearch は `/api/ds/query`（POST）で引いた。
  - `GET /api/datasources/proxy/uid/aoss-logs/...` は空を返した（OpenSearch のプラグインは、汎用のプロキシ経由では署名して引かないと見られる）。
  - AMP でも、`/api/v1/query` を汎用のプロキシ経由で引くと、最初は 0 系列だった。`/api/ds/query` では 396 系列が返った。
  - 同じプロキシ経由でも、`/api/v1/label/__name__/values` は返った。
- Splunk は、ログイン画面の `cval` を付けてログインした。cookie `splunkweb_csrf_token_8000` の値を `X-Splunk-Form-Key` に入れ、`/en-US/splunkd/__raw/services/search/jobs/export` でサーチした。
  - サーチ文に `%` を入れると「Unparsable URI-encoded request data」になる（`-d` で送ったため）。
- Neptune と修復案は、Web の EC2 の上の Web アプリのモジュールを、そのまま python3.13 で呼んで見た。
  - Neptune: `graph`
  - 修復案: `proposals.list_proposals`
- Temporal は UI の API `http://10.0.0.164:8233/api/v1/namespaces/default/workflows` で見た。
- ログは CloudWatch Logs の `filter-log-events` で見た。
  - worker: `/ecs/efukuda-nwc-poc-workflow`
  - Lambda: `/aws/lambda/efukuda-nwc-poc-graph-status`

## 項目と結果

| 項目 | 結果 | 根拠 |
|---|---|---|
| lab の起動（status / forward-status / check） | 通った | 19:42Z の `sudo lab status` でコンテナ 8 つが running、転送ルールあり。`sudo lab check` で IS-IS の隣接 up、ES-2 up、bond0 のスレーブ 2 本 up、VM 同士の ping ok（両方向）、leaf-01 の SNMP で admin=up の行が全部 oper=up |
| Telegraf → MSK → Spark → Splunk | 通った | 30 分のあいだに netops:metrics 49,770 件、netops:gnmi 203 件、netops:logs 11 件、netops:traps 3 件。最後の 1 件は 2〜3 分前。保存済みサーチ netops_gnmi / poll / trap / trap_clear は、どれも 1 分おきに success |
| Telegraf → MSK → Spark → AMP（uid `amp`） | 通った | `count(snmp_interface_ifOperStatus)` = 396。fail-bgp の後、`snmp_bgp_neighbor_session_up == 0` が dc1-leaf-01（peer 10.255.0.1）と dc1-spine-01（peer 10.255.2.1）の 2 系列 |
| Telegraf → MSK → Spark → OpenSearch（uid `aoss-logs`） | 通った | `snmp-logs` に直近の文書あり（kafka_offset が増えていく）。trap-test の OID `.1.3.6.1.4.1.8072.2.3.0.1` の文書（`fields.iso.3.6.1.4.1.8072.2.3.2.1: 1`）が入った |
| Spark → S3 Iceberg（sinks-s3iceberg） | 未確認 | ジョブが RUNNING なのは見た。テーブルの中身は引いていない |
| MSK 単体（Kafka UI） | 未確認 | 下流（Splunk / AMP / OpenSearch）にデータが届いたことからの推定だけ |
| fail-main → Splunk のアラートが SNS に届く | 通った | 19:46:00〜01 に sendmodalert で netops_trap が `published=1/1`。graph-status の Lambda が 19:46:03〜04 に `source: splunk, firing, link_down` を 2 件受けた |
| fail-main → Grafana のアラートが SNS に届く | 通った | graph-status の Lambda が 19:46:06〜07 に `source: grafana, firing, link_down`（dc1-leaf-01 ethernet-1/1、dc1-spine-01 ethernet-1/3）を受けた |
| fail-main → Temporal の `investigate-<anomaly_id>` が始まる | 通った | worker は 19:46:02 に `started investigate-dc1-leaf-01#link_down#ethernet-1/1 (splunk)` と `investigate-dc1-spine-01#link_down#ethernet-1/3 (splunk)` を出した。Temporal の UI の API では、どちらも RUNNING |
| 修復案が pending で入る | 通った | 19:46:13 と 19:46:15 に `proposal ...#1791402235: pending (action=heal-main)` を 2 件 |
| Neptune の状態が変わる | 通った | fail-main の後に link が 1 本 DOWN、heal の後に 0。fail-bgp の後に bgp_session が 2 つ DOWN、機器 3 台が ALARM（trap） |
| 承認 → 修復 | 通った | 19:46:37 に Web の EC2 の上で、ダッシュボードの承認ボタンと同じ関数 `incident_view.decide_proposal(pid, "approved", ..., approver="verify-20261008", confirmed=True)` を呼んだ。worker は 19:46:44 に `decide ...: approved by verify-20261008 (web)`、19:46:48 に `apply sudo lab heal-main -> Success` |
| heal-main → 解消の signal がワークフローに届く | 通った | 19:47:36 に worker が `resolved investigate-dc1-leaf-01#link_down#ethernet-1/1 (grafana)` を出し、修復案は `verified (47s)`。heal-main は手で打たず、承認から worker が SSM で打った |
| 承認待ちのあいだに解消 → obsolete | 通った | 承認しなかった dc1-spine-01 の修復案は、19:47:36 に `obsolete（承認を待つあいだに解消した）` |
| ワークフローが閉じる | 通った | 2 本とも COMPLETED（19:47:37 と 19:47:39） |
| `sudo lab check`（障害の後） | 通った | 19:48:38 に IS-IS / ES / bond / ping / SNMP がすべて ok |
| fail-bgp → bgp_down（Grafana） | 通った | 19:50:00 に `source: grafana, firing, bgp_down` を 2 件。ルールの状態は firing で、dc1-leaf-01 10.255.0.1 と dc1-spine-01 10.255.2.1 が Alerting |
| fail-bgp → bgp_down（Splunk） | 通った | 19:50:01 に `source: splunk, firing, bgp_down` を 2 件 |
| fail-bgp でワークフローが始まらない（link_down だけが始める） | 通った | fail-bgp の後も、Temporal のワークフローは 2 本のまま |
| heal-bgp → 戻る | 通った | 19:51:48 の `sudo lab check` で、BGP の 4 本とも established |
| heal-bgp → 解消（Splunk） | 通った | 19:53:01 に `source: splunk, resolved, bgp_down` を 2 件 |
| heal-bgp → 解消（Grafana） | 未確認 | 19:53:27 までに Grafana の resolved は届かず、そのまま down.sh に進んだ |
| trap-test → trap（Splunk） | 通った | 19:50:01 に `source: splunk, firing, trap, dc1-host-01, .1.3.6.1.4.1.8072.2.3.0.1` |
| trap-test → trap（Grafana） | 失敗 | ルールの評価が毎回エラー（下の「不具合」）。状態は `Normal (Error, KeepLast)` |
| trap の解消（約 10 分後） | 未確認 | 待たずに down.sh に進んだ |
| ダッシュボード（Gradio）の画面と、Temporal の UI の画面 | 未確認 | 画面は開いていない（ポートフォワードを張っていない）。上の承認は同じ処理の関数を呼んだもの。Temporal は UI の API で状態を見た |
| Web の EC2 | 通った | `efukuda-nwc-poc-web` が active、`http://127.0.0.1:8080/` が 200 |

## 障害の時刻（UTC）

| 時刻 | できごと |
|---|---|
| 19:43:55 | `sudo lab fail-main`（dc1-leaf-01 ethernet-1/1 ⇄ dc1-spine-01 の fabric を落とす） |
| 19:46:00〜01 | Splunk の netops_trap が SNS へ送る（published=1/1） |
| 19:46:02 | worker がワークフロー 2 本を始める（source は splunk） |
| 19:46:03〜07 | graph-status の Lambda が link_down を受ける（splunk、続いて grafana）。Neptune の link が DOWN |
| 19:46:13 / 19:46:15 | 修復案 2 件が pending（action=heal-main） |
| 19:46:37〜46 | dc1-leaf-01 の修復案を承認する |
| 19:46:44 / 19:46:45 | worker が承認を受け取り、ワークフローで approved になる |
| 19:46:48 | `sudo lab heal-main` を SSM で打つ（Success） |
| 19:47:36 | Grafana の resolved が届く。leaf-01 は verified（47 秒）、spine-01 は obsolete |
| 19:47:37 | graph-status の Lambda が Neptune の `ConflictException` で失敗する |
| 19:47:37〜39 | ワークフロー 2 本が COMPLETED |
| 19:48:01〜03 | Splunk と Grafana から、link_down と isis_down の resolved |
| 19:48:31 | graph-status の Lambda のやり直しで、Grafana の resolved（leaf-01）が Neptune に反映される |
| 19:48:38 | `sudo lab check` がすべて ok |
| 19:48:50 | `sudo lab fail-bgp`（dc1-leaf-01 ⇄ 10.255.0.1 の iBGP） |
| 19:48:53 | `sudo lab trap-test` |
| 19:50:00〜01 | Grafana と Splunk から bgp_down、Splunk から trap |
| 19:51:04 | `sudo lab heal-bgp` |
| 19:51:48 | BGP 4/4 が established |
| 19:53:01 | Splunk から bgp_down の resolved |
| 19:53:43 | `ops/down.sh` を始める |

所要時間は次のとおり。

- link_down の検知: 障害から Splunk のアラートまで約 2 分 6 秒、Grafana のアラートまで約 2 分 11 秒。
- bgp_down の検知: 約 70 秒。
- 承認から verified まで: 52 秒。
- 修復のコマンドから解消まで: 47 秒。

## 片付け（ops/down.sh）

- 開始は 19:53:43Z、終了は 20:28:35Z（rc=0）。かかった時間は 34 分 52 秒。
- 消した順と件数。
  - workflow 39 件
  - analytics 47 件（Spark のジョブ 3 つを cancel）
  - nautobot 16 件
  - graph 11 件
  - stream
  - lab
  - agent
  - base/core（VPC・サブネット・runtime の SG は残した）
  - ECR は残した（`KEEP_ECR=1`）
  - Runtime のロググループ
  - SSM のパラメータ
- 最後のタグ API の一覧は「残り 189 件」と出した。
  - 中身は、消した直後のリソースと、2026-10-05 以前の EMR のアプリ・ジョブラン（00g99hcujbmag02l など）。
  - タグ API の一覧は当てにならないので、下でサービスごとに API で確かめた。

### 消えたことの確認（20:29:11Z、`scratchpad/gone.sh` / `gone2.sh`）

| サービス | 結果 |
|---|---|
| ECS クラスター | 無し |
| EMR Serverless のアプリ（TERMINATED 以外） | 無し |
| MSK | 無し |
| OpenSearch Serverless（コレクション / 暗号化・ネットワーク・データのポリシー） | 無し |
| Neptune Analytics（グラフ） / Neptune DB クラスター | 無し |
| AMP（ワークスペース） | 無し |
| Lambda | 無し |
| SNS | 無し |
| SQS | 無し |
| Firehose | 無し |
| S3 のバケット | 無し |
| S3 のテーブルバケット | 無し |
| SSM のパラメータ（`/efukuda-nwc-poc/`） | 無し |
| EC2 のインスタンス（terminated 以外） | 無し |
| AgentCore Runtime | 無し |
| CloudWatch のロググループ（/ecs、/aws/lambda、Runtime） | 無し |
| NLB / Cloud Map の名前空間 | 無し |
| ECR | 残った（11 リポジトリ、7.39 GB）。`KEEP_ECR=1` による |
| VPC `vpc-02ec7950cb98632a0` | 残った（下） |

### 残ったもの

- VPC `vpc-02ec7950cb98632a0`（`efukuda-nwc-poc-vpc`、available）。
  - サブネット 3 つ（subnet-06920a97d6429022f / subnet-071d19c3f8bd256be / subnet-0ac8c5ecad35f4a79）
  - SG `efukuda-nwc-poc-runtime`（sg-0bf4c62efe1fc4973）と default の SG
  - 理由: AgentCore Runtime の ENI `eni-044415331148356cf` が残っている（InterfaceType `agentic_ai`、in-use、SG は `efukuda-nwc-poc-runtime`、要求元は AWS 側）。docs/deploy.md の「Runtime の ENI は最大 8 時間残る」のとおり。
  - エンドポイント、NAT、IGW、インスタンスは 0。時間課金のあるものは無い。
  - 指示どおり、down.sh を打ち直していない。手でも消していない。
- ECR 11 リポジトリ（kafka-ui / lab-multitool / temporal / redis / telegraf / worker / lab-srlinux / splunk / grafana / nautobot / agent）。
  - 合計 7.39 GB。deploy.env の `KEEP_ECR=1` で down.sh が残した。

## 費用の見積もり

- 立っていた時間: 18:34:57 から 20:28:35 までの約 1 時間 54 分。
- 時間あたりの見積もり: 約 $2.92/h。これに、OpenSearch Serverless の OCU が最大 $0.33/h 足される。
  - 内訳は MSK、EMR Serverless、ECS Fargate、Neptune Analytics、EC2 2 台、VPC エンドポイントなど。
- 合計: 約 $5.5〜6.2（1 ドル 150 円で約 830〜930 円）。
  - 請求額を見たものではない。料金表から積んだ見積もり。
- 残ったものの費用。
  - ECR: 7.39 GB × $0.10/GB・月 ≈ $0.74/月（約 110 円/月）。
  - VPC、サブネット、SG: 無料。

## 不具合

1. Grafana の trap ルール（`grafana/provisioning/alerting/netops-opensearch.yaml`、uid `nwc-trap`）の評価が毎回エラーになる。
   - エラーの文面:
     - `[sse.dataQueryError] failed to execute query [A]: bucket budget out of bounds: terms aggregations would produce up to 13600 buckets per time bucket, leaving fewer than 20 time buckets within the 65535 bucket limit`
   - 起動直後から trap-test の後まで、ずっと `Normal (Error, KeepLast)` で、Grafana 側からは trap のアラートが出ない。
   - ファイルのコメントは「機器 50 × OID 20 まで（2.34.4 で実測）」とあるが、このデプロイの Grafana 13.2.2 のプラグインは 13,600 と見積もって拒んだ。terms の size か、時間の幅を見直す必要がある。
   - Splunk 側の trap は通っている。
2. graph-status の Lambda が、Neptune Analytics の `ExecuteQuery` で `ConflictException`（concurrent operations）になる。
   - 19:47:37 に Grafana の resolved を書いたとき、同じ秒に Grafana の resolved が 2 件（leaf-01 と spine-01）届いており、書き込みが重なった。
   - 例外で終わり、Lambda の非同期のやり直しで 54 秒後に反映された。データは失われていないが、ERROR のログが出て、反映が遅れる。
   - 関数の中でやり直すと静かになる。

## docs のずれ

1. `docs/deploy.md` の 117 行目と `docs/troubleshooting.md` の 102 行目は「数時間おいて `ops/down.sh` を打ち直す」と書いている。
   - 実際には誰も打ち直しておらず、2026-10-05 の VPC が 3 日残っていた。今回も同じ形で残った。
   - 打ち直すのが誰か、いつかが決まっていない。「残っても無料。次の up.sh が使い回す」を既定の扱いとして書くか、打ち直しの段取りを決めるのがよい。
2. `docs/deploy.md` の 38 行目は、`KEEP_ECR` の保管料を「月数円」と書いている。
   - 実際は 7.39 GB で、約 $0.74/月（約 110 円/月）。
3. `docs/deploy.md` の「最後に `Project=<prefix>` のタグが残っているものを出す。何も出なければ全部消えている」と、down.sh の表示「消した直後の数分は消えたものが出ることがある」について。
   - 今回は 189 件出た。中には何日も前に消えた EMR のアプリやジョブランも入っていて、「数分」では消えない。
   - 消えたかどうかはタグ API でなく、サービスごとの API で見る、と書くのがよい。

## 検査の手筋のずれ（docs ではなく、依頼の手順）

- 依頼の手順にある Grafana の汎用のプロキシ（`GET /api/datasources/proxy/uid/<uid>/<path>`）では、OpenSearch（aoss-logs）は空を返した。
- マネージド版では、AMP と OpenSearch は `/api/ds/query`（POST）で引くと確実。
