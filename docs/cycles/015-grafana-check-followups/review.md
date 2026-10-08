# Grafana のルール検査の残りを直す（015）レビュー

## Round 1

- 対象: `fix/grafana-check-followups` の 211d356（Round 1 の 9c4dbb5、Round 2 の 05c1cec、012 を取り込んだ 8da0db0 を含む）。実装モデル: opus-5.5（エンジニア1）。レビューモデル: cold reviewer = opus、確認 = opus-5.5 / xhigh（エンジニア1）
- cold reviewer に依頼した（初回。review.md がまだ無かった）。入力は design.md と変更ファイル 13 本のパスだけ
- check.sh: 8da0db0 の作業ツリーで rc 0「すべて通過」（build.md の Round 2 の検証方法 3。test_alerts 169 / test_oss_ops 194）

### cold reviewer の結果（review-r01.md をそのまま連結）

# レビュー r01: Grafana のルール検査の残りを直す（015）

対象: `fix/grafana-check-followups`（merge-base `5be0288` からの差分）。根拠は design.md と実コードだけ。

## サマリ

- design.md の A〜F は実装に揃っている。A はページ送りと異常な形の応答の扱い。B は grafana_rules_check / grafana_rules_step の 0 / 1 / 2。C は check-grafana.sh の終了コード 3 と、die を差し替える位置。D は ssm_run の締め切り。E は tf_output・grafana_skip_warn と、両方の up.sh の 9-2。F は GRAFANA_LEFT / GF_UNREAD。
- 壊れる箇所は見つからなかった。Must fix 0 件、Should fix 0 件、Nit 1 件。
- Nit は、1 ページ目の `groupNextToken` が文字列でない偽の値（`0` など）のとき、design.md の「文字列でない → ValueError」にならず、1 ページ目だけで判定に進む件。13.2.3 は文字列か省略しか返さないので、実害はない。
- テストは手元で実行して確かめた。
  - `uv run --group dev --group web python tests/test_alerts.py` → 「通過 169 / 失敗 0」
  - `uv run --group dev --group web python tests/test_oss_ops.py` → 「通過 194 / 失敗 0」
  - docs/development.md に書かれた件数（169 / 194）とも一致する。

### 見た観点 / 見ていない観点

- 見た観点
  - design 整合性
    - design.md の各節と、`ops/grafana_rules_check.py`・`ops/up-common.sh`・`ops/check-grafana.sh`・`ops/up.sh` 9-2・`oss/ops/up.sh` 7-4b / 9-2・tests・docs（troubleshooting / deploy / pipeline / grafana / development）を突き合わせた。
  - correctness
    - grafana_rules_check の分類。`rc:判定` の組み合わせで、OK になるのは SSM 成功と「判定: OK」が揃うときだけかを見た。
    - ssm_run の締め切り。SSM_RUN_WAIT の検証と、締め切りで 2 を返すことを見た。
    - up.sh 9-2 の分岐。state list が読めない・Grafana が残っている・残っていない・tf_output が失敗する場合を見た。
    - check-grafana.sh の die の差し替え位置。common.sh の後、up-common.sh の前にあることを確かめた。
  - security
    - パスワードは Authorization ヘッダーにだけ入る。`add_unredirected_header` なのでリダイレクト先には送らない。
    - 例外の文に出るトークンは 40 文字で切っている。
    - トークンは `urllib.parse.quote(token, safe='')` で URL に埋めている。
  - runtime bugs
    - `$( )` の中で set -e が効かない件。ssm_run は送れないとき 1 を返す。
    - `grep -q` と pipefail の SIGPIPE。state list を変数に読み切ってから見ている。
    - 空の名前で `aws ecs wait` に進まないこと。
  - API compatibility
    - check-grafana.sh の使い方の誤りが 2 から 3 に変わる件。リポジトリの中に呼び出し元が無いことを grep で確かめた。
  - missing tests
    - 異常な形の応答（dataarr / tokint / nostatus / grpdict）、終わらないトークン（100 回の GET で止まる）、締め切り、SSM_RUN_WAIT の不正な値、9-2 の m92 ハーネスの各ケース、check-grafana.sh の 0 / 1 / 2 / 3 を見た。
  - 1 ページ目が偽の値のトークンを返すときの動き
    - 手元に Python の HTTP サーバーを立てて `make_fetch` を呼び、確かめた。結果は `groups: ['g1']`、リクエストは 1 回。
    - 生成物が残っていないことは `git status --short --ignored ops tests` が空であることで確かめた。
- 見ていない観点
  - AWS の上での実際の動き
    - lab の cloud-init と run_on_instance が、既定の 1800 秒の締め切りに収まるか。design.md の未確定 3 にあたる。
    - AWS の Grafana で、ページ送りが実際にどう動くか。
  - `ops/check.sh`（terraform fmt / validate）は打っていない。terraform init が追跡しているロックファイルを書き換えうるため。
  - shellcheck はこの PC に入っていないので打っていない。
  - `bash -n` は、隔離の仕組みに拒まれたので直接は打っていない。シェルの構文は、上のテストがスクリプトを実際に動かしていることで代わりに確かめた。

## Must fix

None

## Should fix

None

## Nit

- [design 整合性] `ops/grafana_rules_check.py:95`
  - 何が起きるか: `while token := data.get("groupNextToken"):` は、値が偽ならループに入らない。そのため design.md A の「groupNextToken が文字列でない → ValueError（読めなかったとして扱う）」は、真の値（`5`、`[1]` など）にしか効かない。
  - 壊れる入力: 1 ページ目が `{"status": "success", "data": {"groups": [g1], "groupNextToken": 0}}` を返すと、ValueError にならない。1 ページ目のルールだけで判定に進む。手元のサーバーで実測し、groups は `['g1']`、GET は 1 回だった。`false` や `[]` でも同じになる。
  - テスト: test_alerts.py:1150 の `/tokint` は真の値（`5`）だけを試していて、偽の値は試していない。
  - 直すなら: 先に `"groupNextToken" in data` と型を確かめる。たとえば `token = data.get("groupNextToken"); if token not in (None, "") and not isinstance(token, str): raise ValueError(...)` のようにして、`None` と `""` だけを「続きなし」とみなす。
  - Nit とする理由: Grafana 13.2.3 の NextToken は omitempty の Go の string で、文字列か省略しか返らない。この入力は今の Grafana では起きないので、実害はない。

## 良かった点

- grafana_rules_check は、「判定:」の行と ssm_run の終了コードの組み合わせで分類している。Python が落ちる、出力が 24,000 文字で切れて判定の行が無い、送れない、締め切りになる、のどれも 2（未確認）になり、OK と取り違えない。テスト `_gc` の 8 通り（okempty・okwithoutok・deadline を含む）で押さえている。
- up.sh 9-2 は state list を変数に読み切ってから grep している。パイプの SIGPIPE を避けつつ、「state が読めない」（GF_UNREAD）と「Grafana が無い」を分けている。20 万行の state でもテスト（left_big）している。
- check-grafana.sh は、`die` を差し替える位置を ops/common.sh の後、ops/up-common.sh の前に置いている。そのため、up-common.sh が読み込み時に行う SSM_RUN_WAIT の検証で止まっても、deploy.env の誤りで止まっても、3 で終わる。どちらもテスト（SSM_RUN_WAIT=0、DEPLOY_ENV_FILE が無い）で確かめている。
- 締め切りで返したあとも、インスタンスの上のコマンドを止めず、あとで結果を見るための `get-command-invocation` を案内している。SSM_RUN_WAIT は `^[1-9][0-9]*$` で検証していて、`01` や `1.5` を通さない。
- design.md の D2/D8（9-2 で terraform が読めなくても止めず、`grafana_skip_warn` で最後の案内まで届かせる）が、ops/up.sh と oss/ops/up.sh の両方で守られている。oss の 7-4b は design どおり `|| exit 1` のまま残っている。
- docs/troubleshooting.md の終了コードの表、9-2 での扱い、development.md のテスト件数が、実装と実際の実行結果に揃っている。

## ユーザーへの質問

None

### 確認（エンジニア1。opus-5.5 / xhigh）

- cold reviewer が返ったあとの `git status --porcelain -uall` は `?? docs/cycles/015-grafana-check-followups/review-r01.md` だけ（ほかに増えたファイルなし）
- 件数: Must fix 0 / Should fix 0 / Nit 1
- Nit `[design 整合性] ops/grafana_rules_check.py:95` を再現した。手元の HTTP サーバーで 1 ページ目の `groupNextToken` だけを変え、`make_fetch(base, "x")()` を呼んだ（スクラッチの nit_falsytoken.py。実行後の `git status --porcelain -uall --ignored ops` は空）

  ```
  token=0: groups=['g1'] GET=1
  token=False: groups=['g1'] GET=1
  token=[]: groups=['g1'] GET=1
  token=5: ValueError groupNextToken が文字列でない（int） GET=1
  token='': groups=['g1'] GET=1
  ```

  - 原因: `:95` の `while token := data.get("groupNextToken"):` が真偽で先に抜けるので、`:96` の型の確かめは真の値にしか届かない
  - 設計との関わり: design.md A（:61「`data.groupNextToken` が空でない文字列のあいだ」、:65「`groupNextToken` が文字列でない」→ `ValueError`）。:65 の字面からは実装のずれ（PM の決まりの (1)）とも読める
  - 実害: design.md :27 の Grafana のソース（`NextToken string json:"groupNextToken,omitempty"`）では文字列か省略しか来ないので、今の Grafana では起きない（ソースを読んだだけ。偽の値を返す Grafana は再現していない）
  - 扱い: Nit のまま直さない。直すかどうかは PM が決める（直すと実装ファイルが変わるので cold reviewer の 2 回目が要る）

### PM の判断（2026-10-09）

- **Nit（`ops/grafana_rules_check.py:95` の `while token := data.get("groupNextToken")` が、偽の非文字列 0 / false / [] で str の検査を抜ける）: 見送り。** design.md A の「`groupNextToken` が文字列でない → ValueError」からはずれているが、Grafana 13.2.3 の `NextToken string json:"groupNextToken,omitempty"`（design.md「読んだ実物」）は文字列しか返さず、偽の非文字列は来ない。真の非文字列は str の検査で ValueError になる。直すと実装ファイルが変わって cold reviewer の 2 回目が要るので、実害の無いこの 1 件のためには回さない。
- **#1（手順 10 の `tf pipeline/analytics output` に守りが無い。`ops/up.sh:1328`、`oss/ops/up.sh:615`）: 見送り。** 手順 10 は手順 8 で analytics を apply した直後なので、apply が通った state が手順 10 で丸ごと読めなくなる形は up.sh の 1 回の実行の中では起きない。Splunk の行も同じ形で 015 より前からある。design.md の範囲外。
- **#9（`ops/check-grafana.sh:42-43` の `2>/dev/null` が terraform のエラーを捨てる）: 見送り。** exit 3 の文は「読めないか空」で偽の OK にはならない（test_oss_ops に検査あり）。015 より前からある形で design.md の範囲外。
