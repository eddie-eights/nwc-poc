# レビュー: `aws_security_group.endpoints` の description も `check_sg_descriptions` の守りに入れる（046）

## Round 1

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は `build.md` Round 1（エンジニア opus-5.5、`900f66d`）。

**cold reviewer: 依頼した**（初回ビルド直後。`Agent` general-purpose、model opus、effort xhigh）。以下が cold reviewer の全文（`review-r01.md` を連結）。

### cold reviewer Round 1

対象: `aws_security_group.endpoints` の description も `check_sg_descriptions` の守りに入れる（046）。ブランチ `feat/159-endpoints-sg-guard`、43956e9..900f66d。

## サマリ

- design.md の要点はそのまま実装されている。
  - コード側は `security_groups.tf` の endpoints の resource ブロックの 2 空白の description を読み、workload の後に `endpoints\t<文言>` を 1 行出す。
  - state 側は見出し `# aws_security_group.endpoints:` をキー `endpoints` にする。
  - 比べる側と die の文言は 043 のまま。
- 止まるべき入力（ブロックが無い・補間・テンプレート・キーの衝突・`security_groups = {` の塊が読めない）は fail-closed で止まる。
- 実物の写し（`scratchpad/da/show-backup.txt`、10-11 03:32）では、endpoints の見出しは 1127 行目で workload（1150〜1495 行目）より前。4 空白の description はコードと一致する。endpoints の文言を変えた写しでは、die の文に `endpoints（state「VPC endpoints old」/ コード「…」）` が出る。
- Must fix は 0 件。
- Should fix は 1 件。行末のコメントの中に `"` があると読めない扱いにならず、誤った die（`ops/down.sh` を勧める）になる。設計の「行末にコメント → 読めない」と食い違う。いまの実物にその行は無い。
- Nit は 6 件。

### 見た観点 / 見ていない観点

見た観点:

- [design.md 整合] 手順 2〜4、T-1〜T-9、未確定事項 1〜3 と、build.md の「設計からの逸脱」「セルフレビュー」を、コードと突き合わせた。
- [correctness] `sg_descriptions_in_code` の awk の境界を、security_groups.tf の写しを書き換えて実行した。
  - 行末のコメント（`"` あり / なし）
  - 三項演算子の式
  - workload 側の行末のコメント
  - OSS 版で `oss_security_groups = {` の塊を読めなくした場合
- [correctness] `sg_descriptions_in_state` と `check_sg_descriptions` を、実物の写しとその改変（文言を変えた、` (tainted)` を付けた）で end-to-end に実行した。
- [runtime bugs] `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` は rc=0。
  - 実物のコードでの出力を確かめた。aws-managed は rc=0 で 17 行、OSS は rc=0 で 22 行。どちらも最後の行は `endpoints\tVPC endpoints - HTTPS from the workload SGs, no outbound` で、endpoints は 1 回だけ出る。
- [API compatibility] `check_sg_descriptions` の呼び出し側（`ops/up.sh:608`、`ops/oss/up.sh:162`）と die の文言は変わっていない。`ops/down-common.sh` の部分 destroy（`destroy_base_core`）は endpoints を残す対象に入っていない。手順 0 の後の `ops/up.sh` で作り直される前提と合う。
- [missing tests] 自分で実行したテスト（`uv run -q --group dev --group web python tests/<file>`、1 本ずつ）は全部 failed 0。
  - test_workflow 475 / test_oss_ops 209 / test_oss 177 / test_oss_roll 66 / test_lab_debug 113
  - test_stream 118 / test_analytics 549 / test_alerts 168 / test_collectors 79 / test_local_compose 145
  - 046 で足した check は全部 ok。
- [security] 変更はローカルのファイルと `terraform show` の出力を awk で読むだけ。外部への送信、シークレットの読み出し、権限の変更は無い。
- docs（troubleshooting.md:26、architecture/core.md:103、architecture/resources/vpc-perimeter.md:98）の「workload の全キーと `endpoints` を見る」の記述とコードの一致。

見ていない観点:

- AWS 上の実物の `terraform show`（endpoints が生きている state）での実行。見たのは 10-11 03:32 の写しだけ。
- gawk / mawk での実行。macOS の `/usr/bin/awk`（BWK）だけで確かめた。
- tests のうち up-common.sh を参照しない 6 本（test_agentcore、test_dashboard_config、test_graph、test_kb_index、test_nautobot、test_sync）は自分では走らせていない。
- [type safety] 該当なし（シェルと awk）。
- [data loss] die で止める側の変更なので、止まらずに壊す経路は Should fix 1 の逆向き（誤って止める）だけを見た。

## Must fix

None

## Should fix

- [correctness] `ops/up-common.sh:39-43`。endpoints の description の行の末尾に、`"` で終わるコメントがあると、コメントまで含めて description として読んでしまう。読めない扱いにならず、`check_sg_descriptions` が誤って die して `ops/down.sh` → `ops/up.sh`（全部の作り直し）を勧める。
  - 設計との食い違い: design.md 手順 2（44 行目）は「行末にコメント」を読めない扱いにすると書いている。コードの 43 行目のコメントも「行末のコメント…は読めない」と言っている。ところが 39 行目の条件は「`^  description += "` で始まり、行末が `"`」だけなので、`  description = "X" # was "Y"` が通る。40 行目の substr で取り出した v は `X" # was "Y` になる。41 行目は `${` と `%{` しか見ていない。
  - 再現（security_groups.tf を一時ディレクトリに写し、193 行目の末尾に ` # was "VPC endpoints SG"` を足して `source ops/up-common.sh; sg_descriptions_in_code` を実行）:
    ```
    rc=0
    最後の行: endpoints	VPC endpoints - HTTPS from the workload SGs, no outbound" # was "VPC endpoints SG
    ```
  - 同じ写しで、state の description は変えずに（実物どおり）`check_sg_descriptions` を実行:
    ```
    rc=1
    DIE: <TF_DIR>/base/core の state の SG の description がコードと違う: endpoints（state「VPC endpoints - HTTPS from the workload SGs, no outbound」/ コード「VPC endpoints - HTTPS from the workload SGs, no outbound" # was "VPC endpoints SG」）。…先に ops/down.…
    ```
  - 対照: `"` を含まないコメント（` # memo`）なら行末が `"` でないので 43 行目に落ち、rc=1 の `NG: aws_security_group.endpoints の description が読めない` で正しく止まる。
  - 直し方の案: v の中にエスケープされていない `"` があれば読めない扱いにする（例: `gsub(/\\"/, "", t)` した写しに `index(t, "\"")` があれば epbad へ）。それを縛る check を `_ep_cases` に 1 件足す。
  - workload 側（043 から、`ops/up-common.sh:47-49` 付近）も同じ穴。`  workflow = "…" # 041 "old"` で rc=0、出力は `workflow\tTemporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)" # 041 "old` になる。直すなら同じ条件を両方に入れるのが自然。workload 側を 046 の範囲外にするなら QUEUE の候補。
  - Should にした理由: 誤った die は全部の作り直しを勧めるので、従うと無駄な時間と費用がかかる。ただし、いまのコードにその形の行は無く、壊れている入力は存在しない。設計が明示した「読めない」の 1 種を取りこぼしているので、Nit よりは上。

## Nit

- [design.md 整合] design.md の本文が実装と食い違ったまま 2 か所残っている。
  - 手順 2 の state 側（45 行目）は「`# aws_security_group.endpoints:`（完全一致）」。実装（`ops/up-common.sh:75`）は `index(...) == 1` の前方一致で、tainted も読む。build.md では「設計からの逸脱」節ではなく、セルフレビューの表の #5 にだけ載っている。
  - T-3（86 行目）と未確定事項 3（100 行目）は「workload が先」の順のまま。こちらは build.md の「設計からの逸脱」（18 行目）に理由つきで載っている。
  - 読んだだけ。再現手順: `grep -n -E '完全一致|T-3' docs/cycles/046-endpoints-sg-description-guard/design.md` と `sed -n 75p ops/up-common.sh`。
  - Nit にした理由: 2 件とも build.md に記録があり、実装の判断（前方一致・実物の並び）は正しい。仕様の正本（design.md）を読む次のサイクルが迷わないよう、前方一致も「設計からの逸脱」節に 1 行足すか、design.md の該当行に追記しておくとよい。
- [missing tests] `tests/test_workflow.py:1603-1605` の既存 check の名前が古い。
  - 名前は「state に workload の SG が 1 つも無い（aws_vpc とルールのブロックだけ）」。ところが `_csd_run(state={})` は既定で endpoints のブロックを含むので、「ルールのブロックだけ」ではなくなった。
  - いまは endpoints の文言がコードと同じなので通る。名前どおりの「SG が 1 つも無い state」は縛れていない（endpoints の比較を壊す変異と区別が付かない）。
  - `endpoints=False` を渡すか、名前を直すとよい。
  - Nit にした理由: 結果の判定は正しく、ほかの T-4 などで endpoints 無しの state は縛られている。
- [missing tests] `tests/test_workflow.py:1479` の `_SG_EP_M` の正規表現（`(?:.*\n)*?` の遅延一致）は、resource ブロックの `}` で止まらない。
  - endpoints のブロックから 2 空白の description が消えると、その次の `endpoints_none` の `  description       = "No outbound"`（security_groups.tf:201）を拾い、`_SG_EP_DESC` が "No outbound" になる。
  - T-8（コードと state の突き合わせ）や実物の出力との比較で気づける形にはなっているが、`(?:(?!\}\n).*\n)*?` のようにブロック内に限るほうが意図に合う。
  - 読んだだけ。再現手順: security_groups.tf の 193 行目を消した文字列に同じ正規表現を当てる。
  - Nit にした理由: テストの土台の取り違えで、本体のコードは影響を受けない。
- [correctness] Should fix 1 と根が同じ別の形。`  description = "a" == "b" ? "x" : "VPC endpoints - …"` のような式も、行頭が `"` で行末が `"` なので通る。
  - 再現（同じ方法）: rc=0、最後の行は `endpoints\ta" == "b" ? "x" : "VPC endpoints - …`。
  - Should fix 1 の直し（内側の `"` を読めない扱いにする）で一緒に塞がる。
  - Nit にした理由: description にこの形の式を書くことは考えにくく、Should fix 1 に含まれる。
- [correctness] 046 より前からの穴（範囲外）。OSS 版で `oss.tf` の `  oss_security_groups = {` の行が読めない形（例: `merge({`）になると、file 2 の塊を読まずに黙って通る。
  - 再現（oss.auto.tfvars に project="nwc-oss" を置き、oss.tf の写しの当該行を `  oss_security_groups = merge({` に変えて `sg_descriptions_in_code` を実行）: rc=0、17 行、stderr は空、kafka のキーは出ない、spark はコード側（aws-managed）の文言のまま。
  - 046 で入った `!sgblk` の止めは file 1（`security_groups = {`）だけを見ている。
  - Nit にした理由: 046 の変更で生まれた穴ではない。QUEUE の候補（「oss_security_groups の塊が読めないときも止める」）。
- [runtime bugs] awk は macOS の `/usr/bin/awk`（BWK）でしか確かめていない（build.md も同じ）。
  - `match` / `RLENGTH` / `substr` / `index` / `ARGV` しか使っておらず、gawk / mawk との差は出ない書き方に見える。
  - 読んだだけ。再現手順: `gawk -f` 相当で `sg_descriptions_in_code` を実行し、出力の差を見る。
  - Nit にした理由: up.sh を打つのは macOS の手元だけで、実害の経路が無い。

## 良かった点

- fail-closed の作りになっている。ブロックが無い、読めない、`${` / `%{` を含む、workload のキーと衝突する、のどれも die で止まり、黙って比べないことが無い。
- endpoints の 1 行を必ず出すと `[ -n "$code" ]` の die が効かなくなる退行を、既存の check で見つけた。`!sgblk` の止めを足し、OSS 版の check（変異 M15）まで足している。
- 偽の `terraform show` を実物と同じアドレス順（endpoints が先）に直した。設計の前提（未確定事項 3）が実物と違うことを、実物の写しで確かめてから変えている。自分でも写しの 1127 行目と 1150 行目で同じことを確かめた。
- taint された endpoints（見出しに ` (tainted)`）を前方一致で読む。作り直しの対象そのものを取りこぼさない。変異 M10 で縛られている。
- 変異 M1〜M16 が全部落ちる記録があり、設計の期待との差（M2 で T-6 が落ちない理由）まで書いてある。
- `check_sg_descriptions` と die の文言、`ops/up.sh` / `ops/oss/up.sh` の呼び出しを変えておらず、互換が保たれている。
- docs 3 本の記述がコードと合っている。endpoints の文言にはパスが無いので「差は出ない」という troubleshooting の説明も正しい（文言は 8c1c7b2 以降変わっていない）。

## ユーザーへの質問

None

### PM の確認と分類（Round 1）

- 作業ツリー: cold reviewer が書いたのは `review-r01.md` だけ（`.gitignore:42` の対象なので status の `--short` には出ない。`ls` で確認）。ほかの変更 0
- テストの取り直し（PM、`900f66d`）: `uv run -q --group dev --group web python tests/test_workflow.py` → 通過 475 / 失敗 0、`tests/test_oss_ops.py` → 通過 209 / 失敗 0、`bash -n ops/up-common.sh` rc=0
- **Should fix 1 `[correctness]`（行末のコメントの中の `"`）: 再現した → Should fix のまま、correctness なので Must と同じ扱いで直す。** `security_groups.tf` を scratchpad に写し、193 行目の末尾に ` # was "VPC endpoints SG"` を足して `TF_DIR=<写し> source ops/up-common.sh; sg_descriptions_in_code | tail -1`:
  ```
  endpoints	VPC endpoints - HTTPS from the workload SGs, no outbound" # was "VPC endpoints SG
  rc=0
  ```
  同じ行を ` # memo`（`"` 無し）にすると `NG: aws_security_group.endpoints の description が読めない` で rc=1（設計どおり）。workload 側も同じ（42 行目の `workflow` の行末に ` # 041 "old"`）:
  ```
  workflow	Temporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)" # 041 "old
  rc=0
  ```
  根本原因: 読む側の条件が「行頭 `  description += "` と行末 `"`」だけで、値の中の `"` を見ていない（043 からの形を endpoints にそのまま写した）。直し: 値にエスケープされていない `"` があれば読めない扱い。workload 側も同じ穴なので 046 で一緒に塞ぐ（別サイクルにするほどの量ではない。design.md の方針 1 に足した）
- Nit 1 `[design 整合]`（design.md の完全一致 / T-3 の順）: design.md を直した（前方一致、endpoints が先、未確定事項 1 と 3 を実物で閉じた）。読んだだけ
- Nit 2 `[missing tests]`（`tests/test_workflow.py:1603` の check の名前が古い）、Nit 3（`_SG_EP_M` の正規表現がブロックの `}` で止まらない）、Nit 4（三項演算子の式。Should fix 1 の直しで一緒に塞がる）、Nit 6（gawk / mawk 未確認。手元は macOS の awk だけ）: 直さない。最終報告に載せる
- Nit 5 `[correctness]`（OSS 版で `oss_security_groups = {` の行が読めない形だと黙って通る。046 より前からの穴）: 1 行の止めで塞がるので 046 で一緒に直す（design.md の方針 1 に足した。T-12）
- build.md の「PM に回すもの」: workload 側の補間（`${` / `%{`）の穴 → Should fix 1 と同じ場所の直しなので 046 で塞ぐ（T-11）。未確定事項 1（実物の show の形）→ エンジニアの写しで確かめられたので QUEUE には足さない

### 判断（Round 1）

- Must fix: 0
- Should fix: 1（correctness → 直す。workload 側と OSS の塊の止めも同時に）
- Nit: 6（2 つは design.md で直した / 1 つは Should fix 1 に含む / 1 つは 046 で直す / 2 つは残す）
- エンジニアに Round 2 を振る（design.md の T-10〜T-12）。実装ファイルが変わるので、Round 2 のあと cold reviewer の 2 回目（完了判定の直前）を呼ぶ

## Round 2

レビューモデル: PM(fable-5-1) / effort: high。2026-10-11。対象は build.md Round 2（エンジニア opus-5.5、`a6720ae`）。
**cold reviewer: 依頼した**（完了判定の直前。Round 2 で実装ファイル `ops/up-common.sh` / `tests/test_workflow.py` が変わった。Agent general-purpose、model opus、effort xhigh）

### 前ラウンドの Should fix の確認（Round 2、PM、`a6720ae`）

- Should fix 1（行末のコメントの中の `"`）: Round 1 と同じ手順を打ち直した。endpoints 側（写しの 193 行目の末尾に ` # was "VPC endpoints SG"`）:
  ```
  NG: aws_security_group.endpoints の description が読めない
  rc=1
  ```
  同じ行を ` # memo` にしても同じ NG で rc=1。workload 側（`workflow` の行末に ` # 041 "old"`）:
  ```
  NG: local.security_groups / oss_security_groups の読めない行:     workflow        = "Temporal server, UI and worker ECS task (IaC/terraform/aws-managed/workflow)" # 041 "old"
  rc=1
  ```
  → 解消
- 全テスト（`uv run -q --group dev --group web python tests/test_*.py` を 16 本）: 失敗 0（agentcore 168 / alerts 168 / analytics 549 / collectors 79 / dashboard_config 3 / graph 78 / kb_index 7 / lab_debug 113 / local_compose 145 / nautobot 68 / oss 177 / oss_ops 209 / oss_roll 66 / stream 118 / sync 104 / workflow 479）。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0
- build.md Round 2 の逸脱（`\` も読めない扱い。`\uNNNN` は state に展開後の値が入り、`\"` も同じ理由で比べられない）: 受け入れる。design.md の方針 1 の文言を「`"` または `\`」に直した
- build.md Round 2 の範囲外の Nit #2（`security_groups.tf:158` の `description = each.value` を式に変えると workload の全 description が変わるのに守りが通す。043 からの穴）: 046 では直さない。QUEUE に `- [ ]` で足す

### cold reviewer Round 2

## サマリ

レビューの対象は、ブランチ `feat/159-endpoints-sg-guard` の `43956e9..a6720ae` で変わった 8 ファイル。重点は Round 2 で変わった `8b724fe..a6720ae` の 2 ファイル（`ops/up-common.sh` +11/-3、`tests/test_workflow.py` +42/-1）。比べた相手は `design.md`。

全体の評価:

- Round 1 の Should fix 1（行末のコメントの中の `"` が値に混ざり、誤った die で全部の作り直しを勧める）は塞がっている。値に `"`、`\`、`${`、`%{` があれば、endpoints 側でも workload の表の側でも「読めない」で止まる。`plain()` の 1 か所で判定している。
- OSS 版で `oss.tf` の `  oss_security_groups = {` が無いときの止め（Nit 5）も入った。
- 設計の T-10〜T-12 は check として入っていて、全部通る。
- 足したそれぞれの分岐を変異させた 5 通りは、どれも check で落ちる。
- 比べる側（`check_sg_descriptions`）と die の文言は 043 のまま。
- Must fix と Should fix は無い。Nit は 3 件で、どれも実害は出ていない。

レビュー中に、作業ツリーの `design.md` / `review.md` / `docs/cycles/QUEUE.md` が未コミットで変わった（20:45 の更新。このレビューが書いたものではない）。design.md の照合は更新後の本文（方針 1 に `\` を足した版）で行った。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合**
  - 方針 1 の読めない値の規則を、`ops/up-common.sh:38` の `plain()` と、それを呼ぶ 2 か所（`:43-46` と `:51`）で照合した。値の中の `"` と `\`、`${` / `%{` の 3 つ。
  - OSS の塊の止め（`:61`）を照合した。
  - T-10〜T-12 とエスケープの check を、`tests/test_workflow.py:1738-1770` で照合した。
  - docs の 3 か所と、`ops/up.sh:606-608`・`ops/oss/up.sh:160-162` のコメントは、設計の文言どおり。
  - `docs/troubleshooting.md` の表は `|` の数が崩れていない（20〜30 行目は全部 3 本。`sed -n 20,30p docs/troubleshooting.md | tr -cd '|\n'` で数えた）。
- **correctness**
  - 一時コピーを書き換えて、実物の `sg_descriptions_in_code` を打った。止めないはずの値は止めていない（rc=0）。
    - workload の値に `#`、`$5 {x}`。
    - endpoints の値に `#`。
  - 止めるはずの値は止める（rc=1、NG に該当行が出る）。
    - 行末の空白。
    - `// was "x"`、`/* "x" */`。
    - description が 2 行（2 行目が式）。
    - `oss.tf` の値に `\"`。
  - 実物の `IaC/terraform/aws-managed` と `IaC/terraform/oss` で `sg_descriptions_in_code` を打つと、どちらも最後の行が `endpoints	VPC endpoints - HTTPS from the workload SGs, no outbound`（rc=0）になる。OSS 版は kafka / efs / opensearch / victoriametrics / neo4j と、上書きした spark が出る。
  - 「AWS の SG の description は `"` と `\` を受け付けない」というコメントの前提は、CreateSecurityGroup の API リファレンスで確かめた。許す文字は `a-z, A-Z, 0-9, spaces, and ._-:/()#,@[]+=&;{}!$*`。したがって `plain()` が正しい値を止めることは無い。`$` と `{` は許す文字なので、`${` を含む実在の値はあり得るが、止める側に倒れるだけで誤った die にはならない。
- **missing tests**: 一時コピー（rsync 相当の `shutil.copytree`。リポジトリは書かない）の `ops/up-common.sh` を変異させ、`tests/test_workflow.py` を打った。5 通りとも落ちた。
  - M-a: `plain` を常に真にする → `sg_descriptions_in_code: endpoints の description が補間・…` で落ちる。
  - M-b: workload の行の `plain` を外す → `check_sg_descriptions: workload の表の行末のコメントに「"」がある…`（T-11）で落ちる。
  - M-c: `ARGC > 2 && !ossblk` の止めを外す → `…oss_security_groups = {」の塊が読めないなら…`（T-12）で落ちる。
  - M-d: `plain` から `\` を外す → `…エスケープ（\" \\ \uNNNN）を含めば読めない扱い…` で落ちる。
  - M-e: `plain` から `"` を外す → `…endpoints の description の行末のコメントに「"」があっても…`（T-10）で落ちる。
- **テストの実行**
  - `bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` は exit 0（`SYNTAX_OK`）。
  - `for t in tests/test_*.py; do .venv/bin/python3 "$t" | tail -1; done` は 16 本とも失敗 0。`test_workflow.py` は「通過 479 / 失敗 0」、`test_oss_ops.py` は「通過 209 / 失敗 0」。
  - Homebrew の `python3` で打つと、`test_workflow.py` は `ModuleNotFoundError: No module named 'yaml'`（`app/agentcore/topology.py:42`）で始まらない。046 とは関係のない環境の差なので、指摘にはしない。
- **security / data loss**: 変更は読み取りの awk と、テストと、コメント・docs だけ。state を書く操作、AWS の呼び出し、ファイルの削除は増えていない。die で止まる方向の変更だけ。

見ていない観点:

- 実物の AWS の state（endpoints が生きている `terraform show`）。046 の方針 3 で AWS では動かさないことになっている。見出しの形は design.md の未確定事項 1 にあるとおり、Round 1 で写しによって閉じたものとして扱った。
- gawk / mawk での動作。手元にあるのは macOS の `/usr/bin/awk` だけ。Round 1 の Nit 6 と同じで、繰り返さない。
- `tests/test_oss_ops.py` の実物の `check_sg_descriptions` の経路は、通ることだけを見た。中身は読み直していない。
- type safety: 型のある言語の変更が無いので、対象外。

## Must fix

None

## Should fix

None

## Nit

- [design.md との整合 + 保守性] die の括弧書きが、Round 2 で増えた止める理由に合っていない（`ops/up-common.sh:94`）。
  - 括弧書きは「local.security_groups の形が変わったなら ops/up-common.sh の sg_descriptions_in_code を直す」。
  - Round 2 で増えた止める理由（行末のコメントの中の `"`、`\` のエスケープ、補間）で直すのは、`.tf` の書き方のほうになる。`up-common.sh` ではない。
  - 直前の stderr の NG に該当行が出るので、利用者は辿れる。
  - 読んだだけ。再現手順: `tests/test_workflow.py:1739` と同じ書き換え（endpoints の description の行末に ` # was "VPC endpoints SG"`）で `_csd_run` を打ち、stderr の最後の行を見る。
  - Nit にした理由: 設計は「die の文言は 043 のまま」と決めていて、NG の行で原因は分かる。困るのは、文言どおり `up-common.sh` を直しにいく人だけ。直すなら括弧書きを「security_groups.tf / oss.tf の書き方か、ops/up-common.sh の sg_descriptions_in_code を直す」に広げる程度でよい。
- [correctness（範囲外の前提）] endpoints に `count` か `for_each` を足すと、endpoints の守りが黙って外れる。
  - コード側は `count = 1` を足しても endpoints の行を出し続ける（rc=0）。
  - state 側の見出しは `# aws_security_group.endpoints[0]:` になり、`ops/up-common.sh:81` の前方一致（`# aws_security_group.endpoints:`）に当たらない。比べる行が出ないので通る。
  - 再現（一時コピー。`scratchpad/adv.py` と `scratchpad/r046r2-count.py`）:
    - コード側（`count = 1` を足す）: `(['endpoints\tVPC endpoints - HTTPS from the workload SGs, no outbound', 'rc=0'], '')`
    - state 側（見出しが `endpoints[0]`、description が古い）: `'rc=0\n' ''`（出力の行が無い）
  - Nit にした理由: いまのコードに `count` は無い。design.md の未確定事項 1 も「外れていたら読まずに通る（いまと同じで悪化はしない）」としている。PM が QUEUE に足した「`description = each.value` の行が無ければ止める」と同じ種類の穴（コードの形の前提を確かめていない）。その行で一緒に扱うなら、endpoints のブロックに `count` / `for_each` の行があれば止める 1 行で塞がる。
- [design.md との整合] 「キーが 0 なら NG」は、OSS 版では OSS のキーを含めた数で判定している（`ops/up-common.sh:59`）。
  - design.md 方針 1 は「`local.security_groups` の塊が無い・キーが 0 ならコードを読む側で NG」。
  - 実装の `n` は file 1 と file 2 を合わせたキーの数。OSS 版で `local.security_groups = {}`（空）にしても、OSS のキーで n>0 になって通る。
  - 再現（一時コピー。`scratchpad/adv.py`）:
    - OSS 版: `ss: security_groups が空 (OSS) => (['endpoints\t…', 'rc=0'], '')`
    - マネージド版: `ss: security_groups が空 (managed) => (['rc=1'], 'NG: …に「  security_groups = {」の塊が無いか、キーが 1 つも無い')`
  - Nit にした理由: 壊れる経路が無い。`security_groups.tf` は両版で同じファイル（シンボリックリンク）なので、空にすればマネージド版で止まる。OSS 版はそのとき実際に作る SG（OSS のキー）だけを比べるので、比較そのものは正しい。design.md の文言を「（OSS 版は oss.tf のキーも数える）」に合わせる程度でよい。

## 良かった点

- 読めない値の判定を `plain()` の 1 つにまとめた。endpoints 側と workload の表の側で同じ規則が効き、片方だけ直し忘れる余地が無い。
- `plain()` のコメントに、正しい値を止めない理由（AWS の SG の description が `"` と `\` を受け付けない）を書いている。API リファレンスの許す文字と一致する。
- テストの `edits` 引数は「ちょうど 1 か所」を `assert` してから置き換える。実物の `security_groups.tf` / `oss.tf` の形が変われば、テストの土台が崩れたことに気づける。
- T-10〜T-12 は止まることに加えて、次の 3 つまで縛っている。だから「誤った die を出さない」ことを直接確かめている。
  - `state の SG の description がコードと違う` を含まない。
  - `tf base/core show -no-color` を打たない。
  - `DONE` に届かない。
- `\uNNNN` で展開後の値が同じになる場合（`endpoint\u0073`）まで check にしている。展開前の綴りで比べて誤って die する経路を塞いだことが分かる。

## ユーザーへの質問

None

### PM の確認と分類（Round 2）

- 作業ツリー: cold reviewer が書いたのは `review-r02.md` だけ（`ls` で確認。design.md / review.md / QUEUE の未コミットの差分は PM が cold reviewer の起動後に書いたもの）
- Must fix 0 / Should fix 0。Nit 3 はどれも直さない（読んだだけ。cold reviewer の再現は一時コピーで打ったもので、いまのコードに `count` も空の表も無い）
  - Nit 1（die の括弧書きが `up-common.sh` を直せと読める）: 設計で「die の文言は 043 のまま」と決めた。NG の行で原因が分かる。最終報告に載せる
  - Nit 2（endpoints に `count` / `for_each` を足すと見出しが `endpoints[0]` になり黙って外れる）: QUEUE の「`description = each.value` の行が無ければ止める」の行に一緒に足した
  - Nit 3（キーが 0 の判定は OSS 版で OSS のキーも数える）: design.md の方針 1 の文言を実装に合わせた
- 見た観点: design 整合 / correctness / missing tests（変異 5 通り）/ security / data loss。見ていない観点: 実物の AWS の state（方針 3 で AWS では動かさない）、gawk / mawk、type safety（対象外）

### 判断（Round 2）

- Must fix: 0 / Should fix: 0 / Nit: 3（直さない。1 は残す、2 は QUEUE、3 は design.md の文言）
- 前ラウンドの Should fix 1 は `a6720ae` で解消（上の再現出力）。全テスト 16 本が失敗 0
- サイクル完了。全体設計 HTML は触らない（`up.sh` の守りの範囲が広がっただけで、構成要素・データの流れ・配置・責務の分担は変わらない）
