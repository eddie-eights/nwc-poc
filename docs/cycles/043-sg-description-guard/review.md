# レビュー: description を変えた SG が残っていれば up.sh を止め、telegraf の SG の説明を揃える（043）

## Round 1（Round 2 の実装 `6129b58` に対して）

- レビュー: PM(fable-5-1) / effort: high。cold reviewer を呼んだ（1 回目。opus / effort: xhigh。初回ビルド直後）。実装モデルは opus-5.5（`build.md` Round 2）
- 対象: `git diff 7d2a8f4..6129b58`（10 ファイル）。テストは `build.md` Round 2 のとおり test_workflow 454 / test_oss_ops 208 / test_stream 114、失敗 0（エンジニアの実行）
- cold reviewer の結果: Must 0 / Should 1 / Nit 4（本文は下に連結）
- 問うてきた点（マネージド版の state に古い runtime の SG が残っていないか）への答え: PM が読み取りで確かめた。`terraform -chdir=…/IaC/terraform/aws-managed/base/core show -no-color | grep -E '^# aws_|^    description'`:

  ```
  # aws_security_group.workload["runtime"]:
      description            = "AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)"
  # aws_subnet.a:  # aws_subnet.b:  # aws_subnet.c:  # aws_vpc.this:
  ```

  マネージド版は新しい文言で止まらない。OSS 版（`IaC/terraform/oss/base/core`）は違う:

  ```
  # aws_security_group.workload["runtime"]:
      description            = "AgentCore Runtime ENIs (terraform/agent)"
  # aws_subnet.a:  # aws_vpc.this:
  ```

### 指摘の確認と分類

1. **Should fix（correctness / 運用の案内。直す）** runtime の SG だけが残った state で、案内どおり `down.sh` を打っても抜けられない。`build.md` Round 2 の指摘 1 と同じ件
   - 再現: 上の OSS 版の state が実物。`ops/down-common.sh:140-150` は Runtime の ENI があると runtime の SG を残す（読んだ）。次の `ops/oss/up.sh` は `check_sg_descriptions` で止まる
   - 根本原因: 設計方針 1・5 の文面が、`down.sh` が runtime の SG を残す場合を想定していない（既存の `internal` の守り `ops/up.sh:592` にはその一言がある）
   - 対処: `design.md` の設計方針 1・5 を直し（違うキーに runtime があれば「ENI が消えてから down.sh」を足す。troubleshooting にも）、エンジニアに Round 3 で実装させる
2. **Nit 1（troubleshooting に「2026-10-08 より前の state は全キーが違う」）** 設計方針 5 に取り込み、Round 3 で一緒に直す（docs の 1 文）
3. **Nit 2（設計に無い振る舞い 2 つ）** 安全側の追加で妥当。`design.md` の設計方針 1 に取り込む（Round 3 の設計で正本に揃える）
4. **Nit 3（`aws_security_group.endpoints` は対象外）** 直さない。設計方針 1 の範囲どおり。最終報告に載せる
5. **Nit 4（検証 5 の grep が 043 の docs 自身に当たる）** `design.md` の検証 5 を `--exclude-dir=cycles` に直す

# コールドレビュー Round 1: description を変えた SG が残っていれば up.sh を止め、telegraf の SG の説明を揃える（043）

対象は `git diff 7d2a8f4..HEAD`（HEAD = 2021e19、実装は 6129b58）と `docs/cycles/043-sg-description-guard/design.md`（Round 2）。

## サマリ

- Must fix は 0 件、Should fix は 1 件、Nit は 4 件。
- 実装は design.md の設計方針 1〜5 に沿っている。
  - 関数 3 つの判定、die の文面、呼ぶ場所、コメント、description 2 つ、docs 3 本が文言まで一致する。
  - 設計に無い追加は 2 つある。コードが空のときの die と、読めない行で止めること。どちらも build.md に記録がある。
- 自分で実行した確認（出力を根拠にする）:
  - `.venv/bin/python tests/test_workflow.py`: 通過 454 / 失敗 0
  - `python3 tests/test_oss_ops.py`: 通過 208 / 失敗 0
  - `.venv/bin/python tests/test_stream.py`: 通過 114 / 失敗 0
  - `.venv/bin/python tests/test_oss_roll.py`: 通過 66 / 失敗 0
  - `bash -n ops/up.sh ops/oss/up.sh ops/up-common.sh`: 構文エラー無し
  - `terraform fmt -check -recursive IaC/terraform/aws-managed/base/core`: 差分無し
- 検証 5 の grep は、`docs/cycles/` を除けば期待どおり。除かないと外れるので Nit 4 に書いた。
- Should fix の 1 件は、Runtime の ENI が残るあいだの案内が欠けていること。ユーザーが down.sh と up.sh を繰り返す形になる。
  - 守りが止めること自体は正しく、作るものも失うものも無いので、Must にはしない。

### 見た観点 / 見ていない観点

- 見た観点（設計との整合）: 設計方針 1〜5 と実装ステップ 1〜5 を、`ops/up-common.sh:24-78`、`ops/up.sh:606-609`、`ops/oss/up.sh:160-163`、`security_groups.tf:24-31`、docs 3 本と行ごとに突き合わせた。
- 見た観点（correctness）:
  - `sg_descriptions_in_state` の見出しの切り出し: `"# aws_security_group.workload[\""` は 31 文字なので、キーは 32 文字目から始まり、`"]:` の手前までになる。
  - 4 空白ちょうどの `description` の照合: 入れ子の `egress` / `ingress` の 8 空白以上、ルールのリソースの別見出しのブロック、`Outputs:` 以降のどれも混ざらない。
  - コードに無いキーは飛ばす。OSS 版の `oss_security_groups` による上書きと追加も効いている。
- 見た観点（runtime bugs）:
  - `set -u` の下で `local` を使っている。
  - `${oss:+"$oss"}` で、マネージド版では awk に余計な引数を渡さない。
  - `terraform show` の失敗と、コードが読めないときは die する（黙って通さない）。
  - die するのは `$( )` の外なので、本体ごと止まる。
- 見た観点（迂回）: base/core を apply するのは `ops/up.sh:776` と `ops/oss/up.sh:302` だけで、守りを通らずに入る口は無い。
- 見た観点（data loss）: 守りは読むだけで、state にも AWS にも書かない。止まるのは手順 1 の前で、作ったものは無い。
- 見た観点（down.sh との組み合わせ）: `ops/down-common.sh:140-150` の `destroy_base_core` が、Runtime の ENI が残るあいだは `workload["runtime"]` を残すことを確かめた。これが Should fix 1 の根拠。
- 見た観点（履歴）: `git log -S` で、48683dd（2026-10-08。007 のディレクトリの移動）が SG の description を全部 `(terraform/…)` から `(IaC/terraform/aws-managed/…)` に変えたことを確かめた。
- 見た観点（missing tests）: `tests/test_workflow.py` に約 18 件、`tests/test_oss_ops.py` に偽の `show` の枝と呼ぶ位置の検査がある。網羅は良かった点に書いた。
- 見た観点（security）: シークレットに触る処理は無い。die の文面に出るのは description の文言だけ。
- 見ていない観点（実物の出力）: 実物の `terraform show -no-color` の `aws_security_group` の出力。手元に state が無く、AWS も使っていない。設計の未確定事項 1 のとおり QUEUE 147 で確かめる。
- 見ていない観点（awk の実装）: GNU awk / mawk では動かしていない。手元に入っていない。読んだかぎり、`match` / `substr` / `RLENGTH` / `ENVIRON` / `split` / `"/dev/stderr"` だけなので、どれでも動く書き方。
- 見ていない観点（Linux の bash）: Linux の bash 5 では動かしていない。テストは macOS の bash 3.2 で通している。
- 見ていない観点（AWS）: AWS 上で実際に DependencyViolation が起きるか（設計の未確定事項 3）。docker も使っていない。
- 見ていない観点（API compatibility / type safety）: シェルの内部関数だけで、外から呼ばれる API も型も無いので対象外。

## Must fix

None

## Should fix

- [correctness / 運用の案内] Runtime の ENI が残るあいだに runtime の SG の description がコードと違うと、die の案内どおり `down.sh` を打っても抜けられない。案内にそのことが書かれていない。
  - 根拠（die の文面）: `ops/up-common.sh:77` の文面は「先に `$OPS_DIR/down.sh` で全部消してから `$OPS_DIR/up.sh`」だけ。
  - 根拠（down.sh が残すもの）: `ops/down-common.sh:140-150` は、Runtime の ENI（種類 `agentic_ai`、最大 8 時間残る）があると `aws_security_group.workload["runtime"]` を消さずに残し（`:147`）、終了コード 0 で終わる。`docs/troubleshooting.md:489` も「次の `ops/up.sh` が使い回す」と書いている。
  - 根拠（description の変更）: 48683dd（2026-10-08）で runtime の description は `AgentCore Runtime ENIs (terraform/agent)` から `AgentCore Runtime ENIs (IaC/terraform/aws-managed/agent)`（`security_groups.tf:42`）に変わった。
  - 起きる手順:
    1. 2026-10-08 より前の state で、Runtime の ENI のせいで runtime の SG が残っている。今後 runtime の description を変えたときも同じ。
    2. `up.sh` が `check_sg_descriptions` で止まる。
    3. 案内どおり `down.sh` を打つと、runtime の SG がまた残る。
    4. `up.sh` が同じ文面でまた止まる。
    - ENI が消えるまで（最大 8 時間）これを繰り返し、その間「時間をおいて打ち直す」とはどこにも出ない。
  - 理由の文面とのずれ: die の文面の理由「ほかの SG のルールが古い SG を参照したまま」は、この場合の止まる理由（Runtime の ENI）と合っていない。
  - 既存の守りとの差: 同じ塊にある `internal` の守り（`ops/up.sh:592`）は「Runtime の ENI が残るあいだは VPC・サブネットと一緒に残るので、時間をおいて打ち直す」と書いている。新しい守りの文面と `docs/troubleshooting.md:26` には同じ一言が無い。
  - 直し方の例: 違うキーに `runtime` があるときだけ文面に足す、または常に足す。設計方針 1 と 5 の文面の変更なので、design.md 側で決める。
  - build.md の Round 2 のセルフレビュー指摘 1 と同じ件で、PM の判断に回されている。
  - Should にする理由: 守りが止めること自体は正しく（通せば runtime の SG の destroy が 20 分待って落ちる）、作るものも失うものも無いが、ユーザーが案内どおりに動いて抜けられない手順が残るので、直さずに閉じるべきではない。

## Nit

- [設計との整合 / docs] `docs/troubleshooting.md:26`、`ops/up.sh:606-608` / `ops/oss/up.sh:160-162` のコメント、`security_groups.tf:24-26` のコメントは、description を変えた SG として workflow（041）と telegraf_dialout / telegraf_dialout_nlb（043）だけを挙げている。
  - 実際は、48683dd（2026-10-08）より前の state なら全キーが違う（`(terraform/…)` → `(IaC/terraform/aws-managed/…)`）。
  - die の文面には実際に違うキーが全部並ぶので、実害は読み手が一瞬戸惑う程度。
  - 例えば troubleshooting.md に「2026-10-08 より前の state は全キーが違う」と一言足すとよい。
  - Nit にする理由: 文面は設計方針 2・4・5 のとおりで、守りの動作には影響しない。
- [設計との整合] design.md の設計方針 1 に無い振る舞いが 2 つ入っていて、記録は build.md（Round 2 の逸脱）だけにある。
  - コードの description が空なら die する（`ops/up-common.sh:68-69`）。
  - `local.security_groups` / `oss_security_groups` の中に読めない行があれば stderr に出して止まる（`ops/up-common.sh:42-44`）。
  - どちらも黙って通さない側の妥当な追加。ただ、仕様の正本は design.md なので、次に設計を触るときに設計方針 1 へ取り込むとよい。
  - Nit にする理由: 振る舞いは安全側で、テスト（コードが壊れたとき、行末のコメント）もある。
- [設計の範囲] `aws_security_group.endpoints`（`security_groups.tf:189`）は守りの対象外。
  - `sg_descriptions_in_state` は `aws_security_group.workload[...]` の見出しだけを拾う。
  - endpoints もほかの SG のルールから参照されている（`sg_ids` に入っている。`security_groups.tf:45`）。description を変えれば同じ DependencyViolation になる形。
  - 今回は endpoints の description を変えていないので実害は無い。対象外であることを `ops/up-common.sh:24-27` のコメントに一言書いておくとよい。
  - Nit にする理由: 設計方針 1 は「workload の SG 全部」と範囲を決めていて、実装はそれに合っている。
- [検証計画] design.md の検証 5 の grep は、書いてあるとおり打つと期待と合わない。
  - `grep -rn 'SG_DESCRIPTION_ROOTS' ops docs tests IaC` は 0 件ではない。043 の design.md と build.md に当たる。
  - `grep -rn 'だけ先に消してもよい' ops docs` は既存の 3 行（`ops/up.sh:598`、`ops/up.sh:604`、`ops/oss/up.sh:158`）のほかに、043 の design.md と build.md に当たる。
  - `docs/cycles/` を除けば期待どおりになることは自分で確かめた。
  - Nit にする理由: 実装の問題ではなく、検証の書き方の範囲の問題（build.md のセルフレビューにも同じ指摘がある）。

## 良かった点

- `terraform show -no-color` を 1 回だけ打ち、失敗したら die する（`ops/up-common.sh:70`）。既存の守りの `2>/dev/null` のように黙って通さない。
- コードに無いキー（`telegraf` など、キーを変えた古い SG）を飛ばして、既存の守りと担当を分けている。テスト（コードに無いキー `telegraf`）もある。
- 4 空白ちょうどの `description` と行頭の `}` でブロックを切るので、次のものを拾わない。テストに `Outputs:` と、ルールの別ブロックの場合がある。
  - `egress` / `ingress` の入れ子の description
  - SG のルールのリソースの description
  - `Outputs:` 以降
- OSS 版で `oss.tf` の `oss_security_groups` が同じキー（spark）を上書きすることを、awk の連想配列 1 つで扱っている。実ファイルを読むテスト（マネージド版と OSS 版）もある。
- 文言をコードから読むので、up.sh に写しが無く、description を次に変えたときも守りを直さなくてよい。
- 読めない行を黙って飛ばさず止める（そのキーを見逃さない）。実装の段階で自分で見つけて直している。
- build.md に、関数を 14 通りに壊して（mutation）テストが落ちることを確かめた記録がある。テストが判定の芯を押さえていることの根拠になる。
- die の文面に、違うキーと state / コードの両方の文言が並ぶので、何が違うかをユーザーがその場で読める。

## ユーザーへの質問

- メインのチェックアウトの `IaC/terraform/aws-managed/base/core` の state に、2026-10-08 より前の description のまま runtime の SG（Runtime の ENI のせいで残ったもの）が残っていないか。
  - 残っていれば、QUEUE 147 の最初の `ops/up.sh` は `check_sg_descriptions` で止まる。Runtime の ENI が残っていると、Should fix 1 の繰り返しになる。
  - このレビューでは worktree の外と AWS に触っていないので確かめていない。
