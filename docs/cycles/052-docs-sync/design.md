# docs を 036〜051 のコードに揃える（052）

設計: PM(opus-5.5) / effort: high。QUEUE 166。2026-10-12。

## 背景

- 2026-10-12 にユーザーが「readme や docs は最新になってる？」と聞いた。PM が README.md と `docs/`（`docs/cycles/` と `docs/verification/` を除く）を 3 つに分け、読み取り専用のサブエージェントでいまのコード（main `eb74225`）と突き合わせた。古い記述はおよそ 35 か所あった。どれも、そのまま誤った操作に繋がるものではない
- 原因は主に 2 つ
  - Temporal の履歴を Nautobot の RDS に置いた変更（036）と、スキーマを作る init のタスク `<prefix>-workflow-init`（042）が、一部の docs とマネージド版のスライドに届いていない
  - 045 で AWS で確かめたことが「AWS では未確認」のまま残っている
- ユーザーは同日、docs だけを直すサイクルを立てることと、「1 本の回線断で修復案が 2 件できる」不具合を QUEUE に足すことを了承した（「進めて」）。その不具合は QUEUE 167 に積んだ。このサイクルでは直さない

## 現物（PM が 2026-10-12 に読んで確かめたもの）

- `docs/data-stores.md:266` の `temporal` の行は「起動のたびにロール・DB・スキーマ・namespace を無ければ作る（cycle 036）」。実際は `IaC/terraform/aws-managed/workflow/ecs.tf:23-29` の init のタスク定義（family `${local.name_prefix}-workflow-init`）がロール・DB・スキーマを作る。サーバーのタスク定義のコメント（同 `:98`）には「ロール・DB・スキーマを入れるのは上の init のタスク」とあり、サーバーはスキーマを待つだけ
- `IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf:26-27` のコメントは「temporal のサーバーのコンテナが起動時に master（nautobot）でロール temporal と DB を作る」。上と同じく古い
- `IaC/terraform/aws-managed/base/core/security_groups.tf:83-85` には `workflow → nautobot_db 5432`、`workflow → workflow 6933-6939` と `7233-7239` の行がある。`:103-105` には `splunk → splunk 8089 / 9887 / 9997` の行がある。`docs/architecture/resources/vpc-perimeter.md:49-64` の SG の表には、この 6 本が無い。`docs/architecture/core.md` の SG の表には入っている
- `docs/architecture/resources/nautobot.md:43` は「SG `<prefix>-nautobot-db` はタスクからだけ受ける」。`docs/nautobot.md` の mermaid（`:44`）と RDS の行（`:55` 付近）は RDS を「台帳」とだけ書き、SG の行は「DB へはタスクからだけ」
- `IaC/terraform/aws-managed/base/ecr/main.tf:14-19` の ECR リポジトリは agent 1 + lab 2 + workflow 3（`worker` / `temporal` / `temporal-ui`）+ pipeline 9 = 15 個。`docs/deploy.md:429`、`docs/architecture/resources/ecr.md:14`、`docs/architecture/resources/README.md:34` は 14 個のまま（`docs/deploy.md:483` は 15、`ecr.md:100` の「ほかの 14 個」は agent を除いた数なので正しい）
- `docs/data-stores.md:342` は「temporal と worker の 2 コンテナ」。サーバーのタスク定義はサーバー・UI・worker の 3 コンテナで、ほかに init のタスク定義がある
- `docs/oss-variant.md:78` は「マネージド版と並べて立てられる」。同じファイルの `:128` と `:130` は、並べて立てる計画を 2026-10-10 にユーザーが取り下げたと書いている（Fargate の vCPU の上限 30 に OSS 版だけで約 21.5）。接頭辞（`<owner>-nwc-oss`）が違うので名前はぶつからない
- `docs/oss-variant.md:9` は、OSS 版の AWS の確認として 2026-10-07 と 10-08 だけを挙げる。036・042・050 のあと OSS 版は AWS で立てていない（`docs/verification/` に OSS 版の 10-09 以降の記録は無い）
- `docs/troubleshooting.md:196` は ACL を false にしてから gnmic が書けるかを「AWS で未確認」とする。`docs/troubleshooting.md:504` は回線断の不具合を「別のサイクル」で直すとするが、QUEUE に行が無かった（QUEUE 167 に積んだ）
- `docs/setup.md:57` の QEMU が要るイメージの一覧に temporal が無い
- `ops/up-common.sh:32-36` の `check_sg_plan` は `TF_CLI_ARGS*` が設定されていると die する。die の文面に外し方まで書いてあるが、docs には無い
- `docs/architecture/resources/` の 10 ファイルが費用の根拠を「`ops/up.sh` の 524〜584 行」と行番号で指す（いまは 1 行ずれている）。`agentcore-bedrock.md:26` と `emr-serverless.md:24` は「`ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`）」と行番号を使わない
- `docs/architecture/README.md:10` がスライドの描き方: `uv run --only-group docs python docs/architecture/render_pptx.py docs/architecture/architecture-managed.deck.md -o docs/architecture-managed.pptx`（OSS 版は `architecture-oss`）。`architecture-managed.deck.md:70` の数え方は「ルートの *.tf をつないで grep -c '^resource "'」
- 全体設計 HTML（`~/Documents/repo/artifacts/projects/nwc-poc-architecture.html`）は 036 の形（Temporal の履歴を RDS）で、init のタスクは描かない（042 の review.md:295 で決めた）。このサイクルでは触らない

## 設計方針

1. **直すのは docs だけ。** README.md と `docs/` の現行の説明、マネージド版と OSS 版のスライド（deck.md と pptx）、それに `pipeline/nautobot/outputs.tf` のコメント 2 行（同じ誤り。`#` の行だけ変える）。`docs/cycles/` と `docs/verification/` は当時の記録なので直さない
2. **1 件ずつ現物で確かめてから直す。** 下の表の「PM 確認」が「済」でないものは点検の報告なので、コード（と `docs/verification/`）を開いて食い違いを確かめてから直す。点検の報告が誤っていたら直さず、`build.md` にその件と根拠を残す
3. **書くのはいまの事実と根拠。** 「AWS で確認済み」と書き換えるときは、どの回のどの項目かを `docs/verification/<ファイル>` の名前と項目の番号で添える。確かめた範囲より広く言わない（例: ACL は metrics だけ確かめた）
4. **コードの行番号を新しく書かない。** 費用の根拠など、触った箇所の行番号の参照は、関数名・手順の番号・節の見出しなど動かない目印に置き換える。触らない箇所の行番号は直さない（範囲を広げない）
5. **スライドは deck.md を直してから pptx を描き直す。** deck.md と pptx を同じ commit に入れる

## 変更対象ファイル

| 番号 | ファイル:行（目安） | 古い点 → 直し方 | 根拠 | PM 確認 |
|---|---|---|---|---|
| D-1 | `docs/data-stores.md:266` | スキーマはサーバーが起動のたびに作る → init のタスク `<prefix>-workflow-init` がロール・DB・スキーマを作り、サーバーは待ってから namespace を作って起きる（cycle 042） | `workflow/ecs.tf:23-29`、`:98` | 済 |
| D-2 | `IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf:26-27`（コメント） | サーバーのコンテナが作る → init のタスクが作る | 同上 | 済 |
| D-3 | `docs/data-stores.md:342` | 2 コンテナ → サーバー・UI・worker の 3 コンテナと init のタスク定義 | `workflow/ecs.tf` | 済 |
| D-4 | `docs/nautobot.md`（mermaid `:44`、RDS の行、SG の行、部品の表の PostgreSQL の行 `:73` 付近） | RDS は台帳だけ・DB へはタスクからだけ → Temporal の履歴（DB `temporal` / `temporal_visibility`）も同じ RDS、workflow のタスク（サーバーと init）からも 5432 で入る | `security_groups.tf:83` | 済 |
| D-5 | `docs/nautobot.md` のシークレットの行（`:59` 付近） | 044 の Deny の対象に「temporal のタスク」を入れている → `DenyNautobotSecrets` と、workflow のロールの `DenyNautobotParameters` を分けて書く | `workflow/iam.tf:150` ほか（点検の報告） | 未 |
| D-6 | `docs/architecture/resources/vpc-perimeter.md:49-64` | SG の表に 6 本が無い → core.md の SG の表と同じ行を足す | `security_groups.tf:83-85`、`:103-105` | 済 |
| D-7 | `docs/architecture/resources/nautobot.md:43` | タスクからだけ → Nautobot のタスクと workflow のタスクから | `security_groups.tf:83` | 済 |
| D-8 | `docs/deploy.md:429`、`docs/architecture/resources/ecr.md:14`、`docs/architecture/resources/README.md:34` | ECR 14 個 → 15 個（`temporal` が 036 で増えた） | `base/ecr/main.tf:14-19` | 済 |
| D-9 | `docs/architecture/resources/ecr.md:62` | 中身でタグが変わるイメージの一覧に `temporal` が無い | `ops/up-common.sh` の `build_temporal_server`（点検の報告） | 未 |
| D-10 | `docs/architecture/resources/ssm-parameter-store.md:38-48`、`:54`、`docs/deploy.md:398-400` | SecureString の表、ECS の secrets で受けるタスク、down.sh が消すパラメータに `/<prefix>/temporal/db-password` と workflow（サーバー・init・worker）が無い | `ops/up-common.sh` の `ensure_temporal_secrets`、`ops/down-common.sh`、`workflow/ecs.tf` の `secrets`（点検の報告） | 未 |
| D-11 | `docs/setup.md:57` | QEMU が要るイメージに temporal が無い。OSS 版の spark・neo4j も無い | `docker/images/temporal-server/Dockerfile`、`ops/up.sh` と `ops/oss/up.sh` の QEMU の検査の一覧（点検の報告） | 一部（`:57` の文面は済） |
| D-12 | `docs/architecture/README.md:62`、`:80-83` | `docker/images` の一覧に `temporal-server` が無い。ビルドのコンテキストが `app/<名前>/` でない例外（temporal-server、nautobot）と、Nautobot のイメージに足すもの（Job・graph.py・toolkit.py）が無い | `ops/up-common.sh` のビルドの関数、`docker/images/nautobot/Dockerfile`（点検の報告） | 未 |
| D-13 | `docs/architecture/resources/nautobot.md:15` | イメージに足すものに `app/agentcore/toolkit.py` が無い | `docker/images/nautobot/Dockerfile`（点検の報告） | 未 |
| D-14 | `docs/workflow.md:31`、`docs/architecture/workflow.md`（概要 `:6` と mermaid） | init のタスクと RDS が無い → 足す | `workflow/ecs.tf:23-72` | 済（コード側） |
| D-15 | `docs/oss-variant.md:174`、`:283` | 「Temporal worker」→ Temporal（サーバー・UI・worker。1 タスク） | 036 | 未 |
| D-16 | `docs/oss-variant.md:78` | 並べて立てられる → 接頭辞が違うので名前はぶつからないが、並べて立てるのは vCPU の上限に余裕が無く未確認。単独で立てる（`:130` を指す） | 同ファイル `:128`、`:130` | 済 |
| D-17 | `docs/oss-variant.md:9` | 036・042・050 のあと OSS 版を AWS で立てていないことを書く（Temporal の RDS・init のタスク・`ops/oss/up.sh` の `check_sg_plan` は OSS 版では AWS で未確認） | `docs/verification/` の一覧 | 済 |
| D-18 | `docs/troubleshooting.md:196`、`docs/pipeline.md:68`、`docs/data-stores.md:545` | ACL を false にしたあとの書き込みは AWS で未確認 → 045 で metrics は確認済み（gnmi・flows・traps は 0 件で未確認） | `docs/verification/20261011-aws-managed-045.md` の B-1 / B-2（点検の報告） | 未 |
| D-19 | `docs/workflow.md:111` | タスクを入れ替えたときにワークフローが続くかは未確認 → 045 の I-2 で確認済み | 同 045 の I-2（点検の報告） | 未 |
| D-20 | `docs/alert-comparison.md:17`、`:218`、`:321`、`docs/pipeline.md:594` | trap のルールの評価は未確認 → 評価のエラーが無いことは確認済み、発火は未確認 | `docs/verification/20261010-aws-managed.md:21`、045 の `:23` / `:93`、`ops/grafana_rules_check.py`（点検の報告） | 未 |
| D-21 | `docs/troubleshooting.md:417-423` | QUEUE の完了した項目で確かめる → 045 の E-1 で経路 1 は通った、経路 2（`:457`）は未確認 | QUEUE 147、045 の E-1（点検の報告） | 未 |
| D-22 | `docs/troubleshooting.md:504` | 「別のサイクル」→ QUEUE に積んだ（未着手）と書く | QUEUE 167 | 済 |
| D-23 | `docs/troubleshooting.md` の `check_sg_plan` の節（`:26-28` 付近） | `TF_CLI_ARGS*` が設定されていると手順 0 で止まることを 1 行足す | `ops/up-common.sh:32-36` | 済 |
| D-24 | `docs/setup.md:13`、`docs/deploy.md:40` | `VPC_CIDR` を立てた環境で変えるなら先に `ops/down.sh`（手順 0 の守りで止まる）を 1 行と `troubleshooting.md` へのリンク | 051 の V-1 / V-3 | 済 |
| D-25 | `docs/deploy.md:294` | init のタスクの「初回は 2〜5 分」は実測ではない → 未実測と書く（045 の A-1 で測ったのはスキーマの作成 1.1 秒だけ） | 045 の A-1（点検の報告） | 未 |
| D-26 | `docs/architecture/core.md:37-49` | IAM の Deny を付けるロールに Bedrock の Knowledge Base の索引の Lambda が無い、拒む API に `ssmmessages` が無い、拒まないものに Firehose のロール `<prefix>-alert-firehose` が無い。044（Nautobot の内部シークレットの Deny）と 047（`<prefix>-ssm-agent`）への 1 行ずつのポインタも無い | `base/core/perimeter.tf`、`agent/kb.tf`（点検の報告） | 未 |
| D-27 | `docs/architecture/resources/emr-serverless.md` | ジョブの止め方（`ops/stop-spark.sh`）が無い → 1 行と `docs/deploy.md` の節へのリンク | `ops/stop-spark.sh` | 未 |
| D-28 | `docs/architecture/resources/` の 10 ファイル（`grep -rn '524〜584' docs`） | 行番号の参照 → 「`ops/up.sh` の費用の目安（手順 0 の終わりのコメントと `COST_CENTS`）」 | `agentcore-bedrock.md:26` の書き方 | 済 |
| D-29 | `docs/architecture/resources/temporal.md:18` | 行末の余分な `\|\|` を 1 本消す | ― | 未 |
| D-30 | `docs/ai-dev-flow.md:136` | 全体設計 HTML が「まだ無く…作る」→ 消す（正本 `~/.claude/skills/cycle-design/flow.md` の書き方に揃える） | `~/Documents/repo/artifacts/projects/nwc-poc-architecture.html` がある | 済 |
| D-31 | `docs/architecture/architecture-managed.deck.md` と `docs/architecture-managed.pptx` | ルートごとのリソースの数（`:50`、`:52`、`:70-71`。数え方は `:70` のとおり数え直し、日付と commit を書き換える）、SG の行（`:144-145`。workflow の 3 行と「タスクの外へは開けていない」）、Temporal の RDS・init のタスク・044 の Deny・047 の ssm-agent・MSK の `allow.everyone.if.no.acl.found=false` / `auto.create.topics.enable=false`、行番号の参照（`:162`）→ 直して pptx を描き直す | 各コード（点検の報告） | 未 |
| D-32 | `docs/architecture/architecture-oss.deck.md` と `docs/architecture-oss.pptx` | base/core の数（`:90`、`:93`）、`ops/oss/up.sh` の行番号の参照 → 直して pptx を描き直す | 同上 | 未 |

## 再利用するもの

- `docs/architecture/core.md` の SG の表の行（D-6 はここから写す）
- `agentcore-bedrock.md:26` の費用の根拠の書き方（D-28）
- `docs/architecture/render_pptx.py` と `docs/architecture/README.md:10` の描き方（D-31 / D-32）
- `docs/verification/20261010-aws-managed.md`、`20261011-aws-managed-045.md`、`20261012-aws-managed-051.md`（「確認済み」の根拠）

## 実装ステップ

1. D-1〜D-30 を、表の順に 1 件ずつ現物で確かめてから直す。点検の報告が誤っていた件は直さず `build.md` に残す
2. D-31 / D-32 の deck.md を直し、`docs/architecture/README.md:10` の描き方で pptx を 2 本描き直す
3. `grep` で古い文言が消えたことを確かめ（検証方法の V-1）、`bash ops/check.sh` を通す
4. 1 本の commit にまとめる（スライドは deck.md と pptx を同じ commit に）。`build.md` に件ごとの結果（直した / 直さなかった理由）を表で書く

## 検証方法

- V-1: 古い文言が残っていない。次がどれも 0 行
  - `grep -rn '起動のたびにロール・DB・スキーマ' docs README.md`
  - `grep -rn '524〜584' docs`
  - `grep -n '並べて立てられる' docs/oss-variant.md`
  - `grep -n '14 個\|14 リポジトリ' docs/deploy.md docs/architecture/resources/ecr.md docs/architecture/resources/README.md`（`ecr.md:100` の「ほかの 14 個」は正しいので、残っていてよいのはその 1 行だけ）
  - `grep -n 'サーバーのコンテナが起動時' IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf`
- V-2: D-6 の後、`vpc-perimeter.md` の SG の表に `workflow` → `nautobot_db` の 5432、`workflow` → `workflow` の 2 行、`splunk` → `splunk` の 3 ポートがある（core.md の表と同じ）
- V-3: `git diff --stat main` の変更が README.md・`docs/`（`docs/cycles/052-docs-sync/` を除き、`docs/cycles/` と `docs/verification/` は 0 行）・`docs/*.pptx` 2 本・`IaC/terraform/aws-managed/pipeline/nautobot/outputs.tf` だけ。outputs.tf の `+` / `-` 行はすべて `#` で始まる
- V-4: pptx の描き直しが rc=0 で、`docs/architecture-managed.pptx` と `docs/architecture-oss.pptx` が deck.md と同じ commit に入っている
- V-5: `bash ops/check.sh` の最後の行が「すべて通過」
- V-6: `build.md` の表で D-1〜D-32 がすべて「直した」か「直さない（理由と根拠）」のどちらか

## 未確定事項とリスク

- 点検の報告（PM 確認が「未」）は、読み取り専用のサブエージェントが出したもので、PM は全件は確かめていない。誤りがあれば直さずに残す（設計方針 2）
- D-31 のリソースの数は数え方（`grep -c '^resource "'`）しだいで、点検の報告（base/core 41、workflow 37）と違う数になりうる。数え直した値を書く
- pptx の描き直しは `uv` が docs のグループを入れる。手元に入らないときは描き直さずに止めて PM に報告する（deck.md だけ先に直さない ── 片方だけ新しくなる）
- `docs/` のテスト（`tests/` の中で docs の文字列を見るもの）が直した文言に当たって落ちることがある。落ちたらテストの期待と docs のどちらが正しいかを確かめ、docs が正しければテストは変えずに PM に報告する
- ここに無い古い記述が見つかったら、直さずに `build.md` に書き足す（範囲を広げない）。PM が次に回すか決める
