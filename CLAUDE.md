# nwc-poc

このリポジトリの決まりが、下の AWS の規則より優先する。

- インフラは Terraform（`IaC/terraform/aws-managed/`）と `ops/up.sh` / `ops/down.sh` で作る。CDK や CloudFormation に置き換えない。
- シークレットは `ops/up.sh` が SSM Parameter Store の SecureString として作る。値は読まない・表示しない。
- MSK の SCRAM の資格情報だけは例外で、`ops/up.sh` が Secrets Manager（`AmazonMSK_<prefix>-collectors`。顧客管理の KMS の鍵 `alias/<prefix>-msk-scram` で暗号化）に作る（MSK の SCRAM は Secrets Manager しか受けない）。値は読まない・表示しない。

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
