# lab の小さな直しを片付ける（024）のレビュー

## Round 1

cold reviewer（opus、general-purpose）に依頼した。レビューのモデル: opus 5.5。

# cold review: lab の小さな直しを片付ける（024）r01

対象: `origin/feat/024-lab-small-fixes`（比較元 `origin/main`）。設計: `docs/cycles/024-lab-small-fixes/design.md`。

## サマリ

Must fix は 0 件。A・B・C・E・F・H・I・J・K・G は design.md の「直し方」の順と範囲どおりに入っている。テストと `ops/check.sh` は自分で打って全部通った。

Should fix は 1 件。I の「事実」と「直し方」に、デバッグ用 Telegraf と stream の Telegraf が「同じ repo」とある。実装はこれが誤りだと見つけて、コードとコメントを「別の repo」に直した。ところが仕様の正本の design.md は直っていない。

Nit は 4 件。

- 手元の `pull` と README の書き方
- S3 の `--exclude` と `--delete` の関係
- `peer()` を pipefail の下で使うこと
- テストの書式

自分で打ったもの（出力の末尾）:

- `uv run --group dev --group web python tests/test_lab_debug.py`: 通過 110 / 失敗 0
- `tests/test_local_compose.py`: 通過 138 / 失敗 0
- `tests/test_stream.py`: 通過 106 / 失敗 0
- `tests/test_analytics.py`: 通過 515 / 失敗 0
- `tests/test_oss_ops.py`: 通過 200 / 失敗 0
- `tests/test_oss.py`: 通過 174 / 失敗 0
- `bash ops/check.sh`: 最後が「すべて通過」。中で terraform validate が走り、5.（netops）も通る。

### 見た観点 / 見ていない観点

見た観点:

- 設計との整合: A〜K の各「直し方」と diff を 1 つずつ照らした。検証 5 の grep（`git ls-files` から docs/cycles と docs/verification を除き、multitool が 0 本）を確かめた。
- 正しさ:
  - `lab.sh` route の `|| true`
  - journalctl のヒント
  - render / pull の 2 イメージ化
  - `docker/compose/lab.sh` のガードを up / render だけにしたこと
  - `kafka_load.sh` の peer と payload
  - `lab-debug.sh` のタグ
  - CFn からの multitool の除去
  - Terraform の `lab_repositories` / outputs / variables / tftpl
  - `upload_lab` の exclude
- docs の数の整合: ECR は 15 から 14。`ecr.md` の表の行数を数えて 14 だった。「渡すのは 4 つ」も見た。
- テスト: red→green の形になっているか。multitool が戻らないことを見る check の範囲。

見ていない観点（ここでは動かしていない）:

- AWS の apply。ECR の multitool repo の destroy。`user_data_replace_on_change` による lab の EC2 の作り直し。
- 実際の `aws s3 sync --exclude` の挙動。
- 本物の containerlab / docker / sr_cli / kafka-producer-perf-test。

これらは AWS と実機が要るので、ここでは扱わない。build.md の主張は根拠に使っていない。

## Must fix

None

## Should fix

- [設計との整合] `docs/cycles/024-lab-small-fixes/design.md:81`, `:147`, `:148`, `:149`, `:238`
  - design.md は次のように書いている:
    - stream の arm64 の Telegraf が「同じ repo `<prefix>-telegraf`」に置かれて、デバッグ用の amd64 と上書きし合う（:81）。
    - log と CFn の Description は「same repository」と書く（:147-149）。
    - 次回は `<prefix>-telegraf:<ver>-<hash>-amd64` をビルドし直す（:238）。
  - 実装は逆の前提で書いてある:
    - デバッグ用の repo は `$REPO_PREFIX-telegraf`（= `<prefix>-debug-telegraf`）。
    - `ops/lab-debug.sh:128` の log と `IaC/cloudformation/lab-debug.yaml:76` の Description は「its own repository `<prefix>-telegraf`」、つまり別の repo と書いている。
    - `tests/test_lab_debug.py:103` の check 名も「別の repo」。
  - 実装の方が事実として正しい（build.md にも逸脱として残っている）。それでも仕様の正本の design.md が、コードと食い違う事実と動機を持ったまま残る。
  - 動く上の害は無い。タグに `-amd64` が付いても、別の repo なので衝突は元から起きない。
  - 分類の理由: 正本が誤った前提を書き続けると、次に読む人が「同じ repo で上書きが起きる」と誤解するので Should fix にした。動作は壊れないので Must ではない。

## Nit

- [正しさ] `docker/compose/README.md:84`, `docker/compose/lab.sh:16-17`, `app/containerlab/lab.sh:166`
  - README は「`down` / `check` などはイメージを見ないので打てる」と書いている。
  - ところが `pull` もガードの外にある。`.env` にイメージが無いと、`for i in "$SRLINUX_IMAGE" "$TREX_IMAGE" …; do docker pull -q "$i"` が空文字の `docker pull -q ""` を打ち、docker の分かりにくいエラーで止まる。
  - ガードを up / render に限ったのは design.md の C のとおり。直すとしたら README の「など」から `pull` を外すか、`pull` もガードに入れる。
  - 分類の理由: design どおりで、失敗しても止まるだけで害が無いので Nit にした。
- [データ損失・運用] `ops/lab-common.sh:100`, `IaC/terraform/aws-managed/pipeline/lab/outputs.tf:28`, `IaC/terraform/aws-managed/pipeline/lab/templates/lab_user_data.sh.tftpl:33`
  - `--exclude "clab-*/*"` を足したので、`--delete` は除外したパスを消さない（aws s3 sync は exclude に当たる宛先側のオブジェクトを削除の対象にしない）。
  - そのため、この直しの前に S3 へ上がった `lab/clab-splab/` は残る。EC2 側の `aws s3 sync --delete s3://…/lab/ $LAB/src/` には exclude が無いので、残ったものは lab の EC2 へ降りる。
  - バケットが down.sh で毎日消えるなら実害はほぼ無い。
  - 分類の理由: 一度きりの残りで、次の up からは出ないので Nit にした。
- [ランタイム] `app/containerlab/trex/kafka_load.sh:40`
  - `peer()` は `sed … | head -1` で、`set -o pipefail` の下で呼ばれる。`head` が先に閉じると、`sed` が SIGPIPE で 141 を返しうる。
  - その場合は `p=$(peer …) || p=""`（:56）で空になり、「peer が無い」で exit 1 に落ちる。
  - `.cli` は小さく、sed の出力は 1 回でバッファを抜けるので、実際に起きる見込みは低い。`sed -n '…/p;q'` にするか、awk で最初の 1 件だけ出すと確実になる。
  - 分類の理由: 起きる条件が実質ないので Nit にした。
- [書式] `tests/test_lab_debug.py:610`
  - `_lab_out =read(...)` の `=` の後ろに空白が無い。
  - 分類の理由: 動作に関係しない書式なので Nit にした。

## 良かった点

- 各直しに、直す前は落ちて直した後に通る形の check が付いている。
  - route の `|| true`、journalctl のヒント、render の 2 プレースホルダと TREX 欠落で止まること
  - kafka_load の nodes / peer / payload、`down` が `SRLINUX_IMAGE` 1 行の `.env` で sudo を 1 回だけ打って通ること
- multitool が戻らないことを `git ls-files` の全体走査で見張る check がある。check 自身に引っかからないよう `"multi" + "tool"` と分けて書いてある。
- ECR の repo 数（14）、「渡すのは 4 つ」、各 docs の数が全部そろっている。design.md の検証 5 に、EC2 の作り直し（`user_data_replace_on_change`）の注記も足してある。
- I で設計の誤り（同じ repo）を実装側で見つけ、コード・CFn・テスト名を事実に合わせて、build.md に逸脱として残した。
- `kafka_load.sh` は、`.cli` が無い・読めないときに set -e で黙って落ちず、理由を出して止まる。
- `_lab_graph` のテストは `SRLINUX_IMAGE` / `TREX_IMAGE` を環境から外す。手元の環境に左右されない。

## ユーザーへの質問

- I の design.md の「同じ repo」の記述（:81, :147-149, :238）を、このサイクルの中で事実（別の repo）に直しますか。それとも build.md の逸脱の記録だけで済ませますか。

### PM の判定

- **Must fix 0。**
- **Should fix 1 → 直した**（この review.md と同じ commit）。
  - design.md の I の「同じ repo `<prefix>-telegraf`」という事実と動機（:81、:147-149、:238）を、実装で見つけた事実に書き換えた。
    - デバッグ用は別の repo `<prefix>-debug-telegraf`。
    - 衝突は元から起きない。
    - タグに `-amd64` を付けるのは見分けのため。
  - 上の「ユーザーへの質問」は、これで解決した扱いにする。
  - あわせて、セルフレビューで残していた `docs/development.md:61` のテストの件数を直した（`test_lab_debug` 97 → 110、`test_oss_ops` 199 → 200）。
- **Nit 4 → 直さない。**

根拠:

- reviewer が 6 本のテストと `ops/check.sh` を自分で回して、通った（件数は上のとおり）。
- PM は diff を読んで、設計の A〜K と照合した（読んだだけ）。
- AWS に依る項目は未確認で、次の AWS 検証で見る。
  - ECR の multitool の repo の削除
  - EC2 の作り直し
  - `aws s3 sync --exclude`

直したあとのテスト結果（実装者が打った出力）:

```
$ uv run --group dev --group web python tests/test_lab_debug.py
通過 110 / 失敗 0
$ uv run --group dev --group web python tests/test_oss_ops.py
通過 200 / 失敗 0
$ bash ops/check.sh
…
すべて通過
```
