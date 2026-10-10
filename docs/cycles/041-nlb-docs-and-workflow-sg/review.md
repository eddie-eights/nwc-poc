# レビュー: NLB の受信と試し方の説明を揃え、workflow の SG の description を直す（041）

## Round 1

- 対象: `a1d387e`（main `865dfcc` から）。cold reviewer: 依頼した（opus / effort xhigh）。PM の確認: fable-5-1 / effort high。

### cold reviewer（review-r01.md）

## サマリ

対象は「NLB の受信と試し方の説明を揃え、workflow の SG の description を直す（041）」の Round 1（`main` 865dfcc → HEAD a1d387e）。
変更はコード以外を含めて 10 ファイル（`docs/cycles` を除くと 9 ファイル、22 行追加 / 15 行削除）。中身は docs と Terraform のコメント、SG の description 1 行、テストの check 1 つ、新しい `build.md` 1 本。

設計方針 1〜6 の文面は、設計どおりに入っている。
設計からずれた所は 2 つで、どちらも `build.md` に書いてある（`core.md:93` も揃えた、`stream/telegraf.tf:16` の頭の一文を残した）。
`./ops/check.sh` は手元で「すべて通過」になった。足した check は、`main` の description では落ち、HEAD では通る。
Must fix は無い。直すべき所は 3 つあり、どれも設計から来ている。

- workflow の SG の作り直しについて、design.md のリスク 1 の書き方が軽すぎる。QUEUE への追記（設計方針 7）もまだ無い。
- `deploy.md` の `SKIP_LAB` の節に、lab の EC2 を前提にした案内が 1 行残っている。
- `lab/telegraf.tf` に足した NetFlow / sFlow の行の括弧書きが、`ops/netflow_send.py` の実際の使い方とずれている。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - 設計方針 1〜6 を、`git diff main` の全行と突き合わせた。
  - 変更対象ファイルの一覧と `git diff --stat=200 main -- . ':!docs/cycles'` を比べた。
  - `app/` と `ops/` に差分が無いことを確かめた。
  - 検証 3 は自分で grep し直した。「受けない」を含む行のうち trap / 162 の 7 行すべてに、「lab の SG からは受けない」と「送る側だけ」の両方が入っている。
  - `grep -rn "dev server" IaC/ docs/ --exclude-dir=cycles` は 0 件だった。
- **correctness（docs の事実が合っているか）**
  - `app/spark/snmp_sinks.py:461-470`（`with_sysname`）を読んだ。
  - `app/syslog-ng/syslog-ng.conf.in:14,56,60`（`keep-hostname(yes)`、`tags.sysName=$HOST`、`tags.source=$SOURCEIP`）を読んだ。
  - `app/containerlab/lab.sh:323-334`（DNAT は 162 / 5140 / 2055 / 6343 の 4 本）を読んだ。
  - `ops/netflow_send.py:1-19` を読んだ。
  - `ops/up.sh:296-300`（`SKIP_LAB` の注意）を読んだ。
  - `security_groups.tf:115-126`（NLB の受信の行）を読んだ。
  - `savedsearches.conf` の `coalesce` が使われている 3 か所を読んだ。
  - `docs/pipeline.md:313` の見出しと 347 行目の箇条書きを確かめた。
  - `IaC/terraform/aws-managed/workflow/ecs.tf` のコンテナ名が temporal / ui / worker であることを確かめた。
- **runtime bugs / data loss（SG の description の変更）**
  - `security_groups.tf:14`（「description は変えると作り直し。付いている ENI があると消えない」）を読んだ。
  - `aws_security_group.workload` に `lifecycle` / `create_before_destroy` が無いことを grep した。
  - `ops/up.sh:586-605` にある、過去の SG の作り直し用のガードを読んだ。
- **missing tests**
  - 足した check の正規表現を、`git show main:…security_groups.tf` と HEAD の両方に当てた。
    - main の値 `Temporal dev server and worker ECS task …` では False。
    - HEAD では True。
- **テストの実行**
  - `./ops/check.sh` を実行した。最終行は「すべて通過」。`tests/test_workflow.py` は `通過 371 / 失敗 0`。
  - `terraform fmt -check -recursive` を IaC/terraform/aws-managed と IaC/terraform/oss の両方で実行し、どちらも rc=0 だった。
  - 実行後の `git status --porcelain -uall` は空だった（check.sh は追跡しているファイルを書き換えていない）。

見ていない観点:

- AWS での動作。description を変えたときに、残っている環境で `terraform apply` がどう振る舞うかは静的に読んだだけで、plan も apply もしていない（下の Should fix 1 は「見込み」）。
- security。この cycle は通信の表（`sg_flows`）の行を変えていないので、ルールの増減は無い。差分にある security_groups.tf の行は description とコメントだけ。
- type safety と API compatibility。SG のキーと名前、outputs は変わっていない。SG の ID が作り直しで変わる件は Should fix 1 に入れた。
- My Repo の全体設計 HTML。対象外。

## Must fix

None

## Should fix

- **[design.md との整合性 + runtime bugs / data loss] `docs/cycles/041-nlb-docs-and-workflow-sg/design.md:71`（リスク 1）と設計方針 7、`docs/cycles/QUEUE.md:145`**

  リスク 1 は「環境が残っている PC で `up.sh` を打つと、workflow のタスクが SG の入れ替えで一度止まる」と書いている。同じリポジトリの次の 2 か所は、これより重い結果を書いている。

  - `IaC/terraform/aws-managed/base/core/security_groups.tf:14`: 「SG の description は変えると作り直しになる（付いている ENI があると消えない）ので、変えるときは down してから」
  - `ops/up.sh:587-588`: 「消すところで DependencyViolation になる」

  さらに、`aws_security_group.workload` には `create_before_destroy` が無い（grep で 0 件）。名前も `<prefix>-workflow` で固定なので、先に新しい SG を作る形にはできない。

  041 より前に立てて workflow を残している環境に `ops/up.sh` を打つと、`base/core` の apply は次の順に進む見込み（apply はしていない）。
  1. workflow の SG に付いたルールを消す。消えるのは `aws_vpc_security_group_*_rule` の `security_group_id` が workflow の行で、endpoints への 443、nautobot_db への 5432、自分宛ての 7233〜7239 / 6933〜6939、web からの 8233。
  2. SG そのものは、ECS のタスクの ENI と、ほかの SG のルールからの参照が残っているので消せない。DependencyViolation で失敗する。

  結果として、apply は失敗し、走っている Temporal のタスクは送信のルールを失う。

  いま AWS に環境は無い（design.md:8 で ECS クラスターと RDS が 0）ので、すぐには壊れない。Must にしなかった理由はここにある。
  ただし、設計方針 7 で PM が足すはずの QUEUE 145 の行に、041 の件はまだ無い（`grep "残った修正をまとめて" docs/cycles/QUEUE.md` に「041」も「workflow の SG」も無い）。
  足すときの文言は「一度止まる」ではなく、「041 より前に立てた環境が残っていれば、先に `ops/down.sh`」にするべき。
  ほかの SG の作り直しと同じく、`up.sh` に止めるガード（`ops/up.sh:596-605` の形）を置くかどうかも、QUEUE の候補にできる。

  Should にした理由: 環境が無いあいだは誰も困らない。困るのは、文言が直らないまま誰かが環境を残して `up.sh` を打ったときで、そのとき apply の失敗とタスクの通信断が起きる。

- **[design.md との整合性 + correctness] `docs/deploy.md:95`**

  設計方針 4 は、96 行目の直しの意図を「`SKIP_LAB` の節の中で lab の EC2 を使う案内をしない」と書いている。
  ところが、直していない 95 行目は「NetFlow / sFlow は lab の SR Linux が出さないので、`ops/netflow_send.py` で送ったときだけ来る」のまま残っている。

  - AWS で `ops/netflow_send.py` を打つ場所は lab の EC2 のホストだけ（`ops/netflow_send.py:8`）。
  - NLB の受信は `lab_mgmt` の CIDR と `lab` の SG からだけ（`security_groups.tf:118-126`）。
  - そのため `SKIP_LAB` では、`netflow_send.py` で送っても何も来ない。`ops/up.sh:300` も「trap・syslog・NetFlow・sFlow は lab の EC2 からしか来ない」と出している。

  新しい 96 行目は「lab の EC2 から `logger` で送る試し方…**も**使えない」と書いている。95 行目と並べて読むと、`netflow_send.py` の方は使えるように読める。
  検証 4 の grep（「lab の EC2 から」で始まる行が無い）は字面では通るが、設計の意図は満たしていない。

  Should にした理由: 動きは何も変わらない。困るのは、`SKIP_LAB` で立てた人が `netflow_send.py` で確かめようとして何も届かず、原因を探すことになる場合。95 行目は変更対象の外なので、直すかどうかは PM が決める。

- **[correctness] `IaC/terraform/aws-managed/pipeline/lab/telegraf.tf:10-11`（新しく足した行）**

  足した行は、DNAT の経路（機器 → 203.0.113.1:2055 / 6343 → NLB）を説明したあとに、括弧で「lab の SR Linux は送れないので、届くのは ops/netflow_send.py で試しに送ったぶん」と書いている。現物とは 2 点ずれる。

  - `ops/netflow_send.py` は lab の EC2 のホストから NLB の DNS 名へ直接送る（`:8`）。この DNAT の経路は通らない。しかも送るのは NetFlow v5 だけ（`:1`、`:19` の `DEFAULT_PORT = 2055`）なので、6343 の sFlow には何も届かない。
  - 「SR Linux は送れない」は sFlow には言い過ぎ。`ops/netflow_send.py:3` は「sFlow だけ。コンテナ版で出るかは未確認」と書いていて、`docs/deploy.md:95`、`docs/collection.md:32`、`docs/data-stores.md:272` も「NetFlow を出さない」と言っている。

  文面は設計方針 4 をそのまま写したもので、設計から来ている。

  Should にした理由: コメントだけなので動きは変わらない。ただ、この cycle の目的は説明の食い違いを無くすことで、新しく足した行に食い違いが 1 つ入る。
  直すなら、たとえば「lab の SR Linux は NetFlow を出さない（sFlow はコンテナ版で出るか未確認）。ops/netflow_send.py はこの DNAT を通らず、lab の EC2 のホストから NLB へ直接送る」。

## Nit

- **[correctness（言葉の選び方）] `docs/troubleshooting.md:452`**
  - 「Spark の `with_sysname` は `DEVICE_MAP` を引かず」とあるが、Spark が受けるのは引数の `--device-map`（`app/spark/snmp_sinks.py:168`）。`DEVICE_MAP` は Splunk のタスクの環境変数で、`docs/pipeline.md:704` は両者を書き分けている。
  - 「Splunk の `coalesce('tags.sysName', …)` も同じ」の `coalesce` は保存検索 `nwc_gnmi` / `nwc_trap` / `nwc_trap_clear`（`savedsearches.conf:67,113,151`）にしか無く、どれも `logs` を読まない。
  - 中心の主張（`sysName` があるので機器の名前に置き換わらない）は、`snmp_sinks.py:465-466` と `syslog-ng.conf.in:14,56` で正しい。文面は設計の文そのまま。

- **[correctness（読み違えやすさ）] `docs/architecture/core.md:84`、`docs/architecture/resources/vpc-perimeter.md:63`、`docs/pipeline.md:349`**
  - 揃えた文言の括弧の中は「lab の SG の 162 は送る側だけ。送り元が機器の管理 IP のままの DNAT だけを受ける」。2 文目に主語が無く、直前の「lab の SG の 162 は」を受けて「lab の SG が受ける」と読める。
  - `pipeline.md:349` は「送る側だけで、」と 1 文につないでいるので、この読み違いがいっそう起きやすい。
  - 2 文目の主語を「NLB の 162 は」と書けば、読み違いは無くなる。

- **[design.md との整合性] `docs/architecture/resources/vpc-perimeter.md:63`**
  - ポートの順が「5140/udp、2055/udp、6343/udp」で、写し元の `core.md:84`（「2055/udp、6343/udp、5140/udp」）と違う。用途の並びも同じく違う。
  - 設計方針 2 の文面どおりではあるが、同じ方針 2 は「`core.md:83-84` と同じ 2 行」とも言っている。

- **[correctness] `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:16-17`**
  - 「NetFlow / sFlow」の箇条の中に、syslog の `logger` の案内が入っている。
  - リンク先の「正しい送り方」には 2 つの送り方がある。1 つ目は TRex の netns から 203.0.113.1 へ送って DNAT を通す。「lab の EC2 のホストから NLB へ」に当たるのは 2 つ目だけ。

- **[missing tests] `tests/test_workflow.py:1632-1634`**
  - 「dev server」を大文字小文字を区別して探している。「Dev Server」や「start-dev」に戻されても落ちない。
  - 一方で「Temporal server, UI and worker」がある、という肯定の条件も付いているので、実際に退行を見逃すのは、その文字列を残したまま「dev」を書き足したときだけ。

- **[design.md との整合性（記録）] `docs/cycles/041-nlb-docs-and-workflow-sg/build.md` の検証 5**
  - `stream/telegraf.tf | 6 +-` と貼ってあるが、HEAD の `git diff --stat=200 main` では `7 ++++---`。合計の「22 insertions(+), 15 deletions(-)」は一致しているので、貼るときに写し間違えた見込み。

- **[design.md との整合性（範囲外）] `docs/cml-sandbox.md:558`**
  - 「trap の 162 は NLB の SG が `lab` の SG から受けない」が、揃えた文言（「送る側だけ」）の外に 1 か所残っている。
  - 設計の 5 か所には入っていないので、残ったのは正しい。次に揃えるときの候補。

## 良かった点

- 設計方針 1〜5 の文面が、設計の引用どおりに入っている。行番号のずれも、文言で探して当てていた（`core.md:93` を足した理由も `build.md` に書いてある）。
- 足した check は、直す前の値で落ち、直したあとで通る。`main` の description と HEAD の両方に正規表現を当てて、自分でも確かめた。
- 正規表現は `^    workflow\s+= "…"$` で、`terraform fmt` の揃えの幅が変わっても当たる。行が見つからないときは `None` になって check が落ちるので、見逃す方向には外れない。
- `app/` と `ops/` には差分が無く、設計の範囲を守っている。
- `build.md` のセルフレビューが、上の Should fix 1〜3 と同じ所（S1〜S3）をもう拾っていて、PM の判断に回している。
- `./ops/check.sh` は手元で「すべて通過」になった。

## ユーザーへの質問

None

### PM の確認（Round 1）

再現と分類（読んだだけのものはそう書く）:

- Should fix 1 [design 整合性 + runtime]: `grep -c create_before_destroy …/security_groups.tf` → `0`。`ops/up.sh:587-588` に「消すところで DependencyViolation になる」、`security_groups.tf:14` に「付いている ENI があると消えない」。apply はしていない（環境が無い）ので「見込み」のまま。Should のまま → **直した**: design.md のリスク 1 を書き直し、QUEUE 145 に「041 より前の環境が残っていれば先に `ops/down.sh`」を足した。`up.sh` のガードは QUEUE の新しい行へ。
- Should fix 2 [design 整合性 + correctness]: `ops/up.sh:300` が「trap・syslog・NetFlow・sFlow は lab の EC2 からしか来ない」、`ops/netflow_send.py:8` が「lab の EC2 のホストから」。再現。→ **直した**: `deploy.md:95-96` を「`netflow_send.py` も lab の EC2 のホストから打つものなので来ない」に。
- Should fix 3 [correctness]: `ops/netflow_send.py:1,3,8,19`（NetFlow v5 だけ、sFlow はコンテナ版で未確認、NLB の DNS 名へ直接）。再現。→ **直した**: `lab/telegraf.tf:10-11` を reviewer の案の形に。
- Nit 1（`troubleshooting.md:452`）: `app/spark/snmp_sinks.py:168` が `--device-map`。再現 → **直した**（`--device-map`（Splunk の `DEVICE_MAP` と同じ表）。coalesce の括弧は消した）。
- Nit 2（2 文目の主語）: 読んだだけ → **直した**（3 か所を「NLB の 162 が受けるのは…DNAT だけ」に）。
- Nit 3（ポートの順）: 読んだだけ → **直した**（`vpc-perimeter.md:63` を `core.md:84` と同じ順に）。
- Nit 4（`stream/telegraf.tf:16-17`）: 読んだだけ → **直した**（logger は 2 つ目の送り方だと書いた）。
- Nit 5（テストの大文字小文字）: → **直した**（`.lower()`）。main の description では `False`、`Temporal Dev Server and worker` でも `False`、HEAD の値では `True`（下の出力）。
- Nit 6（build.md の stat の写し間違い）: 直さない（記録）。
- Nit 7（`cml-sandbox.md:558`）: 範囲外。QUEUE に足した（telegraf の SG の description と `up.sh` のガードと一緒に）。

セルフレビュー（build.md）の Should fix との対応: S1 = Should fix 3、S2 = Should fix 2、S3 = Should fix 1、S5 = Nit 1。S4（`vpc-perimeter.md` の表の抜け）は 041 より前からある欠落で、直していない（QUEUE には足さない。`core.md` が正本で、表の一致はテストしない方針のまま）。

```
$ ./ops/check.sh | tail -1
すべて通過
$ grep -n "lab の SG からは受けない" <6 ファイル> | grep -vc "送る側だけ"
0
```

結論（Round 1）: Must 0 / Should 3（3 件とも直した）/ Nit 7（5 件直した、1 件記録、1 件 QUEUE）。実装ファイル（docs と .tf のコメント、テスト）が変わったので、cold reviewer の 2 回目を呼ぶ。
