# レビュー: docs を 036〜051 のコードに揃える（052）

## Round 1

レビューモデル: PM(opus-5.5) / effort: high。2026-10-12。対象は `build.md` Round 1（エンジニア opus / high、commit `978045b`）と PM の修正 `96f4e21`。

**cold reviewer: 依頼した**（opus / xhigh。初回ビルドの直後）。

### cold reviewer の結果（review-r01.md をそのまま連結）

# cold review Round 1: docs を 036〜051 のコードに揃える（052）

対象: `bddbcb7..96f4e21`（ブランチ `docs/166-docs-sync`、HEAD `96f4e21`）。レビューの日付: 2026-10-12。レビューした人は文脈を持たない cold reviewer。

## サマリ

- 変更は 33 ファイル。docs が 30、pptx が 2、`IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf` のコメントが 1 行。
- design.md の D-1〜D-32 は、確かめたものがすべて現物（コード・ops・検証の記録）と合っていた。確かめたのは、init のタスク定義と secrets、`DenyNautobotSecrets` と `DenyNautobotParameters` の付け先、SG の表、ECR の 15 個、QEMU の一覧、ビルドのコンテキスト、`TF_CLI_ARGS` の守り、045 / 051 / 20261010 の項目の番号、デッキの数と目印。
- V-1 の grep は、`docs/cycles/` を除いてどれも 0 行だった。残ったのは、残してよい `ecr.md:100` の 1 行だけ。
- V-5 の `bash ops/check.sh` は自分で打った。最後の行は「すべて通過」（模擬テストは「通過 457 / 失敗 0」）。打ったあとの `git status --porcelain` は空だった。
- 設計の背景に「原因 2」として書かれた「045 で AWS で確かめたことが『AWS では未確認』のまま残っている」に当たる行が、D の一覧から漏れて resources の 4 ファイルに残っている。このサイクルで直した行と同じ事実について、docs の中で食い違いができた（Should fix 3 件）。
- 件数は Must fix 0、Should fix 3、Nit 1。

### 見た観点 / 見ていない観点

- 見た観点（design.md との整合）: D-1〜D-32 を現物と照合した。V-1・V-3・V-5 は打ち直し、V-2・V-4 は記録と現物で照合した。
- 見た観点（正しさ）: docs の記述を、`workflow/ecs.tf`・`workflow/iam.tf`・`base/core/security_groups.tf`・`base/core/perimeter.tf`・`ops/up.sh`・`ops/up-common.sh`・`ops/down.sh`・`ops/down-common.sh`・`docker/images/temporal-server/init.sh` と、`docs/verification/` の 3 本に突き合わせた。
- 見た観点（リンクとスライド）: 追加された相対リンク 10 本は、すべて実在するパスを指していた。pptx 2 本は XML の文字列（日付、`workflow-init`、手順 0「道具と認証」、「logs のバケット … は残す」）を数え、deck.md と同じ数だった。
- 見た観点（範囲とテスト）: outputs.tf の差分は `#` の行だけだった。テストは `ops/check.sh` を打った。
- 見ていない観点（コードの観点）: security、runtime bugs、data loss、API compatibility、type safety。コードの変更が outputs.tf のコメント 1 行だけのため。
- 見ていない観点（図と見た目）: mermaid の図（`docs/nautobot.md`、`docs/architecture/workflow.md`）が描けるかは描画していない。pptx の見た目（はみ出し・重なり）は開いていない。
- 見ていない観点（拾い漏れの探索）: D の一覧が拾えなかった古い記述は、「未確認」を含む行のうち 036〜051 の話題（Temporal / init / ACL / trap）に絞って探しただけ。docs 全体は総当たりしていない。

## Must fix

None

## Should fix

- [design.md との整合 / 正しさ] Grafana の `trap` のルールの評価が、resources の 2 ファイルの 3 行で「AWS では未確認」のまま残っている。D-20 で直した `docs/alert-comparison.md:17` / `:218` / `:321` と `docs/pipeline.md:594`（評価がエラーにならないことは AWS で確認済み）と、同じ事実で食い違う。
  分類の理由: D の一覧（合意した範囲）に無い行なので Must にはしない。ただ、設計の背景の原因 2 そのもので、このサイクルのあとに docs の中で矛盾が生まれた。
  再現（打った）:
  ```
  $ grep -n '008 で直した。AWS では未確認\|手元の Grafana で通したが、AWS では未確認' docs/architecture/resources/grafana.md docs/architecture/resources/sns-sqs-lambda.md
  docs/architecture/resources/sns-sqs-lambda.md:139:  - Grafana の `trap` は、ルールの評価がエラーで出なかった（008 で直した。AWS では未確認）。
  docs/architecture/resources/grafana.md:138:  - `trap` はルールの評価が毎回エラーで出なかった。「AWS 検証で見つけた不具合 3 件を直す（008）」でルールを絞り、手元の Grafana で通したが、AWS では未確認。
  docs/architecture/resources/grafana.md:141:  - 008 でルールを絞り手元の Grafana で通したが、AWS では未確認。
  ```
  根拠: `docs/verification/20261011-aws-managed-045.md:93` の H-2 が「判定: OK（4 本とも評価のエラーなし）」。`docs/verification/20261010-aws-managed.md:21` の 2 本目も同じ判定。
  直し方の案: 3 行を D-20 と同じ書き方にする（評価のエラーは AWS で確認済み、発火は未確認）。`grafana.md:127` と `sns-sqs-lambda.md:133` の「`trap` は AWS では未確認」は発火の話なので、そのままでよい。

- [design.md との整合 / 正しさ] MSK の ACL を厳しくした（`allow.everyone.if.no.acl.found=false`）あとの AWS での振る舞いが、`docs/architecture/resources/msk.md` で「未確認」のまま残っている。D-18 で直した `docs/data-stores.md:545`・`docs/pipeline.md:68`・`docs/troubleshooting.md:197`（045 の B-1 / B-2 で gnmic が `metrics` に、E-1 で syslog-ng が `logs` に書けた）と食い違う。
  分類の理由: 上と同じ。D の一覧の外だが、背景の原因 2 に当たり、直した 3 か所と矛盾する。
  再現（打った）:
  ```
  $ sed -n 27p docs/architecture/resources/msk.md
    2026-10-09 の AWS（当時は既定の true）では ACL を入れる前も書けた（`docs/verification/20261009-aws-managed.md` の「A.」「B.」）。false にしてからは AWS で未確認。
  $ sed -n 85p docs/architecture/resources/msk.md
    - false にしてからの AWS の振る舞い（SCRAM の収集器が ACL のあとに書けること、Telegraf・Spark・Kafbat UI が変わらないこと、`UnderReplicatedPartitions` が 0 のまま）は未確認。
  ```
  根拠: 045 の B-1（`:49`。configuration の rev 1 に `allow.everyone.if.no.acl.found=false`）、B-2（`:50`。`metrics` が 5244 → 5282、「Spark 3 本 RUNNING、Kafbat UI がトピックを読める」）、E-1（`:70`。`logs` が 4 → 5）。
  直し方の案（`:27`）: 確かめた範囲（gnmic → `metrics`、syslog-ng → `logs`）と、まだの範囲（GoFlow2 → `flows` は 0 件）に分けて書く。
  直し方の案（`:85`）: 確かめたもの（gnmic と syslog-ng の書き込み、Spark、Kafbat UI）を外し、Telegraf と `UnderReplicatedPartitions` を未確認として残す。`grep -n 'UnderReplicated\|Telegraf\|telegraf' docs/verification/20261011-aws-managed-045.md` で当たるのは ECR の一覧の `:128` の 1 行だけで、この 2 つは 045 に記録が無い。

- [design.md との整合 / 正しさ] Temporal の資源のページ `docs/architecture/resources/temporal.md` に、045 で AWS で確かめたことが 4 行「未確認」のまま残っている。このサイクルで直した `docs/workflow.md:112`（D-19。I-2 で確認済み）と `docs/deploy.md` の 8-5（D-25。A-1 / I-3）と食い違う。temporal.md は、このサイクルでは D-29（行末の `||`）だけを直した。
  分類の理由: 上と同じ。
  再現（打った。同じ grep に出る `:102` と `:124` は、045 に根拠が無いので対象から外した）:
  ```
  $ grep -n 'AWS の RDS では未確認\|Fargate では未確認\|AWS では未確認' docs/architecture/resources/temporal.md
  57:  - 履歴は RDS の `temporal` にあり、新しいタスクの temporal が続きから動かす。手元の docker で、ワークフローを起こしてからコンテナを再起動しても `Running` のまま残ることを確かめた（2026-10-10。…）。AWS の RDS では未確認。
  63:  - master のパスワード（`NAUTOBOT_DB_PASSWORD`）は init のタスクにしか渡らない。…（手元の docker で確認。Fargate では未確認）。…
  117:| RDS の PostgreSQL 18 | …RDS の 18、TLS（`SQL_HOST_VERIFICATION=false`）、master での `CREATE EXTENSION btree_gin` は AWS では未確認。…
  118:| 2 タスク | デプロイの入れ替わりの間だけ並ぶ。AWS では未確認 |
  ```
  根拠（045、`:57` について）: I-2「入れ替え後に承認待ちが残る」（`:101`）。入れ替えのあと、2 本が同じ run_id のまま RUNNING だった。
  根拠（045、`:63` について）: A-5（`:40`）。読める `environ` 6 本に `NAUTOBOT_DB_PASSWORD` が無かった。ただし `/proc/1/environ` は `Permission denied` で見られていない。
  根拠（045、`:117` について）: `:26` と A-4（`:39`）で RDS は PostgreSQL 18.3。A-6（`:41`）で、`btree_gin` の GIN の索引が通り、TLS で繋がっていた。
  根拠（045、`:118` について）: I-2「新旧 2 タスクが並んだ間の membership」（`:102`）。
  気をつける点: 045 には I-2 が 2 行ある（`:101` と `:102`）。`docs/workflow.md:112` と同じく、項目名を添えて指す。`:63` は `/proc/1/environ` を見られていないので、「確認済み」に丸めず、見えた範囲だけを書く。

## Nit

- [design.md との整合] design.md の V-3 は、`git diff --stat main` で `docs/cycles/`（052 を除く）が 0 行になることを求めている。そのとおり打つと `docs/cycles/QUEUE.md` の 2 行が出る。これは PM の設計の commit `bddbcb7` の分（166 に `→ 052-docs-sync` を付け、167 を足した）。build.md は HEAD=`bddbcb7` と比べて V-3 を通しており、実装が触った範囲は正しい。
  分類の理由: 実装の誤りではない。design.md の V-3 の書き方が、設計の commit で QUEUE を変える分を見込んでいないだけ。
  再現（打った）:
  ```
  $ git diff --stat main -- . ':!docs/cycles/052-docs-sync'
   docs/cycles/QUEUE.md                               |   2 ++
   （ほかは docs/ の 30 ファイル、pptx 2 本、outputs.tf。34 files changed）
  ```

## 良かった点

- D の各行を、行番号ではなく現物で確かめた跡がある。たとえば、`DenyNautobotSecrets`（044）と `DenyNautobotParameters`（042）を分けて書いた。対象のロールと prefix の扱い（prefix を問わない / 自分の prefix だけ）は、コード（`workflow/iam.tf:146-154`、`pipeline/lab/iam.tf:71`、`base/core/web.tf:73`、`base/core/runtime.tf:34`、`workflow/gateway.tf:93`）のとおりだった。
- 費用の根拠を、行番号から「手順 0 の終わりのコメントと `COST_CENTS`」に替えた。新しく足した参照にも行番号が無い。`ops/up.sh` の 520〜580 行で、目印が実在することを確かめた。
- 045 の I-2 が 2 行あることに気づき、`docs/workflow.md:112` で項目名を添えて指している。
- 「並べて立てられる」を、「名前はぶつからない」と「並べて立てるのは未確認で、単独で立てる」に分けた。根拠の行（`oss-variant.md:131` の「全体」の行）も実在する。
- pptx を deck.md と同じ commit（`978045b`）で描き直した。XML の主要な文字列の数も deck.md と一致した。
- `deploy.md` の down.sh の節に `/<prefix>/temporal/db-password` を足した。「nautobot のルートが消えなかったときは Nautobot の分だけ残す」は、`ops/down-common.sh` の `delete_up_ssm_params`（`/<prefix>/nautobot/` だけを残す）と合っている。

## ユーザーへの質問

None

### PM の確認（2026-10-12）

#### Should fix の再現と直したもの

- 指摘 1（Grafana の `trap` の評価）
  - 再現した。cold reviewer と同じ grep で `grafana.md:138` / `:141`、`sns-sqs-lambda.md:139` の 3 行が出た。
  - 根本原因: 設計の点検（D の一覧）が、045 で確かめたことを `docs/` の直下の文書だけで拾い、`docs/architecture/resources/` を探していなかった。実装は D の一覧どおり。
  - 直した: 3 行を D-20 と同じ形（評価がエラーにならないことは AWS で確認済み、`trap` の発火は未確認）にした。根拠は `20261010-aws-managed.md` の 2 本目と 045 の H-2。
  - PM が足したもの: 同じ原因で `grafana.md:128`（OpenSearch Serverless を SigV4 でルールの評価に使えるか。`trap` のルールは OpenSearch Serverless を SigV4 で引く。`pipeline/analytics/grafana.tf` の先頭のコメント）と `:132`（9-2 の確かめが「AWS では未実行」）も古かったので、一緒に直した。`docs/alert-comparison.md:321` と同じ書き方。
- 指摘 2（MSK の ACL）
  - 再現した。`sed -n 27p` と `sed -n 85p` の出力は cold reviewer と同じ。
  - 直した: 確かめたもの（gnmic → `metrics`、syslog-ng → `logs`、Spark の 3 本は RUNNING、Kafbat UI はトピックを読める。045 の B-1 / B-2 / E-1）と、未確認のまま（GoFlow2 の `flows`、Telegraf、`UnderReplicatedPartitions`）に分けた。
- 指摘 3（`temporal.md`）
  - 再現した。cold reviewer と同じ grep で `:57` / `:63` / `:117` / `:118` が出た。
  - 直した: 045 の I-2（2 行。項目名を添えて指す）/ A-4 / A-5 / A-6 で確かめた分だけ書き換えた。A-5 は「読める `environ` 6 本に無い」と「`/proc/1/environ` は `Permission denied` で見ていない」をそのまま書き、確認済みに丸めていない。
  - PM が足したもの: 同じ事実の表の行 `:119`（ECS Exec と healthCheck。「Fargate の ECS Exec では未確認」）も A-5 に合わせた。
- 残した「未確認」（045 に根拠が無い）
  - `grafana.md:127` と `sns-sqs-lambda.md:133`: `trap` の発火の話。
  - `temporal.md:63` の SSM の Deny: 045 の G-1 の simulate は web / runtime / tools / lab のロールだけで、workflow のサーバーのロールは入っていない。
  - `temporal.md:102`: worker が apply の直後に落ちないかは 045 に記録が無い。
  - `temporal.md:124` と `s3-tables-athena.md:92`（`expired` の経路）: 045 の「不具合」の 1 に「05:53Z に `expired` になり」とあるが、観測したのか approval_timeout からの計算なのかが記録から判別できない。確認済みにしない。
  - `pipeline.md:107` と `lab-ec2.md:44`（lab の EC2 のホストから NLB へ直接 `logger`）: 045 の E-1 は TRex の netns から DNAT 越しに送った別の経路。
- 拾い漏れの探索（cold reviewer の「見ていない観点」を PM が補った）: `grep -rn '未確認' docs README.md` を、045 の話題（btree / PostgreSQL 18 / membership / リング / environ / ACL / `trap` の評価 / run_id / 入れ替え / 9-2 / init）で絞って docs 全体に打った。上で直したもの・残したもの以外に、古い行は無かった。`grep -rn '未実行'` は `alert-comparison.md:327`（Athena のクエリ。045 の話題ではない）だけ。

#### 直したあとの確かめ

```
$ grep -n -E 'AWS では未確認|未実行|Fargate では未確認|AWS で未確認' docs/architecture/resources/{grafana,sns-sqs-lambda,msk,temporal}.md
grafana.md:127（trap の発火。残す）
grafana.md:138（書き換えた行の末尾「`trap` の発火は AWS では未確認」）
sns-sqs-lambda.md:133（trap の発火。残す）
temporal.md:57（書き換えた行の末尾「入れ替えのあとに承認して先へ進むかは AWS では未確認」）
temporal.md:63（SSM の Deny の「（AWS では未確認）」。残す）
temporal.md:102 / :124（残す）
```

- 書き換えた表の行（`grafana.md:128` / `:132`、`temporal.md:117`〜`:119`）の `|` の数は見出しと同じ 3（列は崩れていない）。
- `git diff --stat`: `grafana.md` 8、`msk.md` 4、`sns-sqs-lambda.md` 2、`temporal.md` 10（4 files changed, 12 insertions(+), 12 deletions(-)）。docs だけ。
- `bash ops/check.sh` → 終了コード 0、最後の行「すべて通過」（各ブロックの「通過 N / 失敗 0」が 15 個、失敗は 0。出力の Traceback は異常系のテストが出すログで、どのブロックも失敗 0）。

#### Nit（直さない）

- V-3 の `git diff --stat main` に、設計の commit `bddbcb7` の `QUEUE.md` の 2 行が出る件。実装が触った範囲は正しい（build.md は `bddbcb7` と比べて 0）。

#### エンジニアのセルフレビューから PM に回された Should fix

- `docs/workflow.md` の init の所要「2〜5 分」: PM が `96f4e21` で直した（未実測と書き、045 の A-1 の init の本体 1.1 秒を添えた。`deploy.md` の 8-5 と揃う）。

#### 見た観点 / 見ていない観点（PM）

- 見た観点（design 整合性 / correctness）: 上の 3 件を 045 の行（`:26` / `:39`〜`:41` / `:49`〜`:50` / `:70` / `:83` / `:93` / `:101`〜`:102` / `:108`〜`:114`）と `20261010-aws-managed.md:21` に突き合わせた。
- 見ていない観点: security / runtime bugs / data loss / API compatibility / type safety（コードの変更は `outputs.tf` の `#` の 1 行だけ）。mermaid の描画と pptx の見た目は開いていない（cold reviewer と同じ）。

### 判断

- Must fix: 0
- Should fix: 3（cold reviewer。すべて直した。同じ原因の 3 行を PM が足して直した）＋ セルフレビューから回された 1（`96f4e21` で直した）
- Nit: 1（直さない）
- cold reviewer の 2 回目: 依頼しない。Must fix が 0 で、直したのは docs の文言だけ（実装ファイルは不変）。
- 全体設計 HTML: 触らない（構成は変わらない）
- QUEUE 166 を完了にする（2026-10-12）。次に回すものは無し
