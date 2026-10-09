# up.sh の片付けと Kafbat UI と Firehose の直しを片付ける（026）

設計: PM(opus-5.5) / effort: high。実装はエンジニア（opus-5.5 以下）。2026-10-09。

## 背景

023 のセルフレビューと 014 のセルフレビュー、2026-10-09 の AWS 検証で出て BACKLOG に残っていた、ops と Terraform の小さな直し 4 件を 1 サイクルに束ねる。どれも互いに独立で、1 件ずつ 1 commit にする。

| # | BACKLOG の行 | 出所 |
|---|---|---|
| A | `ops/up.sh` の `exec` の前の片付けを `on_exit` に揃える | 023 のセルフレビューの Should fix 1 |
| B | `ensure_fixed_secret` にも `put-parameter` の最中の割り込みの検査を足す | 023 のセルフレビューの Should fix 2 |
| C | Kafbat UI のユニットが 75 で failed のまま残ると `degraded` になるのを直す | 014 のセルフレビューの Nit 5、2026-10-09 の AWS で実測 |
| D | analytics の Firehose を IAM ロールの反映を待ってから作る | 2026-10-09 の AWS 検証の 2 回目の `up.sh` で実測 |

### 調査で分かった事実（2026-10-09、origin/main 3e06e4c）

**A. exec の前の片付け**

- `ops/up.sh:156-165` の `on_exit()` は、`GRAPH_PID` の apply を待ち、`TF_AWS_CONFIG` / `NAUTOBOT_CTX` / `MSK_SCRAM_INPUT` / `SECRET_INPUT` を消す。`:166` が `trap on_exit EXIT`。
- `ops/up.sh:1349-1355` の手順 10 は、`trap - EXIT` のあと `if [ -n "$TF_AWS_CONFIG" ]; then rm -f "$TF_AWS_CONFIG"; fi` だけを手で消して `exec aws ssm start-session …` する。`NAUTOBOT_CTX` は消さない。
- 023 で `ops/oss/up.sh:656-659` は次の形にした（ここに揃える）。
  ```
  # exec すると EXIT の trap が走らないので、ここで同じ on_exit を 1 回呼んで片付ける
  trap - EXIT
  on_exit
  exec aws ssm start-session --region "$REGION" --target "$INSTANCE_ID" \
  ```
- `GRAPH_PID` は graph の apply を待ったところ（`ops/up.sh:985` の `rc=0; wait "$GRAPH_PID" || rc=$?; GRAPH_PID=""`）で空に戻すので、手順 10 の時点で `on_exit` を呼んでも `wait` は走らない。
- テスト。`tests/test_oss_ops.py:1026-1030` が OSS 版の `on_exit` を見る（`up.count("\ntrap - EXIT\non_exit\nexec aws ssm start-session ") == 1 and up.count("\ntrap ") == 2`）。`:1157` は `ops/up.sh` の手順の並び（`NO_DASHBOARD_PORTFORWARD` → `\ntrap - EXIT` → `\nexec aws ssm start-session`）を見る。

**B. ensure_fixed_secret の割り込み**

- `ops/up-common.sh:150-172` の `ensure_fixed_secret` は、`ensure_secret`（`:112-149`）と同じグローバル `SECRET_INPUT`（`:106`）に `umask 077` の一時ファイルを作り、`put-parameter` のあと `rm -f -- "${SECRET_INPUT:?}"; SECRET_INPUT=""` で消す。呼び出しは `ops/up.sh:922-923` と `ops/oss/up.sh:348-349`（gnmic の username / password）。
- `tests/test_oss_ops.py:877-911` の `SECRET_SH` は `ensure_secret` だけを打つ。`:898-911` は `_on_exit`（`ops/up.sh` の `on_exit` を正規表現で抜き出したもの）を組み込み、`FAKE_SSM_PUT_SIGNAL=INT` / `TERM`（偽の `aws` の `put-parameter` が親の shell と自分にシグナルを送る。`:171-172`）で、`returncode < 0`、`put-parameter` が 1 回、SSM に名前が無い、`nwc-secret.` が残らない、の 4 つを見る。

**C. Kafbat UI の終了コード 75**

- `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:84-89` のユニットは `Restart=always` / `RestartSec=30` / `RestartPreventExitStatus=75` / `TimeoutStartSec=0`。スクリプトは SSM のパラメータが無いと `:52` の `exit 75`。
- 2026-10-09 の AWS（`docs/troubleshooting.md:278-283`、`docs/pipeline.md:367`）で、stream の前の起動ではユニットが `failed`（`status=75`）、`NRestarts=0` で止まり、`systemctl is-system-running` が `degraded` だった。
- systemd の `SuccessExitStatus=` は、並べた終了コードを成功の扱いにする（systemd.service(5)）。成功で終わった `Type=simple` のユニットは `inactive (dead)` になり、`failed` に数えない。`RestartPreventExitStatus=` は `Restart=` より優先する（同じ文書）。`Wants=` は `inactive` のユニットも起こす。**AWS では未確認。**
- 75 に触れている記述:
  - `web_user_data.sh.tftpl:31`（「75 で終わり、systemd は起こし直さない（RestartPreventExitStatus）」）、`:33`、`:131`（「75 で failed のまま止まっていても」）
  - `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf:7`
  - `docs/troubleshooting.md:278-283`（「ユニットは failed のまま止まり」「`degraded` になった」）と `:298-308`（`RestartPreventExitStatus=75` は main プロセスの終了コードを見る）
  - `docs/pipeline.md:365-371`
  - `docs/architecture/resources/web-ec2.md:26`、`:28`（failed とは書いていない）
- テスト。`tests/test_stream.py:550-558` がユニットの行を字面で見る（`re.findall(r"^RestartPreventExitStatus=.*$", _kunit, re.M) == ["RestartPreventExitStatus=75"]`）。
- `ops/oss/` の `web_user_data.sh.tftpl` はシンボリックリンクなので、OSS 版も一緒に直る。

**D. Firehose と IAM の反映**

- `IaC/terraform/aws-managed/pipeline/analytics/history.tf:23` が `aws_iam_role.alert_firehose`、`:40` が `aws_iam_role_policy.alert_firehose`、`:118-149` が `aws_kinesis_firehose_delivery_stream.alert_events`。`:147-148` は次のとおり。
  ```
  # ロールの権限が付く前に作ると、Firehose がテーブルを確かめられずに作成が失敗する
  depends_on = [aws_iam_role_policy.alert_firehose]
  ```
- 2026-10-09 の 2 回目の `up.sh` で、作成が `InvalidArgumentException: The security token included in the request is invalid. Ensure that the provided IAM role associated with firehose is not deleted.` で止まった。同じコマンドの打ち直しで通った（IAM の反映遅れ）。
- 同じ対策の前例が `IaC/terraform/aws-managed/agent/kb.tf:107-114`（`time_sleep.kb_collection_ready`、`create_duration = "60s"`）にある。`agent/versions.tf:12-15` が `time = { source = "hashicorp/time", version = "~> 0.13" }`、`agent/.terraform.lock.hcl:49-` が `0.14.2` の block。
- `pipeline/analytics/versions.tf` は aws だけ。`IaC/terraform/oss/pipeline/analytics/` の `history.tf` / `versions.tf` / `.terraform.lock.hcl` は aws-managed へのシンボリックリンクで、OSS 版の init は `TF_INIT_LOCKFILE=readonly`（`ops/oss/up.sh:43`）なので、lock に time の block が無いと OSS 版の init が落ちる。
- テスト。`tests/test_analytics.py:399-` が `history.tf` を読み、Firehose とロールの中身を見る。

## 設計方針

**A. `ops/up.sh` の手順 10 は `trap - EXIT; on_exit` にする**

1. `ops/up.sh:1351-1352` の 2 行を、`ops/oss/up.sh:656-658` と同じ 3 行（コメント 1 行 + `trap - EXIT` + `on_exit`）に置き換える。手で消す行は消す。
2. テスト。`tests/test_oss_ops.py:1030` の隣（または `ops/up.sh` を読む既存の節）に 1 check 足す。`read("ops/up.sh")` で `count("\ntrap - EXIT\non_exit\nexec aws ssm start-session ") == 1` と `count("\ntrap ") == 2`、かつ `NO_DASHBOARD_PORTFORWARD` の行より後ろにあること。先に今の `ops/up.sh` で落ちることを見て `build.md` に貼る（赤→緑）。

**B. `ensure_fixed_secret` の割り込みの検査を足す**

1. `tests/test_oss_ops.py:911` の後ろに、`FIXED_SECRET_SH`（`ensure_fixed_secret /x-nwc-oss/gnmic/gnmi-password "test-value" "desc"` を 1 回打つ）を作り、`:898-911` と同じループで `INT` / `TERM` の 2 つを打つ。見るのは同じ 4 つ（`returncode < 0`、`put-parameter` が 1 回、SSM に `/x-nwc-oss/gnmic/gnmi-password` が無い、`nwc-secret.` が残らない）。
2. 赤→緑。`_on_exit` を組み込まずに打つと `nwc-secret.` が残ることを 1 回見て `build.md` に貼る（テストには入れない）。
3. コードは直さない（直すところが見つかったら、そのときに直して `build.md` に書く）。

**C. Kafbat UI のユニットに `SuccessExitStatus=75` を足す**

1. `web_user_data.sh.tftpl:87` の `RestartPreventExitStatus=75` の次の行に `SuccessExitStatus=75` を足す。`RestartPreventExitStatus=75` は残す（`Restart=always` は成功で終わっても起こし直すので、止めるのはこちら）。
2. コメントを直す。`:31` は「75 で終わり、systemd は成功の扱いにして（SuccessExitStatus）起こし直さない（RestartPreventExitStatus）。ユニットは inactive (dead) で止まり、failed に数えないので `systemctl is-system-running` は degraded にならない」。`:131` の「75 で failed のまま止まっていても」は「75 で止まっていても」。`kafka_ui.tf:7` も同じ趣旨で 1 か所。
3. docs を直す。
   - `docs/troubleshooting.md:278-283` は「ユニットは `inactive (dead)` で止まり（`SuccessExitStatus=75`）、自分では起こし直さない。026 より前は `failed` のまま止まり、`systemctl is-system-running` が `degraded` になった（2026-10-09 の AWS）」の形にする。今の状態は未確認と書く（AWS で確かめたら外す）。
   - `docs/troubleshooting.md:298-308` に「`SuccessExitStatus=75` もあるので、`status=75` でもユニットは failed にならない」を 1 行足す。
   - `docs/pipeline.md:366-367` を同じ形にする。
   - `docs/architecture/resources/web-ec2.md:26` を「終了コード 75 は成功の扱いにして起こし直さない」にする。
   - `docs/cycles/` と `docs/verification/` は記録なので直さない。
4. テスト。`tests/test_stream.py:557-558` の行の一覧に `"SuccessExitStatus=75"` を足し、`re.findall(r"^SuccessExitStatus=.*$", _kunit, re.M) == ["SuccessExitStatus=75"]` を足す。check の名前に「75 は成功の扱いにして failed に数えない（degraded にしない。cycle 026）」を足す。先に落ちることを見る（赤→緑）。

**D. Firehose の前に `time_sleep` を挟む**

1. `pipeline/analytics/versions.tf` の `required_providers` に `time = { source = "hashicorp/time", version = "~> 0.13" }` を足す（`agent/versions.tf` と同じ書き方。コメントは「Firehose を IAM の反映を待ってから作る（history.tf の alert_firehose_iam）」）。
2. `pipeline/analytics/.terraform.lock.hcl` に `agent/.terraform.lock.hcl` の `registry.terraform.io/hashicorp/time` の block（`0.14.2`）をそのまま写す（provider の名前の順に並べる）。OSS 版は lock を書き換えない init なので、ここで入れておかないと落ちる。
3. `history.tf` の Firehose の前に足す。
   ```
   # ロールとポリシーを作った直後は、Firehose が assume できずに作成が止まることがある（IAM の反映遅れ。
   # 2026-10-09 の AWS で InvalidArgumentException: The security token included in the request is invalid … を実測し、打ち直しで通った）。
   # 30 秒は推定。足りなければ延ばす
   resource "time_sleep" "alert_firehose_iam" {
     create_duration = "30s"

     depends_on = [aws_iam_role.alert_firehose, aws_iam_role_policy.alert_firehose]
   }
   ```
   Firehose の `depends_on` を `[time_sleep.alert_firehose_iam]` にし、`:147` のコメントに「ロールの反映も待つ」を足す。`history.tf` に `count` は無いので付けない。
4. テスト。`tests/test_analytics.py` の Firehose の節に 1 check 足す。`history.tf` に `resource "time_sleep" "alert_firehose_iam"` があり、`create_duration` が `"30s"` 以上で、`depends_on` にロールとポリシーがあり、Firehose の `depends_on` が `time_sleep.alert_firehose_iam` を含む。`versions.tf` に `hashicorp/time` があり、lock に `registry.terraform.io/hashicorp/time` の block があること。先に落ちることを見る（赤→緑）。
5. `terraform validate` を両方のルートで通す。`terraform -chdir=IaC/terraform/aws-managed/pipeline/analytics init -backend=false` と `-chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly` のあと `validate`。worktree の `.terraform/` は gitignore なので commit に入らない。

**やらないこと。** Firehose の作成を `up.sh` で打ち直す形（BACKLOG の代案）はとらない（Terraform の中で閉じるほうが OSS 版にも効く）。Kafbat UI の `Restart=` や `Wants=` の形は変えない。`ensure_fixed_secret` のコードは B の検査で問題が出ない限り触らない。AWS での確認は、このサイクルの中ではしない（PM が 024・025 の未確認と一緒にまとめて 1 回やる）。

## 変更対象ファイル

| ファイル | 変更 |
|---|---|
| `ops/up.sh` | A。手順 10 を `trap - EXIT` + `on_exit` に |
| `tests/test_oss_ops.py` | A の check 1 つ、B の check 2 つ（INT / TERM） |
| `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl` | C。`SuccessExitStatus=75` とコメント |
| `IaC/terraform/aws-managed/pipeline/stream/kafka_ui.tf` | C。コメント 1 か所 |
| `tests/test_stream.py` | C。ユニットの行の検査 |
| `docs/troubleshooting.md`、`docs/pipeline.md`、`docs/architecture/resources/web-ec2.md` | C。75 で止まったときの状態 |
| `IaC/terraform/aws-managed/pipeline/analytics/history.tf` | D。`time_sleep` と Firehose の `depends_on` |
| `IaC/terraform/aws-managed/pipeline/analytics/versions.tf`、`.terraform.lock.hcl` | D。time の provider |
| `tests/test_analytics.py` | D の check |
| `docs/cycles/026-ops-kafbat-firehose-fixes/build.md` | 新規 |

## 再利用するもの

- `ops/oss/up.sh:656-658` の 3 行（A）
- `tests/test_oss_ops.py:898-911` のループと `_on_exit`、偽の `aws` の `FAKE_SSM_PUT_SIGNAL`（B）
- `agent/kb.tf:107-114` の `time_sleep`、`agent/versions.tf` と lock の time の block（D）

## 実装ステップ

1 件 1 commit。順は A → B → C → D（どれも独立）。

1. A: テストを足して落ちるのを見る → `ops/up.sh` を直す → 通す。
2. B: 赤（`_on_exit` 無しで残る）を見て `build.md` に貼る → テストを足す → 通す。
3. C: テストを足して落ちるのを見る → tftpl・tf・docs を直す → 通す。
4. D: テストを足して落ちるのを見る → versions・lock・history を直す → 通す → 両方のルートで `terraform validate`。
5. `bash ops/check.sh` を通し、`build.md` を書く。セルフレビュー（`/robust`）をして、直すかどうかは実装したエンジニアが決める。

## 検証方法（期待出力まで）

1. `uv run --group dev python tests/test_oss_ops.py` が `通過 203 / 失敗 0`（2026-10-09 は 200。A で 1、B で 2 増える）。
2. `uv run --group dev python tests/test_stream.py` が `通過 108 / 失敗 0`（2026-10-09 は 108。既存の check に条件を足すので同数）。
3. `uv run --group dev python tests/test_analytics.py` が `通過 520 / 失敗 0`（2026-10-09 は 519）。
4. `bash ops/check.sh` が最後まで通り、`すべて通過` で終わる（terraform validate を全ルートで含む）。
5. `terraform -chdir=IaC/terraform/oss/pipeline/analytics init -backend=false -lockfile=readonly` が成功する（lock に time がある）。
6. `grep -c "^SuccessExitStatus=75$" IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl` が `1`。
7. `grep -n "failed のまま" IaC/terraform/aws-managed docs/pipeline.md docs/architecture/resources/web-ec2.md -r` が 0 行（`docs/troubleshooting.md` は経緯として残るので見ない）。
8. ほかのテスト（test_agentcore 161、test_graph 78、test_sync 103、test_workflow 327、test_alerts 168、test_kb_index 7、test_lab_debug 110、test_nautobot 68、test_oss 174、test_oss_roll 66、test_local_compose 138、test_collectors 79、test_dashboard_config 3）は件数が変わらず、失敗 0。

## 未確定事項とリスク

1. **C は AWS で未確認。** `SuccessExitStatus=75` と `RestartPreventExitStatus=75` と `Restart=always` を並べたとき、75 で `inactive (dead)` になり `degraded` にならないことは systemd の文書から推した。PM が AWS の確認で `systemctl is-system-running` と `systemctl show -p ActiveState,Result <prefix>-kafka-ui` を見る。
2. **C は次に base/core を apply すると Web の EC2 を作り直す**（`user_data_replace_on_change = true`）。いま AWS に何も立てていないので影響は無い。
3. **D の 30 秒は推定。** IAM の反映遅れの長さは決まっていない。AWS の確認で通らなければ延ばす。1 回通っても、遅れが短かっただけの可能性は残る。
4. **D は既に作ってある環境で 1 回 30 秒待つ**（`time_sleep` を新しく作るため）。Firehose は作り直さない（`depends_on` の変更は置き換えにならない）。plan で確かめる。
5. **D の lock の block を手で写す。** hash が合わないと init が落ちる。aws-managed 側は `-lockfile=readonly` を付けずに init して、lock が書き換わらないこと（`git diff` が写した block だけ）を確かめる。
