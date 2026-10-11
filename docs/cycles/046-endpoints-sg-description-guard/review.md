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
