# graph の status Lambda を IAM の伝播を待って作る（029）— 実装

## Round 1

実装モデル: opus-5.5 / effort: high

### 実装内容

- `IaC/terraform/aws-managed/pipeline/graph/versions.tf`: `time`（`hashicorp/time`、`~> 0.13`。analytics と同じ）
- `IaC/terraform/aws-managed/pipeline/graph/sync.tf`: `time_sleep.status_iam`（30s、triggers はロールの `unique_id`、depends_on はロールとポリシー）。Lambda の `depends_on` に `time_sleep.status_iam`
- `IaC/terraform/oss/pipeline/graph/sync.tf`: 上と同じ変更（実ファイル。設計との差 2）
- `IaC/terraform/aws-managed/pipeline/graph/.terraform.lock.hcl`: `hashicorp/time` 0.14.2 のブロック（analytics の lock と同一）。OSS 側の lock はこのファイルへのリンク
- `tests/test_sync.py`: check 1 つ（マネージド版と OSS 版の `sync.tf`、`versions.tf` の `time` のブロック、lock）
- `docs/troubleshooting.md`: `InsufficientRolePermissions` の行と、表の下の「graph-status の作成」

### 設計との差

1. lock は `init -upgrade` でなく、analytics の lock の `hashicorp/time` のブロックを写してから `init -backend=false`（`-upgrade` なし）。`-upgrade` は aws / archive の版も上げうるので、検証方法の「lock の diff が time のブロックの追加だけ」と衝突する。設計のリスク 2 の方法。init と `providers lock` が変更なしで受け付けた（下の生ログ）。
2. OSS 版の `IaC/terraform/oss/pipeline/graph/sync.tf` はリンクでなく実ファイル（`versions.tf` と lock はリンク）なので、設計のリスク 3 に従い同じ変更を入れた。check も OSS 版を見る（件数は 1 つのまま）。
3. `docs/troubleshooting.md` は表の行に加えて、表の下の「graph-status の作成」の古くなった箇条（「IAM の反映は待たない」「恒久対策は BACKLOG の候補」）を直し、待ちを延ばすときに直す 3 か所（マネージド版と OSS 版の `sync.tf`、`tests/test_sync.py` の `_iam_wait`）を書いた。

### 検証（最後の編集のあと）

```
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/graph init -backend=false -input=false
- Reusing previous version of hashicorp/time from the dependency lock file
- Using previously-installed hashicorp/time v0.14.2
Terraform has been successfully initialized!
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/graph validate
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/oss/pipeline/graph init -backend=false -input=false -lockfile=readonly
- Reusing previous version of hashicorp/time from the dependency lock file
- Using previously-installed hashicorp/time v0.14.2
Terraform has been successfully initialized!
$ terraform -chdir=IaC/terraform/oss/pipeline/graph validate
Success! The configuration is valid.
$ terraform -chdir=IaC/terraform/aws-managed/pipeline/graph providers lock
Success! Terraform has validated the lock file and found no need for changes.
$ terraform fmt -check -recursive IaC/terraform/aws-managed/pipeline/graph IaC/terraform/oss/pipeline/graph
（出力なし、rc=0）
$ uv run --frozen python3 tests/test_sync.py | tail -1
通過 104 / 失敗 0
$ uv run --frozen python3 tests/test_oss.py | tail -1
通過 174 / 失敗 0
$ uv run --frozen python3 tests/test_analytics.py | tail -1
通過 528 / 失敗 0
$ uv run --frozen python3 tests/test_oss_ops.py | tail -1
通過 203 / 失敗 0
$ git diff --stat 4d3d071 -- IaC/terraform/aws-managed/pipeline/graph/.terraform.lock.hcl
 .../aws-managed/pipeline/graph/.terraform.lock.hcl  | 21 +++++++++++++++++++++
 1 file changed, 21 insertions(+)
$ diff <(analytics の lock の time のブロック) <(graph の lock の time のブロック)
（差分なし）
$ git status --porcelain -uall   # .terraform/ は出ない（.gitignore の .terraform/）
 M docs/troubleshooting.md
 M tests/test_sync.py
```

`ls -l` で OSS 側の `.terraform.lock.hcl` と `versions.tf` はマネージド版へのリンクのまま。

`bash ops/check.sh`（セルフレビューの直し前に実行）: fmt「差分なし」、managed / OSS の 18 ルートの init + validate がすべて OK、構文エラーなし、模擬テスト全部 `失敗 0`、最後の行「すべて通過」。

AWS（`ops/up.sh` で `time_sleep.status_iam: Creation complete after 30s` → Lambda が `State=Active`）: 未実行（PM が実施。実装の範囲外）。

### 赤 → 緑（退行の注入）

`/private/tmp/.../scratchpad/mutate.py` が 1 つずつ書き換えて `tests/test_sync.py` を打ち、元に戻す。

```
managed の Lambda の depends_on から外す: rc=1 AssertionError: status の Lambda は time_sleep.status_iam（30 秒。…
OSS の Lambda の depends_on から外す: rc=1 AssertionError: …
managed の time_sleep を消す（名前を変える）: rc=1 AssertionError: …
OSS の time_sleep を消す（名前を変える）: rc=1 AssertionError: …
30s を 10s に: rc=1 AssertionError: …
triggers を外す: rc=1 AssertionError: …
time_sleep の depends_on からポリシーを外す: rc=1 AssertionError: …
versions.tf から time を外す: rc=1 AssertionError: …
versions.tf の time の版を変える: rc=1 AssertionError: …
versions.tf の time をコメントだけにする: rc=1 AssertionError: …
本物を /* */ で囲み 1s の実物を置く: rc=1 AssertionError: …
lock から time を外す: rc=1 AssertionError: …
```

戻したあと `通過 104 / 失敗 0`。

### セルフレビュー

- 自分: opus-5.5 / effort: high（PM のサブエージェントとして動いていて effort を切り替えられないので high のまま。既定の xhigh ではない）
- 反対弁護人: opus（general-purpose、文脈付き、読み取り専用）。終了後 `git status --porcelain -uall` は空

| # | 分類 | 観点 | 場所 | 破綻シナリオ | 確かめたこと | 片付け |
|---|---|---|---|---|---|---|
| 1 | Should fix | missing tests | `tests/test_sync.py:640-644` | `versions.tf` の `time` のブロックを消してコメントに `source  = "hashicorp/time"` を残す、または版を変えると、部分文字列の検査が通る（Terraform は暗黙で time を引くので validate も通る）。本物を `/* */` で囲み 1s の実物を置いても正規表現はコメントに当たる | 反対弁護人がリポジトリ外のコピーで `通過 104` を再現。自分は直した後に 3 通りの注入で落ちることを確認（上の表の下 3 つ） | 直した（`versions.tf` は `time = { source … version = "~> 0.13" }` のブロックで縛り、`sync.tf` の `resource "time_sleep"` は 1 つだけ） |
| 2 | Nit | docs | `docs/troubleshooting.md:31,47-48` | 案内どおりマネージド版だけ 60s にすると test_sync が赤くなり、OSS 版とずれる | test_sync の `_iam_wait` が 30s を固定、OSS 版 `sync.tf` が実ファイルであることを `ls -l` で確認 | 直した（直す 3 か所を書いた） |
| 3 | Nit | docs | `docs/troubleshooting.md:47-50` | 箇条の主語が無く、「2 回目も止まるなら」が 029 より前の事例の 2 回目を指すように読める | 読んだだけ | 直した |
| 4 | Nit | docs | `docs/architecture/resources/sns-sqs-lambda.md:16` | Lambda の行に 30 秒の待ちが無い（`firehose.md` には 026 の待ちがある） | `grep` で記述が無いことを確認 | 設計の変更対象外なので直さず報告に回す |

反対弁護人が反証できなかった点（本人も確認）: lock の time のブロックが analytics と同一、OSS の `-lockfile=readonly` の init が `.terraform` 無しのコピーでも通る、OSS 版冒頭の「違うのは 4 つだけ」は崩れていない、閉域の Deny に ec2 は無い、destroy の順序（Lambda → time_sleep → ポリシー → ロール）。

参考（指摘にしない）: triggers はロールの `unique_id` だけで、ポリシーの in-place 変更では待ち直さない（analytics と同じ形）。Lambda が作られるのは新しいロールのときか tainted からの replace のときだけなので、今のコードでは困らない。既に立っている graph への apply で Lambda が replace されないのは Terraform の仕様からの推論で、AWS では未確認。

「問題なし」とした観点と根拠:
- 設計整合性: design.md の実装ステップ 1〜3 と検証方法を突き合わせた（上の生ログ）。
- correctness / runtime: `ops/check.sh` が全部通過。`ops/` の init（`tf_init_root`。OSS は `-lockfile=readonly`）は上の readonly の init と同じ形で通る。
- security / data loss: IAM のポリシーと Lambda の中身は変えていない（diff は `time_sleep` と `depends_on` だけ）。
