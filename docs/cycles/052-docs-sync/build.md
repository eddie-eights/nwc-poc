# docs を 036〜051 のコードに揃える（052）の実装

## Round 1

実装モデル: opus-5.5 / effort: high

### 件ごとの結果

点検の報告（PM 確認「未」）もすべて現物（コード・`docs/verification/`）で確かめ、報告どおりだった。直さなかった件は無い。

| 番号 | 結果 | 確かめた現物 |
|---|---|---|
| D-1 | 直した（`docs/data-stores.md` の `temporal` の行: init のタスクがロール・DB・スキーマ、サーバーは待って namespace） | `workflow/ecs.tf` の init とサーバーのタスク定義とコメント |
| D-2 | 直した（`outputs.tf` の `#` の 1 行） | 同上 |
| D-3 | 直した（3 コンテナ + init のタスク定義） | `workflow/ecs.tf` |
| D-4 | 直した（`docs/nautobot.md` の mermaid・RDS・SG・PostgreSQL の行） | `base/core/security_groups.tf` の `workflow → nautobot_db` |
| D-5 | 直した（`DenyNautobotSecrets` の対象から temporal を外し、workflow の `DenyNautobotParameters` を別に書いた） | `workflow/iam.tf` の `DenyNautobotParameters`、`DenyNautobotSecrets` は `base/core/web.tf`・`runtime.tf`、`workflow/gateway.tf`（tools の Lambda）、`pipeline/lab/iam.tf`、`IaC/cloudformation/lab-debug.yaml` |
| D-6 | 直した（`vpc-perimeter.md` の SG の表に 3 行（6 本）） | `security_groups.tf` の `sg_flows`、core.md の SG の表 |
| D-7 | 直した | `security_groups.tf` |
| D-8 | 直した（14 → 15 を 3 か所） | `base/ecr/main.tf`（1 + 2 + 3 + 9） |
| D-9 | 直した（`temporal` を足した） | `ops/up-common.sh` の `build_temporal_server`（タグは `dir_tag`） |
| D-10 | 直した（`ssm-parameter-store.md` の表と ECS の行、`deploy.md` の down.sh の消すもの） | `ensure_temporal_secrets`、`ops/down-common.sh`、`workflow/ecs.tf` の `secrets` |
| D-11 | 直した（temporal と OSS 版の spark / neo4j） | `ops/up.sh` と `ops/oss/up.sh` の QEMU の検査のコメント |
| D-12 | 直した（`temporal-server` を一覧に、コンテキストの例外 2 つ、Nautobot に足すもの） | `build_temporal_server`、`nautobot_context`、`docker/images/nautobot/Dockerfile` |
| D-13 | 直した（`toolkit.py`） | `docker/images/nautobot/Dockerfile` |
| D-14 | 直した（`docs/workflow.md` に init のタスク、`docs/architecture/workflow.md` の概要と mermaid に init と RDS） | `workflow/ecs.tf` |
| D-15 | 直した（2 か所） | `workflow/variables.tf`、036 |
| D-16 | 直した（名前はぶつからない・並べるのは未確認で単独、`:130` を指す） | 同ファイルの取り下げの記述 |
| D-17 | 直した（036・042・050 は OSS 版では AWS で未確認） | `docs/verification/` に OSS 版の 10-09 以降が無い |
| D-18 | 直した（3 か所。metrics だけ確認済み、ほかは 0 件で未確認） | `docs/verification/20261011-aws-managed-045.md` の B-1 / B-2 |
| D-19 | 直した | 045 の I-2 |
| D-20 | 直した（4 か所。評価のエラーが無いことは確認済み、発火は未確認） | `20261010-aws-managed.md`、045 |
| D-21 | 直した（経路 1 は 045 の E-1 で通った（logs 4 → 5）、経路 2 は未確認） | 045 の E-1 |
| D-22 | 直した（QUEUE に積んだ・未着手） | QUEUE 167 |
| D-23 | 直した（troubleshooting の表に 1 行） | `ops/up-common.sh` の `check_sg_plan` |
| D-24 | 直した（`setup.md` に 1 行、`deploy.md` の `VPC_CIDR` の行） | `docs/verification/20261012-aws-managed-051.md` の V-1 / V-3 |
| D-25 | 直した（所要は未実測、測れたのはスキーマの 1.1 秒） | 045 の A-1 |
| D-26 | 直した（KB の索引の Lambda、`ssmmessages`、044 / 047 のポインタ、`<prefix>-alert-firehose`） | `base/core/perimeter.tf`、`agent/kb.tf`、`base/core/ssm_agent.tf`、`pipeline/analytics/history.tf` |
| D-27 | 直した（「ジョブの止め方」の行） | `ops/stop-spark.sh`、`ops/common.sh` の `emr_cancel_jobs` |
| D-28 | 直した（10 ファイル） | `agentcore-bedrock.md` の書き方 |
| D-29 | 直した | ― |
| D-30 | 直した | `~/Documents/repo/artifacts/projects/nwc-poc-architecture.html` がある |
| D-31 | 直した（数え直し: base/core 41・workflow 37。日付と commit、SG の workflow の 3 行、init・RDS・044・047・MSK の 2 設定、行番号の参照）。pptx を描き直した | 下の V-4 |
| D-32 | 直した（base/core 41、行番号の参照 2 つ）。pptx を描き直した | 下の V-4 |

数え直し（`cat <root>/*.tf | grep -c '^resource "'`）: マネージド版 base/ecr 6、base/logs 6、base/core 41、agent 22、workflow 37、lab 7、stream 57、graph 11、nautobot 16、analytics 54。OSS 版 base/core 41、stream 66、graph 24、analytics 80。

### 変更ファイル

- `IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf`（`#` の 1 行）
- `docs/`: ai-dev-flow.md、alert-comparison.md、data-stores.md、deploy.md、faq-fukuda-nwc-poc.md、nautobot.md、oss-variant.md、pipeline.md、setup.md、troubleshooting.md、workflow.md
- `docs/architecture/`: README.md、core.md、workflow.md、architecture-managed.deck.md、architecture-oss.deck.md、resources/ の README.md・ecr.md・emr-serverless.md・nautobot.md・opensearch-serverless.md・prometheus.md・s3-tables-athena.md・sns-sqs-lambda.md・splunk.md・ssm-parameter-store.md・telegraf.md・temporal.md・vpc-perimeter.md・web-ec2.md
- `docs/architecture-managed.pptx`、`docs/architecture-oss.pptx`

### 設計からの逸脱

- `docs/faq-fukuda-nwc-poc.md` の Temporal の RDS の Q（「起動のたびにロール・DB・スキーマ・namespace を無ければ作る」）を直した。D の表に無いが、V-1 の 1 本目の grep に当たるため（init のタスクがロール・DB・スキーマ、サーバーが namespace）
- D-31 は設計が `:162` だけを挙げたが、deck.md の中のほかの行番号の参照（`ops/up.sh:470`、`pipeline.md:34` / `:35`、`workflow.md:21`、`docs/workflow.md:27`、`down.sh:129-132`）も節の見出し・手順の名前に置き換えた（設計方針 4。行がずれて古くなっていたもの）
- V-1 の grep は `docs/cycles/052-docs-sync/design.md` 自身（検索語を引用している）にも当たる。これは数えない

### 範囲外で見つけた古い記述（直していない）

- `docs/workflow.md` の init のタスクの所要「2〜5 分」（D-25 と同じ未実測の値。D の表には `deploy.md:294` だけ）
- 2026-10-12 の反対弁護人の指摘 1（下）: 同じ `docs/workflow.md` の行は、D-25 で直した `deploy.md` の 8-5 と食い違ったまま残る。045 の A-1 / I-3 の実測（1.1 秒 / 0.5 秒）に揃えるのが次の候補

### 検証（最後の編集のあとに取り直した）

V-1（`docs/cycles/052-docs-sync/` の design.md と build.md は検索語を引用しているので除く）

```
$ grep -rn '起動のたびにロール・DB・スキーマ' docs README.md   → design.md:15 / :92、build.md だけ
$ grep -rn '524〜584' docs                                      → design.md:26 / :69 / :93 だけ
$ grep -n '並べて立てられる' docs/oss-variant.md                → rc=1
$ grep -n '14 個\|14 リポジトリ' docs/deploy.md docs/architecture/resources/ecr.md docs/architecture/resources/README.md
docs/architecture/resources/ecr.md:100:| ライフサイクルポリシー（古…（「ほかの 14 個」。残ってよい 1 行）
$ grep -n 'サーバーのコンテナが起動時' IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf → rc=1
```

V-2

```
docs/architecture/resources/vpc-perimeter.md:54:| workflow | nautobot_db | 5432/tcp | …
docs/architecture/resources/vpc-perimeter.md:55:| workflow | workflow | 6933〜6939 / 7233〜7239（tcp） | …
docs/architecture/resources/vpc-perimeter.md:61:| splunk | splunk | 8089 / 9887 / 9997（tcp） | …
core.md の同じ 3 行と diff → diff_rc=0
```

V-3（commit 前の作業ツリー対 HEAD=bddbcb7。commit 後の main 対比も同じ中身に build.md が加わる）

```
$ git diff --stat HEAD | tail -1
 33 files changed, 95 insertions(+), 72 deletions(-)
$ git diff --stat HEAD -- docs/cycles docs/verification README.md   → 空
$ git diff --name-only HEAD | grep -v '^docs/'
IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf
$ outputs.tf の +/- 行の頭 3 文字 → "-# " と "+# " の 2 行
$ git status --porcelain -uall | grep -v '^ M' → ?? docs/cycles/052-docs-sync/build.md
```

V-4

```
render_pptx.py architecture-managed → RC_MANAGED=0、architecture-oss → RC_OSS=0
python-pptx で文字列を数えた（スライドの本文・表・ノート）:
  managed 10 slides: base/core（41） 1、workflow（37） 1、workflow-init 2、ssm-agent 1、allow.everyone 1、7233〜7239 2、bddbcb7 1、手順 0「道具と認証」 1、:470 0、:556 0、2026-10-12 2
  oss 10 slides: bddbcb7 1、base/core|oss.tf の SG・EFS|41 1、手順 0「道具と認証」 1、:161 0、:88-91 0、2026-10-12 2
deck.md と pptx は同じ commit に入れる
```

V-5

```
$ bash ops/check.sh → rc=0
最後の行: すべて通過（ログの「失敗 0」15 行、「失敗 [1-9]」0 行）
```

V-6: 上の「件ごとの結果」で D-1〜D-32 はすべて「直した」。

### セルフレビュー

- 自分: opus-5.5 / effort high（サブエージェントの中なので切り替えられない）
- 反対弁護人: opus / effort xhigh（文脈を渡し、読み取り専用。終わったあと `git status --porcelain -uall` は ` M` 33 本と build.md だけで、増えたファイルなし）

自分で見つけて直したもの

| 分類 | 観点 | 場所 | 中身 | 片付け |
|---|---|---|---|---|
| Should fix | 正確さ | `docs/architecture/core.md` の IAM の Deny の節 | `DenyNautobotSecrets` を「Nautobot 以外のロールに読ませない」と書いていた。実際の付け先は `base/core/web.tf`・`runtime.tf`、`workflow/gateway.tf`（tools の Lambda）、`pipeline/lab/iam.tf`、`lab-debug.yaml` だけ（`grep -rn DenyNautobotSecrets IaC`） | 付け先を列挙した |

反対弁護人の指摘（全件を現物で再現した）

| # | 分類 | 観点 | 場所 | 指摘 | 再現 | 片付け |
|---|---|---|---|---|---|---|
| 1 | Should fix | 一貫性 | `docs/workflow.md`（init の所要の行） | 「2〜5 分」が残り、D-25 の `deploy.md` と食い違う | `grep -rn '2〜5 分' docs` で 1 行 | 設計の D の表に無いので直さない（設計の「未確定事項とリスク」の最後）。上の「範囲外」に書き、PM に報告 |
| 2 | Nit | 確かめた範囲 | `docs/workflow.md` の「タスクが入れ替わる」の行（D-19） | 「続く」に「確認済み」が付くと、入れ替え後に承認して進むことまで確かめたと読める | 045 の I-2（1 本目）は入れ替え後も 2 本が同じ run_id で RUNNING・Athena の行が増えないまで | 直した。AWS で見た範囲を書き、入れ替え後に承認して進むかは未確認と書いた。手元の docker で確かめたことも残した |
| 3 | Nit | 正確さ | `troubleshooting.md`・`pipeline.md`・`data-stores.md` の ACL の行（D-18） | `gnmi`・`flows`・`traps` を一括で「未確認」としていた。`logs`（syslog-ng）は ACL のあとに書けている。`traps` は Telegraf が IAM で書き ACL の対象外 | 045 の B-2（logs 4、gnmi 0 は on-change の既知）と E-1（logs 4 → 5） | 直した（3 か所同じ文）。syslog-ng の `logs` を足し、`flows` は未確認、`gnmi` の 0 件は既知の挙動と分けた。`traps` は ACL に関係しないので外した |
| 4 | Nit | 確かめた範囲 | `docs/deploy.md` の 8-5（D-25） | 「1.1 秒だけ」は狭い。2 回目の init も 0.5 秒と測れている | 045 の I-3 | 直した（A-1 / I-3） |
| 5 | Nit | 一貫性 | `core.md` の IAM の節、`architecture-managed.deck.md` のノート | `DenyNautobotSecrets` の付け先に lab-debug の EC2 が抜けている | `IaC/cloudformation/lab-debug.yaml` に `Sid: DenyNautobotSecrets` | 直した（2 か所）。pptx を描き直した |
| 6 | Nit | 体裁 | `architecture-managed.deck.md` のノートの参照 3 つ | 見出しでない一文を「」で指している | 指す先は箇条書きの一文（検索で見つかる） | 直さない（設計方針 4 の「動かない目印」は満たす） |

反対弁護人が当たらないとしたもの（根拠を受け取り、要所を自分でも確かめた）: D-20 の評価のエラー、D-21 の経路 1、D-26 の perimeter（`ssmmessages`、`<prefix>-alert-firehose` の除外、`agent/kb.tf` の付け先、`ssm_agent.tf` に GetParameter が無い）、D-5 / D-10 の temporal の db-password（サーバーと init が受け、down.sh は ManagedBy のタグで消す）、D-11 の QEMU の一覧（`ops/up.sh` と `ops/oss/up.sh` のコメント）、D-12 のビルドのコンテキスト、数え直しと MSK の 2 設定、変更した .md の表の列数（崩れ 0）、新しいコードの行番号 0。

「問題なし」の観点と根拠: 表の崩れ（反対弁護人のスクリプトで列数を検査）、行番号（追加行に `:数字` の参照なし）、設計範囲（V-3）、テスト（V-5 の `ops/check.sh`）。

Must fix: 0。未解消の Should fix: 1（指摘 1。範囲外のため PM に報告）。
