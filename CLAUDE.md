# nwc-poc

このリポジトリの決まりが、下の AWS の規則より優先する。

- インフラは Terraform（`IaC/terraform/aws-managed/`）と `ops/up.sh` / `ops/down.sh` で作る。CDK や CloudFormation に置き換えない。
- シークレットは `ops/up.sh` が SSM Parameter Store の SecureString として作る。値は読まない・表示しない。
- MSK の SCRAM の資格情報だけは例外で、`ops/up.sh` が Secrets Manager（コレクターごとの `AmazonMSK_<prefix>-syslog-ng` / `-goflow2` / `-gnmic`。顧客管理の KMS の鍵 `alias/<prefix>-msk-scram` で暗号化）に作る（MSK の SCRAM は Secrets Manager しか受けない）。値は読まない・表示しない。

## セッションの役（2026-10-10）

- **このリポジトリで人が起動するセッションは PM の 1 つだけ。** このフォルダ（cwd）で起動したセッションは、題名や会話の記憶にかかわらず PM として動く（AI 開発フローは `docs/ai-dev-flow.md`、正本は claude-settings の `cycle-design/flow.md`）。エンジニアは PM のサブエージェント（`Agent`）。
- **PM は coordinator。サイクルをまたいで同じセッションを使い、clear しない。** ユーザーの窓口も PM（秘書は置かない）。実装が背景で走っている間もユーザーの質問に答え、根拠は会話の記憶ではなくファイル（QUEUE、各サイクルの `design.md` / `build.md` / `review.md`、`git log`）から取る。 ユーザーが手で `/clear` するのは問題ない（役・現在地・待ちはこのファイルとフックから戻る）。背景のサブエージェントが走っている間だけ「終わるまで待って」と答え、未記録の判断は先に `review.md` / QUEUE に書く。
- プロジェクト別メモリに「このセッション」「セッション ID」を主語にした役のメモを書かない。役はこのファイルで決まる。
- 現在地（`docs/cycles/QUEUE.md` の着手行と直近の完了、ユーザーの判断待ち、最新サイクルの `review.md` の末尾、直近の commit）は `.claude/settings.json` の SessionStart フック `cycle-context.py` が起動・再開・圧縮のたびに注入する（スクリプトが `~/.claude/hooks/` に無い環境では何もしない）。引き継ぎメモは作らない。次に回すものは QUEUE に `- [ ]` で足す。
- **ユーザーの判断・確認・外部の返事を待つ件は `待ち(ユーザー): <何を>（yyyy-mm-dd〜）` と書く**（QUEUE の行か最新サイクルの `review.md` / `build.md` / `design.md`）。フックと `/waiting` がそれを一覧する。解けたら文言を消す。

<!-- BEGIN AWS Agent Toolkit rules -->
# AWS Guidance

- Where these AWS rules conflict with the project's own instructions, the
  project's instructions take precedence.
- Prefer the AWS MCP Server for AWS interactions — it provides sandboxed
  execution, observability, and audit logging. If unavailable, use the
  AWS CLI directly.
- Before starting a task, check whether a relevant AWS skill is available.
  Load the skill with `retrieve_skill` and prefer its guidance over
  general knowledge.
- When uncertain about specific AWS details (API parameters, permissions,
  limits, error codes), verify against documentation rather than guessing.
  State uncertainty explicitly if you cannot confirm.
- When creating infrastructure, prefer infrastructure-as-code (AWS CDK or
  CloudFormation) over direct CLI commands.
- When working with infrastructure, follow AWS Well-Architected Framework
  principles.
- Do not use em dashes in AWS resource names or descriptions. Use
  hyphens instead.

## Secret Safety

- MUST load the `aws-secrets-manager` skill first for any secret,
  credential, API key, token, or password task. MUST NOT call
  `secretsmanager get-secret-value` or `batch-get-secret-value`, and MUST
  NOT hit the Secrets Manager Agent daemon directly. MUST use
  `{{resolve:secretsmanager:secret-id:SecretString:json-key}}` with
  `asm-exec` so the secret resolves at runtime without entering context.
<!-- END AWS Agent Toolkit rules -->
