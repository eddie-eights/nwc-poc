# 残った修正をまとめて AWS で動作確認して直す（045）

設計: PM(fable-5.1) / effort: high。QUEUE 147（本体）と QUEUE 87（Kafbat UI の再起動の時間。同じ回で測る）。

## 背景

- 2026-10-10 のユーザー決定: 修正が全部終わったら一度に AWS で動作確認と修正を済ませる。036〜044 の 9 サイクルは「AWS では確かめない」で来ており、見るものは各 `design.md` が QUEUE 147 の行に積んである（本文は `docs/cycles/QUEUE.md` の 147 行目が正本。ここにはその行を検査項目に展開したものだけを書く）。
- 前回の AWS の回は `docs/verification/20261010-aws-managed.md`（2026-10-10、024〜026 の項目）。記録の形はそれに揃える。
- いまの AWS（2026-10-11 に boto3 で見た）: ECS / RDS / EC2 / MSK / CloudFormation は 0 件。残っているのは時間課金の無い VPC だけ（`efukuda-nwc-poc-vpc` 1 つと `efukuda-nwc-oss-vpc` 2 つ）。17 の RDS も 041 より前の SG も無いので、`ops/down.sh` / `ops/oss/down.sh` を先に打つ必要は無い。OSS 版も立てない（Fargate の vCPU の枠）。
- 打つのは PM（`ops/up.sh` / `ops/down.sh` はメインのチェックアウト `/Users/eight/Documents/Dev/sandbox/nwc-poc` から。`deploy.env` の中は見ない）。AWS の読み取りは AWS MCP の `run_script`（boto3）。EC2 の中は SSM Run Command（`AWS-RunShellScript`）、ECS のタスクの中は ECS Exec。シークレットの値は一切出さない（`--query Parameter.Name` までで止める）。

## 設計方針

1. **立てるのは 1 環境、全部入り。** `OWNER=efukuda AGENT=1 PIPELINE=1 WORKFLOW=1 CREATE_KB=1 SYSLOG_STANDARD=RFC5424 IMAGE_TAG=v045 NO_DASHBOARD_PORTFORWARD=1 ops/up.sh`（約 $3.4/h）。`CREATE_KB=1` は Knowledge Base のロールの `s3:prefix` の条件を見るため、`RFC5424` は lab の syslog の試験行を崩さず入れるため、`IMAGE_TAG=v045` は 039 / 040 / 042 で変わった temporal のイメージを必ずビルドさせるため（ECR の `v1_manual` は古い）。ほかは `deploy.env` のまま（`KEEP_ECR=1` / `STORES` 既定 / 1 AZ）。
2. **`ops/up.sh` は 2 回打つ。** 1 回目で立て、検査 A〜H を済ませたあと、2 回目を `IMAGE_TAG=v045b` で打つ。2 回目は 043 のガード（`check_sg_descriptions` が止まらず手順 1 へ進む）と、039 の「イメージが変わってタスクが入れ替わっても承認待ちが残る」と、042 の「前の init が止まっているのを待ってから run-task」を同時に見る。2 回目の前に承認待ちの修復案を 1 つ作っておく（`sudo lab fail-main` → Grafana / Splunk のアラート → SQS → エージェントの修復案）。
3. **直すものが出たら、そのサイクルの中で直す。** 修正はエンジニア（`Agent`、opus / high、worktree）に出し、PM がローカル main へマージしてメインのチェックアウトを pull し、必要なら `ops/up.sh` を打ち直す。環境を立てたまま直しきれない（半日を超える）なら、いったん `ops/down.sh` で消し、QUEUE に `- [ ]` で残して次の回に回す。
4. **検査の記録は `docs/verification/20261011-aws-managed-045.md`**（形は 2026-10-10 の記録と同じ: まとめ / 立てたもの / 項目と結果の表 / 不具合 / 片付け）。「通った」は出力を見たものだけ。`build.md` にはこの記録へのポインタと、直した修正の一覧を書く。
5. **終わったら `ops/down.sh` で消し、消えたことをサービスごとの API で確かめてから報告する。** 残ってよいのは ECR（`KEEP_ECR=1`）と削除を予約した KMS の鍵と、時間課金の無い VPC / SG / SSM Parameter（報告に載せない）。
6. ユーザーの判断を待たずに進める。止まるのは、Must fix に当たる不具合が 3 ラウンド直らないときと、設計判断がユーザーにしか決められないときだけ。

## 変更対象ファイル

| ファイル | 内容 |
| :--- | :--- |
| `docs/verification/20261011-aws-managed-045.md` | 新規。検査の記録（設計方針 4） |
| `docs/cycles/045-aws-batch-verify/build.md` / `review.md` | サイクルの記録 |
| `docs/cycles/QUEUE.md` | 147 と 87 に着手印。不具合で次に回すものがあれば `- [ ]` |
| 不具合が出たファイル | そのとき決める（エンジニアに出す） |

## 再利用するもの

- `ops/up.sh` / `ops/down.sh` / `ops/stop-spark.sh` / `ops/check.sh`（手元のテスト）。
- `app/containerlab/lab.sh` の `fail-main` / `heal-main`（dc1-a-leaf-01 の ethernet-1/1 を落とす・戻す）。
- `docs/troubleshooting.md` の「正しい送り方」（TRex の netns から `logger -n 203.0.113.1 -P 5140 --rfc5424`）。
- Run Command のやり方は `ops/up-common.sh:108`（`AWS-RunShellScript`、スクリプトを base64 で送る）。
- 前回の記録 `docs/verification/20261010-aws-managed.md`（片付けの確かめ方の表）。

## 実装ステップ

1. `ops/up.sh`（1 回目）を上の引数で打ち、`UP_RC=0` まで待つ（MSK 約 30 分、全体 50〜70 分）。途中で止まったら理由を記録して直す。
2. 検査 A〜H（下の検証方法）を打ち、記録に書く。
3. `sudo lab fail-main` で承認待ちの修復案を 1 つ作る。
4. `ops/up.sh`（2 回目、`IMAGE_TAG=v045b`）を打ち、検査 I を見る。
5. 検査 J（lab-debug のスタックは立てない。QUEUE 147 に無いので範囲外）は無し。`ops/down.sh` で消し、検査 K で消えたことを確かめる。
6. `build.md` / 記録 / QUEUE を書き、commit、push、PR、マージ。

## 検証方法

期待する出力まで書く。`<prefix>` = `efukuda-nwc-poc`。

### A. Temporal の履歴を RDS に残す（036）と init のタスク（042）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| A-1 | `ops/up.sh` 8-5 のログ | `<prefix>-workflow-init` の `run-task` → `tasks-stopped` → exitCode 0 で通り、所要時間（イメージの取得 + スキーマ）が出る。サーバーの `startPeriod` 300 秒に収まるか（収まらなければ 042 のリスク 3 を次に回す） |
| A-2 | `ecs describe-tasks`（init の止まったタスク） | `lastStatus=STOPPED`、`containers[].exitCode=0` |
| A-3 | `ecs describe-task-definition`（`<prefix>-workflow` と `<prefix>-workflow-init`） | サーバーの `secrets` の name が `POSTGRES_PWD` だけ。init は `POSTGRES_PWD` と `NAUTOBOT_DB_PASSWORD`。temporal のコンテナに `stopTimeout: 120` |
| A-4 | サーバーのログ（CloudWatch Logs） | 「初期化のタスク … を待つ（n/30）」のあと「スキーマは temporal=… / temporal_visibility=…」。RDS の PostgreSQL 18 でスキーマが入っている（`update-schema` のエラーが無い） |
| A-5 | ECS Exec でサーバーのタスクに入る | `ps -o pid,ppid,stat,comm` で PID 1 が `tini`、`temporal-server` の ppid が 1、`Z` 無し。`for p in /proc/[0-9]*; do echo "$(cat $p/comm) $(tr '\0' '\n' < $p/environ \| grep -c NAUTOBOT_DB_PASSWORD)"; done` が全部 `0`（`1` が出たら退行）。`grep -lz NAUTOBOT_DB_PASSWORD /proc/[0-9]*/environ` が空。`aws ssm get-parameter --name /<prefix>/nautobot/db-password --with-decryption --query Parameter.Name` が `AccessDeniedException`、`/<prefix>/nautobot/url` は名前が出る。`tr` で `SQL_TLS_ENABLED` の値が小文字 `true`（`env \| grep SQL_TLS`）。`schema_want` / `version_ge` の `ls \| sed \| sort -t. -k1,1n -k2,2n` が busybox で alpine と同じ順（サーバーのログの版の行で見る） |
| A-6 | RDS で `pg_stat_activity`（init のタスクの psql か、ECS Exec の `temporal` のロールから） | 接続数の合計が `max_connections`（約 110）に収まる。`btree_gin` が `pg_extension` にある。TLS は `SQL_TLS_ENABLED=true` + ホスト名検証なしで繋がっている（A-4 が通れば可） |
| A-7 | Fargate の broadcast address | サーバーのログに `0.0.0.0` の広告が無い（`getent hosts $(hostname)` がタスクの IP を返す。ECS Exec で打つ） |
| A-8 | 起動中の SIGTERM（040） | 2 回目の `up.sh` の 8-5 で init のタスクが走っている間に `ecs stop-task` → `exitCode 143` と「SIGTERM を受けたので初期化を止める」のログ。`up.sh` の打ち直しでやり直せる（3 回目は打たず、init だけ `run-task` で起こし直して exit 0 を見る） |

### B. MSK の ACL（PR #59）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| B-1 | Kafbat UI（SSM ポートフォワード 8082）か `kafka-topics` | `allow.everyone.if.no.acl.found=false` / `auto.create.topics.enable=false` がクラスターの設定にある |
| B-2 | 収集器（SCRAM）が ACL 後に書けている | Kafbat UI で `metrics` / `gnmi` / `logs` / `flows` / `traps` のメッセージ数が増える。IAM の主体（Telegraf・Spark・Kafbat UI）が影響を受けない（Spark のジョブが RUNNING、Kafbat UI がトピックを読める） |

### C. Knowledge Base のロール（PR #58 / #56）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| C-1 | `ops/up.sh` 4-3 の取り込み | `get-ingestion-job` が `COMPLETE`、失敗 0（`s3:ListBucket` の `s3:prefix` 条件で `knowledge-base/` だけ読める） |
| C-2 | Web の「チャット」で手順書を引く質問 | 答えに Knowledge Base の引用が付く |

### D. Spark を止めて起こし直す（PR #58）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| D-1 | `ops/stop-spark.sh` | 3 ジョブが `CANCELLED` まで待って終わる（rc 0） |
| D-2 | `ops/up.sh`（2 回目）の 7-5 | 3 ジョブが checkpoint から起き直る（`list-job-runs` で RUNNING が 3） |

### E. syslog の試験行（037）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| E-1 | lab の EC2 で TRex の netns から `logger -n 203.0.113.1 -P 5140 --rfc5424 -t acl-probe "syslog test <epoch>"` | 1〜2 分後に `logs`（Kafbat UI のトピック `logs`、または Grafana / Splunk の検索）に `acl-probe` の行。無ければ VPC Flow Logs（REJECT、dstPort 5140）で切り分け |

### F. Kafbat UI（QUEUE 87）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| F-1 | stream のあとで Web の EC2 を `reboot-instances` | `journalctl -b -u <prefix>-kafka-ui -o short-precise` で、ユニットの最初の起動から `active (running)` までの秒数と、69 で終わった回数（`NRestarts`）を記録。`systemd-analyze blame` の `<prefix>-kafka-ui` と `<prefix>-web` の値。合否ではなく計測（QUEUE 87 は「測る」） |
| F-2 | 同じ再起動のあと | `systemctl is-system-running` が `running`、`curl 127.0.0.1:8082/` が 200 |

### G. Nautobot の内部のシークレットの Deny（044）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| G-1 | `iam simulate-principal-policy` | `<prefix>-web` / `-runtime` / `-tools` / `-lab` × `ssm:GetParameter` × `arn:aws:ssm:ap-northeast-1:<acct>:parameter/<prefix>/nautobot/db-password` → `explicitDeny`（4 つとも）。`-web` と `-lab` × `parameter/other-nwc/nautobot/db-password` → `explicitDeny`。`-web` × `parameter/<prefix>/nautobot/api-token` → `allowed` |
| G-2 | Web の EC2（Run Command） | `aws ssm get-parameter --name /<prefix>/nautobot/db-password --with-decryption --query Parameter.Name` → `AccessDeniedException`。`--name /<prefix>/nautobot/db-password:1` → `AccessDeniedException`。`label-parameter-version --labels probe` のあと `--name …/db-password:probe` → `AccessDeniedException`（通ったら Resource の末尾 `*` の再設計）。`unlabel-parameter-version` で外す。`--name /<prefix>/nautobot/api-token --query Parameter.Name` → 名前が出る |
| G-3 | lab の EC2（Run Command） | `db-password` が `AccessDeniedException` |
| G-4 | Web の「トポロジ」タブ →「リンクを編集」で追加 | Nautobot に通る（api-token が読めている）。足したリンクは消しておく |

### H. 立ち上がりの全体

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| H-1 | 1 回目の `ops/up.sh` | `UP_RC=0`。各ルートの added 数と所要時間を記録 |
| H-2 | 9-2 の Grafana のルールの検査 | `判定: OK` |
| H-3 | `sudo lab fail-main` のあと | Grafana か Splunk のアラート → SQS → 「承認」タブに修復案が 1 つ（承認しない。I-2 のために残す） |

### I. 2 回目の `ops/up.sh`（`IMAGE_TAG=v045b`。043 / 039 / 042）

| 番号 | 検査 | 合格 |
| :--- | :--- | :--- |
| I-1 | 手順 0 のあと | `check_sg_descriptions` が止まらず手順 1 へ進む（`terraform show -no-color` の実物の形で通る） |
| I-2 | 8-5 でタスクが入れ替わったあと | 「承認」タブ（Athena）と Temporal UI（8233）に H-3 の修復案が承認待ちのまま残る。新旧 2 タスクが並んだ間 `cluster_membership` が 1 クラスター（サーバーのログに membership のエラーが無い） |
| I-3 | 8-5 の init | 前の init が止まっているのを確かめてから `run-task`（ログの文言）。exit 0 |
| I-4 | A-8 | 上に書いたとおり |

### K. 片付け

`OWNER=efukuda ops/down.sh` が `DOWN_RC=0`。boto3 で EC2（`Project=efukuda-nwc-poc` の instance が全部 `terminated`、ENI / EBS / EIP 0）、MSK / ECS / Neptune / RDS / EMR Serverless / Firehose / OpenSearch Serverless / Lambda / S3 / S3 Tables / Cloud Map / CloudWatch Logs / IAM ロール / Secrets Manager / NLB / CloudFormation / Bedrock の Knowledge Base / AgentCore Runtime・Gateway / SQS が接頭辞 `efukuda-nwc-poc` で 0 件。ECR と `PendingDeletion` の KMS の鍵は残ってよい。

## 未確定事項とリスク

1. **所要時間と費用。** 1 回目 50〜70 分 + 検査 1〜2 時間 + 2 回目 20〜30 分 + down 35 分で、約 4〜5 時間 × $3.4/h ≈ $15。直すものが出て打ち直すと増える。
2. **Temporal × PostgreSQL 18（036 のリスク 1）。** スキーマが入らなければ RDS の `db_engine_version` を 16 に下げる修正を出す（Nautobot は 16 で動く）。これが出たときだけ環境を消して立て直す（RDS の作り直し）。
3. **init の時間が startPeriod 300 秒を超える（042 のリスク 3）。** 超えても壊れない（タスクが作り直されるだけ）。超えたら `startPeriod` を伸ばす修正を次に回す。
4. **`:1` とラベルで Deny をすり抜ける（044 のリスク 6）。** すり抜けたら Resource を `parameter/*/nautobot/db-password*` にする再設計（同じサイクルで直す。Must fix）。
5. **H-3 の修復案が出るまでの時間。** アラートの評価（毎分）→ SQS → エージェントで数分。出なければ 2 回目の `up.sh` は承認待ち無しで打ち、I-2 は「未確認」と書く。
6. **A-6 の `pg_stat_activity` は master のパスワードが要る。** 値を見ない形で打てるのは init のタスクの中（`run-task` で `psql` を打つ override）か、ECS Exec の `temporal` のロール（`POSTGRES_PWD` が環境にある）で `psql` が入っていれば。temporal のイメージに psql が無ければ接続数は RDS の CloudWatch の `DatabaseConnections` で代える。
7. **lab-debug（`ops/lab-debug.sh`）のロールの Deny（044）は立てないので未確認のまま**（QUEUE 147 の文面に無い。CloudFormation のテンプレートは `tests/test_lab_debug.py` で見ている）。
