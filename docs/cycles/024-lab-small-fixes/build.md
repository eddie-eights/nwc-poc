# lab の小さな直しを片付ける（024）の実装記録

実装モデル: opus-5.5 / effort: high

2026-10-09。ブランチ `feat/024-lab-small-fixes`（origin/main 65b97d3 から）。1 件 1 commit で、順序は設計どおり A → B → C → E → F → I → J → K → H → G。D は commit なし。

| # | commit | 件名 |
|---|---|---|
| A | 31b5867 | lab.sh の failover の route() を IS-IS の経路が無くても止まらなくする |
| B | 0d4a153 | lab graph の開けないときの案内を journalctl -u にする |
| C | daad8cb | docker/compose/lab.sh のイメージの必須を up / render だけにする |
| D | なし | 確認のみ（設計の「調査で分かった事実 D」のとおり、消すものが残っていない） |
| E | b2dcffe | 011 の design.md の nb_map.py のパスを今の app/nautobot/nwc/ に揃える |
| F | 37a36aa | kafka_load.sh の gnmi のレコードの BGP の peer を機器ごとの実在の値にする |
| I | b403510 | デバッグ用 Telegraf の ECR タグにも -$LAB_ARCH を付ける |
| J | 9835fdc | lab.sh の trex_cfg / edge_ports と trex/stl の 2 つにテストを足す |
| K | e548e1a | upload_lab の S3 への sync で containerlab の作業ディレクトリ clab-*/ を除く |
| H | d812595 | 使われない multitool のイメージを lab から外す |
| G | 3ba09f4 | lab.sh render の ${TREX_IMAGE:?} をテストで見張る |

## ステップごと

テストの件数は、そのステップの commit の時点で打った `uv run --group dev --group web python tests/<名前>.py` の最後の行。

### A. route() の `|| true`

- `app/containerlab/lab.sh` の `route()` で、next-hop-group の番号を取るパイプの末尾に `|| true` を付けた。
- `tests/test_lab_debug.py` には、`route()` の本体を抜き出して偽の `srl()` で打つ check を 2 つ足した。経路が無い場合と、経路がある場合。
- 赤→緑（検証 2）。
  - 直す前: 終了コード 1、stdout は空。
  - 直した後: 終了コード 0、stdout は `'  (IS-IS の経路が無い)\n'`。
- `test_lab_debug.py` は 97 → `通過 99 / 失敗 0`。

### B. journalctl -u

- `lab.sh` の graph の案内を `sudo journalctl -u <接頭辞>-lab-graph` にした。
- 既存の graph の check に 2 つの条件を足した。案内に `journalctl -u` の字面があることと、`systemctl status` が出ないこと。件数は変わらない。
- `test_lab_debug.py` は `通過 99 / 失敗 0`。

### C. docker/compose/lab.sh の必須を up / render だけに

- `docker/compose/lab.sh` の `:?` の検査を `up` / `render` のときだけにした。`sudo env` にはイメージを全部渡したまま（設計の「やらないこと」）。
- README に 3 つの副項目を足した。
  - up は止まる。
  - down / check は打てる。
  - 古い `.env` には `.env.example` から `TREX_IMAGE` を写す。
- `tests/test_local_compose.py` に 1 つ足した。`SRLINUX_IMAGE` だけの `.env` で `down` を打つと、終了コード 0 で `sudo` を 1 回打ち、`TREX_IMAGE=` が空で渡る。
- 同じ形の `.env` で `up` を打つと `TREX_IMAGE が無い` で止まる。これは既存の check が見ている。
- `test_local_compose.py` は 138 → `通過 139 / 失敗 0`。

### D. lag / ES

確認のみで、commit は無い。設計の「調査で分かった事実 D」がそのまま記録になる。

### E. 011 の design.md のパス

- `docs/cycles/011-lab-isis-trex-x86/design.md` の `app/nautobot/netops/nb_map.py` を、`app/nautobot/nwc/nb_map.py` に直した。
- `grep -rn 'nautobot/netops' docs/cycles/011-lab-isis-trex-x86/` は 0 行（終了コード 1）。

### F. kafka_load.sh の peer

- `app/containerlab/trex/kafka_load.sh` に `peer()` を足し、`payload()` の bgp_neighbor のレコードに機器ごとの peer を入れた。
- peer が取れないときは、理由を出して `exit 1` する。
- テストを 3 つ足した。
  - `nodes()` の出力が `.in` の `mgmt-ipv4` と一致する。
  - 6 台の peer。
  - payload に固定値が無いこと。payload のレコードは json で読んで確かめる。
- `test_lab_debug.py` は 99 → `通過 102 / 失敗 0`。
- 検証 4 の出力（`nodes` と `peer` を抜き出して打った。6 行で、空の peer は無い）:

  ```
  dc1-s-leaf-01 10.255.0.1
  dc1-s-leaf-02 10.255.0.1
  dc1-spine-01 10.255.1.1
  dc1-spine-02 10.255.1.1
  dc1-a-leaf-01 10.255.0.1
  dc1-a-leaf-02 10.255.0.1
  ```

### I. デバッグ用 Telegraf のタグ

- `ops/lab-debug.sh:129` を `TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH" || die …` にし、`:128` の log も直した。
- `IaC/cloudformation/lab-debug.yaml` の `TelegrafImageTag` の Description を直した。
- `test_lab_debug.py` の dbg 側の字面と check の名前を合わせた。
- `test_lab_debug.py` は `通過 102 / 失敗 0`（±0）。
- 検証 6:

  ```
  ops/lab-debug.sh:129:    TELEGRAF_TAG="$(telegraf_tag)-$LAB_ARCH" || die "app/telegraf/ のタグを作れなかった"
  ops/up.sh:633:  TELEGRAF_TAG=$(telegraf_tag) || die "app/telegraf/ のタグを作れなかった"
  ```

  `bash -n ops/lab-debug.sh` は終了コード 0。

### J. trex_cfg / edge_ports / trex/stl のテスト

- テストだけを 6 つ足した。
  - `trex_cfg` の yaml。
  - `edge_ports` の 4 本。
  - `syslog_payload` の形。
  - severity が範囲外なら ValueError。
  - `trap_payload` の先頭。
  - stl の既定の宛先。
- 赤→緑。`(i ^ 1)` を一時的に `i` にすると、trex_cfg の check が `AssertionError` で落ちた。戻すと `通過 108 / 失敗 0`。
- 検証 7 の出力は次のとおり（`trex_cfg eth1 eth2 eth3 eth4`。先頭のコメントは略した）。

  ```
  - version: 2
    port_limit: 4
    low_end: true
    interfaces: ['eth1', 'eth2', 'eth3', 'eth4']
    port_info:
      - ip: 10.100.0.11
        default_gw: 10.100.0.12
      - ip: 10.100.0.12
        default_gw: 10.100.0.11
      - ip: 10.100.0.13
        default_gw: 10.100.0.14
      - ip: 10.100.0.14
        default_gw: 10.100.0.13
  ```

### K. upload_lab の `clab-*/*`

- 2 か所の `--exclude` の末尾に `"clab-*/*"` を足した。
  - `ops/lab-common.sh` の `upload_lab`。
  - `pipeline/lab/outputs.tf` の `upload_lab_command`。
- テストの期待の集合にも足した。
- `test_lab_debug.py` は `通過 108 / 失敗 0`（±0）。
- 検証 8: `grep -c 'clab-\*/\*'` の結果は `ops/lab-common.sh:1`、`IaC/terraform/aws-managed/pipeline/lab/outputs.tf:1`。

### H. multitool を外す

設計の 1〜7 の順に手を入れた。

1. lab 本体と ECR を直した。
2. ミラーを外した。
3. lab の EC2 と CFn を直した。
4. 手元を直した。
5. docs の 8 ファイルを直した。数も直した。
   - ECR のリポジトリ 15 → 14 個。
   - デバッグ用の repo 4 → 3 つ。
   - 「lab の 3 つ」→「2 つ」。
6. テストを直した。
   - 「`MULTITOOL_IMAGE` が無ければ止まる」の check を消した（−1）。
   - 「戻らない」check を足した（+1）。

その他:

- `terraform fmt -check`（`base/ecr` / `pipeline/lab`）は両方とも終了コード 0。
- `terraform validate`（`init -backend=false -lockfile=readonly`。`TF_DATA_DIR` は scratchpad。state には触っていない）は両方とも `Success! The configuration is valid.`。
- `bash -n` は、変えた 7 本（`app/containerlab/lab.sh` / `setup.sh`、`ops/lab-common.sh` / `up.sh` / `oss/up.sh` / `lab-debug.sh`、`docker/compose/lab.sh`）が全部終了コード 0。
- `shellcheck` はこの PC に入っていないので打てていない。
- 赤→緑（「戻らない」check）。`docs/setup.md` に `MULTI` + `TOOL_IMAGE` の 1 行を一時的に足すと、`AssertionError: …: ['docs/setup.md']` で落ちた。戻すと `通過 108 / 失敗 0`。
- 件数:
  - `test_lab_debug.py` は 108 → `通過 108 / 失敗 0`。内訳は「戻らない」の +1 と、`LAB_IMAGES` の 3 → 2 で既定値の check が 1 回減った分の −1。
  - `test_local_compose.py` は 139 → `通過 138 / 失敗 0`。
- 検証 5 の grep は `git grep -l -i multitool -- . ':!docs/cycles' ':!docs/verification'` で打った。出力は 0 行（終了コード 1）。

### G. `${TREX_IMAGE:?}` を見張る

- `tests/test_lab_debug.py` に 2 つ足した。
  - `render)` の次の行の字面。
  - `_lab_graph("render", SRLINUX_IMAGE="x")` が終了コード 0 でなく、stderr に `TREX_IMAGE` を出し、`を作った` を出さず、外のコマンドを打たない。
- `_lab_graph` の環境の除外に、`SRLINUX_IMAGE` と `TREX_IMAGE` を足した。
- 赤→緑。`lab.sh` の render から `"${TREX_IMAGE:?}"` を一時的に外すと、字面の check が `AssertionError` で落ちた。戻すと `通過 110 / 失敗 0`。
- 打つ方の check は、印を外しただけでは落ちなかった（字面の check を飛ばした写しで `通過 109 / 失敗 0`）。`lab.sh` が `set -euo pipefail` なので、未定義の `TREX_IMAGE` は sed の行の展開でも同じく止まる。下のセルフレビューの残した 2 を参照。
- `test_lab_debug.py` は 108 → `通過 110 / 失敗 0`。

## 設計からずらした点

1. **E: 2 か所直した。**
   - 設計の指定は `:27` の 1 行だった。
   - 同じパスが `:65` にもあり、検証（`grep -rn 'nautobot/netops'` が 0 行）を満たすために両方を直した。
2. **C: README に書き足し先が無かった。**
   - README には「止まる」の記述がそもそも無かった。
   - そのため、`up` / `down` / `check` の振る舞いを新しい副項目で書き足した。
3. **F: 設計の例の値が違っていた。**
   - 設計の例 `dc1-a-leaf-01 203.0.113.11` は誤りで、現物は `.31`（`.11` は `dc1-s-leaf-01`）。
   - 期待値は `.in` から組んだ。
4. **F: neighbor の数が設計の記述と逆だった。**
   - 設計の「s-leaf は 4 つの neighbor」は逆で、現物は spine が 4、leaf が 2。
   - 「最初の overlay の neighbor を取る」方針は変えていない。
5. **F: 設計のスニペットに 2 つ足した。**
   - `peer()` の sed に `2>/dev/null` を付けた。
   - `payload()` の `p=$(peer …)` に `|| p=""` を付けた。
   - どちらも、`set -e` の下で `.cli` が無いと黙って落ち、設計の「空なら理由を出して exit 1」に届かないため。
6. **I: 設計の前提「同じ repo で混ざる」が現物と違っていた。**
   - デバッグ用の Telegraf は `<prefix>-debug-telegraf`（CFn のスタックの repo）で、stream の `<prefix>-telegraf` とは repo が別だった（`lab-debug.sh:145` の注記、`lab-debug.yaml` の `TelegrafRepository`）。
   - タグの変更はそのまま入れた。理由は、lab のイメージとそろえてタグから arch が分かるようにするため。
   - 説明の文言は現物に合わせた。
     - log は「stream の arm64 は別の repo」とした。
     - CFn の Description は「…pushes the arm64 build … to its own repository <prefix>-telegraf」とした。
     - check の名前も同様に直した。
   - CFn の Description には、既存の「Required when CreateInstance is true」を残した。
7. **J: `sh_const` では読めない行を別の正規表現で読んだ。**
   - `lab.sh` の `TREX_NET=10.100.0; TREX_IP_BASE=11` は 1 行に 2 つの代入があり、`sh_const` では読めない。
   - この行は別の正規表現で読んだ。
8. **J: `sys.dont_write_bytecode = True` を足した。**
   - `trex/stl` を import すると、`app/containerlab/` に `__pycache__` ができる。
   - `app/containerlab/` は `upload_lab` が S3 へ丸ごと送るので、`__pycache__` を作らないようにした。
9. **K: 除外は既存の並びの末尾に足した。** 設計の「順序は変えない」に従った。
10. **H: OSS 版も同じく外れる。**
    - `IaC/terraform/oss/base/ecr` と `pipeline/lab` の `.tf` と tftpl は、aws-managed への symlink。
    - そのため OSS 版の lab の repo と user_data からも multitool が外れる。設計のファイル一覧には無いが、同じ差分。
    - `ops/oss/up.sh` の `ecr_has` は設計どおり直した。
11. **H: 「戻らない」check の書き方。**
    - xargs の grep ではなく、テストの中で `git ls-files -z` の全ファイルを読んで探す形にした。symlink は先を読み、NUL を含むものは飛ばし、`.env` は読まない。
    - check 自身にも字面で書かない（`"multi" + "tool"` の変数 `_MT` から組む）。
    - 追跡ファイルが 100 本を超えていることも条件にした。`git ls-files` が空を返して素通りするのを防ぐため。
12. **H: 検証 5 の grep は `git grep` で打った。**
    - この worktree のエージェントは、git を含む複合のパイプを打てない。
    - そのため `git grep -l -i multitool -- . ':!docs/cycles' ':!docs/verification'` で同じ範囲を見た。
13. **design.md の検証 5 に 1 文足した。**
    - 設計の「未確定事項 2」は、`instance.tf` を読んで EC2 が作り直されるなら、その旨を design の 5. に書くよう指示している。
    - `instance.tf:27` に `user_data_replace_on_change = true` があったので、design.md の検証 5 に「同じ apply で lab の EC2 は作り直し（`1 to replace`、10 分程度）」を足した。
14. **件数が設計の見込みと違う。**
    - 設計の見込みは `test_lab_debug` 111 程度、実測は 110。
    - H の −1（`LAB_IMAGES` の既定値の check が 3 → 2 回）を、設計は数えていなかった。

## 検証方法の結果

1. **テスト（最後の commit 3ba09f4 で打った）**

   ```
   $ uv run --group dev --group web python tests/test_lab_debug.py
   通過 110 / 失敗 0
   $ uv run --group dev --group web python tests/test_local_compose.py
   通過 138 / 失敗 0
   $ uv run --group dev --group web python tests/test_stream.py
   通過 106 / 失敗 0
   $ uv run --group dev --group web python tests/test_analytics.py
   通過 515 / 失敗 0
   $ uv run --group dev --group web python tests/test_oss_ops.py
   通過 200 / 失敗 0
   $ uv run --group dev --group web python tests/test_oss.py
   通過 174 / 失敗 0
   ```

   `test_analytics.py` は途中で `[snmp_sinks] splunk: HEC が 400 を返した…` を出すが、これはテストが偽の HEC で起こしている想定の出力で、終了コードは 0。

2. **A の赤→緑**
   - 直す前: 終了コード 1、stdout は空。
   - 直した後: 終了コード 0、stdout は `  (IS-IS の経路が無い)`。
3. **C**
   - `.env` が `SRLINUX_IMAGE=example.com/srl:1` の 1 行だけの木で `docker/compose/lab.sh down` を打った。
     - `sudo` を 1 回打ち、そのコマンドに `TREX_IMAGE=`（空）と `SRLINUX_IMAGE=example.com/srl:1` が入る。
     - 終了コードは 0。
     - H の後なので、`MULTITOOL_IMAGE=` は入らない。
   - 同じ形の `.env` で `up` を打つと、`TREX_IMAGE が無い` で止まる。
   - どちらも `test_local_compose.py` の check で見ている。
4. **F**: 上の 6 行。空の peer は無い。
5. **H**
   - grep は 0 本（終了コード 1）。
   - validate は 2 ルートとも Success。
   - **AWS の項目は未確認。** 理由: このサイクルでは AWS に何も作らない指示。次回の AWS 検証にまとめる。対象は次の 4 つ。
     - ECR の apply が `aws_ecr_repository.lab["multitool"]` を destroy する。
     - `describe-repositories` が `RepositoryNotFoundException` を返す。
     - lab の EC2 が作り直される。
     - 7 コンテナが上がる。
6. **I**: 上の 2 行。`bash -n` は 0。
7. **J**: 上の yaml の出力と、赤→緑。
8. **K**: 1 / 1。
9. **`bash ops/check.sh`**
   - 終了コード 0、最後の行は `すべて通過`。
   - 段 1 は fmt、段 2 は 18 ルートの validate で、全部 OK。
   - 段 4 の件数は次のとおり。

     ```
     161 / 168 / 515 / 79 / 3 / 78 / 7 / 110（lab_debug）/ 138（local_compose）/ 68 / 174 / 200 / 66 / 106 / 103 / 327 項目
     ```

   - 段 5 は旧名 netops の grep で、通過。

### 未確認（実機が要るもの。次回の AWS 検証にまとめる）

- **H: 次の `ops/up.sh` の ECR の apply で、`<prefix>-lab-multitool` の repo が中のイメージごと消えること。**
  - `force_delete = true` なので消える見込み。
  - 見るもの: `describe-repositories` が `RepositoryNotFoundException` を返すこと。
- **H: lab の EC2 が作り直されること。**
  - user_data が変わり、`user_data_replace_on_change = true` なので、`1 to replace` になる見込み。
  - 見るもの: 作り直したあと、`lab status` で 7 コンテナ（SR Linux 6 台と TRex）が上がること。
- **H: デバッグ用のスタックの更新。**
  - `MultitoolRepository` の削除（`EmptyOnDelete: true`）と、パラメータ `MultitoolImageTag` の削除が、既存のスタックで通ること。
- **I: lab-debug の Telegraf のビルドし直し。**
  - 新しいタグ `…-amd64` は ECR に無いので、初回の `ops/lab-debug.sh up` で 1 回ビルドし直す見込み。
  - 見るもの: そのイメージで Telegraf が起きること。
- **K: `aws s3 sync --exclude "clab-*/*"` が、手元の `app/containerlab/clab-splab/` を実際に送らないこと。**
  - 見るもの: `aws s3 ls s3://<bucket>/lab/ --recursive | grep clab-` が 0 行。
- **A / B / F / J の lab の EC2 の上での振る舞い。** containerlab の上で打っていない。
  - A: failover の切替前の表示。
  - B: journalctl の案内。
  - F: kafka_load.sh の gnmi のレコード。
  - J: TRex の起動。
  - テストは、関数を抜き出して偽物で打つところまで。
- **shellcheck。** この PC に入っていない。

### セルフレビュー

観点ごとに見た。

- **correctness**
  - A の `|| true` は、next-hop の for の中にある既存の形と同じ。
  - F の peer は `.cli` の現物から取る。
  - H は `git grep` で 0 本。
  - G の字面は、H の後の形と一致する。
- **security**
  - 権限は広げていない。CFn の `EcrPull` の Resource は 4 → 3 本に減った。
  - `.env` は読んでいない。「戻らない」check は `.env` を飛ばす（そもそも追跡されていない）。
  - tfstate と deploy.env にも触っていない。terraform は `-backend=false` で、`TF_DATA_DIR` は scratchpad。
- **runtime bugs**
  - C のラッパーは、`down` のときも sudo env に空のイメージを渡す。`app/containerlab/lab.sh` の `down` はイメージを見ないので問題ない。
  - F の `peer()` が空のときは、理由を出して止まる。
- **data loss**
  - H で ECR の `<prefix>-lab-multitool` が中身ごと消える。使うノードが無いので失うものは無い。上流から写し直せる。
  - lab の EC2 の作り直しで、EC2 の中に手で置いた物は消える。lab の EC2 は毎回 up で作り直す運用で、S3 の `lab/` が正本。
- **API compatibility**
  - terraform の output `lab_multitool_repository_url` を消した。読んでいる所は `git grep` で 0。
  - CFn のパラメータ `MultitoolImageTag` を消した。渡しているのは `ops/lab-debug.sh` だけで、そこも消した。
  - 手元の `.env` に `MULTITOOL_IMAGE` が残っていても、ラッパーは読まないので害は無い。
- **type safety**: 対象外（Python は J のテストだけ）。
- **missing tests**
  - lab の EC2 と AWS の上の振る舞いは、上の未確認のとおり。
  - G の打つ方の check は印を単独では見張れない（下の残した 2）。

#### 直したもの

1. 「戻らない」check が、check 自身の名前に字面を持っていた（最初の版）。自分自身を拾って必ず落ちるので、`_MT` から組む形に直した。
2. 「戻らない」check が `git ls-files` の空の結果でも通ってしまう形だった。`len(_ls) > 100` を条件に足した。
3. I の check の名前と log が「lab の 3 つ」のままだった。H の後の形「2 つ」に直した（H の commit）。

#### 残したもの（直していない）

1. **Should fix（docs）: `docs/development.md:61` のテストの件数が古い。**
   - 古い値: `test_lab_debug` 97。今は 110。
   - `test_oss_ops` も 199 のままで、今は 200。これは 024 より前からずれている。
   - 設計の変更対象に無いので触っていない。BACKLOG に回すかは PM の判断。
   - 追記: PM の判断でこのサイクルに入れ、cold review 後に直した（下の「cold review 後の直し」）。
2. **Nit: G の打つ方の check は、`${TREX_IMAGE:?}` の印だけを外しても落ちない。**
   - `lab.sh` の `set -u` が同じく止めるため。
   - 印を見張るのは字面の check の方で、赤→緑はそちらで確かめた。
   - 打つ方は「TREX_IMAGE が無ければ render が splab.clab.yml を作らない」という振る舞いを見る check として残した。
3. **Nit: 「戻らない」check は追跡ファイルを全部読む。**
   - 今の規模では `test_lab_debug.py` 全体の時間に目立つ差は無い。
   - ファイルが大きく増えたら `git grep` に替える余地がある。

#### /robust（1 回。Pre-Mortem 3 つ）

1. **「次の AWS の `ops/up.sh` が ECR の段で止まる」としたら。**
   - 考えられる原因: lab-multitool の repo にイメージが残っていて、destroy が拒まれる。
   - 確かめたこと: `base/ecr/main.tf` の lab の repo は `force_delete = true`（既存の設定で、変えていない）。
   - 結論: イメージがあっても消える。未確認の項目に入れた。
2. **「デバッグ用のスタックの更新が `UPDATE_ROLLBACK` になる」としたら。**
   - 考えられる原因: 消す `MultitoolRepository` に push 済みのイメージがあり、repo を消せない。
   - 確かめたこと: テンプレートの ECR の repo は全部 `EmptyOnDelete: true`。`test_lab_debug.py` の既存の check が見ている。
   - 結論: パラメータの削除も、`lab-debug.sh` の `deploy()` から同時に消したので、残ったパラメータを渡して失敗することは無い。実機は未確認の項目に入れた。
3. **「手元の compose の lab が、古い `.env` のせいで上がらなくなる」としたら。**
   - 2 つの場合を見た。
     - 古い `.env` に `MULTITOOL_IMAGE` が残っている場合: ラッパーは読まず、sudo env にも渡さないので影響しない。
     - 011 より前の `.env`（`TREX_IMAGE` が無い）の場合: `up` は `TREX_IMAGE が無い` で止まる。`down` / `check` は C で打てるようにした。
   - README にも、`.env.example` から `TREX_IMAGE` を写す手順を書いた。
   - 結論: 新たに止まる形は無い。

Pre-Mortem から足した直しは無い。

## cold review 後の直し

cold review（`review.md` の Round 1）は Must fix 0、Should fix 1、Nit 4 だった。直したのは PM の判断による次の 2 つ。Nit 4 件は直していない。

1. **Should fix [設計との整合]: design.md の I を、実装で見つけた事実に書き換えた。**
   - 当初の design.md は、stream の Telegraf と「同じ repo `<prefix>-telegraf`」に置くので amd64 と arm64 が上書きし合う、としていた。
   - 実際は、デバッグ用の repo は別（`<prefix>-debug-telegraf`）で、衝突は元から起きない。タグに `-amd64` を付けるのは、lab のイメージとそろえて見分けるため。
   - 書き換えたのは、「調査で分かった事実」の I（旧 :81）、「直し方」の I の 1〜3（:147-149。log・CFn の Description・check の名前を実装の文言に合わせた）、未確定事項 6（:238）。
   - 上の「設計からずらした点」の 6 は、この直しで design.md 側にも反映された。
2. **`docs/development.md:61` のテストの件数を直した。** `test_lab_debug` 97 → 110、`test_oss_ops` 199 → 200。

docs だけの直しなので件数は変わらない見込みだったが、取り直した。

```
$ uv run --group dev --group web python tests/test_lab_debug.py
通過 110 / 失敗 0
$ uv run --group dev --group web python tests/test_oss_ops.py
通過 200 / 失敗 0
$ bash ops/check.sh
…
すべて通過
```

`bash ops/check.sh` の終了コードは 0。
