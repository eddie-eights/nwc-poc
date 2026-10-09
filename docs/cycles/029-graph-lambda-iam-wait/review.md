# graph の status Lambda を IAM の伝播を待って作る（029）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した）。確認は PM（fable-5-1）。実装は opus（`build.md`）。

### cold reviewer の結果

## サマリ

対象は「graph の status Lambda を IAM の伝播を待って作る（029）」。`4d3d071..HEAD` の 4 commit のうち、コードとドキュメントの 6 ファイル（+65 / -7 行。`build.md` は対象外）を、`design.md` と照らして読んだ。

- `time_sleep.status_iam` の形（30s、`triggers` はロールの `unique_id`、`depends_on` はロールとポリシー）と、Lambda の `depends_on` は、design の A-2 / A-3 と一字一句同じ。手本の analytics（`history.tf` の `alert_firehose_iam`）とも同じ形。
- OSS 版の `sync.tf` はリンクではなく実ファイルだった（`ls -la` で確認）。design のリスク 3 に従って同じ変更が入っている。
- `versions.tf` と lock は、OSS 版からマネージド版へのリンク。だから 1 か所の変更で両方に効く。
- 総評: 設計どおりで、壊れる箇所は見つからなかった。Must fix は 0。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - A-1〜A-6 を 1 項目ずつ diff と照合した。
  - design との差は 2 つ。どちらも design のリスク 2 / 3 に書かれた範囲に収まる。
    - lock は `init -upgrade` ではなく、analytics の lock のブロックを写した。
    - OSS 版の `sync.tf` にも同じ変更を入れた。
  - 「やらないこと」が守られていることも確認した。`ops/up.sh` の apply の 2 回打ち、Lambda の中身、IAM のポリシーには変更が無い。
- **correctness**
  - lock の `hashicorp/time` のブロックが analytics の lock と同一であることを確認した。両方のファイルから `awk` でブロックを抜き出して `diff` し、差分は無かった。
  - terraform の静的な検査を、両方のルートで通した。手順は次のとおり。
    - worktree の `.terraform/` には `time` が入っていなかった。worktree を汚さないよう、graph の 2 ルートと参照先の `app/` のファイルを scratchpad へ複製した。
    - 複製の上で `terraform init -backend=false -input=false -lockfile=readonly` を打った。両方とも `Installed hashicorp/time v0.14.2` が出て、初期化に成功した。
    - 続く `terraform validate` は、両方とも `Success! The configuration is valid.` だった（Terraform v1.16.0）。
    - readonly で通ったので、この PC のハッシュは lock にある。
  - `terraform fmt -check -recursive`（2 ルート）は rc=0 だった。
  - ロールの作り直しと権限の伝播を、次のように追った。
    - ロールを作り直すと、`triggers` で `time_sleep` が replace される。Lambda は `depends_on` があるので、その後でロールの ARN を in-place で更新する。
    - 既存の環境への apply では、`time_sleep` が 1 回作られて 30 秒待つだけになる。`depends_on` が変わっても Lambda は replace されない。
    - destroy の順序は、029 の前と変わらない（Lambda を消してから `time_sleep`、そのあとポリシー）。
- **runtime bugs**
  - `ops/up.sh` / `ops/oss/up.sh` の init を確認した（`ops/common.sh` の `tf_init_root`）。OSS は `-lockfile=readonly` で打つが、lock に `time` があるので止まらない。
  - `-target` / `-replace` で graph の一部だけを apply する箇所が無いことを grep で確認した。
- **missing tests**
  - `uv run --frozen python3 tests/test_sync.py` は `通過 104 / 失敗 0` だった。design の期待（103 → 104）と一致する。
  - `tests/test_oss.py` は `通過 174 / 失敗 0`、`tests/test_analytics.py` は `通過 528 / 失敗 0` で、件数は変わっていない。
  - 足された check の正規表現は、scratchpad の使い捨てスクリプトで変異を入れて確かめた。両方の `sync.tf` で、次の変異はすべて False になる（落ちる）。
    - Lambda の `depends_on` から `time_sleep.status_iam` を消す。
    - `create_duration` を 60s にする。
    - `triggers` を `.arn` にする。
    - `depends_on` を Lambda から購読（subscription）の側へ移す。
  - 元のファイルは True になる。
- **security / data loss / API compatibility / type safety**
  - IAM のポリシーと Lambda の中身に変更は無い。外部入力も state の操作も無い。
  - provider を 1 つ足しただけで、出力や変数のインターフェースは変わっていない。該当なし。

見ていない観点:

- **AWS での実地の動作**（30 秒で足りるか、`time_sleep.status_iam: Creation complete after 30s` のあとで Lambda が `State=Active` になるか）は確かめていない。design でも PM が 1 回打つ範囲で、実装の範囲外。
- **Linux など別の OS・CPU での init** は確かめていない。lock の `h1:` のハッシュは 1 つだけで、analytics の lock と同じ状態。この PC 以外で OSS 版を readonly で init すると止まりうるが、これは 029 の前からある性質（`ops/common.sh` の案内どおり）で、029 で増えたものではない。
- **worktree の中での `terraform validate`** は打っていない。worktree を汚さないためで、上の複製で代わりに確かめた。

## Must fix

None

## Should fix

None

## Nit

- [design.md との整合性（ドキュメント）] `docs/architecture/resources/sns-sqs-lambda.md:16` に IAM の反映待ちの説明が無い。
  - 構成の資料の `<prefix>-graph-status` の行は、Lambda の構成を説明しているが、作成時に IAM の反映を 30 秒待つことには触れていない。
  - analytics の側では、026 の cold review の Nit 3 を受けて、`docs/architecture/resources/firehose.md:64` に `time_sleep.alert_firehose_iam` の説明を足している（BACKLOG の 132 行）。graph でも同じ扱いにするなら、1 行足すと両方の資料がそろう。
  - Nit とした理由: design の変更対象に入っていない。また `docs/troubleshooting.md` には書いてあるので、止まった人が辿れなくなることは無い。
- [体裁] `docs/troubleshooting.md:31` の表の行が、同じ案内を 2 回書いている。
  - 「表の下の `graph-status の作成`」への案内が、1 つのセルの中に 2 回出てくる。行が長いので、「30 秒では足りないときに延ばす」と「その場で打ち直す」の 2 つの案内を、表の下の箇条にまとめると短くなる。
  - Nit とした理由: 内容に誤りは無く、読み手が迷うことも無い。

## 良かった点

- 手本（analytics の `alert_firehose_iam`）と、形もコメントの書き方も揃っている。後で `create_duration` を延ばすときに、比べて読みやすい。
- 「`versions.tf` と lock はリンクだが、`sync.tf` は実ファイル」という OSS 版の非対称を実物で確かめ、`sync.tf` に同じ変更を入れ、check も両方のファイルを見るようにしている。片方だけ直って片方が古いまま、という取りこぼしを防いでいる。
- lock を `-upgrade` で作り直さず、analytics のブロックを写した。aws / archive の版が上がらず、lock の diff が `time` の追加だけに収まっている（design の検証方法と合う）。
- check は文字列の有無だけでなく、Lambda のブロックの `depends_on` の行に限って縛っている。購読の側の `depends_on` と取り違えても通らない（変異で確認）。
- troubleshooting に、待ちを延ばすときに一緒に直す 3 か所（2 つの `sync.tf` と `_iam_wait`）が書かれている。

## ユーザーへの質問

None

### PM の確認

- Must fix 0 / Should fix 0 なので、Round 1 が完了判定のラウンドを兼ねる。cold reviewer の 2 回目は同じ diff への同じ依頼になるので呼ばない。
- `git diff 4d3d071 bb803c2`（6 ファイル）を PM も読んだ。設計 A-1〜A-6 との差は build.md の 3 点（lock は写した、OSS 版 sync.tf は実ファイル、troubleshooting の表の下も直した）で、いずれも設計のリスクに書いた範囲。
- 取り直したテスト: `uv run --frozen python3 tests/test_sync.py` → `通過 104 / 失敗 0`。`tests/test_analytics.py` → `通過 528 / 失敗 0`。
- Nit 1: PM が `docs/architecture/resources/sns-sqs-lambda.md:16` に `time_sleep.status_iam` のあとに作ることを 1 文足した。Nit 2（troubleshooting の表のセルの重複した案内）: 直さない。
- AWS で 30 秒が足りるかは未確認。この後の 1 回の `ops/up.sh` で確かめて `docs/verification/` に書く。
