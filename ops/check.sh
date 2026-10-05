#!/usr/bin/env bash
# nwc-poc - AWS に触らずに打てる検査をまとめて打つ。docs/development.md の「手元で確かめる」と同じ内容。
#   1. terraform fmt -check -recursive（terraform/ と、OSS 版（cycle 005）の oss/terraform/）
#   2. 9 つのルートで init -backend=false + validate（provider を取るだけで state には触らない）。terraform/ と oss/terraform/ の両方。
#      oss/terraform/ のルートは terraform/ のファイルへのシンボリックリンクと oss.auto.tfvars（project = nwc-oss）なので、
#      terraform/ を変えると両方の validate に効く
#   3. スクリプトの構文（ops/*.sh は bash -n、リポジトリの .py は全部 ast.parse）
#   4. 模擬テスト 11 本（AWS に触れない）
# 最後の行が「すべて通過」なら健全。途中で落ちたらそこで止まる。
set -euo pipefail

cd "$(dirname "$0")/.."

ROOTS=(base/ecr base/core agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot workflow)
TF_BASES=(terraform oss/terraform)

log() { printf '\n== %s\n' "$*"; }
die() {
  printf '\n!! %s\n' "$*" >&2
  exit 1
}

command -v terraform >/dev/null || die "terraform が無い（docs/setup.md の「Terraform を打つ PC 側」）"

log "1. terraform fmt -check -recursive terraform oss/terraform"
for base in "${TF_BASES[@]}"; do
  terraform fmt -check -recursive "$base" || die "整形されていないファイルがある。terraform fmt -recursive $base で直す"
done
echo "差分なし"

log "2. 9 つのルートの validate（terraform/ と oss/terraform/）"
for base in "${TF_BASES[@]}"; do
  for r in "${ROOTS[@]}"; do
    terraform -chdir="$base/$r" init -backend=false -input=false >/dev/null || die "$base/$r の init が失敗した"
    terraform -chdir="$base/$r" validate >/dev/null || die "$base/$r の validate が失敗した（terraform -chdir=$base/$r validate で中身を見る）"
    echo "$base/$r  OK"
  done
done

log "3. ops スクリプトの構文"
bash -n ops/up.sh ops/down.sh ops/deploy-env.sh ops/check.sh ops/lab-debug.sh ops/lab-common.sh lab/lab.sh lab/setup.sh
if command -v python3 >/dev/null; then PY=(python3); else PY=(uv run --python 3.13 python); fi
# .py は名指しにせず全部見る（名指しにすると、ファイルを足したときに検査から漏れる）
find agent graph lab nautobot ops oss spark splunk tests tools web workflow -name '*.py' -not -path '*/__pycache__/*' -print0 |
  xargs -0 "${PY[@]}" -c 'import ast, sys
for f in sys.argv[1:]:
    ast.parse(open(f, encoding="utf-8").read(), f)'
echo "構文エラーなし"

log "4. 模擬テスト"
if command -v uv >/dev/null; then
  for t in tests/test_app.py tests/test_graph.py tests/test_stream.py tests/test_sync.py tests/test_analytics.py tests/test_workflow.py tests/test_alerts.py tests/test_kb_index.py tests/test_lab_debug.py tests/test_nautobot.py tests/test_oss.py; do
    # test_nautobot は web/topology_view.py を読むので gradio と pandas（web のグループ）も要る
    uv run --group dev --group web python "$t" || die "$t が失敗した"
  done
else
  echo "uv が無いので飛ばす（docs/development.md の「手元で確かめる」の通り uv sync --group dev --group web を入れてから打つ）"
fi

printf '\nすべて通過\n'
