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

## Round 1（2026-10-09）

Round 1 のセルフレビューの D2・D8 を PM に回し、判断をもらった。プランモードには入らず、design.md を上書きした（差し戻しからの再設計）。

### PM の判断

- 9-2 で terraform が読めないとき（`tf output` の失敗・空、`state list` の失敗のどちらも）は `exit 1` にしない。`GRAFANA_WARN` に「state が読めず Grafana のルールを確かめていない。Grafana が安定するのも待っていない。確かめ直すのは `ops/check-grafana.sh`」を入れ、9-2 の `aws ecs wait` とルールの検査を飛ばして最後の案内まで進む
- 理由: BACKLOG 81 の「止める」の意図は「空の引数で `aws ecs wait` に進まない」で、up.sh 全体を止めることではない。`ops/up.sh` の 8-5（:1235）の方針（最後の案内まで届かせる）に揃える。D8 の「黙って飛ばす」は無くし、D2 と同じ警告にする
- design.md の設計方針と検証方法、`docs/troubleshooting.md` の理由をこの判断に合わせて直す。テストは「tf output 失敗 / 空」「state list 失敗」の両方で警告が入り `ecs wait` に進まないことを見る

### 自分で決めたこと

- **`oss/ops/up.sh` の 7-4b の `AN_CLUSTER` は止めるまま。** 7-4b の手順で、すぐ下の同じ state の `opensearch_service_names` / `victoriametrics_service_names` も読めなければ `die` で止まる。ここだけ警告にしても次の行で止まる。PM の判断の範囲は 9-2。9-2 に来たときは、この値は空でない
- **`state list` は変数に読み切ってから grep する。** パイプのままだと `pipefail` の偽が「`state list` の失敗」と「Grafana が無い」のどちらか分からない
- **警告の文は 1 つにまとめる（`grafana_skip_warn`）。** state の一覧か出力のどれが読めなかったかは、その前の terraform のエラーと `tf_output` の赤い `NG:` の行に出る

### 却下した案

- **Round 0 の決定どおり止める（`|| exit 1`）。** 10 の配るコマンド、ほかの警告（`LAB_WARN` / `NAUTOBOT_WARN` / `WF_WARN` / `COST_NOTE`）の再掲、ポートフォワーディングに届かない。8-5 の方針と食い違う（D2）
- **`state list` が読めないときは今のまま黙って 9-2 を飛ばす。** 確かめていないことが利用者に分からない（D8）
- **9-2 だけ `tf_output` の `NG:` を黄色にする（止めない版の `tf_output` を作る）。** 呼ぶ所は 9-2 の 3 か所だけで、続けて黄色の警告が「確かめていない」と言うので、関数を増やすほどではない

## Round 2（2026-10-09）

- セルフレビュー（反対弁護人）の指摘「9-2 を打つ回と打たない回のテストが `GRAFANA_WARN` を見ていない」を採り、検証方法 2 に「どれも `GRAFANA_WARN` は空のまま」、4 に「外側の `if` に `else grafana_skip_warn` を足すと打たない回のテストが落ちる」を足した（検証項目の追加。設計方針・範囲は変えていない）
