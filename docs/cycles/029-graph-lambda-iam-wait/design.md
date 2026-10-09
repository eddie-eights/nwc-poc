# graph の status Lambda を IAM の伝播を待って作る（029）

設計: PM(fable-5-1) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-10。

## 背景

| # | BACKLOG の行 | 出所 | 扱い |
|---|---|---|---|
| A | graph の status Lambda を、ロールのポリシーが IAM に行き渡ってから作る | 2026-10-10 の AWS 検証（`docs/verification/20261010-aws-managed.md` の不具合 1） | 直す |

2026-10-10 の `ops/up.sh` の 1 本目で、`aws_iam_role_policy.status: Creation complete after 1s` の直後に `aws_lambda_function.status` を作り始め、`State=Failed / StateReasonCode=InsufficientRolePermissions` で止まった。VPC の ENI を作る権限が IAM に行き渡る前に Lambda が検査したと見る。同じ引数の 2 本目は tainted の Lambda を replace して通った。analytics の Firehose（026 で `time_sleep` を入れた）と同じ種類。

### 調査で分かった事実（2026-10-10、origin/main 8a0f40a）

- `IaC/terraform/aws-managed/pipeline/graph/sync.tf:117-142` の `aws_lambda_function.status`。`vpc_config` を持ち、`depends_on = [aws_cloudwatch_log_group.status, aws_iam_role_policy.status]`。
- `IaC/terraform/aws-managed/pipeline/graph/versions.tf` の provider は `aws ~> 6.0` と `archive ~> 2.7` だけ。`time` は無い。`.terraform.lock.hcl` は git に入っている（`git ls-files` で確認）。
- 手本は `IaC/terraform/aws-managed/pipeline/analytics/history.tf:120-131`。
  ```hcl
  # 30 秒は推定。足りなければ延ばす。
  # triggers はロールが作り直されたら待ちも作り直すため（state を残したまま接頭辞を変えたときも待つ）
  resource "time_sleep" "alert_firehose_iam" {
    create_duration = "30s"
    triggers = {
      role = aws_iam_role.alert_firehose.unique_id
    }

    depends_on = [aws_iam_role.alert_firehose, aws_iam_role_policy.alert_firehose]
  }
  ```
  analytics の `versions.tf:10-11` に `time = { source = "hashicorp/time" … }` がある（版の指定はそこを写す）。
- `tests/test_sync.py:625-640` が `sync.tf` の文字列を縛っている（`"depends_on = [aws_lambda_permission.status]" in tf`（SNS の購読の側）、`"vpc_config" in tf and "NEPTUNE_GRAPH_ID = aws_neptunegraph_graph.graph.id" in tf …`）。Lambda の `depends_on` の行を見る check は無い。
- OSS 版の `IaC/terraform/oss/pipeline/graph/` は aws-managed へのシンボリックリンクで組んである（`tests/test_oss.py` の `links_to_managed`）。`sync.tf` / `versions.tf` がリンクなら OSS 版にも同じ変更が効き、`.terraform.lock.hcl` も OSS 側にあるなら揃える（実物は `ls -l` で確かめる）。
- `docs/troubleshooting.md` に 2026-10-10 に足した行（`InsufficientRolePermissions` の打ち直し）がある。

## 設計方針

**A. `time_sleep` を挟む（analytics と同じ形）**

1. `versions.tf` の `required_providers` に `time` を足す（analytics の `versions.tf` の `time` のブロックをそのまま写す）。
2. `sync.tf` の `aws_lambda_function.status` の直前に次を足す。
   ```hcl
   # Lambda は作成時に実行ロールで ENI を作れるかを検査する。ポリシーの作成完了の直後に作ると IAM の伝播前で
   # InsufficientRolePermissions になった（2026-10-10 の AWS 検証）。analytics の Firehose と同じく 30 秒待つ（推定。足りなければ延ばす）。
   # triggers はロールが作り直されたら待ちも作り直すため
   resource "time_sleep" "status_iam" {
     create_duration = "30s"
     triggers = {
       role = aws_iam_role.status.unique_id
     }

     depends_on = [aws_iam_role.status, aws_iam_role_policy.status]
   }
   ```
3. Lambda の `depends_on` を `[aws_cloudwatch_log_group.status, aws_iam_role_policy.status, time_sleep.status_iam]` にする。
4. `.terraform.lock.hcl` を更新する: worktree で `terraform -chdir=IaC/terraform/aws-managed/pipeline/graph init -backend=false -upgrade`（state は触らない。backend が無ければ `-backend=false` は無くてよい）。できた lock の `hashicorp/time` のブロックが analytics の lock と同じ版・同じ hash であることを確かめる。`.terraform/` は commit しない。OSS 側に別の lock があれば同じにする。
5. `tests/test_sync.py` に check を 1 つ足す（`:625-640` の近く）: `versions.tf` に `hashicorp/time`、`sync.tf` に `resource "time_sleep" "status_iam"`、`create_duration = "30s"`、`role = aws_iam_role.status.unique_id`、`depends_on = [aws_iam_role.status, aws_iam_role_policy.status]`、Lambda の `depends_on` に `time_sleep.status_iam`、lock に `registry.terraform.io/hashicorp/time`。
6. `docs/troubleshooting.md` の `InsufficientRolePermissions` の行を「029 で `time_sleep` を入れた。出たら 30 秒では足りなかったということなので延ばす」に書き換える。`docs/verification/20261010-aws-managed.md` は触らない（記録）。

**やらないこと。** `ops/up.sh` の 7-3 で apply を 2 回打つ回避はしない（analytics と直し方を揃える）。Lambda の中身と IAM のポリシーは変えない。

## 変更対象ファイル

| ファイル | 件 | 何を |
|---|---|---|
| `IaC/terraform/aws-managed/pipeline/graph/versions.tf` | A | `time` の provider |
| `IaC/terraform/aws-managed/pipeline/graph/sync.tf` | A | `time_sleep.status_iam` と Lambda の `depends_on` |
| `IaC/terraform/aws-managed/pipeline/graph/.terraform.lock.hcl` | A | `hashicorp/time` の追加（OSS 側に別の lock があればそれも） |
| `tests/test_sync.py` | A | check 1 つ |
| `docs/troubleshooting.md` | A | `InsufficientRolePermissions` の行 |

## 再利用するもの

- `IaC/terraform/aws-managed/pipeline/analytics/history.tf:120-131` の `time_sleep` と `versions.tf` の `time`。
- `tests/test_sync.py` の `tf`（`sync.tf` の全文）と `check`。

## 実装ステップ

1. A の 1〜4 を直し、`terraform -chdir=IaC/terraform/aws-managed/pipeline/graph validate`（managed と OSS の両方）が Success。1 commit。
2. check を足して赤（`git stash` は使わず、`sync.tf` の `time_sleep` の行を一時的に消して落ちることを見る）→ 緑。docs の行。1 commit。
3. `build.md` を書く（頭は `実装モデル: opus-5.5 / effort: high`。validate の出力、テストの出力、セルフレビュー）。

## 検証方法（期待出力まで）

- `terraform -chdir=IaC/terraform/aws-managed/pipeline/graph validate` と `terraform -chdir=IaC/terraform/oss/pipeline/graph validate` が `Success! The configuration is valid.`（init は `-backend=false`）。
- `uv run --frozen python3 tests/test_sync.py` が `通過 104 / 失敗 0`（2026-10-10 は 103。1 増える）。`tests/test_oss.py`（174）と `tests/test_analytics.py`（528）の件数は変わらない。
- `git status` に `.terraform/` が出ない。lock の diff が `hashicorp/time` のブロックの追加だけ。
- AWS（PM が 1 回だけ打つ。実装の範囲外）: `ops/up.sh` の 1 本目で graph の apply が `time_sleep.status_iam: Creation complete after 30s` → `aws_lambda_function.status: Creation complete` と出て、`GetFunction` が `State=Active`。1 本で `UP_RC=0`。

## 未確定事項とリスク

1. 30 秒で足りるかは 1 回の AWS 検証でしか分からない（analytics も 1 回）。足りなければ `create_duration` を延ばす（60s）。
2. lock の更新に provider の取得（ネットワーク）が要る。取れなければ analytics の lock から `hashicorp/time` のブロックを写してもよい（版と hash は同じものになる。`terraform init` が lock を受け付けることを確かめる）。
3. OSS 版の graph のディレクトリが `sync.tf` / `versions.tf` をリンクで持っていないなら（独自のファイルなら）、OSS 側にも同じ変更を入れる。
