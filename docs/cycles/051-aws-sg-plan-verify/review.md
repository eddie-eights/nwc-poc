# レビュー: 050 の plan の守りを AWS で 1 回通す（051）

## Round 1

レビューモデル: PM(opus-5.5) / effort: high。2026-10-12。対象は `build.md` Round 1（PM 自身。エンジニア無し）。

**cold reviewer: 依頼しない。** 理由: このサイクルの差分（`git diff --stat e0827ac`）は `docs/cycles/051-aws-sg-plan-verify/*` / `docs/cycles/QUEUE.md` / `docs/verification/20261012-aws-managed-051.md` だけで、実装ファイル（`IaC/` / `app/` / `ops/` / `docker/` / `tests/`）は 1 バイトも変わっていない。

### 記録の確認（PM）

- `[design 整合性]` 設計の検証方法 V-1〜V-5 が記録の表に 1 行ずつある。合格の文言とログ（スクラッチパッドの `v1.log`〜`v5.log`）を突き合わせた: 通った 5 / 未確認 0 / 失敗 0
- `[correctness]` 止まる側は V-1（runtime 1 本）と V-3（endpoints + workload 16 本。`terraform state list` の `aws_security_group.workload[` 16 と一致）で die の一覧がすべて `replace、cannot_update`、`grep -c '1\. ECR'` = 0。V-3 の前後で base/core の state の `serial 4302` と sha256 が同じ（何も書いていない）。通る側は V-2 / V-4 で `UP_RC=0`、V-2 の base/core は `0 destroyed`
- `[runtime bugs]` 本物の state と AWS の data source で plan のエラー・Warning 0 行（V-1〜V-4 の `grep -ci warning` = 0）。`-refresh=false` でも止まらない（design のリスク 1 は出ない）。守りは 3〜4 秒
- `[security]` 記録にシークレットの値は無い（`grep -n -i -E 'password|token|secret'` は 0 行）。state は `serial` / `lineage` の先頭 8 字 / sha256 の先頭 / `state list` のアドレスだけ出し、中身は見ていない
- `[data loss]` V-1 / V-3 は手順 0 で止まり、何も作らず何も消していない（V-3 の state の hash で確かめた）
- 見ていない観点: API compatibility / type safety / missing tests（コードを変えていない）

### 片付けの確認

- `ops/down.sh` は `DOWN_RC=0`（06:30:51〜06:34:17、157 destroyed）
- boto3（06:35 ごろ）: EC2 `i-0b666e107c7e49950` `terminated`、VPC エンドポイント 0、EIP 0、NAT 0、タグ付きの VPC / サブネット / SG / ENI 0、ロググループ 0、IAM ロール 0。S3 は logs のバケットだけ（設計どおり）、ECR は `KEEP_ECR=1` で残す
- 時間課金の残るものは無し

### 判断

- Must fix: 0
- Should fix: 0
- Nit: 0
- 全体設計 HTML: 触らない（構成は変わらない）
- QUEUE 165 を完了にする（2026-10-12）。次に回すものは無し
