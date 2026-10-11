# 047 EC2 のロールの `AmazonSSMManagedInstanceCore` を自前の SSM Agent の許可に置き換える ── レビュー

## Round 1

対象: 5897a6b → 5659256（エンジニア opus 5.5 / high、Round 1。build.md の Round 1）。cold reviewer に依頼した（初回ビルド直後）: opus / xhigh。結果は下の「cold review r01」（`review-r01.md` を連結）。

PM の確認（fable-5-1 / high）:

- 全テスト 16 本を取り直した（PM の worktree、5659256）: test_lab_debug 121 / test_stream 119 / test_nautobot 68 / test_oss 177 ほか、全部失敗 0。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0
- build.md のセルフレビュー: Must 0 / Should 1（直した。T-9 を足した）/ Nit 4（最終報告へ）。エンジニアの逸脱 1（T-8 を 3 つのロールに絞る。ECS のタスクの実行ロールが `ssm:GetParameters` を正当に使う）と 2（description にマネージドポリシーの名前を書かない）と 5（T-9）は design.md に取り込んだ
- lab の `kafka_load.sh` の `msk-bootstrap`（エンジニアの Nit 1）は読んだだけ: `app/containerlab/trex/README.md` の「前提」1 が、lab のロールに MSK の許可とこの読み取りが無いことを 047 より前から書いている。道具はいまも動かないので Allow は足さず、design.md の未確定事項 7 に書いた
- cold review の Nit 4 件: Nit 1（`ssm-parameter-store.md:86` の「までは」→「より前は」）と Nit 4（`docs/architecture/README.md:90` に `ssm_agent.tf`）と Nit 2（design.md の M8 を M14 に）は PM が docs を直した。Nit 3（T-9 が `NotAction` の Allow を見ない、T-8 の実行ロールの判定が `exec` の字面）は直さない。実装ファイルは Round 1 から変わらないので、cold review の 2 回目は呼ばない
- AWS での確認（design.md の検証方法の「AWS で動かす」）はマージ後にメインのチェックアウトから PM が打ち、結果を下の「AWS での確認」に書く

### cold review r01（opus / xhigh）

# 047 コールドレビュー Round 1

対象: 「EC2 のロールの AmazonSSMManagedInstanceCore を自前の SSM Agent の許可に置き換える（047）」。`git diff 5897a6b HEAD`（16 files changed, 533 insertions(+), 23 deletions(-)）を、作業ツリーの `design.md`（PM の未コミットの直しを含む版。T-8 を Web・lab・lab-debug のロールに絞った文言、T-9、M8、リスク 7）と突き合わせた。

## サマリ

- 設計どおりに実装されている。
  - base/core の `aws_iam_policy.ssm_agent`（`<prefix>-ssm-agent`）は 13 + 4 + 6 の Action で、設計の表と同じ順になっている。Resource は `*`。
  - `output ssm_agent_policy_arn` を足した。
  - Web の `web_ssm` を差し替えた。
  - lab の `lab_ssm` は count を付けず、remote state を `try` と 2 つ目の postcondition で読む。
  - lab-debug は `ManagedPolicyArns` を消し、インラインの `ssm-agent` を `Policies[1]` に置いた。`network-perimeter` は `Policies[2]` に移った。
  - OSS 版の `ssm_agent.tf` はリンクにした。
  - `DenyNautobotSecrets` は 3 つとも残り、コメントを書き換えた。
  - docs 4 本を直した。
- Must fix 0 / Should fix 0 / Nit 4。
- 残るのは docs の言い回し、設計と build.md の変異の番号のずれ、字面で検査するテストの既知の穴だけ。どれも実行時の挙動やセキュリティの退行には効かない。

### 見た観点 / 見ていない観点

見た観点:

- 設計との整合
  - `ssm_agent.tf` の Sid・Action・順が design.md の表と同じ。
  - `web.tf` の `web_ssm`、`pipeline/lab/iam.tf` の `lab_ssm`、`locals.tf` の `try` と postcondition を見た。
  - `lab-debug.yaml` の `Policies[0..2]` と、OSS 版の `IaC/terraform/oss/base/core/ssm_agent.tf` のリンク先を見た。OSS 版の `pipeline/lab` もリンクなので同じ `locals.tf` を通る。
  - design.md:58 の docs の指示を確かめた。`docs/architecture/core.md:22` の `<prefix>-network-perimeter` は「IAM の Deny」の層の行で、base/core のリソース一覧ではない。`<prefix>-ssm-agent` は Deny ではないので、ここに足さないのが正しいと判断した。
- correctness
  - Action の一覧は、手元で知っている v2 の 15 + 4 + 6 から `ssm:GetParameter` / `GetParameters` を抜いた形と一致する。
  - attachment の `policy_arn` の参照で、作る順は ssm_agent → attachment になる。
  - 上げる順は `ops/up.sh` の base/core → lab と `ops/oss/up.sh` の 302 → 348 行目、消す順は `ops/down.sh` と `ops/oss/down.sh` の lab → base/core。
- security
  - コメント以外の行に `AmazonSSMManagedInstanceCore` が残っていない（`git grep` で `IaC/` と `ops/` を見た）。
  - IaC のマネージドポリシーの添付は `AmazonECSTaskExecutionRolePolicy` だけ。
  - Web のパラメータの読み取りは全部 `/<prefix>/` の下。lab は `telegraf-address` と `telegraf-source-cidr` の 2 つだけ。
- runtime bugs
  - lab-debug のロールのインラインの合計: lab-assets 1088、ssm-agent 886、network-perimeter 454、合計 2428 で上限 10240 より小さい（scratchpad の `size.py` で、空白を除き置換した値）。
- API compatibility
  - output は足しただけで、消した output は無い。
- missing tests
  - T-1〜T-9 を流した。設計の M8 と NotAction の変異は、scratchpad の写しで自分で入れて確かめた。

自分で実行したもの（出力の抜粋）:

- `uv run -q --group dev --group web python tests/test_lab_debug.py` → `通過 121 / 失敗 0`
  - T-1〜T-9 は ok。T-9 は `見た箇所 28、いま外れ: []`。
- `uv run -q --group dev --group web python tests/test_stream.py` → `通過 119 / 失敗 0`
- `uv run -q --group dev --group web python tests/test_nautobot.py` → `68 項目すべて通過`
  - `neptune read failed, using static data` の行も出るが、047 とは関係ない。
- `terraform fmt -check -recursive` → rc=0
  - `IaC/terraform/aws-managed` と `IaC/terraform/oss` の両方で見た。
- `terraform init` / `validate` → 3 つとも `Success! The configuration is valid.`
  - base/core、pipeline/lab、OSS 版の base/core を、scratchpad に `cp -RL` した写しで見た。作業ツリーに `.terraform` を作らないため。

見ていない観点:

- AWS の上の挙動。新しい許可で PingStatus が Online になるか、Session Manager と Run Command（`ssm_run`）が通るか。
- `aws cloudformation validate-template`（AWS が要る）。
- AWS の上の AmazonSSMManagedInstanceCore の現行版との照合。上の一致は手元の知識との比較だけ。
- 古い state で postcondition が apply を止め、destroy は通ること。コードを読んだのと build.md の実測の記述だけで、自分では再現していない。
- `ops/check.sh` の全体と、ほかの 13 本のテスト。
- 動いている環境を 047 に上げるときの、attachment の作り直しの数秒のあいだの SSM Agent の挙動。

## Must fix

None

## Should fix

None

## Nit

- [docs] `docs/architecture/resources/ssm-parameter-store.md:86` の「cycle 047 までは Web・lab・lab-debug のロールが AWS の管理ポリシー…で…読めた」は、047 を含むようにも読める。
  - 同じ事実を `IaC/terraform/aws-managed/base/core/locals.tf:29` のコメントは「cycle 047 より前は」と書いており、揃っていない。「cycle 047 より前は」に揃えるとよい。
  - Nit とした理由: 意味は文脈で取れ、挙動にも検査にも効かない言い回しだから。
  - 根拠: `sed -n 86p docs/architecture/resources/ssm-parameter-store.md` の出力に `cycle 047 までは Web・lab・lab-debug のロールが AWS の管理ポリシー（ssm:GetParameter / GetParameters が *）で…` とある。
- [設計との整合] 変異の番号が design.md と build.md でずれている。
  - design.md:93（作業ツリーの版）の M8 は「`web_assets` に `ssm:GetParameter` の Resource `"*"` の Statement を足す → T-9」。build.md の M8 は「postcondition を `true`」（build.md:126）で、設計の M8 にあたるのは build.md の M14（build.md:226）。
  - 中身は揃っている。設計の M8 を scratchpad の写し（`IaC` `ops` `tests` `app` `docker` を `cp -R`）の `web.tf:40` に 1 行で入れ、写しの `tests/test_lab_debug.py` を流すと T-9 で落ちた: `AssertionError: T-9 … （見た箇所 29、いま外れ: ['IaC/terraform/aws-managed/base/core/web.tf:40']）`、rc=1。
  - 入れる前の写しは `ok T-9 …（見た箇所 28、いま外れ: []）`。写しはそのあと `deploy.env.example` が無くて止まるが、これは写した範囲の都合で 047 とは関係ない。
  - 設計を確定するときに、番号を build.md に合わせるか対応を 1 行書くとよい。
  - Nit とした理由: 検査の中身は設計どおりにあり、番号の対応がずれているだけだから。
- [missing tests] T-9 は字面で切り出すので、Allow の `NotAction` を見ない。
  - scratchpad の写しの `web.tf:40` に `{ Effect = "Allow", NotAction = "s3:*", Resource = "*" },` を入れても、T-1〜T-9 は全部 `ok` だった。この形ならパラメータは全部読めてしまう。
  - T-8 で許す側は、`aws_iam_role_policy` の `role` の参照に `exec` という字面を含むかで決まる。名前の付け方しだいで素通りする。
  - どちらも build.md の「ジンテーゼ」が既知の限界として書いており、IAM の評価の全体は PM が AWS で確かめる段取りになっている。
  - Nit とした理由: いまの IaC に `NotAction` の Allow は無く、退行を防ぐ網の穴でしかないから。
- [docs] `docs/architecture/README.md:90` の base/core の一覧は「閉域の Deny（perimeter.tf）」をファイル名つきで挙げるが、`ssm_agent.tf` は挙げていない。「ロール」に含まれるとも読める。
  - design.md:58 の grep（`AmazonSSMManagedInstanceCore\|network-perimeter`）には当たらない行なので、設計の指示に反してはいない。足すなら「SSM Agent の許可（ssm_agent.tf）」。
  - Nit とした理由: 一覧の粒度の好みの範囲で、設計の指示の外だから。

## 良かった点

- perimeter と同じ受け渡し（output、remote state、`try`）にそろえている。そのうえで perimeter と違って count で飛ばさない理由（無いと管理ノードにならない）と、古い state を postcondition で止める理由を、`pipeline/lab/iam.tf:17-18` と `locals.tf:36` / `:55` のコメントに書いている。
- T-7 は、YAML のインラインの `ssm-agent` を `ssm_agent.tf` をパースした結果と比べる（Sid・Action・順・キーの集合・3 つ）。Terraform と CloudFormation の片方だけを直す退行を捕まえられる。
- 設計に無い T-9 をセルフレビューから足し、M14〜M19 で落ちることを build.md に残している。設計の M8 を自分で入れたときも T-9 で落ちた。
- `DenyNautobotSecrets` は 3 つとも残した。「マネージドポリシーに勝つ」というもう事実でない説明は、いまの Allow（`/<prefix>/*` とほかのルートの Allow）に合わせて書き換えた。
- description にマネージドポリシーの名前を書かず、T-1（コメント以外の行に名前が無い）と食い違わないようにしている。

## ユーザーへの質問

None
