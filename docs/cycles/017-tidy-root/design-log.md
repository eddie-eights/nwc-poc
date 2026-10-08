# 根に残ったものを app/ ops/ docs/ に片付ける（017）— design-log

## Round 0（2026-10-09、PM）

ユーザーの決定（2026-10-09）: 「それで進めて。デプロイ用の sh コマンド系は ops でいい」。置き場所はそれで決まったので、質問は無し。

### 決めたこと

- `oss/ops/` → `ops/oss/`。`tools/handler.py` と `tools.json` → `app/gateway/`。`tools/netflow_send.py` → `ops/`（デプロイ用ではないが試験用のスクリプトで、ユーザーの「sh コマンド系は ops」に寄せた。`app/` に置くとイメージや Lambda に入るものと紛れる）。`GLOSSARY.md` → `docs/`。
- SSM のタグ `ManagedBy` は据え置かず `ops/oss/up.sh` に変える。据え置き案（`OPS_DIR=oss/ops` のまま）は、タグとパスが食い違い、`roll-nodes.sh` の案内が存在しないパスを出すので却下。移行の仕組み（古いタグも消す `down.sh`）は、古いタグのパラメータが 2026-10-09 に 1 本（`/efukuda-nwc-oss/kafka/cluster-id`）しか無く、PM がその場で消して 0 本にしたので入れない。
- `docs/cycles/` の記録は書き換えない（過去の事実）。
- 動作は変えない。新しい test も足さない。

### 調べた所

- Explore エージェントの棚卸し（参照ファイルの一覧、tests の行番号、`OPS_DIR` の流れ）を、PM が `oss/ops/up.sh:28-45`、`oss/ops/down.sh:18-30`、`ops/common.sh:1-12`、`ops/down-common.sh:178-186`、`ops/check.sh:48-56`、`gateway.tf:1-22`、`tests/test_oss.py:1935-1942`、`tests/test_collectors.py:200-228`、`tests/test_workflow.py` の `tools` 行、`.gitignore:1-30`、`.env.example:103-107` で読み直した。
- BACKLOG の「参照は gateway.tf 2、app.py 1、test_workflow 3、docs 1」のうち `app.py 1` は `strands.tools` の import で、パスの参照ではない（design.md に訂正を書いた）。
- `docker/images/agentcore/Dockerfile` と `base/core` の `upload_web_command` は `tools/handler.py` を含まない（grep 0）。

## Round 1（2026-10-09、PM）

cold review の 1 回目（opus、エンジニア1 が呼んだ。Must fix 0 / Should fix 5 / Nit 4）への PM の判断。設計を正本にし、直すものは先に design.md に書いた。

- **S1（Nautobot のイメージのタグが変わる）**: 方針 6 の「イメージの版」は `oss-images.sh` と `IMAGE_TAG` の版で、`dir_tag` は含まないと明記した（方針 6 の補足）。コメントは戻さず、次の `up.sh` で 1 回作り直すのを織り込む（リスク 6）。実装は変えない。
- **S2（verification の書き換え）**: 007 の方針（`docs/development.md:7`「当時のパス」）が設計表より優先。`docs/verification/20261008-oss-aws.md` を 03840c8 の内容に戻し、変更対象表から外した。`development.md:7` の 017 の 1 文は「verification と cycles は当時のまま」の形に書き直す。検証 4c を足した。
- **S3（`security_groups.tf` の `why`）**: 設計表どおり書き換える。`aws_vpc_security_group_*_rule` の `description` なので in-place の更新（SG 自体ではない）。方針 6 の例外として明記。plan は AWS の検証で見る（未確認）。
- **S4（README と pptx の食い違い）**: pptx はこのサイクルで作り直さない。`docs/architecture/README.md:7` に注を足し、BACKLOG に「architecture の pptx 2 本を 007 と 017 のあとのパスで作り直す」を足した（リスク 5）。
- **S5（「OSS 版の ops/up.sh」の言い回し）**: 017 の目的（根のパスの食い違いを無くす）に反するので、このサイクルで直す。方針 7 と実装ステップ 2b、検証 4b を足した。実装ファイルが変わるので、cold review の 2 回目（完了判定の直前）は PM が PR に対して呼ぶ。
- **D1（`docs/oss-variant.md:66` が旧パスを字面で持つ）**: 方針 3 の 1 文は意図どおり。検証 4 の期待出力に除外として書いた。
- **Nit N1〜N4**: 直さない。N3 は S2 で解消（verification は当時のまま）。N4 は基準を 03840c8 と書いてあるので、検証 6 はそのまま。
