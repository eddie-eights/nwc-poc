# S3 の置き場を整える（035）の設計ログ

## Round 0（2026-10-10、PM と ユーザー）

### きっかけ

AWS の 1 回の検証を「一旦ストップ」したあと、ユーザーの「いまって spark のチェックポイントは s3 に保管してる？」から始まった。checkpoint は `analytics/checkpoint/<MSK の uuid>/` にあり、ユーザーが「`/spark/checkpoint/` の方が分かりやすい」と言ったので pm/aws-verify で改名した（9 ファイル、未コミット）。続けて「s3 の構成も全体的に見直した方がいい」。

### 案 1: 部品名でプレフィックスを切る（PM の最初の案）

1 バケットのまま、`web/` `kb/` `lab/` `spark/` `firehose/` のように**書く部品の名前**で揃える。EMR のログは `spark/logs/`。

- 利点: いまの `web/` `lab/` と同じ軸。変更が小さい
- 欠点: ログに期限を付けるにはプレフィックスごとのライフサイクルが要り、バケットを `ops/down.sh` で消すとログも消える

### 案 2: 種類でプレフィックスを切る

`assets/` `logs/` `state/` のように**データの種類**で切る。

- 欠点: `assets/web/` のように 2 段になり、`ops/up.sh` の `s3 cp` 先が全部変わる。種類の境目（checkpoint は state か assets か）で迷う

### ユーザーの質問「logs は別バケットに分けるメリットはある？」

PM の答え: AWS のベストプラクティスは「ライフサイクル・アクセス権・暗号化の要件が違うデータは別のバケット」。ログは (1) 7 日で消したい、(2) `ops/down.sh` で消さずに残して調べたい、(3) 書くのが AWS のサービス（EMR / Firehose）で VPC の外からも書く、の 3 点で配布物と要件が違う。1 バケットでもプレフィックスごとのライフサイクルで (1) はできるが、(2) は無理（バケットごと消える）。

### ユーザーの決定（原文）

「logsはバケット分ける、assetsでOK」「logsはdown.shで消さなくてよくて7日で消えるようにしたい」

→ バケット 2 本。assets は案 1 の部品名、logs は種類名（`emr/` `firehose-errors/`）。これが `design.md` の正本。

### 却下した案

- **Temporal の履歴や RDS のスナップショットを S3 に置く**: 聞かれたので調べたが（「temporal ワーカーの履歴って rds に保管してる？」→ RDS に保管。「temporal は本番でも dev 起動？」→ 本番向け起動は別の話）、S3 の話ではない。BACKLOG の候補にするかはユーザー未回答。
- **logs バケットにも `DenyOutsideVpc` を付ける**: `base/logs` は VPC より先に作り、VPC より長生きする。Firehose は VPC の外から書く。付けると down.sh のあとの logs バケットが VPC の無い Deny を持つことになる。付けない。
- **`KEEP_LOGS=0` のような切り替えで down.sh から消せるようにする**: 消す手段は `terraform -chdir=… destroy` の 1 コマンドで足りる。切り替えは増やさない。
- **logs の接頭辞を `s3://<bucket>/spark/…` にして assets と揃える**: logs の中は「誰が書いたか」より「何のログか」で探すので種類名にした。

### 設計の補足

- 今日の checkpoint の改名（9 ファイルの未コミット差分）はこのサイクルに吸収し、差分は捨てた。エンジニアが origin/main（3d497de）から新しい配置で書く。
- `terraform state mv` は要らない。2026-10-10 時点で AWS のリソースは全部消えている。

## Round 1（2026-10-10、実装で design.md と現物が食い違ったところ）

- OSS 版の `base/logs` の symlink は 6 本でなく 7 本（`IaC/terraform/oss/base/ecr/` と同じく `.terraform.lock.hcl` もリンクする）。design.md の 3 か所（設計方針、変更対象ファイル、検証方法）を直した。
