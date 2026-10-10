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

### cold reviewer 2 回目（review-r02.md。対象 `01a1f33`。opus / effort xhigh）

## サマリ

対象は「NLB の受信と試し方の説明を揃え、workflow の SG の description を直す（041）」の Round 2。

- 全体: `main`（865dfcc）→ HEAD（01a1f33）。13 ファイル、435 行追加 / 20 行削除。そのうち `docs/cycles` を除くと 9 ファイル。
- 前ラウンドからの差分: `a1d387e..01a1f33`。11 ファイル。中身は review.md の新規 185 行と、docs・コメント・テストの 1〜4 行ずつの直し。

Round 1 で直すとした 8 件（Should 3 件、Nit 5 件）は、どれも記録どおりに直っている。
`./ops/check.sh` は手元で「すべて通過」（rc=0）になった。
Must fix は無い。

Should fix は 2 件ある。

- Round 1 の Should fix 2 の直しで書き直した `docs/deploy.md:95` に、この cycle の主題である「NLB が誰から受けるか」について、正本と違う一文が新しく入った。
- Round 1 の Should fix 1 の直し（SG の作り直しのリスク）が、マネージド版の `ops/up.sh` / `ops/down.sh` しか書いていない。OSS 版も同じ `security_groups.tf` を使う。

### 見た観点 / 見ていない観点

見た観点:

- **design.md との整合性**
  - 設計方針 1〜7 を、`git diff main..HEAD` の全行と突き合わせた。
  - Round 1 の review.md にある PM の確認（「直した」とした 8 件）を、`git diff a1d387e..01a1f33` と 1 件ずつ照らした。
  - `diff <(sed -n 83,84p docs/architecture/core.md) <(sed -n 62,63p docs/architecture/resources/vpc-perimeter.md)` を実行し、IDENTICAL だった（設計方針 2 と Round 1 の Nit 3）。
  - 検証 3 は自分で grep し直した。6 ファイルで「受けない」を含み、かつ trap / 162 に触れる行は 7 行あり、7 行とも「lab の SG からは受けない」と「送る側だけ」の両方を含む。
  - `grep -rni "dev server" IaC app ops docs`（`docs/cycles` を除く）は 0 件だった。
  - 検証 4 の deploy.md の grep を、build.md と同じパターンで打ち直した（下の Nit 1）。
- **correctness（docs とコメントの事実）**
  - `security_groups.tf:115-126` を読んだ（NLB の受信は `lab_mgmt` の CIDR と `lab` の SG。162 は `only = "egress"`）。
  - `:160-182` の `only` の扱い（egress と ingress の for_each の条件）を読んだ。
  - `app/spark/snmp_sinks.py:168,461-470` を読んだ（`--device-map` と `with_sysname`）。
  - analytics の `variables.tf:274-275`、`splunk.tf:131`、`outputs.tf:57-58` を読んだ。`--device-map` と `DEVICE_MAP` は同じ `var.device_map` から来ている。
  - `app/syslog-ng/syslog-ng.conf.in:13-17,56,60` を読んだ（`keep-hostname(yes)`、`use-dns(no)`、`tags.sysName=$HOST`、`tags.source=$SOURCEIP`）。
  - `app/containerlab/lab.sh:323-335` を読んだ（DNAT は 162 / 5140 / 2055 / 6343）。
  - `ops/netflow_send.py:1-19` を読んだ。
  - `ops/up.sh:290-305` と `:586-605` を読んだ。
  - `docs/pipeline.md` の見出しを見た。313 行目が「gnmic と Telegraf に入る」、次の `##` は 352 行目で、347 行目の `forward-status` の箇条書きはこの節の中にある。
  - `docs/troubleshooting.md:395-470`（「正しい送り方」の 2 つの送り方）を読んだ。
- **runtime bugs / data loss（SG の description の変更）**
  - `aws_security_group.workload` の定義（`security_groups.tf:151-159`。`lifecycle` は無い）を読んだ。
  - OSS 版の `IaC/terraform/oss/base/core/security_groups.tf` がマネージド版へのシンボリックリンクであることを `ls -la` で確かめた。
  - `oss.tf:75-78`（`workload_security_groups` が `local.security_groups` を引き継ぐ）を読んだ。
  - `ops/oss/up.sh:5,152-160` と `ops/oss/down.sh:50-52` を読んだ。
- **security**
  - `security_groups.tf` の差分は、description 1 行とコメント 1 行だけ。通信の表（`sg_flows`）の行は増えても減ってもいない。
- **missing tests**
  - 足した check の正規表現と条件を、python で次の 4 通りに当てた。

    | 当てた中身 | 結果 |
    |---|---|
    | main の値 | False |
    | HEAD の値 | True |
    | HEAD に「Dev Server」を足したもの | False |
    | `=` の揃えを詰めたもの | True |
- **テストの実行**
  - `./ops/check.sh` を実行した。末尾は「すべて通過」で rc=0。`tests/test_workflow.py` は `通過 371 / 失敗 0` で、足した check が `ok` の行に出ている。
  - `terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss` は rc=0。
  - 実行後の `git status --porcelain -uall` は空だった。

見ていない観点:

- AWS での動作は見ていない。SG の作り直しで `terraform apply` がどう失敗するかは、静的に読んだだけ。plan も apply もしていない。
- `aws_vpc_security_group_*_rule` の `referenced_security_group_id` が、変わったときにその場で更新されるのか、作り直しになるのか。provider の版に当たって確かめていないので、design.md のリスク 1 の「ほかの SG のルールの参照が残って」が本当にそうなるかは確かめていない。ECS のタスクの ENI だけでも `DependencyViolation` になる、という結論は変わらない。
- type safety と API compatibility は、SG のキー・名前・outputs に変更が無いことだけを見た。
- My Repo の全体設計 HTML は対象外。

## Must fix

None

## Should fix

- **[design.md との整合性 + correctness] `docs/deploy.md:95`（Round 1 の Should fix 2 の直しで書き直した行）**

  新しい 95 行目は「`ops/netflow_send.py` も lab の EC2 のホストから打つもの（NLB は lab の SG からしか受けない）なので来ない」と書いている。正本と比べて、ずれが 2 つある。

  - 「NLB は lab の SG からしか受けない」は、正本と違う。`base/core/security_groups.tf:118-126` では、NLB の受信は次の 2 つ。
    - `lab_mgmt`（203.0.113.0/24）から 162 / 5140 / 2055 / 6343
    - `lab` の SG から 5140 / 2055 / 6343

    この cycle は、NLB が誰から何を受けるかの説明を正本に揃えるためのもの（design.md:9）。それなのに、Round 1 の直しでその主題について新しい食い違いが 1 つ入った。
  - 同じ文の「NetFlow / sFlow は lab の SR Linux が出さず」は、Round 1 の Should fix 3 で `lab/telegraf.tf:10` から外した言い過ぎと同じ。そちらは「sFlow はコンテナ版で出るか未確認」に直してあり、根拠は `ops/netflow_send.py:3`。
    しかも `SKIP_LAB` の節では lab の SR Linux 自体が無いので、この理由はそもそも要らない。

  直すなら、`ops/up.sh:300` と同じ言い方にする。たとえば次のとおり。
  > trap・syslog・NetFlow・sFlow は lab からしか来ない（NLB が受けるのは lab の管理ネットワークの CIDR と lab の SG からだけ）。`ops/netflow_send.py` も、`logger` で送る試し方（…）も、lab の EC2 のホストから打つので使えない。

  Should にした理由: `SKIP_LAB` のときの結論（何も来ない）は正しいので、動きは変わらない。困るのは、この行を NLB の受信の説明として読んだ人。管理ネットワークの CIDR からの DNAT（trap の本来の経路）が受けられないと読み違える。Round 1 で同じ種類のずれ（新しく足した行に入った事実の食い違い）を Should にしたので、それに揃えた。

- **[runtime bugs / data loss（記載漏れ）] `docs/cycles/041-nlb-docs-and-workflow-sg/design.md:71`（リスク 1）、`docs/cycles/QUEUE.md:145`、`:146`**

  Round 1 の Should fix 1 の直しは、SG の作り直しのリスクと回避策を、マネージド版の `ops/up.sh` / `ops/down.sh` の名前でしか書いていない。OSS 版も同じ形で影響を受ける。

  - `IaC/terraform/oss/base/core/security_groups.tf` は、マネージド版の `security_groups.tf` へのシンボリックリンク。
  - `oss.tf:75-78` は、`workload_security_groups` に `local.security_groups` をそのまま引き継ぐ。workflow は `oss_replaced` に入っていない。
  - `ops/oss/up.sh:5` は workflow をいつも作る。

  このため、041 より前に立てた OSS 版の環境が残っていると、`ops/oss/up.sh` でも `aws_security_group.workload["workflow"]`（`nwc-oss-workflow`）が作り直しになる。そこでマネージド版と同じように apply が失敗する見込み。回避策は `ops/oss/down.sh`。

  QUEUE:145 のガードの候補も `ops/up.sh:596-605` の形しか挙げていない。OSS 版には別のガードの塊（`ops/oss/up.sh:152-160`）がある。

  直すなら、リスク 1 と QUEUE の 2 行に「OSS 版は `ops/oss/up.sh` / `ops/oss/down.sh`。ガードは `ops/oss/up.sh` にも」を足す。

  Should にした理由: いまは環境が無い（design.md:8）ので、誰も困らない。困るのは、041 より前の OSS 版を残した PC で `ops/oss/up.sh` を打ったとき。マネージド版にしか注意が無いと、その人は書いてある回避策を自分の環境に当てはめられない。Round 1 で同じリスクのマネージド版の書き方を Should にしたので、それに揃えた。

## Nit

- **[design.md との整合性（検証の記録）] design.md:66 の検証 4、`docs/cycles/041-nlb-docs-and-workflow-sg/build.md` の検証 4**

  Round 1 の直しのあと、`deploy.md:96` は「lab の EC2 から `logger` で送る試し方…も同じ理由で使えない」で始まるようになった。そのため、build.md と同じ grep を打つと、0 件ではなく 1 件が当たる。

  ```
  $ grep -n "SKIP_LAB" -A6 docs/deploy.md | grep -cE '^[0-9]+-\s*(- )?lab の EC2 から'
  1
  ```

  行の中身は「使えない」という否定なので、設計の意図（lab の EC2 を使う案内をしない）は満たしている。
  ただ、build.md に貼ってある期待出力（0）は今の状態では再現しない。Round 1 の PM の確認でも、この検証は打ち直していない。
  記録として、review.md か build.md に「Round 1 の直しのあと 1 件当たるが、否定の文」と 1 行残すとよい。

- **[correctness（QUEUE の記述）] `docs/cycles/QUEUE.md:145`**

  - 「`security_groups.tf:27,29` の telegraf_dialout / telegraf_dialout_nlb の description が『traps, syslog and MDT』のまま」とあるが、その文字列が入っているのは 27 行目（telegraf_dialout）だけ。29 行目（telegraf_dialout_nlb）は「Internal NLB in front of the Telegraf dial-out task」で、古さの中身が違う。いまの NLB は syslog-ng と GoFlow2 の前にも立っている。
  - `security_groups.tf:25` に「dialout と NLB の description は cycle 012 で syslog と MDT が抜けても変えない（変えると作り直し）」という記録済みの判断がある。この行はそれをくつがえす候補なので、その行を直すことも書いておくと、次の cycle で見落とさない。
  - 同じ行の括弧の中に、題名（NLB の受信の説明と telegraf の description）と関係の無い「workflow の SG のガードを `up.sh` に置くか」が入っている。「1 行 1 件」の決まりに照らすと、別の行に分けるのが合う。

- **[correctness（言い切りの強さ）] `IaC/terraform/aws-managed/pipeline/stream/telegraf.tf:17`**

  「syslog も同じく lab の EC2 のホストから logger で NLB へ直接送れる」と言い切っている。リンク先の `docs/troubleshooting.md` の「正しい送り方」の 2 つ目は「AWS では未確認」と書いている。
  一方、NetFlow の同じ経路は 2026-10-09 に `flows` で 2 件の実績がある（`troubleshooting.md` の「届かなかったわけ」の 1）。
  コメントでは「送れる（AWS では未確認）」くらいにしておくと、docs と揃う。

## 良かった点

- Round 1 で直すとした 8 件が、review.md の記録どおりに差分へ入っている。
  - Should fix 1〜3
  - Nit 1（`--device-map`）、Nit 2（2 文目の主語）、Nit 3（ポートの順）、Nit 4（「2 つ目」）、Nit 5（`.lower()`）
- `core.md:83-84` と `vpc-perimeter.md:62-63` の 2 行が、`diff` で字面まで一致している。
- 「162 は受けない」を言う 7 か所が、全部同じ 2 語を含んでいる。2 文目の主語も「NLB の 162 が受けるのは…」に揃い、読み違いが無くなった。
- `troubleshooting.md:452` の `--device-map`（Splunk の `DEVICE_MAP` と同じ表）は、analytics の `variables.tf:275` と `outputs.tf:57-58` の配線と合っている。
- `lab/telegraf.tf:10-11` の括弧は、`ops/netflow_send.py:1,3,8` と合う形に直った（DNAT を通らない、NetFlow v5 だけ、sFlow は未確認）。
- テストの check は、大文字小文字を変えた退行でも落ちる。`terraform fmt` の揃えの幅が変わっても当たる（python で確かめた）。
- `app/` と `ops/` には差分が無く、通信の表（`sg_flows`）の行も変わっていない。

## ユーザーへの質問

None

### PM の確認（Round 2）

- 対象: `01a1f33`。cold reviewer: 依頼した（2 回目 / 2 回。opus / effort xhigh）。PM の確認: fable-5-1 / effort high。
- Should fix 1（deploy.md:95）: 再現した。`base/core/security_groups.tf:118-126` を読んだ。NLB の受信は `lab_mgmt`（203.0.113.0/24）から 162/5140/2055/6343 と `lab` の SG から 5140/2055/6343 の 2 つで、「lab の SG からしか受けない」は誤り。`ops/up.sh:300` と同じ言い方に直した（SR Linux の言い過ぎも外した）。
- Should fix 2（design.md リスク 1、QUEUE）: 再現した。`ls -l IaC/terraform/oss/base/core/security_groups.tf` はマネージド版へのシンボリックリンク、`oss.tf:75-78` は `local.security_groups` を引き継ぎ、`ops/oss/up.sh:5` は workflow をいつも作る。リスク 1 と QUEUE 145（AWS で見るもの）に `ops/oss/down.sh` を足した。
- Nit 1（検証 4 の grep）: 再現した（`grep -n "SKIP_LAB" -A6 docs/deploy.md | grep -cE '^[0-9]+-\s*(- )?lab の EC2 から'` は Round 1 の直し後 1。今回 95-96 行目を書き直したので、いまは `ops/netflow_send.py` で始まる行になり 0 に戻る）。
- Nit 2（QUEUE の telegraf の description）: 再現した（`:29` は「Internal NLB in front of the Telegraf dial-out task」）。QUEUE の行を `:27` / `:29` の実物と `security_groups.tf:25` の記録済みの判断に直し、up.sh のガードを別の 1 行に分けた。
- Nit 3（stream/telegraf.tf:17）: 「（AWS では未確認）」を足した。
- `./ops/check.sh`: 末尾「すべて通過」。
- 結論: Must 0 / Should 2（2 件とも直した）/ Nit 3（3 件とも直した）。cold reviewer は 2 回使い切ったので呼ばない。直したのは docs とコメントだけで、実装（`security_groups.tf` の description、テスト）は Round 1 のまま。サイクル完了。QUEUE の 3 行を完了にした。
