# コレクターごとに SCRAM のユーザーを分ける（031）のレビュー

## Round 1

実行モデル: cold reviewer は opus（初回ビルド直後に依頼した。Must fix 0 なので完了判定のラウンドも兼ね、2 回目は呼ばない）。確認は PM（fable-5-1）。実装は opus（build.md）。

対象: `475840b..4785d3c`（origin/feat/031-scram-per-collector。030 の 475840b の上。PR の前に origin/main を取り込んだ）。

### cold reviewer の結果

# cold review r01: コレクターごとに SCRAM のユーザーを分ける（031）

## サマリ
`git diff 475840b HEAD`（HEAD `4785d3c`、5 commit、28 ファイル、+457 / -151）を `docs/cycles/031-scram-per-collector/design.md` と突き合わせた。中身は ops（secret を 3 本作って消す・鍵を 1 回だけ消す）、Terraform（data source の for_each、ECS の secrets の map、IAM、association）、Spark の ACL（`SCRAM_USERS`）、案内の文言、テスト 4 本、docs。

設計の A / B / C はどれも設計どおりに入っていて、設計に無い変更（OSS 版 `kafka.tf` の空の map、`FAKE_SM_DESCRIBE_DENY`、docs の追加箇所、troubleshooting の「031 をまたぐ」）は build.md の「設計との差」に理由つきで書かれ、どれも必要か無害。テスト 4 本と terraform validate（managed / OSS）は手元で緑。Must fix は無い。残りは、設計の範囲の外に残る「ACL の無いトピックには誰でも書ける」穴と、実行ロールが 3 本とも読める点の 2 つ（どちらも docs に書いてあり、今回のマージを止めるものではない）。

### 見た観点 / 見ていない観点

見た観点:
- design.md との整合性: A-1〜A-4、B、C の各項目を diff と照合した。secret 名・username・Description・up.sh の 3 行（for を使わない）・down.sh の 3 回と `delete_msk_scram_key` 1 回・msk.tf の `scram_collectors` / for_each / `kafka_collector_secrets` の map / IAM の Resource / association・collectors.tf と gnmic.tf の添字・コメント 3 箇所・die の文言 2 つと conf.in のコメント・`SCRAM_USERS` / `ensure_acls` / `made` の形 / main のログ。どれも設計どおり。
- correctness: `ops/down-common.sh` の `delete_msk_scram` / `delete_msk_scram_key` の分岐（stream が残った・delete 失敗・describe 失敗・NotFound）と `MSK_SCRAM_KEEP_KEY` の流れ、`ensure_msk_scram_secret` の引数なし時の停止（`${1:?}`）、username を python の argv で渡す経路、syslog-ng.sh の文字チェック（`syslog-ng` の `-` は許可文字に入る）を読んだ。
- security: secret の値が画面・argv に出ないこと（テストで縛られている）、ECS のタスクに自分の secret だけが入ること、IAM の範囲、Kafka の ACL の残る穴（下の Should fix）。
- runtime bugs: `bash -n ops/up-common.sh ops/down-common.sh ops/up.sh ops/down.sh app/gnmic/gnmic.sh app/syslog-ng/syslog-ng.sh` → OK。Terraform は TF_DATA_DIR を scratchpad に向けて `init -backend=false -lockfile=readonly` のうえ `terraform -chdir=IaC/terraform/aws-managed/pipeline/stream validate` と `-chdir=IaC/terraform/oss/pipeline/stream validate` → どちらも `Success! The configuration is valid.`（OSS 版はシンボリックリンクの collectors.tf / gnmic.tf が `["syslog-ng"]` の添字で map を引くので、`kafka.tf` の空 map が要ることを確認）。
- data loss: down で 1 本でも消せない・確かめられないときに鍵を残す（残った secret が復号できなくなるのを防ぐ）ことを確認。旧来の「secret を消せなかったら return で鍵を残す」と同じ意味を、3 本に広げても保っている。
- API compatibility: `kafka_collector_secrets` の型を list → map に変えたので参照元を全部 grep した（`collectors.tf:80,172`、`gnmic.tf:92` の 3 箇所だけで、全部添字つき）。`ensure_msk_scram_*` / `delete_msk_scram` の呼び手は `ops/up.sh` / `ops/down.sh` だけ。`SCRAM_USER` / `SCRAM_TOPICS` の参照は残っていない。
- missing tests: 下記コマンドで実測。
  - `uv run --frozen python3 tests/test_stream.py` → `通過 112 / 失敗 0`
  - `uv run --frozen python3 tests/test_oss.py` → `通過 174 / 失敗 0`
  - `uv run --frozen python3 tests/test_oss_ops.py` → `通過 206 / 失敗 0`
  - `uv run --frozen python3 tests/test_analytics.py` → `通過 529 / 失敗 0`
  - `grep -rn "AmazonMSK_.*-collectors\|User:collectors\|SCRAM_USER\b" app ops IaC docs tests CLAUDE.md | grep -v "docs/cycles/\|docs/verification/"` → 0 件

見ていない観点:
- AWS 上の動的確認（association の in-place 更新か replace か、MSK がハイフン入りの SCRAM ユーザー名と 3 本の secret を受けるか、ACL が実際に効いて User:syslog-ng が flows に書けないか、Topic authorization failed が出ないか）。指示どおり AWS は呼んでいない。設計でも PM の範囲。
- `terraform plan`（state も AWS も無いので打っていない）。
- `test_nautobot` などほかのテストファイル（今回の変更と無関係として対象外。build.md には全件の結果が載っているが自分では打っていない）。
- type safety は Python の変更が定数と内側のループだけで、型注釈の対象が無い。

## Must fix
None

## Should fix

- `[security]` ACL の無いトピック（`traps`）と、Spark が ACL を入れる前は、どの SCRAM ユーザーも書ける穴が残る。`IaC/terraform/aws-managed/pipeline/stream/msk.tf:124-125`（`auto.create.topics.enable=true`）、`docs/pipeline.md:68,155`（2026-10-09 に Spark を起こさず `metrics` に書けた = `allow.everyone.if.no.acl.found` が効いている、と書いてある）。
  - 破綻シナリオ: syslog-ng のタスクが乗っ取られて `KAFKA_SASL_USER=syslog-ng` の資格情報が漏れると、ACL の無い `traps`（Telegraf が IAM で書くトピック）に偽のトラップを書け、アラートの経路に入る。`auto.create.topics.enable=true` なので、ACL の無い新しいトピック名でも書ける見込み（推測。CLUSTER の CREATE も ACL が無ければ通る、という Kafka の既定の振る舞いから）。設計の背景（「1 つのコレクターが乗っ取られると他のトピックにも書ける」）が閉じるのは 4 トピックの範囲だけ。
  - 分類理由: 031 より前からある穴で、design.md の範囲（4 トピックの ACL をユーザーごとに絞る）は満たしており、`docs/pipeline.md:155` に推測として書いてある。今マージしても新しく壊れるものは無いので Must ではない。ただ設計の目的に対する残リスクなので、BACKLOG に「ACL の無いトピックに SCRAM ユーザーが書けないようにする」（例: `traps` に SCRAM ユーザーの DENY を入れる、または `allow.everyone.if.no.acl.found=false` にしたとき IAM の口が影響を受けないか確かめる）を足すのを勧める。

## Nit

- `[security]` 3 つの実行ロールが 3 本の secret を全部読める。`IaC/terraform/aws-managed/pipeline/stream/msk.tf:41-47`（`kafka_collector_execution_statements` の `ScramSecret` の Resource が 3 本の ARN）を `collectors.tf:258,281` と `gnmic.tf:183` が共有している。
  - 設計（A-3 の「IAM の `ScramSecret` の Resource を `[for c in local.scram_collectors : …]` に」）どおりで、`docs/data-stores.md:556-557` にも書いてある。実行ロールの資格情報はコンテナの中には渡らないので、設計の想定（コレクターのコンテナが乗っ取られる）では効かない。効くのは、そのロールを渡してタスク定義を登録できる人（`iam:PassRole` を持つ人。もともと強い権限）だけ。
  - 分類理由: 設計どおりで、今の脅威モデルでは誰も困らない。ロールごとに自分の 1 本だけにする（`kafka_collector_execution_statements` も collector → statements の map にする）と、最小権限が IAM でもそろう、という好みの範囲。
- `[missing tests]` `app/syslog-ng/syslog-ng.sh:50` の die の文言（`AmazonMSK_<接頭辞>-syslog-ng`）を縛るテストが無い。gnmic 側は `tests/test_stream.py:437-438` が縛っている。
  - 分類理由: 文言だけで動作に効かず、031 より前も syslog-ng 側は縛っていなかった。
- `[design.md との整合性]` 検証の件数で `test_stream` が設計時点「不明」→ 112、`test_oss_ops` が 203 → 206、`test_analytics` が 528 → 529。足した check の数とつじつまは合う（analytics は「ほかのコレクターのトピックには ACL を入れない」の 1 件）。記録のみ。

## 良かった点

- `delete_msk_scram_key` を分けたうえで、`MSK_SCRAM_KEEP_KEY` で「1 本でも消せない・確かめられないときは鍵を残す」を保ったこと。3 本に広げたときに鍵だけ消えて残りの secret が復号できなくなる、という一番まずい壊れ方を閉じている。しかも 1 本だけ delete が落ちる（`FAKE_SM_FAIL=<名前>`）と 1 本だけ describe が落ちる（`FAKE_SM_DESCRIBE_DENY`）の 2 つの枝をテストで縛った。
- 名前の一覧（`msk.tf` の `scram_collectors`、`ops/up.sh` の 3 行、`ops/down.sh` の 3 行、`SCRAM_USERS` のキー）が同じ順で揃うことを `tests/test_stream.py` と `tests/test_analytics.py` が機械的に見ているので、どれか 1 か所にコレクターを足し忘れると落ちる。
- タスク定義が自分のキーを 1 回ずつだけ引くこと（`_ks_refs == ['["syslog-ng"]', '["goflow2"]', '["gnmic"]']`）と、ACL のペアの集合が完全一致すること（他人のトピックに ACL が無い）を、否定形まで含めてテストにしている。
- 3 本のパスワードが互いに違うこと、値が出力にも argv にも出ないことを縛っている。
- OSS 版がシンボリックリンクで同じ collectors.tf / gnmic.tf を読むことに気づいて、空の map を合わせ、validate を両方打っている。
- 031 をまたいで作った stream を消すときに止まる件と、古い共有 secret を down.sh が消さない件を troubleshooting に先回りで書いている（設計の「未確定 2」の帰結と一致）。

## ユーザーへの質問

- Should fix の `traps` などの ACL の無いトピックの穴を、031 の範囲外として BACKLOG に積むか、031 の追加の作業にするか。設計の背景の文（「他のトピックにも書ける」）をどこまで閉じるつもりかで決まる。


### PM の確認

**再現したもの**

- Should（ACL の無いトピックにどの SCRAM ユーザーも書ける）: `IaC/terraform/aws-managed/pipeline/stream/msk.tf` の `auto.create.topics.enable=true` と、`docs/pipeline.md` の `allow.everyone.if.no.acl.found` の記述を読んだ。ACL を張るトピックは logs / flows / metrics の 3 つで、`traps` には ACL が無い。031 より前（`User:collectors` が 1 本だったとき）から同じ穴で、031 の設計の範囲外。読んだだけで、MSK では確かめていない。
- テスト（PM、origin/main を取り込んだあと）: 下の「テスト実測」。

**直したもの**

- origin/main（029 / 030 / 032 / 033 / 034）を取り込んだ（`git merge origin/main`。自動で解けた。test_stream / test_oss / test_analytics / troubleshooting.md が両側で変わっていた）。

**直さなかったもの（最終報告へ）**

- Should（traps の穴）: 031 の範囲外。BACKLOG に「ACL の無いトピックに SCRAM ユーザーが書けないようにする」を足す（cold reviewer の質問への答え）。
- Nit 1（3 つの実行ロールが 3 本の secret を全部読める）: 設計どおり（build.md の R2 と同じ）。最小権限にするのは BACKLOG。
- Nit 2（`app/syslog-ng/syslog-ng.sh` の die の文言を縛るテストが無い）: missing tests だが文言だけなので据え置き。
- Nit 3（件数の記録）: 下の実測で更新。
- build.md の未解消 R5（031 より前の共有 secret `AmazonMSK_<prefix>-collectors` を down.sh は消さない）: troubleshooting.md の手で消す案内のまま。AWS の検証で残っていれば手で消す。

**未確認**

- AWS: association が in-place で 3 本、3 タスク RUNNING、Spark のログの ACL 行、コレクターのログに `Topic authorization failed` が無いこと、down.sh で secret 3 本が消えること、ハイフン入りの SCRAM ユーザー名（`syslog-ng`）が MSK で通ること。PM が AWS の 1 回の検証で見る。
- `terraform plan`。

**テスト実測（PM、origin/main 取り込み後 1d8b0c2）**

- `uv run --frozen python3 tests/test_stream.py` → `通過 112 / 失敗 0`
- `uv run --frozen python3 tests/test_oss.py` → `通過 177 / 失敗 0`
- `uv run --frozen python3 tests/test_oss_ops.py` → `通過 206 / 失敗 0`
- `uv run --frozen python3 tests/test_analytics.py` → `通過 535 / 失敗 0`
- `bash -n` ops/up.sh / down.sh / up-common.sh / down-common.sh / app/syslog-ng/syslog-ng.sh / app/gnmic/gnmic.sh → 無言
- `terraform validate -no-color`（`IaC/terraform/aws-managed/pipeline/stream` と `IaC/terraform/oss/pipeline/stream`）→ 両方 `Success! The configuration is valid.`

