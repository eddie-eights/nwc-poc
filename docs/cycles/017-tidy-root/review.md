# 根に残ったものを app/ ops/ docs/ に片付ける（017）レビュー

## Round 1

- 対象: `chore/tidy-root` の 88bfac1（rename の fd9918b、参照の書き換えの f92399c、build.md の 88bfac1）。実装モデル: opus-5.5（エンジニア1）。レビューモデル: cold reviewer = opus、確認 = opus-5.5 / xhigh（エンジニア1）
- cold reviewer に依頼した（初回。review.md がまだ無かった）。入力は design.md と変更ファイル 63 本のパス（rename は旧 → 新）と「未解消の Must fix: なし」と書き出し先だけ
- check.sh: f92399c の作業ツリーで rc 0「すべて通過」（build.md の検証 1。16 本の件数は docs/development.md:37 と同じ）。`docs/cycle-006-design` の最新（013 のマージ後）を取り込んでの取り直しはまだ（design.md の未確定事項 3）

### cold reviewer の結果（review-r01.md をそのまま連結）

# review r01 — 根に残ったものを app/ ops/ docs/ に片付ける（017）

- 対象: `03840c8..f92399c`（63 ファイル。rename は fd9918b、参照の書き換えは f92399c）。build.md（88bfac1）は実装者の記録として読んだ。
- 前回の未解決の Must fix: なし。

## サマリ

- Must fix は 0 件。rename とパスの書き換えそのものは正しい。動かした 2 本（`app/gateway/handler.py`・`tools.json`）は blob が同じで、`gateway.tf` の zip の中の名前も変わらない。`ops/oss/up.sh`・`down.sh` の `. "$(dirname "$0")/../…"` と `cd "$(dirname "$0")/../.."` は新しい深さに合っている。
- Should fix は 4 件。うち 2 件（Nautobot のイメージのタグ、SG のルールの description）は、design.md の変更対象表が指定した書き換えが、同じ design.md の方針 6「動作は変えない。…Terraform の資源・イメージの版はそのまま」とぶつかるもの（設計どうしの食い違い）。Nautobot のタグの件は build.md のセルフレビューに無い、このレビューで新しく見つけたもの。残り 2 件は docs の事実の誤り（build.md の S1・S3 を独立に確かめた）。
- Nit は 5 件。
- 自分で打ったテスト（`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python tests/<名前>.py`。打ったあと `git status --porcelain -uall` は空）:
  - test_workflow 通過 327 / 失敗 0、test_oss_ops 通過 194 / 失敗 0、test_oss 通過 173 / 失敗 0、test_oss_roll 通過 66 / 失敗 0、test_collectors 通過 79 / 失敗 0、test_nautobot 69 項目すべて通過、test_stream 通過 96 / 失敗 0、test_sync 通過 103 / 失敗 0、test_lab_debug 通過 104 / 失敗 0、test_local_compose 通過 132 / 失敗 0、test_alerts 通過 169 / 失敗 0、test_analytics 通過 504 / 失敗 0、test_app 通過 161 / 失敗 0、test_kb_index 通過 7 / 失敗 0、test_dashboard_config 通過 3 / 失敗 0、test_graph 通過 78 / 失敗 0。
  - 件数は build.md の検証 1 の数と全部一致する。

### 見た観点 / 見ていない観点

- 見た観点
  - 設計との整合: 変更対象表の各行、方針 1〜6、検証 4・6・7・8 の内容。
  - correctness: `ops/oss/up.sh`・`down.sh` の相対パス（`..` の段数）と `OPS_DIR=ops/oss`、`ops/common.sh` 経由の SSM のタグ `ManagedBy=$OPS_DIR/up.sh` と `down-common.sh:182` の絞り込みが同じ値になること、`gateway.tf:10,16` の `file()`・`archive_file` の source、`ops/check.sh:52` の `find`。
  - イメージのタグへの波及: `dir_tag`（`ops/lab-common.sh:54-76`）の入力になるディレクトリのうち、この範囲で中身が変わったもの（`git diff --stat 03840c8 f92399c -- app/nautobot app/agentcore/graph.py app/agentcore/toolkit.py app/containerlab docker/images/nautobot` と、Telegraf・syslog-ng・Grafana・Splunk・Spark・Neo4j の各ディレクトリ）。
  - Terraform の差分: SG のルールの map のキーと `description`、tools Lambda の zip。
  - security: down.sh が消す相手の範囲（OSS 版の SSM はタグ `ManagedBy=ops/oss/up.sh` だけ。マネージド版の `ops/up.sh` とは別の値のまま）。
  - tests の弱化: tests/ の diff は 12 ファイルとも追加行数 = 削除行数（`git diff --numstat`）で、中身は全部パスの 1 行対 1 行の置換。`check(` の追加・削除は無い。`test_nautobot.py` の pathspec から `oss` を外したのは `oss/` が追跡ファイルから消えたので同値。
  - docs: `docs/verification/20261008-oss-aws.md`、`docs/development.md:7`、`docs/oss-variant.md:66`、`docs/architecture/README.md:7,46-54`、`README.md`、`docs/GLOSSARY.md`（rename だけ。中に相対リンクは無い）。`docs/architecture-oss.pptx` のスライドの XML を `unzip -p` で読んだ。
  - 機械置換のやりすぎ: MCP の `tools/list`・`tools/call`（`app/agentcore/mcp_client.py`）や `.env.example:103` の `tools/` は置換されていない。`.env.example` は開いていない（grep の当たりの行番号だけ）。
- 見ていない観点
  - `ops/check.sh` を通しでは打っていない（terraform の init が worktree に `.terraform/` を書くため）。terraform fmt / validate、`bash -n`、`ast.parse` は build.md の記録だけ。
  - `terraform plan` と AWS。SG の description が in-place で変わる（作り直しにならない）ことは確かめていない。
  - tools Lambda の zip を実際に作って hash を比べていない（blob とソースの並びを読んだだけ）。
  - design.md の未確定事項 3（`docs/cycle-006-design` の最新をマージしての取り直し）は、まだ起きていないので見ていない。
  - `docs/` の書き換えのうち `collection.md`・`data-stores.md`・`pipeline.md`・`troubleshooting.md`・`faq-fukuda-nwc-poc.md`・`docs/architecture/resources/*.md` は diff を流し読みしただけで、1 行ずつは照合していない。
  - `docs/architecture-managed.pptx` は開いていない。

## Must fix

None

## Should fix

- [設計との整合・runtime] `app/nautobot/requirements-oss.txt:2` のコメントの書き換えで、Nautobot のイメージのタグが変わり、次の up.sh でイメージの作り直しと Nautobot のタスクの入れ替えが起きる
  - 場所: `app/nautobot/requirements-oss.txt:2`（`oss/ops/up.sh` → `ops/oss/up.sh`。design.md:66 の変更対象表が指定）。波及先は `ops/up-common.sh:260`（`cp -R app/nautobot/. "$1/"` で app/nautobot/ を丸ごと context に写す）、`ops/lab-common.sh:62-70`（context の全ファイルの相対パスと中身を sha256 に足す）、`ops/up.sh:650-651,690,1013`、`ops/oss/up.sh:199-200,250,441`、`IaC/terraform/aws-managed/pipeline/nautobot/locals.tf:83`。
  - シナリオ: この範囲で Nautobot の context の材料のうち中身が変わったのは `requirements-oss.txt` だけ（上の `git diff --stat` の結果。`app/containerlab/` の変更は `kafka_load.sh` のコメントで、`lab_topology.py:510-513` は `srlinux/*.cli` と topo しか読まないので `lab_seed.json` は変わらない）。それでも `NAUTOBOT_TAG` は `<NAUTOBOT_VERSION>-<別の 12 桁>` になる。KEEP_ECR=1 で ECR を残していても `ecr_has` が外れて `NEED_NAUTOBOT=1` になり、マネージド版・OSS 版とも次の up.sh で Nautobot のイメージを buildx で作り直して push し、`tf_apply pipeline/nautobot -var nautobot_image_tag=<新しいタグ>` でタスク定義が新しい版になる。環境が立っていれば Nautobot のサービスが入れ替わる（DB は RDS 側なので中身は消えない）。
  - 方針 6 は「イメージの版はそのまま」と書いている。`<版>` の部分（`NAUTOBOT_VERSION`）は変わらないので字面には反しないと読むこともできるが、`ops/up-common.sh:259` が書くとおりこのタグは中身のハッシュで、イメージは別物として作り直される。build.md の「問題なしとした観点」にも S1〜S4 にも、この波及は書かれていない。
  - 直し方の案: (a) `requirements-oss.txt:2` のコメントを旧パスのまま戻し、検証 4 の grep の除外に足す、(b) このままにして、次の up.sh で Nautobot が 1 回作り直されることを build.md と design.md に書く。どちらにするかは設計の判断なので PM に回す。
  - Should にした理由: 次の up.sh で確実に起きる挙動の変化で、方針 6 の「動作は変えない」とぶつかるが、データは消えず up.sh も止まらない。

- [docs の正しさ・当時の記録の改変] `docs/verification/20261008-oss-aws.md` の打ったコマンドと手順が、2026-10-08 には無かったパスに書き換わっている（build.md の S1 を確かめた）
  - 場所: `docs/verification/20261008-oss-aws.md:286`（「`OWNER=efukuda KEEP_ECR=1 bash ops/oss/down.sh` を、up.sh と同じ worktree で打った」）、`:311`（「この worktree から `…bash ops/oss/down.sh` を打ち直せば、…消えるはず（未確認）」）。原因は design.md:75 の変更対象表がこのファイルを置換の対象に入れていること。
  - シナリオ: `:311` の手順に従って、state のある「この worktree」（当時の commit。`oss/ops/` しか無い）で `bash ops/oss/down.sh` を打つと No such file で止まる。新しいチェックアウトで打つと、`ops/oss/down.sh:26-27` が `TF_DIR=IaC/terraform/oss`・`OPS_DIR=ops/oss` を使うので、当時の `oss/terraform/` の state も、タグ `ManagedBy=oss/ops/up.sh` の付いた cluster-id のパラメータも対象に入らず、手順に書いた「消えるはず」が成り立たない。`:286` は当時打っていないパスを打ったと記録している。
  - `:7` に注（スクリプトのパスは 017 で書き換えた）があるが、同じファイルの `oss/terraform/` は当時のまま残るので、1 つの段落の中で 2 つの時期のパスが混ざる。`docs/development.md:7` は前半で「`docs/verification/` は当時のパス」と書き、後半で 017 が書き換えたと書いていて、1 つの段落の中で食い違う。
  - Should にした理由: 記録の手順をそのまま打つと動かない（壊れるものは無い）。設計の変更対象表を変えることになるので、実装者ではなく PM の判断が要る。

- [設計との整合・Terraform の差分] `security_groups.tf:114` の `why` の書き換えで、SG のルール 4 本の description が次の base/core の apply で変わる（build.md の S2 を確かめた）
  - 場所: `IaC/terraform/aws-managed/base/core/security_groups.tf:114`（`why = "NetFlow - forwarded for the switches, or ops/netflow_send.py on the lab EC2"`。design.md:62 が指定）。この行には `only` が無いので、egress（lab の SG）と ingress（telegraf_dialout_nlb の SG）の両方の `description = each.value.why` になる。`IaC/terraform/oss/base/core/security_groups.tf` はこのファイルへのシンボリックリンク。
  - シナリオ: map のキー（`"${f.from}-${f.to}-${f.protocol}-${f.port}"`）に `why` は入っていないので、資源のアドレスは変わらない。次の `ops/up.sh` と `ops/oss/up.sh` の base/core の apply（立っている環境があれば plan）で、マネージド版 2 本と OSS 版 2 本の description の更新が出る。方針 6 の「Terraform の資源はそのまま」と、検証の「AWS では確かめない（動作を変えないため）」の前提が崩れる。in-place の更新で済むかは、私は plan を打っていないので確かめていない（build.md は provider のソースを読んだだけと書いている）。
  - Should にした理由: 通信には影響しない（ルールの中身は同じ）が、設計が「変えない」と約束したものが変わり、plan を見る人が No changes を期待していると食い違う。直すかどうかは設計の判断。

- [docs と成果物の食い違い] `docs/architecture/README.md:7` が `architecture-oss.pptx` の中身を「`ops/oss/up.sh`」のスライドと書くが、pptx は旧パスのまま（build.md の S3 を確かめた）
  - 場所: `docs/architecture/README.md:7`。`unzip -p docs/architecture-oss.pptx 'ppt/slides/*.xml'` を grep すると `oss/ops/up.sh` が 1 か所、`oss/terraform/` が 1 か所あり、`ops/oss/` は 0 か所。
  - シナリオ: README の説明を読んで pptx を開くと、スライドに書いてある名前（`oss/ops/up.sh`）が README と違い、そのパスはもう無い。pptx は zip なので検証 4 の grep では拾えない。
  - Should にした理由: この変更で新しく入った事実の誤り（017 の前は README と pptx が一致していた）。動作には影響しない。

## Nit

- [設計どうしの食い違い] 方針 3 の 1 文（`docs/oss-variant.md:66` の「`oss/ops/up.sh` で立てたものは…`oss/ops/down.sh` で消す」）が、検証 4 の「grep が 0 行」に当たる
  - 場所: `docs/oss-variant.md:66`、design.md:45（方針 3）と検証 4。build.md の「設計からの逸脱」1 に記録済み。
  - シナリオ: 検証 4 を字面どおり打つと 1 行残り、「0 行」を合格の条件にしている人には失敗に見える。
  - Nit にした理由: 意図して残した 1 行で、実装は設計の字面に従っている。設計の検証の書き方の問題。

- [保守性] 「OSS 版の ops/up.sh」「OSS ops/up.sh」という既存の言い回しが、017 の後はマネージド版の `ops/up.sh` と読める（build.md の S4）
  - 場所: build.md:359 の `git grep` の当たり（`IaC/terraform/oss/pipeline/**`、`app/{agentcore,dashboard,temporal}/requirements-oss.txt`、`docker/images/{neo4j,spark}/Dockerfile`、`tests/test_oss.py:1899` など）。
  - シナリオ: コメントを読んだ人が、OSS 版の手順をマネージド版の `ops/up.sh` に探しにいく。
  - Nit にした理由: コメントだけで動作に関係せず、`oss/ops` の字面を含まないので設計の置換の組の外。BACKLOG に回せば足りる。

- [missing tests] `ops/oss/up.sh` の SSM の description `(created by ops/oss/up.sh)` を縛る test が無い（build.md の N1）
  - シナリオ: 1 か所を旧パスに戻しても test は通る（build.md の R7）。
  - Nit にした理由: 検証 4 と検証 8 が拾い、description は消す相手の選び方に使われていない（`down-common.sh:182` はタグで絞る）。方針 6 が test を足さないと決めている。

- [missing tests・既存] `ops/check.sh:52` の `find` に `app` があることを見る test が無い（build.md の N2）
  - シナリオ: `find` から `app` を外しても test は通り、`app/gateway/handler.py` の構文エラーを check.sh が見逃す（ただし test_workflow が handler を import するので、そこで落ちる）。
  - Nit にした理由: 017 の前から同じ穴（前は `tools` を見ていなかった）で、017 で増えたものではない。

- [設計の検証の書き方] 検証 6 の `git diff --stat -M docs/cycle-006-design` は、動くブランチを基準にしている
  - シナリオ: `docs/cycle-006-design` に 013 などがマージされたあとに打つと、017 と関係の無い差分が混ざり、「動かした 9 本が rename で出る」かどうかが読みにくくなる。build.md は設計のベース `03840c8` で打っていて、その形のほうが再現できる。
  - Nit にした理由: 結果の読み方の問題で、実装の正しさには関係しない。

## 良かった点

- rename だけの commit（fd9918b）と参照の書き換えの commit（f92399c）を分けていて、`git log --follow` で履歴が追える。`handler.py`・`tools.json`・`GLOSSARY.md` は R100 で、Gateway の Lambda の中身は変わらない。
- `OPS_DIR=ops/oss` に変えたことで SSM のタグ・description・down.sh の絞り込みが同じ値で揃い、test_oss_ops がその値を縛っている（R4・R5 で落ちることを実装者が確かめている）。
- `ops/oss/up.sh`・`down.sh` の頭の `..` の段数を、test の期待値（`'/../lab-common.sh"'` など）と一緒に直していて、旧形への退行も test が拾う（R9〜R11）。
- tests の diff が全部 1 行対 1 行のパスの置換で、件数も中身も弱めていない。
- 実装者のセルフレビューで、設計どうしの食い違い（S1・S2・D1）を直さずに PM に回していて、判断の材料（場所・シナリオ・確かめた範囲）が揃っている。

## ユーザーへの質問

- 方針 6 の「イメージの版はそのまま」は、`dir_tag` の中身のハッシュの部分も含む意味か（含むなら Should fix の 1 件目は `requirements-oss.txt:2` を戻す方向になる）。
- `docs/verification/` を当時のパスのまま残す（007 の方針）か、017 の書き換えを残す（設計の変更対象表）か。どちらかに決めると Should fix の 2 件目と `docs/development.md:7` の扱いが決まる。

### 確認（エンジニア1。opus-5.5 / xhigh）

- cold reviewer が返ったあとの `git status --porcelain -uall` は `?? docs/cycles/017-tidy-root/review-r01.md` だけ（ほかに増えたファイルなし）
- 件数（cold reviewer の分類）: Must fix 0 / Should fix 4 / Nit 5。確認後の分類: Must fix 0 / Should fix 5（Nit 2 を Should fix のまま残す。下）/ Nit 4
- **Should fix 1（Nautobot のイメージのタグ）を再現した。build.md のセルフレビューに無かったもの**
  - 03840c8 と HEAD（88bfac1）を `git archive` で取り出し、`ops/lab-common.sh:54-78` の `dir_tag` と `ops/up-common.sh:257-264` の `nautobot_context` と同じ計算で、中身のハッシュで決まるものを比べた（スクラッチの tags017.py。読むだけ。AWS には触らない）

    ```
    == dir_tag のイメージ（版の部分は X）
      telegraf   03840c8 X-5e4849f9cbd4  HEAD X-5e4849f9cbd4  同じ
      syslog-ng  03840c8 X-90f5978c4447  HEAD X-90f5978c4447  同じ
      grafana    03840c8 X-b8d6d51ee31d  HEAD X-b8d6d51ee31d  同じ
      splunk     03840c8 X-071b361034b8  HEAD X-071b361034b8  同じ
      spark      03840c8 X-384311cb6a49  HEAD X-384311cb6a49  同じ
      neo4j      03840c8 X-e8013fd894bf  HEAD X-e8013fd894bf  同じ
      nautobot   03840c8 X-83eb721caa36  HEAD X-0cad931cdd12  違う
      nautobot の context の diff -r:
        diff -r <tmp>/ctx_old/requirements-oss.txt <tmp>/ctx_new/requirements-oss.txt
        2c2
        < # docker buildx build --platform linux/arm64 --build-arg REQUIREMENTS=requirements-oss.txt -f docker/images/nautobot/Dockerfile <context>（OSS 版の oss/ops/up.sh の build_nautobot）
        ---
        > # docker buildx build --platform linux/arm64 --build-arg REQUIREMENTS=requirements-oss.txt -f docker/images/nautobot/Dockerfile <context>（OSS 版の ops/oss/up.sh の build_nautobot）
    == Lambda の zip の材料・wheels の requirements（ファイルの sha256 の頭 12 桁）
      graph status（index.py）: 213544924d3b / 213544924d3b  同じ
      graph status（graph.py）: 128950c1c02e / 128950c1c02e  同じ
      kb_index（index.py）: 8983e6f2a422 / 8983e6f2a422  同じ
      gateway tools（handler）: 7609a12ecad1 / 7609a12ecad1  同じ
      gateway tools（tools.json）: 81199b62871f / 81199b62871f  同じ
      wheels app/dashboard/requirements.txt: c3d165a5952f / c3d165a5952f  同じ
      wheels app/dashboard/requirements-oss.txt: 4a08a1a3a581 / 4a08a1a3a581  同じ
      neo4j layer app/graph/requirements-oss.txt: 58a699ad13bd / 58a699ad13bd  同じ
    == lab の S3（app/containerlab/ の木）
      0a0c1db518c5 / ffd2cf07ca7d  違う
    ```

  - 原因: design.md:66 の変更対象表が `app/nautobot/requirements-oss.txt` のコメントの書き換えを指定している。`app/nautobot/` は丸ごと Nautobot のイメージの context に写り（`ops/up-common.sh:260`）、タグはその中身のハッシュ（`ops/lab-common.sh:54-78`）なので、コメント 1 行でもタグが変わる。方針 6（design.md:51「イメージの版はそのまま」）は、タグが中身のハッシュで決まるイメージがあることを見ていない（設計どうしの食い違い）。セルフレビューで見落としたのは、build.md の「問題なしとした観点」で入れ替わりを確かめたのが Gateway の Lambda（blob の比較）だけで、`dir_tag` で決まるイメージのタグを見ていなかったため
  - 波及: マネージド版（`ops/up.sh:650-651`）と OSS 版（`ops/oss/up.sh:199-200`）の次の up.sh で、KEEP_ECR=1 で ECR を残していても `ecr_has` が外れて `NEED_NAUTOBOT=1` になり、Nautobot のイメージを 1 回作り直してタスクを入れ替える。DB は RDS（`IaC/terraform/aws-managed/pipeline/nautobot/database.tf`）なので中身は消えない（読んだだけ）
  - ほかの中身のハッシュで決まるもの（上の 6 つのイメージ、Lambda の zip の材料、wheels の requirements）は変わらない（上の出力）
  - lab の S3 の木は変わる（`app/containerlab/trex/kafka_load.sh` のコメント）。`upload_lab`（`ops/lab-common.sh:100-107`）の `aws s3 sync` がその 1 本を上げ直すだけで、lab の EC2 は起動のたびに S3 を読む（同じ箇所のコメント）。`grep -rn -E 'filesha|source_code_hash|source_hash|etag|containerlab' IaC --include='*.tf'` の当たりで中身のハッシュを資源に結ぶのは Lambda の 3 本と neo4j の layer の `source_code_hash`（`graph/sync.tf:124`、`agent/kb.tf:209`、`workflow/gateway.tf:192`、`oss/pipeline/graph/sync.tf:65`）だけで、lab の EC2（`pipeline/lab/instance.tf:22`）が受けるのは `containerlab_version` だけ。lab の EC2 は入れ替わらない
  - 扱い: 直さない。PM の判断に回す（案は cold reviewer の (a) コメントを戻して検証 4 の除外に足す / (b) 1 回の作り直しを design.md と build.md に書く）
- Should fix 2〜4 は build.md の S1・S2・S3 と同じもの。cold reviewer の記述は build.md の `#### PM の判断に回したもの` と食い違わない（S1 は反対弁護人が verify-oss-20261008 を `ls` した結果と `ops/oss/down.sh:26-27`、S2 は `security_groups.tf:153,156,166,169` とシンボリックリンクと provider のソースを読んだだけ、S3 は pptx の `ppt/slides/*.xml` の中身）。扱いも同じ（PM の判断）
- Nit
  - Nit 1（`docs/oss-variant.md:66` が検証 4 に当たる）: build.md の D1 と同じ。検証 4 の実測は 1 行
  - Nit 2（「OSS 版の ops/up.sh」の言い回し）: build.md の S4。cold reviewer は「コメントだけ・置換の組の外」で Nit にしたが、どちらも格下げの実測ではない。こちらは Should fix のまま PM に回す
  - Nit 3（`created by ops/oss/up.sh` を縛る test が無い）: build.md の N1。R7 の注入で tests は通り、検証 4 と検証 8 が拾うことは build.md の inject017b の出力にある
  - Nit 4（`ops/check.sh:52` の find の `app` を縛る test が無い）: build.md の N2。017 の前からある穴
  - Nit 5（検証 6 の基準 `docs/cycle-006-design` が動く）: build.md の検証 6 は 03840c8 に対して 9 本、`docs/cycle-006-design` に対して 68 本（012 の 2 commit が混ざる。rename は同じ 9 本）。設計の検証の書き方の問題で、直さない
- cold reviewer の質問 2 件（方針 6 が dir_tag のハッシュを含むか、`docs/verification/` を当時のパスのまま残すか）は、Should fix 1 と 2 の判断として PM に回す
- 前ラウンドの Must fix: なし（Round 1）

### PM の判断

2026-10-09。PM が先に design.md を直した（df42f51）。実装は build.md の Round 2。

- Should fix 1（Nautobot のタグ）: (b) このまま。方針 6 に「イメージの版に `dir_tag` は含まない。Nautobot は次の up.sh で 1 回作り直す」。実装は変えない
- S1（verification）: 戻す。`docs/verification/20261008-oss-aws.md` を 03840c8 の内容にし、`docs/development.md:7` を変更対象表の文にする（0ae2439）
- S2（`security_groups.tf`）: (a) このまま。方針 6 に in-place の例外として書いた
- S3（README と pptx）: (b) pptx は触らない。`docs/architecture/README.md:7` に注を足す（0ae2439）。BACKLOG は PM が足した
- S4（言い回し）: このサイクルで直す。方針 7、commit 3（2dc6237）
- D1: 検証 4 の期待に `oss-variant.md` の方針 3 の 1 文を除外として書いた。実装は変えない
- Nit N1〜N4: 直さない
- cold review の 2 回目は PM が PR に対して呼ぶ
