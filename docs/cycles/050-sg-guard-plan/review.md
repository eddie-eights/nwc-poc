# 050 SG の作り直しと削除を terraform plan で止める: レビュー

## Round 1

レビュー: cold reviewer（opus-5.5 / effort: xhigh）に依頼した（初回ビルド直後）。対象は `3d58a34`（52a39c6..3d58a34）。PM(opus-5.5) が確認した。Must 0 / Should 0 なので、このラウンドが完了判定の直前のレビューを兼ねる（以後、実装ファイルは 1 バイトも変えていないので 2 回目は依頼しない）。

### cold reviewer の結果（review-r01.md をそのまま連結）

# 050 SG の作り直しと削除を terraform plan で止める — cold review Round 1

## サマリ

- 対象:
  - `52a39c6..3d58a34` の差分（build.md を含めて 11 ファイル、追加 500 行・削除 811 行）。
  - `docs/cycles/050-sg-guard-plan/design.md`（699f3cb の直しを含む今の版）と `build.md` の Round 1。
- 結論:
  - Must fix 0 / Should fix 0 / Nit 5。
  - `check_sg_plan` は design の方針 1〜8 どおり。止めるべき場面はすべて止まる側に倒れている（SG の replace / delete、plan の失敗、change_summary が無い、python の失敗、TF_CLI_ARGS*）。
  - 作り直し・削除になる SG を誤って通す経路は見つからなかった。
- 自分で打ったテスト:
  - `uv run --directory /Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/pm-042 --group dev --group web python tests/test_workflow.py` → 「通過 457 / 失敗 0」。
    - 050 の check（T-1 の 2 件、T-2〜T-13、T-18、T-14）がすべて ok。
    - T-14 は skip の行が出ず、本物の terraform で走った。`terraform version` → `Terraform v1.16.0 on darwin_arm64`。
  - `uv run --directory /Users/eight/Documents/Dev/sandbox/nwc-poc/.claude/worktrees/pm-042 python tests/test_oss_ops.py` → 「通過 210 / 失敗 0」。050 の T-15、T-16、T-17 が ok。

### 見た観点 / 見ていない観点

見た観点:

- 設計整合性
  - design の方針 1〜8、検証の T-1〜T-17、変異の M1〜M8 を、コード・テスト・build.md の逸脱と照らした。
- correctness
  - plan に渡す変数:
    - `-var owner` は apply（`ops/up-common.sh:14` の `tf_apply_only`）と同じ。
    - 手順 0 では渡さない手順 3 の変数（`interface_endpoints` / `network_perimeter` / `endpoints_az_num` / `create_opensearch_endpoint`）は、SG にも VPC にも効かない。
      - `security_groups.tf:154-199` の SG は 2 つ。`oss.tf:76-79` の `workload_security_groups` は `local.oss` だけで決まる。`aws_vpc.this` は `var.vpc_cidr` だけで決まる。
    - base/core の変数で既定値が無いのは `owner` だけ（`variables.tf`）。`-input=false` の plan が「No value for required variable」で落ちることはない。
  - python の終了コードと bash の `case`（`ops/up-common.sh:81-87`）の対応。
  - `set -euo pipefail` の下での `|| rc=$?` / `|| prc=$?`。pipefail 下での printf → python のパイプ。
  - SG のリソースは `aws_security_group.workload` と `aws_security_group.endpoints` の 2 つだけで、どちらも `resource_type == "aws_security_group"` で拾える。
  - 呼ぶ場所（`ops/up.sh:587-613`、`ops/oss/up.sh:154-167`）:
    - PY・認証（`tf_use_cli_credentials`）・`MAIN_VARS` が手順 0 より前に決まっている。
    - `MAIN_VARS` に足すのは手順 3 だけ。
- security
  - plan ファイルを残さない。`-input=false` が付いている。TF_CLI_ARGS* を止めている。
  - stderr に出るのは diagnostic の `@message` / `detail` と、JSON でない行と、change_summary が無いときに読んだ JSON の行だけ。plan -json の UI の行は、アドレス・action・reason は持つが、属性の値は持たない。
- runtime bugs
  - bash 3.2 のマルチバイトの変数名（`${rc}` / `${prc}` で回避済み）。
  - `read` の IFS。
  - uv の終了コード（Nit 1）。
- data loss
  - 止まるときは、apply より前・何かを作るより前（手順 1 の ECR の前）に die すること。
- missing tests
  - 上の 2 本を打って、050 の check が ok なことを見た。

見ていない観点:

- AWS で実際に打つこと。design の方針 8 と未決 2 のとおり打っていない。本物の base/core の `plan -json` が出す aws_security_group の行（design の未決 1）も見ていない。
- ほかの 14 本のテスト、`ops/check.sh`、`bash -n` は打っていない（build.md の記録を読んだだけ）。
- 変異 M1〜M9 の打ち直し（build.md の表を読んだだけ）。
- `/bin/bash`（3.2）の上で `check_sg_plan` を単独で打つこと（テストの中で通ったものだけ）。
- API compatibility / type safety: 外に出す API や型の変更が無いので対象外。

## Must fix

None

## Should fix

None

## Nit

- **[runtime bugs] `ops/up-common.sh:41`・`:81-86`: PY が uv のとき、uv 自身が rc=2 / 3 で終わると、python の終了コードと区別できず、誤った文言で止まる。**
  - PY が uv になるのは、python3 が無い PC だけ（`ops/up.sh:439`、`ops/oss/up.sh:132` の `uv run --python 3.13 python`）。
  - uv が rc=2 で終わると、「plan が失敗した（rc=0。上の Terraform のエラー）」と出る。
  - uv が rc=3 で終わると、hits が空なので、一覧が「（）」だけの die になる。
  - Nit とした理由: どちらも止まる側で、通してしまうことはない。build.md のセルフレビューの指摘 2 と同じ。
  - 読んだだけ。再現手順:
    - `PY=(/bin/bash -c 'cat >/dev/null; exit 2' x)` にする。
    - `tf` を、`version` と `change_summary` の 2 行を出す関数にする。
    - その状態で `check_sg_plan` を呼ぶ。

- **[missing tests] `tests/test_workflow.py:1470-1484`（T-1）、`tests/test_oss_ops.py` の T-15: マネージド版の `ops/up.sh` が正しい場所で 1 回だけ plan を打つことを縛るテストが無い。**
  - T-1 は、`check_sg_plan` の位置を文字列の並び順で見ているだけ。
    - `if [ -f "$TF_DIR/base/core/terraform.tfstate" ]`（`ops/up.sh:593`）の中にあることは縛っていない。
    - 偽の terraform で `ops/up.sh` を通して、plan を 1 回だけ打つことも確かめていない。
  - 通して打つ check（T-17）は `ops/oss/up.sh` にしか無い。
  - Nit とした理由:
    - design の T-1 の要求は満たしている。`check_sg_plan` の本体は共通で、T-2〜T-14 が見ている。マネージド版の呼び出しは OSS 版と同じ形で、読んで正しい。
    - if の外に出ても、state が無い初回の plan は全部 create になって通るだけで、誤って通す方向には壊れない。
    - if の中にあることを縛っていない件は、build.md のセルフレビューの指摘 3 と同じ。
  - 読んだだけ。再現手順: `ops/up.sh:612` の `check_sg_plan …` の行を 613 行の `fi` の後へ移して `tests/test_workflow.py` を打つ。並びは「tf_init base/core の後・log "1. ECR" の前」のままなので、T-1 は ok のままと読める（打ってはいない）。

- **[設計整合性] `docs/troubleshooting.md:28`: 手で確かめる例 `terraform -chdir=<TF_DIR>/base/core plan -json -refresh=false -var owner=<OWNER>` が、`check_sg_plan` の打ち方（`ops/up-common.sh:38`）と違う。**
  - 例に足りないもの:
    - `-input=false`。
    - `tf()` が付ける認証（`ops/common.sh:34` の `TF_AWS_ENV`）。
    - `VPC_CIDR` を設定しているときの `-var vpc_cidr=…`。
  - そのまま手で打つと、認証で落ちるか、既定の vpc_cidr で VPC と SG の replace という、up.sh と違う差分が出ることがある。
  - 同じ表の 27 行の「state の lock が残っている」も不正確。base/core の state は手元のファイル（699f3cb で design を直した点）なので、「別の terraform が同じルートで走っている」と書く方が正確。
  - Nit とした理由: change_summary が無いという、まれな止まり方の調べ方にしか効かない。守りそのものには影響しない。build.md のセルフレビューの指摘 7 と同じ。
  - 読んだだけ。

- **[設計整合性] `docs/architecture/core.md:102`、`docs/architecture/resources/vpc-perimeter.md:96`: 項目の頭の文が「SG の説明（description）を変えると作り直しになる」のままで、続く文より狭い。**
  - 続く文は、name / description / vpc_id の変更とキーの削除で止まる、と書いている。
  - Nit とした理由: 次の文が正しいので、本文を読む人は誤らない。頭の文だけを拾う人に、name とキーの削除が見えないだけ。
  - 読んだだけ（`sed -n 101,104p docs/architecture/core.md` と `sed -n 96,99p docs/architecture/resources/vpc-perimeter.md` で見た）。

- **[設計整合性] `docs/cycles/050-sg-guard-plan/design.md:107`: 変異の表が「M1 … → T-5 / T-6 / T-16」のままで、build.md と食い違う。**
  - build.md は、逸脱 5（`build.md:51-53`）と変異の表（`build.md:125`）で「M1 では T-16 は落ちない」と書いている。
  - 699f3cb は S3 backend の誤りだけを直し、この行は直していない。
  - Nit とした理由: M1 は T-5 と T-6 で落ちるので守りの穴ではなく、design の記述が食い違っているだけ。build.md のセルフレビューの指摘 6 と同じ。
  - 読んだだけ。

## 良かった点

- 3 段の守りを、Terraform 自身の plan の答え（planned_change の action）1 つに置き換えた。
  - 置き換えた 3 段: awk で HCL の表を読む、書き方の守り、console。
  - 043〜049 で 1 つずつ足してきた穴（name、vpc_id、キーの削除、endpoints、OSS 版）がまとめて塞がった。
  - 削除 811 行・追加 500 行で、コードも減った。
- 失敗の枝が、すべて止まる側に倒れている（plan の rc≠0、change_summary が無い、python の例外、TF_CLI_ARGS*）。
  - Warning だけは stderr に出して通す、という線引きも design どおり。T-9 と T-14(a) がそれを確かめている。
- T-14 が Terraform v1.16.0 の本物の `plan -json` を terraform_data で打っている。偽の JSON の形だけに合わせたテストになっていない。
- セルフレビューで見つけた python の失敗の枝の穴を、T-18 と M9 を足して自分で塞いだ。
- `MAIN_VARS` を手順 0 の前に出し、plan と apply に同じ vpc_cidr を渡すようにした。
  - 手順 3 の変数が SG に効かないことは、コメント（`ops/up-common.sh:37`）にも書いてあり、コードと合っている。

## ユーザーへの質問

None


### PM の確認（2026-10-12）

- 全テスト 16 本を pm-042 で取り直し（docs の Nit を直した後）: 失敗 0（`test_workflow.py` 457 / `test_oss_ops.py` 210 ほか）。`bash -n ops/up-common.sh ops/up.sh ops/oss/up.sh` rc=0。`ops/check.sh` rc=0（「すべて通過」）
- エンジニアのセルフレビューの Should fix 1（python の失敗の枝を縛る check が無い）: いまのコードで「ok 050 T-18 check_sg_plan: plan の出力を読む python が例外で落ちたら（planned_change の change が dict でない）…」。解消
- 本物の terraform の check が skip されずに走ったこと: 「ok 050 T-14 check_sg_plan: 本物の terraform plan -json（v1.16.0 の形）で、(a) SG でない replace と宣言の無い変数の Warn…」
- cold reviewer の Nit 3（troubleshooting の手で打つ plan の例と「state の lock が残っている」）、Nit 4（core.md / vpc-perimeter.md の項目の頭の文が description だけ）、Nit 5（design の変異の表の M1 → T-16）: 安い docs なので PM が直した。design の「S3 backend」の誤り（エンジニアの Nit 5）は 699f3cb で直した
- 直さない Nit: cold reviewer の Nit 1（PY が uv のとき uv 自身が rc=2 / 3 で終わると文言が不正確。止まる側）、Nit 2（マネージド版の up.sh で plan が `if [ -f …tfstate ]` の中で 1 回だけ打たれることを通しで縛るテストが無い）、エンジニアの Nit 4（SG の remove / move / resource_drift の行を通す check が無い）。最終報告に載せる
- 見た観点: design 整合性、correctness、runtime bugs、missing tests（cold reviewer と上の実行）。security / data loss は守りが止める側に倒れる変更なので cold reviewer の確認に依った。AWS では動かしていない（本物の state と data source での plan は QUEUE の「050 の plan の守りを AWS で 1 回通す」）
- サイクル完了。QUEUE 163 と 164 を完了に。全体設計 HTML は触らない（`up.sh` の手順 0 の守りの中身が変わっただけで、構成要素・データの流れ・配置は変わらない）
