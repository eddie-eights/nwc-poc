# レビュー: 残った修正をまとめて AWS で動作確認して直す（045）

## Round 1

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は `build.md` Round 1（PM 自身。エンジニア無し）。

**cold reviewer: 依頼しない。** 理由: このサイクルの差分は `docs/verification/20261011-aws-managed-045.md` / `docs/cycles/045-aws-batch-verify/*` / `docs/cycles/QUEUE.md` だけで、実装ファイル（`IaC/` / `app/` / `ops/` / `docker/` / `tests/`）は 1 バイトも変わっていない。レビューの対象は「AWS で出力を見た」という記録の正確さで、それは出力を見た本人にしか確かめられない（cold reviewer は環境が消えたあとでは再現できない）。

### 記録の確認（PM）

- `[design 整合性]` 設計の検証方法 A〜K の全項目が記録の表に 1 行ずつある。合格の文言と結果の文言を突き合わせた: 通った 24 / 計測 1（F-1）/ 未確認 2（A-8 = I-4、I-3 の文言）/ 失敗 0
- `[correctness]` 未確認の 2 つはどちらも「init が 0.2〜1.1 秒で終わる」という同じ実装の制約。合格条件のほうが AWS で作れない状態を要求していた（設計の見落とし）。コードの退行ではない。手元の docker（040 のセルフレビュー）と `docker/images/temporal-server/common.sh:23` の読みに依ることを記録に明記した
- `[security]` 記録にシークレットの値は無い（`grep -n -i -E 'password|token|secret' docs/verification/20261011-aws-managed-045.md` で出るのはパラメータの名前と「値は出さない」の文言だけ）
- `[data loss]` G-4 で Nautobot に作ったケーブルは消した。インタフェース ethernet-1/45 ×2 は残る仕様だが環境ごと消えた
- `[missing tests]` 設計のリスク 2（PostgreSQL 18）/ 3（startPeriod）/ 4（`:1` とラベル）/ 5（修復案が出る時間）/ 6（`pg_stat_activity`）はどれも実測で閉じた。リスク 7（lab-debug の Deny）は設計どおり未確認のまま（QUEUE 147 の範囲外）
- 見ていない観点: runtime bugs / API compatibility / type safety（コードを変えていない）

### 片付けの確認

- `ops/down.sh` は exit 0（08:38Z〜09:28Z ごろ）。記録の「片付け」のとおり
- boto3（09:40Z ごろ）: EC2 2 台とも `terminated`、ENI / EBS / EIP 0。MSK / ECS / Neptune Analytics / RDS / EMR Serverless / Firehose / OpenSearch Serverless / Lambda / S3 Tables / Cloud Map / CloudWatch Logs / IAM ロール / Secrets Manager / NLB / CloudFormation / Knowledge Base / AgentCore Runtime・Gateway / SQS は接頭辞 `efukuda-nwc-poc` で 0 件。残るのは logs のバケット（設計どおり）、ECR 15 本（`KEEP_ECR=1`）、`PendingDeletion` の KMS の鍵（2026-10-18）、時間課金の無い VPC・サブネット・Runtime の SG
- 課金の残るものは無し

### 判断

- Must fix: 0
- Should fix: 0
- Nit: 0
- 全体設計 HTML: 触らない（構成は変わらない）
- QUEUE 147 と 87 を完了にする（2026-10-11）。次に回すものは無し（159 / 160 は元から未着手）
- サイクル完了（2026-10-11）
