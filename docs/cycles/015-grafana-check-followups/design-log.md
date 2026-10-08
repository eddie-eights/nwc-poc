# 設計の経緯（015）

## Round 0（2026-10-08）

依頼は PM（エンジニア1 のリーダー）から。ユーザーに聞くことは PM に聞く決まりなので、調査のあと前提 5 つを PM に送り、確認を 1 回取った。

### PM に出した前提（すべて了承）

1. 終了コードは 0 OK / 1 NG（ログの案内あり）/ 2 未確認（401、届かない、待ち切れ、SSM の締め切り、判定の行が無い。ログの案内なし）/ 3 確かめる前に止まった（使い方の誤り（今は 2）、`deploy.env`、`tf output`、Web の EC2 か Grafana が無い）
2. SSM Run Command を送れない・失敗したも 2（確かめに行ったが結果が分からない側）
3. 締め切りは `SSM_RUN_WAIT`（秒、既定 1800）。過ぎたら「…秒たっても結果が分からない（最後の状態: …）」と `aws ssm get-command-invocation` のコマンドを出して 2
4. 81: 9-2 で `tf output` が失敗か空なら止める（`die`）
5. 83: マネージド版だけ。`ANALYTICS_LEFT=1` で state に Grafana があるときも 9-2 を打つ。OSS 版は analytics をいつも作るので変えない
6. N2: v13.2.3 の `group_limit` は既定 -1 なので送らない。`groupNextToken` を `group_next_token` で追い、同じトークンが来たら読み直し、最後は未確認

### PM が足したこと

- 「1 の終了コードは、up.sh / oss/ops/up.sh の 9-2 の受け側（grafana_rules_step）の警告文も 1 / 2 / 3 で変えてください（NG はログ案内、未確認は確かめ直す check-grafana.sh のコマンド、3 は die）。docs/troubleshooting.md に 4 つの値の表を 1 つ」
- 「3 の SSM_RUN_WAIT は deploy.env の変数ではなく環境変数のままでよいです。.env.example には書かず、docs/deploy.md か ops の説明に 1 行」
- 「N2 は「13.2.3 の既定は group_limit=-1 なのでトークンは来ない。来たときのための道」であることを design.md に書き、トークンの道はテストの偽サーバーで 2 ページ返す形で縛ってください（同じトークン 2 回で未確認になることも）」

### 却下した案

- **83 で前の回の Grafana を `tf output -raw grafana_service_name` が空でないかで見分ける。** 出力の読み取りが失敗したとき（81 で止めたい場合）と「無い」を分けられない。state の一覧の `aws_ecs_service.grafana[` を見る形にした
- **81 を 9-2 の中で `GF_SERVICE=$(tf …) || die …; [ -n … ] || die …` と書き下す。** マネージドと OSS で 4 か所に同じ 2 行が並ぶので、`tf_output` にまとめた
- **締め切りで `aws ssm cancel-command` を打つ。** Web の再起動やグラフの初期ロードを途中で切ると、EC2 の上の状態が中途半端になる。止めずに `get-command-invocation` のコマンドを案内する形にした
- **`SSM_RUN_WAIT` を `deploy.env` のキーにする。** PM の指示で環境変数のまま
- **`grafana_rules_check.py` で `group_limit` を明示して小さなページで読む。** 13.2.3 の既定（-1）で 1 回で全部返るので、要求の回数を増やすだけになる
