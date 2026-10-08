## サマリ

対象は `git diff eededf8 eceb91b` の 13 ファイル（+918 / -53）。中身はコード 3 本（`web_user_data.sh.tftpl`、`base/ecr/outputs.tf`、`pipeline/stream/kafka_ui.tf` のコメント）、テスト 1 本（`tests/test_stream.py`）、文書 6 本、サイクルの記録 3 本。

設計（design.md）の 6 行（71・72・73・74・75・78）は、どれも設計どおりに入っている。

- 行 72: Docker と Kafbat UI の節を関数 `kafka_ui_setup` にまとめ、各行を `|| return 1` で繋ぎ、`|| echo … >&2` で呼んでいる。
- 行 75: `param()` が `(ParameterNotFound)` なら 75、それ以外は 69 を返す。ユニットに `RestartPreventExitStatus=75` と `.err` を消す `ExecStopPost` を足し、Web のユニットに `Wants=` を足している。

`set -e` が `||` の左で呼ぶ関数の中では効かないこと、コマンド置換のサブシェルからの `exit` が `|| exit $?` で伝わること、値を標準出力、エラーを `.err` に分けていることを、コードを読んで確かめた。correctness・security・data loss のどれにも、マージを止める問題は見つからなかった。

指摘は、`Wants=` で入った「止めても Web の再起動で起きてしまう」挙動が文書に無いこと（Should 1 件）と、細かい差（Nit）だけ。

### 見た観点 / 見ていない観点

**見た観点**

- **design.md との整合性**
  - 変更対象ファイルの表と実装ステップ 1-1〜1-5・2・3・4 を、diff と照らした。
  - 追加された文書 2 本（`docs/architecture/resources/web-ec2.md`、`ssm-parameter-store.md`）は design.md の一覧に無いが、build.md の「設計から逸脱した点」に書いてある。
  - 触らないと決めた `ops/up.sh`・`oss/ops/up.sh` は `eededf8..eceb91b` の diff に入っていない（`git diff --stat` で確認）。
- **correctness**
  - 描いた後の user_data（`git show eceb91b:…/web_user_data.sh.tftpl`）を 1 行ずつ追った。
  - `param()` の各分岐、`IMAGE=$(param image) || exit $?` の伝わり方、heredoc の後ろの `|| return 1` の構文、関数の最後の `systemctl enable --now` の戻り値、`$${@:2}` のエスケープを確かめた。
  - `ops/up.sh` の 4-4（reboot。stream の apply より前）と 8-3（`if [ -z "$SKIP_STREAM" ] || …`）の位置、`oss/ops/up.sh` の 7-5（`if` の外で、`tf_apply pipeline/stream` より後）の位置を読んで、起こし直しの流れを確かめた。
- **security**
  - `.err` が `umask 077` の後に作られること。
  - `.err` に入るのは AWS CLI の標準エラーだけで、値は標準出力に分かれていること。
  - パスワードを echo する行が無いこと。
  - `.err` が `ExecStopPost` で消えること。
- **runtime bugs**
  - 次のどれでも Web が巻き込まれないこと: Docker の節の各段が失敗したとき、`Wants=` の先のユニットが無いとき、`docker.service` が起きないとき。
  - systemd の実測は build.md の検証 4 の生ログ（a・b・c・d）を読んだだけで、自分では再実行していない。
- **data loss**
  - `aws s3 sync --delete` は位置が変わっただけで、引数は同じ。
  - env と `.err` の `rm -f` は `/run` の自前のファイルだけを消す。
- **API compatibility**
  - `kafka_ui_repository_url` は description だけの変更で、値は変わらない。
  - `kafka_ui.tf` はコメントだけの変更。
  - `.err` のパスは新設。
- **missing tests**
  - `uv run --group dev --group web python tests/test_stream.py` を HEAD（750266d。`eceb91b..HEAD` の test_stream.py の差は 3 行）で自分で走らせた。最後の行は `通過 88 / 失敗 0`。
  - 追加された分岐ごとに検査があることを確かめた。
    - 75 と 69 の分け方: 4 つのパラメータ × pnf / pnf-old / deny / unreachable / nocreds。
    - `kafka_ui_setup` の各段の失敗: 7 通りと成功 2 通り。
    - 部品の並び。
    - Web のユニットの `Wants=`。
    - 8-3 と 7-5 の位置と条件。

**見ていない観点**

- **AWS 上の実機**: cloud-init、実物の SSM・ECR・MSK と、AL2023 の AWS CLI の標準エラーの形。設計にも未確認と書いてある。
- **手元の systemd のコンテナ（検証 4）の再実行**: build.md の記録を根拠にした。
- **`bash ops/check.sh` の全体**: 自分では走らせていない。build.md の記録だけを見た。
- **変異の検査（`self/mut14.py`）の再実行**: スクラッチパッドにあり、渡されていない。
- **type safety**: 対象は shell と Terraform の文字列と Python のテストだけなので、該当なし。
- **010 より前の環境で起きる `DependencyViolation`**（行 73）の実地の確認: AWS が要るので、していない。010 のコミット 3c3e6c0 で、旧 Fargate のサービスがクラスター `aws_ecs_cluster.telegraf`（`<prefix>-telegraf`）にあり、名前が `<prefix>-kafka-ui` だったことだけを確かめた。

## Must fix

None

## Should fix

- [design.md との整合性 + runtime の挙動（文書）] `IaC/terraform/aws-managed/base/core/templates/web_user_data.sh.tftpl:138`（`Wants=${name_prefix}-kafka-ui.service`）と `docs/troubleshooting.md` の Kafbat UI の行
  - **何が起きるか**: `Wants=` によって、Kafbat UI を手で止めても次の Web の起動で起き直す。手で止めるのは、たとえば t4g.medium のメモリを Gradio に空けたいとき。
    - 起き直すのは、`ops/up.sh` の打ち直し（8-3、`WORKFLOW=1` なら 8-6）と、手で打つ `systemctl restart <prefix>-web` のとき。build.md の検証 4 b-1 で、failed や inactive から起きることが実測されている。
    - systemd の仕様では、Web の `Restart=on-failure` による自動の起こし直しも Wants= の先に start ジョブを積むはずだが、これは実測していない。
  - **文書の状態**: この挙動はテンプレートのコメント（:131）にしか書いていない。`docs/troubleshooting.md`・`docs/pipeline.md`・`docs/architecture/resources/web-ec2.md` には「止まっていれば起こす」としか無い。
  - **困ること**: 「止めておきたいなら `sudo systemctl mask --runtime <prefix>-kafka-ui`（戻すときは `unmask`）」に当たる手段が書かれていないので、運用者が止めたつもりの Kafbat UI が黙って戻ってくる。
  - **Should にした理由**: 壊れるのではなく、このサイクルで入った挙動の変化が運用者向けの文書に無いだけだから。Web は巻き込まれない（検証 4 の c で実測済み）。

## Nit

- [design.md との整合性] 設計の検証方法 2 では「Web のユニットに `Wants=x-nwc-poc-kafka-ui.service`（描いた user_data で）」と書いている。実際の検査（`tests/test_stream.py` の `_wunit`）は、描く前のテンプレート（`Wants=${name_prefix}-kafka-ui.service`）を見ている。置き換えは単純な代入なので実害は無いが、設計の文言と違う。直すなら、`_krendered` で `Wants=x-nwc-poc-kafka-ui.service` を見る 1 行を足すか、設計の文言を直す。
- [design.md との整合性] `docs/architecture/resources/web-ec2.md` と `ssm-parameter-store.md` の変更が design.md の「変更対象ファイル」に無い。build.md の逸脱には書いてある。正本の design.md の表にも足しておくと、後から見たときに追いやすい。
- [運用（文書）] このサイクルは user_data を変えている。`web.tf:101` が `user_data_replace_on_change = true` なので、立てたままの環境では次の base/core の apply で Web の EC2 が作り直され、インスタンス ID が変わる。`docs/architecture/resources/web-ec2.md:51` に一般則はあるが、`docs/deploy.md:102` の 010 の注意と並ぶ形の一言が、design.md の「未確定事項とリスク」にも deploy.md にも無い。down.sh で毎回消す運用なら影響は小さい。
- [correctness（残る穴）] ユニットの `RestartPreventExitStatus=75` は、スクリプトの 75 だけでなく、`exec docker run` の後のコンテナの終了コードにも効く。Kafbat UI の JVM が 75 で終わることは考えにくいが、そうなった場合は起こし直さずに止まる。コメントか design.md に一言あるとよい。
- [missing tests（保守性）] 8-3 と 7-5 の検査が、行の位置（`_s75.split("\n")[2]`）と完全一致の文字列に頼っている。また 5 つの変異が `ValueError` で落ちて検出されている（build.md の逸脱 3 つ目、セルフレビューの Nit 9）。どちらも誤検出の側に倒れるだけで、取りこぼしにはならない。
- [体裁] `docs/troubleshooting.md` の Kafbat UI の行が 1 セルに 75 / 69 / setup failed / 8-3 より前で止まった回 / ECR / ホップ数 / パスワード / イメージの入れ替えを詰め込んでいて、長い。表の外に小節を立てて箇条書きにすると読みやすい。

## 良かった点

- 終了コードを 75 と 69 に分ける根拠として、AWS CLI のエラーの形を偽のエンドポイントで実測し、表にしている（254 / 255 / 253。ParameterNotFound と AccessDenied は終了コードでは分けられない）。テストの偽の `aws` も同じ形と古い形（pnf-old）の両方を出す。
- `kafka_ui_setup || echo` の形で `set -e` が効かなくなることを踏まえ、`|| return 1` で 1 行ずつ繋いでいる。テストも各段の失敗で「そこで止まり、後ろの段を打たない」まで縛っている（期待値の表が具体的）。
- `logger` を使わない判断の根拠（AL2023 のイメージに util-linux が無い。無いと `||` の右側が 127 で落ちて user_data が止まる）が設計に書いてある。行 72 の目的に反する落とし穴を避けている。
- `up.sh` に触らず、8-3 / 7-5 の Web の restart と `Wants=` で起こす構成にしている。その restart が stream の apply より後にあって、stream を作る回はいつも通ることを、テストで縛っている。
- `Wants=` が弱い依存で Web を巻き込まないことを、systemd 252.23 のコンテナで 4 通り（SIGKILL、docker の停止、docker が起きない、69 のループ）実測して生ログを残している。

## ユーザーへの質問

- AL2023 の EC2 に入っている AWS CLI でも、標準エラーに `(ParameterNotFound)` が出るかは未確認のまま（design.md の未確定事項）。次に AWS で動かす回に、`SKIP_STREAM=1` で `journalctl -u <prefix>-kafka-ui` に `Not retrying` が 1 回だけ出て `NRestarts=0` になることを見る項目を、検証の手順に入れるか。

## Round 1（PM の確認）

- cold reviewer に依頼した（初回。モデル opus。入力は design.md、`git diff eededf8 eceb91b` の 13 ファイルのパス、書き出し先だけ）。`git status` で増えた新規ファイルは `review-r01.md` 1 本だけ
- Must fix 0 / Should fix 1 / Nit 6
- Should fix（`Wants=` で手で止めた Kafbat UI が Web の start / restart で戻るのに docs に無い）: 読んで確認した（`web_user_data.sh.tftpl:131-138` のコメントと `Wants=` の行にだけ書いてあり、`docs/troubleshooting.md:94`・`docs/pipeline.md:156`・`docs/architecture/resources/web-ec2.md:18` には無かった）。docs だけの直しなので PM が直した: 3 本に「手で止めても Web の start / restart のたびに起き直す。止めたままにするなら `systemctl mask --runtime <prefix>-kafka-ui`（戻すのは `unmask`）」を足した。実測はしていない（systemd の `Wants=` の仕様と build.md の検証 4 b-1 の実測を根拠にした。読んだだけ）
- Nit 6 件は直さず BACKLOG へ（design.md の検証 2 の文言と `_wunit` の差、design.md の変更対象ファイルに web-ec2.md / ssm-parameter-store.md が無い、`user_data_replace_on_change` で Web の EC2 が作り直される一言、コンテナの終了コード 75 にも `RestartPreventExitStatus` が効く、8-3 / 7-5 の検査が行位置と完全一致に頼る、troubleshooting の Kafbat UI の行が長い）
- reviewer の質問（AL2023 の AWS CLI の `(ParameterNotFound)` の形）は、最後の AWS 検証の項目に入れる: `SKIP_STREAM=1` で `journalctl -u <prefix>-kafka-ui` に `Not retrying` が 1 回、`NRestarts=0`
- テスト: Should fix は docs だけなので、関係する `tests/test_stream.py` を走らせ直した（下に出力）
  - `uv run --group dev --group web python tests/test_stream.py` → `通過 88 / 失敗 0`
