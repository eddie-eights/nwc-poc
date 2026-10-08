# lab を IS-IS で組み直し、各 leaf に TRex をつなぎ、lab の EC2 を x86 にする（011）— review r01

対象は b8f5961..fa6234f（84 ファイル、+2312 / −1198）。判断の根拠は design.md と実際のコードだけ。

## サマリ

実装は design.md の表と実装ステップどおりに入っている。

- **トポロジ:** 7 台・12 回線、TRex は各 leaf の e1-3 へ 1 本ずつ。LAG / bond0 / ES は消えた。
- **x86 への移行:** m6i.xlarge、x86_64 の AMI、amd64 の rpm とイメージ。
- **ECR:** trex のリポジトリが足された。
- **名前の置き換え:** 新しい名前（s-leaf / a-leaf / trex）が app・ops・IaC・docs・tests に揃っている。

Must fix はない。ただし、このサイクルで機器の名前を変えたことで、古い `.cli` が S3 から EC2 へ流れ込む経路が 1 つできる（Should fix 1 件）。

**分類の基準:**

- **Must fix:** design.md に反するか、今の入力・状態で壊れるもの。
- **Should fix:** design.md には反しないが、到達できる状態で動作が壊れるか誤るもの。
- **Nit:** 今は壊れないが、将来ずれやすい点や読みにくい点。

### 見た観点 / 見ていない観点

**自分で打って確かめたこと**（出力はこのセッションの Bash）:

- **テスト 15 本:** `uv run --group dev --group web python tests/<name>.py` を全部打ち、どれも失敗 0 だった。

  | テスト | 通過 | テスト | 通過 | テスト | 通過 |
  |---|---|---|---|---|---|
  | test_alerts | 158 | test_lab_debug | 97 | test_oss_roll | 66 |
  | test_analytics | 489 | test_local_compose | 122 | test_stream | 83 |
  | test_app | 161 | test_nautobot | 69 | test_sync | 103 |
  | test_dashboard_config | 3 | test_oss | 171 | test_workflow | 325 |
  | test_graph | 78 | test_oss_ops | 156 | test_kb_index | 7 |

  本数は `docs/development.md` の新しい値と一致した。
- **Terraform:**
  - `terraform fmt -check -recursive IaC/terraform/aws-managed` は差分なし。
  - `terraform validate` は `pipeline/lab` と `base/ecr` の 2 ルートで `Success!`。どちらも既にある `.terraform` で打った。
- **シェルの構文:** `bash -n` が 8 本とも通った（`lab.sh`、`setup.sh`、`trex/kafka_load.sh`、`ops/lab-common.sh`、`ops/lab-debug.sh`、`ops/up.sh`、`oss/ops/up.sh`、`docker/compose/lab.sh`）。
- **design 検証 6 の grep:**
  - 空にはならない。当たるのは Web の EC2・RDS・Neptune の `t4g`、日付の付いた `docs/verification/` と `docs/alert-comparison.md` の過去の記録、`tests/test_sync.py` の合成データ（bond0 / lag をわざと足す検査）。
  - lab の現行の記述に残っている古い名前はなかった。build.md「検証 6」の説明と同じ結果。
  - design の式そのもの（`t4g` を含むこと、`--gnmi-targets | grep -c` が行数を数えること）が、成り立たない期待値を書いている。これは design 側の書き方の問題で、実装の指摘にはしない。
- **トポロジの出力:** `lab_topology.py app/containerlab` と `--layers` の出力。evi のトンネル 6 本（4 台の全組）を目で見た。

**読んで確かめたこと:**

- **lab の定義:** `gen_lab.py` と、生成された `splab.clab.yml.in`・`srlinux/dc1-a-leaf-01.cli`。
- **lab.sh:**
  - `trex start|stop|status`、`trex_cfg`、`edge_ports`。
  - `check`、`failover`、`trap-test`、`routers` とその呼び出し元。
- **EC2 の支度:** `setup.sh` と `trex/`。
- **ops:** `lab-common.sh`（`mirror_image`、`build_telegraf`、`upload_lab`、`dir_tag`）、`lab-debug.sh`、`ops/up.sh`、`oss/ops/up.sh`。
- **IaC:** lab の TF（variables / instance / iam / outputs / tftpl）、`base/ecr`、`lab-debug.yaml`。
- **app:**
  - `impact` の `END_ROLES` の変更（agentcore/topology.py と temporal/rules.py）。
  - `awsio.read_topology`、dashboard、nb_map、snmp_sinks、netops_sns、Grafana の provisioning、`tools.json`。
- **tests の差分**（TRex の項目と、LAG / bond / ES を合成データで見張る項目）と build.md。

**見ていない観点:**

- **`bash ops/check.sh` の全体。** 打っていない。`terraform init` が `.terraform.lock.hcl`（追跡しているファイル）を書き換えうるので、既存のファイルを変えない制約から避けた。上の 15 本、fmt、2 ルートの validate、bash -n で代わりにした。
- **shellcheck。** この PC に入っていないので打っていない。
- **AWS の実機。** TRex 2.41 の af_packet での起動、メモリ、`kafka_load.sh`、STL のプロファイルの実送信は design の未確定事項 2〜4 で、このサイクルの合格条件の外にある。
- **docs の各ページ。** 文言は grep で古い名前と費用の値を見ただけで、全文は読んでいない。

## Must fix

None

## Should fix

- [runtime bugs] 古い機器名の `.cli` が S3 から lab の EC2 へ戻り、`sudo lab logs` が途中で止まる。
  - **原因:**
    - `ops/lab-common.sh:103` の `upload_lab` は `aws s3 sync` を `--delete` なしで打つ。そのため、このサイクルで消した `srlinux/dc1-leaf-0*.cli` / `dc1-leafsw-0*.cli` が `s3://<bucket>/lab/srlinux/` に残る。
    - EC2 の側は `aws s3 sync --delete s3://<bucket>/lab/ $LAB/src/` で S3 の中身をそのまま写す（`lab_user_data.sh.tftpl:34`、`lab-debug.yaml:468`）ので、古い `.cli` も EC2 に来る。
  - **壊れ方:**
    - `lab.sh:74` の `routers()` は `srlinux/*.cli` の glob で機器を数える。
    - `lab logs`（引数なし。`lab.sh:209`）は、ソート順で `dc1-a-leaf-01/02` のあとに古い `dc1-leaf-01` へ `x dc1-leaf-01 tail …`（= `docker exec clab-splab-dc1-leaf-01`）を打つ。そこで `set -e` によって終わり、s-leaf と spine のログは出ない。
    - `lab forward-status`（`lab.sh:331`）は、存在しない機器の空の行を 6 行出す（`|| true` があるので止まりはしない）。
  - **起きる条件:** バケットが残ったまま、このサイクルのコードで材料を置き直したとき。
    - マネージド版・OSS 版では、土台（`base/core`）が立った状態で `ops/up.sh` を打ち直したとき（バケットは `ops/down.sh` で消えるので、down してから up するなら起きない）。
    - デバッグ用では、011 より前から立っているスタックで `ops/lab-debug.sh sync` / `up` を打ったとき。
  - **直し方の候補:** どちらか。
    - `upload_lab` の `srlinux/` に `--delete` を付ける。
    - `routers()` を `splab.clab.yml` の nodes（`kind: nokia_srlinux`）から取る。

## Nit

- [runtime bugs] デバッグ用の Telegraf だけ、ECR のタグにアーキが入っていない。
  - lab の 3 つのイメージは、arm64 の古い写しと名前がぶつからないよう `-amd64` を付けた（`lab-common.sh:12-18`）。
  - `ops/lab-debug.sh:141-145` の Telegraf は、`telegraf_tag`（`app/telegraf/` と Dockerfile のハッシュ）だけのタグで `linux/amd64` をビルドする。`ecr_has` がタグを見つけるとビルドを飛ばす。
  - 今回は Dockerfile にコメントを 1 行足したのでハッシュが変わり、arm64 のときのタグとはぶつからない。今は壊れていない。
  - ただ、同じ仕組みで守っていない。デバッグ用の EC2 のアーキを再び変えたときなどに、同じタグの別アーキを引く。lab のイメージと同じく `-$LAB_ARCH` を付けると揃う。
- [design consistency] `IaC/terraform/aws-managed/base/ecr/outputs.tf` の説明が、ECR に置くタグを上流の版のまま書いている。
  - 該当は 7 / 12 / 17 行目。`with tag 26.7.2`、`with tag v0.10.0`、`with tag 2.41` となっている。
  - 実際に置くタグは `lab-common.sh` の `*_ECR_TAG`（`26.7.2-amd64` など）で、lab の `variables.tf` の `trex_image_tag` の説明はそう書いている。
  - multitool の説明には amd64 の記述もない。
- [correctness] ワーカーの事前チェックでは、TRex が中継として数えられたままになっている。
  - `app/temporal/awsio.py:107` の `read_topology` は機器の `role` を読まない。そのため `rules.impact` の `END_ROLES` が効かず、TRex が 4 台の leaf をつなぐ中継として数えられる。
  - 結果として、fail-main の状態から `heal-main` を事前チェックしたときの答えがチャットと違う。
    - チャットの `what_if`（`role` あり）は「冗長が戻る」を出す。
    - ワーカーでは a-leaf-01 の本数が spine-02 + TRex の 2 本と数えられるので、「冗長が戻る」を出さない。
  - docstring（`rules.py:90`）に書いてあり、今の `ACTION_CHANGES` では判定（verdict）は変わらない。それでも 2 つの経路の答えがずれるので、`read_topology` で `n.role` も返すと揃う。
- [runtime bugs] `lab.sh` の TRex の起動中の判定が、サブコマンドによって違う。
  - `trex start` は `pgrep -f _t-rex-64`（`lab.sh:372`）で判定する。
  - `status` と `stop` は `t-rex-64`（`lab.sh:382` / `:384`）で、ラッパーのスクリプトにも当たる。
  - `start` を続けて 2 回打つと、ラッパーが `_t-rex-64` を exec する前の 2 回目は「動いていない」と見て、もう 1 つ起こしうる。3 つを同じ式にすると揃う。
- [missing tests] `lab.sh` の `trex_cfg` が作る YAML を見るテストがない。
  - 見るべき中身は `port_limit`、`interfaces`、`default_gw` が組の相手（`i^1`）になること。
  - `edge_ports` の links の読み取りと、`trex/stl/*.py` の構文にもテストがない（`grep -n 'trex_cfg\|edge_ports\|udp_trap\|udp_syslog' tests/*.py` が 0 件）。
  - design の検証 10 は手で打つ構文検査で、build.md も手で `yaml.safe_load` しただけ。ポートの本数や IP の割り方を変えたときに気づけない。
- [design consistency] `app/telegraf/telegraf.conf.in:131-134` の gNMI の購読 `evpn_es` が残っている。
  - これは `ethernet-segments/.../oper-state` を 60 秒ごとに sample する購読で、lab に ES がなくなったので何も返さない。
  - design の変更対象に Telegraf は入っておらず、害もない。ただ、lab の側から ES を消したことと対にならない。

## 良かった点

- **ECR のタグに `-amd64` を付けた。** design が見落としていた衝突を避けている。KEEP_ECR=1 で残った arm64 の写しと同じタグになると、`ecr_has` が写しを飛ばし、x86_64 の EC2 が arm64 を引いて起きなくなる。理由も `lab-common.sh` に書いてあり、test_lab_debug で TF・CFn の既定値との一致も見張っている。
- **`impact` に `END_ROLES` を入れた。** TRex が 4 台の leaf につながっても leaf どうしの中継として数えないようにした。test_app に「片系が DOWN のまま残りを落とすと TRex があっても danger」などの境界の項目があり、agentcore と temporal の 2 つの `impact` のソースと `END_ROLES` の一致を test_workflow が検査している。
- **消した lab の LAG / bond / ES を、テストでは合成データで見張り続けている。** test_sync は写しに LAG / bond / ES を足して `lag` の分岐を確かめる。design の「`lag` の分岐は残す（データが無いだけ）」がテストで守られている。
- **build.md が design とずれた点を全部書いている。** 検証 3 / 6 の式のずれ、shellcheck が起点でも通っていなかったこと、TRex のアドレスを決めた理由。レビューで追い直しやすい。

## ユーザーへの質問

- a-leaf / s-leaf の意味と、どちらに写すかはまだ確かめていない（design の未確定事項 1）。今の写し方は WAN 側を s-leaf、DC 側を a-leaf にしている。逆なら名前と管理 IP の対応が入れ替わるだけだが、`fail-main`・`fail-bgp`・`heal-main` の対象（`dc1-a-leaf-01`）の意味も変わる。実機確認の前に、この写し方でよいか確定してほしい。

## Round 1（PM の確認）

- cold reviewer に依頼した（1 回目。モデル opus、実装 Opus 5.5 以上）。上の r01 がその全文。
- Should fix（古い `.cli` が S3 から戻る）: 読んで確かめた。`ops/lab-common.sh:103` の `upload_lab` に `--delete` が無く、EC2 側は `--delete` 付き（`pipeline/lab/templates/lab_user_data.sh.tftpl:34`、`IaC/cloudformation/lab-debug.yaml:468`）。`app/containerlab/lab.sh:12` は `set -euo pipefail`、`:74` の `routers()` は `srlinux/*.cli` の glob、`:209` の `logs` はその for ループ。`git ls-files app/containerlab/srlinux/` は 011 の 6 本だけなので、011 より前に置いた S3 には `dc1-leaf-0N.cli` / `dc1-leafsw-0N.cli` が残る。AWS では打っていない（再現は読みだけ）。Should fix のまま → エンジニア4 に直しを依頼（`--delete` と `routers()` を `splab.clab.yml` から取る、両方）。
- Nit「ワーカーの事前チェックで TRex が中継のまま」（`app/temporal/awsio.py:107`、`rules.py:95` の `END_ROLES` が `role` 無しで効かない）: 読んで確かめた。チャットとワーカーで同じ what-if の答えがずれるので **correctness の Should fix に格上げ**（格上げは推測でよい）→ エンジニア4 に直しを依頼（`n.role` を返す）。
- 残りの Nit 5 件は直さず BACKLOG へ。`trex start|stop|status` の pgrep の式の不一致だけは、1 の commit のついでに揃えてよいと伝えた。
- 次: エンジニア4 の Round 2 を待ってマージし、cold reviewer の 2 回目を呼ぶ。
# lab を IS-IS で組み直し、各 leaf に TRex をつなぎ、lab の EC2 を x86 にする（011）— review r02

## サマリ

2 回目のレビュー。重点は 1 回目のあとの直し（`git diff fa6234f..HEAD`）の 11 ファイル、約 +110 / −20 行。同じ範囲に 014 の変更（`base/ecr/outputs.tf` の kafka_ui、`web_user_data`、`test_stream` など）も混ざっているが、design.md の範囲外なので見ていない。

宿題の 2 件は、どちらも直っている。

- **古い `.cli` が S3 から戻り `lab logs` が止まる（r01 の Should fix）:** 直っている。手当ては 2 段ある。
  - `upload_lab` と lab の output `upload_lab_command` に `--delete` を付けた。rpm は `--exclude` で消させない。
  - `routers()` を `srlinux/*.cli` の glob から、`$TOPO.in` の nodes の `kind: nokia_srlinux` を読む形に変えた。
  - S3 を掃除し直さなくても、EC2 側の機器一覧が古い名前を拾わない。
- **ワーカーの事前チェックが TRex を中継と見る（PM が Should fix に格上げ）:** 直っている。
  - `awsio.read_topology` が `n.role AS role` を返すようになった。
  - `rules.impact` の `END_ROLES` がワーカーの経路でも効く。
  - docstring と `docs/workflow.md` の「worker は役割を読まない」も直っている。

Must fix・Should fix は無い。Nit が 2 件。

**分類の基準:**

- **Must fix:** design.md に反するか、今の入力・状態で壊れるもの。
- **Should fix:** 到達できる状態で動作が壊れるか誤るもの。
- **Nit:** 今は壊れないか、このサイクルより前からあるもの。

### 見た観点 / 見ていない観点

**自分で打って確かめたこと:**

- **テスト 6 本:** `uv run --group dev --group web python tests/<name>.py` で打ち、どれも失敗 0 だった。

  | テスト | 通過 |
  |---|---|
  | test_lab_debug | 104 |
  | test_workflow | 327 |
  | test_oss | 171 |
  | test_graph | 78 |
  | test_sync | 103 |
  | test_app | 161 |

  本数は `docs/development.md` の値（2026-10-09）と一致した。
- **routers() の awk:** 実物の `app/containerlab/splab.clab.yml.in` に当てて打った。出力は `dc1-s-leaf-01 / -02 / dc1-spine-01 / -02 / dc1-a-leaf-01 / -02` の 6 行で、TRex（`kind: linux`）は入らない。
- **シェルの構文:** `bash -n app/containerlab/lab.sh` と `bash -n ops/lab-common.sh` が通った。

**読んで確かめたこと:**

- **upload_lab の --delete は lab/ の外に届かない。**
  - `lab/` に書き込むのは `upload_lab` だけだった。呼び元は `ops/up.sh:851`、`oss/ops/up.sh:295`、`ops/lab-debug.sh:97/149` で、`grep -rn '/lab/'` で確かめた。
  - lab の EC2 のロールは `lab/*` に `s3:GetObject` だけで、書かない（`pipeline/lab/iam.tf:42-46`、`lab-debug.yaml:366-369`）。
  - そのため `--delete` が消すのは、手元から消えた・改名したファイルと古い版の rpm だけになる。
  - `--exclude "$CONTAINERLAB_RPM"` は送り元から見た相対のキー（`containerlab_<v>_linux_amd64.rpm`）に当たり、S3 側の今の版の rpm を消させない。
  - arm64 の旧 rpm は消える。setup.sh が rpm を選ぶときの曖昧さも減るので、良い方向の副作用。
- **routers() の呼び元:**
  - `logs`（`lab.sh:217`）と `forward-status`（`lab.sh:339`）の 2 か所だけ。
  - `setup.sh` と `lab.sh` のほかの所に `srlinux/*.cli` の glob は残っていない。
- **`$TOPO.in` が EC2 と手元の両方にある。**
  - EC2: `upload_lab` は `splab.clab.yml` だけを除くので、`.in` は送られる。
  - 手元: `docker/compose/lab.sh` は `app/containerlab/lab.sh` をそのまま呼ぶ。
- **read_topology の role:**
  - グラフの device の頂点は `role` を持つ（`app/agentcore/graph.py:244` の `DEVICE_KEYS`）。
  - 呼び元は `worker.py:102`（precheck）と `:355`（`maintenance_hold`）。キーが 1 つ増えるだけなので、`maintenance_hold` は壊れない。
- **テストの足し分:**
  - test_lab_debug: `upload_lab` と output の除外の集合が一致することを見る。偽の docker を置き、古い `dc1-leaf-01.cli` を混ぜた `logs`、`trex stop` / `status` も見る。
  - test_workflow: `read_topology` の形のまま `rules.impact` に渡すと、TRex が中継にならず `danger` になることを見る。
  - golden の `neptune_cypher.json`。
- **lab.sh の TREX_PROC:** r01 の Nit「pgrep の式が start / stop / status で違う」も、`TREX_PROC=t-rex-64` に揃えて直っている。test_lab_debug で検査している。

**見ていない観点:**

- **`bash ops/check.sh` の全体。** 打っていない。`terraform validate` の前の `init` が、追跡している `.terraform.lock.hcl` を書き換えうるため（r01 と同じ理由）。上の 6 本のほか、test_stream / test_local_compose などは、今回の直しに関わらないので打っていない。
- **`terraform validate` / `fmt`。** 今回の TF の変更は `outputs.tf` の文字列 1 本だけ。test_lab_debug の照合で代わりにした。
- **shellcheck。** この PC に無い（`which shellcheck` が空）。
- **AWS の実機。** `aws s3 sync --delete --exclude` の動きを実際のバケットで打ってはいない。「`--exclude` に当たるものは送り先でも消さない」は AWS CLI の仕様として読んだだけ。
- **014 の変更。** design.md の範囲外。

## Must fix

None

## Should fix

None

## Nit

- [security + runtime bugs] `upload_lab` の除外に、手元の containerlab が作る `app/containerlab/clab-*/` が入っていない。
  - **該当:** `ops/lab-common.sh:105`。今回のサイクルより前からある抜けで、`--delete` が原因ではない。今回の Round 2 は同じ行の除外を触っている。test_lab_debug は除外の集合を 4 つちょうど（`splab.clab.yml`・`__pycache__/*`・`*.DS_Store`・rpm）に固定しているので、ここで付け足すのが自然。
  - **起きる条件:** WSL で `docker/compose/lab.sh up` を打ったのと同じチェックアウトで、`ops/up.sh`（か `ops/lab-debug.sh up` / `sync`）を打ったとき。
    - containerlab は `app/containerlab/clab-splab/` を root の持ち物として作る（`docker/compose/README.md:117`、`.gitignore`）。
  - **何が起きるか:** どちらか。
    - 中身を読めれば、`clab-splab/` が `s3://<バケット>/lab/` に上がる。containerlab が作る lab の TLS の秘密鍵と、手元の lab の状態も含む。
    - root だけが読めるファイルがあれば、`aws s3 sync` が失敗する。`upload_lab` は `return 1` を返し、`up.sh` が「lab の材料を置けなかった」で止まる。
  - **Nit にした理由:** 手元の compose と AWS を同じチェックアウトから打つ人に限られ、このサイクルで新しく入った経路でもない。
  - **直すなら:** 除外に `--exclude "clab-*/*"` を足し、`outputs.tf` の `upload_lab_command` と test_lab_debug の期待集合にも足す。
- [design consistency] review.md の Round 1 に「残りの Nit 5 件は直さず BACKLOG へ」とあるが、`docs/cycles/BACKLOG.md` に該当の行が見当たらない。
  - 該当の Nit は次の 4 件（pgrep の不一致は直ったので、残りは 4 件）。
    - デバッグ用の Telegraf の ECR タグにアーキが無い。
    - `base/ecr/outputs.tf:7/12/17` の `with tag 26.7.2` などが、実際に置くタグ（`-amd64` 付き）と違う。今も残っている。
    - `trex_cfg` / `edge_ports` / `trex/stl/*.py` のテストが無い。
    - `telegraf.conf.in` の `evpn_es` の購読が残っている。
  - 確かめ方: `grep -n 'evpn_es\|trex_cfg\|-amd64\|with tag' docs/cycles/BACKLOG.md` が 0 件。
  - コードの問題ではなく記録の漏れ。BACKLOG は PM が書く決まりなので、PM が足す。

## 良かった点

- **古い `.cli` の問題を 2 段で直した。** S3 の側は `--delete` で掃除する。EC2 の側は、機器一覧をファイルの有無ではなく、トポロジの定義から取るようにした。片方が漏れても `lab logs` は止まらない。
- **rpm を `--exclude` で守った理由をコメントとテストの両方に書いた。** `--delete` を付けると、毎回 sync が rpm を消して cp が置き直す。この見落としやすい挙動を、test_lab_debug が除外の集合の一致として見張っている。lab-common.sh と outputs.tf の 2 か所の一致も検査している。
- **`logs` の回帰テストで実際の状況を再現した。** S3 に残った古い `dc1-leaf-01.cli` を実際に混ぜ、偽の docker の呼び出しが 6 台・この順であることを見ている。
- **read_topology の直しをテストで閉じた。** 形の検査だけでなく、`read_topology` が返す形のまま `rules.impact` に流し、TRex が 2 台の leaf をつないでいても leaf の孤立と `danger` が出ることまで確かめている。PM が格上げした理由（チャットとワーカーで答えがずれる）を直接閉じるテストになっている。

## ユーザーへの質問

None

## Round 2（PM の確認）

- cold reviewer に依頼した（2 回目。完了判定の直前。モデル opus）。上の r02 がその全文。Must fix 0 / Should fix 0 / Nit 2。
- Round 1 の Should fix 2 件の解消: `ops/lab-common.sh:103-105` を読み、`--delete` と `--exclude "$CONTAINERLAB_RPM"` が入っているのを確かめた。`bash ops/check.sh` は f766351（Round 2 のマージ直後。実装は不変）で打ち、最後の行が `すべて通過`、`test_lab_debug` 104 / `test_workflow` 327（`logs` の回帰テストと `read_topology` → `rules.impact` のテストを含む）。AWS では打っていない。
- Nit 1（`upload_lab` の除外に `clab-*/` が無い）: `grep -n 'clab-' ops/lab-common.sh IaC/terraform/aws-managed/pipeline/lab/outputs.tf` が 0 件、`.gitignore:16` に `app/containerlab/clab-*/` があることを確かめた（読んだだけ。S3 には打っていない）。手元の compose と AWS を同じチェックアウトから打つ人に限られるので Nit のまま。BACKLOG に足した。
- Nit 2（Round 1 の Nit が BACKLOG に無い）: そのとおり。この commit で BACKLOG に足した（セルフレビュー S1 / S2 / N2〜N10 の 11 行と cold review Round 1 の Nit 4 行、Round 2 の Nit 1 行）。
- サイクル完了。AWS の実機（m6i.xlarge、TRex の起動、`aws s3 sync --delete`）は PM の AWS 検証でまとめて見る。
