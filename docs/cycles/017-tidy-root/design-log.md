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
