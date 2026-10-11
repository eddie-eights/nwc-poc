# build: 050 の plan の守りを AWS で 1 回通す（051）

## Round 1

実装モデル: PM(opus-5.5) / effort: high（エンジニアは起動していない。コードの変更は無い）。2026-10-12。

### 経過

- 05:48（日本時間）V-1 を打った（`VPC_CIDR=10.20.0.0/16 OWNER=efukuda AGENT=0 PIPELINE=0 WORKFLOW=0 NO_DASHBOARD_PORTFORWARD=1 ops/up.sh`、メインのチェックアウト `e0827ac`）。手順 0 の認証の検査で止まった: 「aws: [ERROR]: Your session has expired. Please reauthenticate using 'aws login'.」→「NG: 認証が通っていない（aws configure か aws login で入り直す）」、`UP_RC=1`。守りより前なので何も作っていない（AWS には触れていない）
- ユーザーが `aws login` で入り直したので、06:21 から V-1〜V-5 を打ち直した。結果は `docs/verification/20261012-aws-managed-051.md`

### 結果

| 番号 | 結果 |
|---|---|
| V-1 | 通った。`aws_security_group.workload["runtime"]（replace、cannot_update）` と runtime の ENI の一言で `UP_RC=1`、「1. ECR」0 行、Warning 0 行。守り 4 秒 |
| V-2 | 通った。守り 3 秒、base/core `152 added, 0 changed, 0 destroyed`（runtime の SG・VPC・サブネットの作り直し 0）、`UP_RC=0` |
| V-3 | 通った。die の一覧が `aws_security_group.endpoints` と `workload[...]` 16 本（state list の数と同じ）すべて `replace`、runtime の一言、`UP_RC=1`、「1. ECR」0 行。state の `serial 4302` と sha256 が前後で同じ。守り 4 秒 |
| V-4 | 通った。守り 4 秒、base/ecr・base/logs・base/core とも `No changes`、`UP_RC=0` |
| V-5 | 通った。`DOWN_RC=0`（157 destroyed）。AWS MCP で EC2 `terminated`、VPC エンドポイント 0、EIP 0、タグ付きの VPC・サブネット・SG・ENI 0 |

### セルフレビュー

コードの変更が無いので `/robust` は回していない。design の合格条件を項目ごとにログと AWS の API の出力で突き合わせた（上の表）。Must 0 / Should 0。
