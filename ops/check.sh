#!/usr/bin/env bash
# nwc-poc - AWS に触らずに打てる検査をまとめて打つ。docs/development.md の「手元で確かめる」と同じ内容。
#   1. terraform fmt -check -recursive（IaC/terraform/aws-managed/ と、OSS 版（cycle 005）の IaC/terraform/oss/）
#   2. 10 のルートで init -backend=false + validate（provider を取るだけで state には触らない）。IaC/terraform/aws-managed/ と IaC/terraform/oss/ の両方。
#      IaC/terraform/oss/ のルートは IaC/terraform/aws-managed/ のファイルへのシンボリックリンクと oss.auto.tfvars（project = nwc-oss）なので、
#      IaC/terraform/aws-managed/ を変えると両方の validate に効く
#   3. スクリプトの構文（git が追跡している .sh は全部（git ls-files '*.sh'）1 つずつ bash -n。bash -n a b は a しか見ない。リポジトリの .py は全部 ast.parse）
#   4. 模擬テスト（tests/test_*.py を全部。AWS に触れない）
#   5. 旧名（cycle 019 で nwc に改めた名前）が戻っていない（git ls-files の追跡ファイルを grep。docs/cycles と docs/verification は記録なので見ない）
# 最後の行が「すべて通過」なら健全。途中で落ちたらそこで止まる。
set -euo pipefail

cd "$(dirname "$0")/.."

ROOTS=(base/ecr base/logs base/core agent pipeline/lab pipeline/stream pipeline/analytics pipeline/graph pipeline/nautobot workflow)
TF_BASES=(IaC/terraform/aws-managed IaC/terraform/oss)

log() { printf '\n== %s\n' "$*"; }
die() {
  printf '\n!! %s\n' "$*" >&2
  exit 1
}

command -v terraform >/dev/null || die "terraform が無い（docs/setup.md の「Terraform を打つ PC 側」）"

log "1. terraform fmt -check -recursive IaC/terraform/aws-managed IaC/terraform/oss"
for base in "${TF_BASES[@]}"; do
  terraform fmt -check -recursive "$base" || die "整形されていないファイルがある。terraform fmt -recursive $base で直す"
done
echo "差分なし"

log "2. 10 のルートの validate（IaC/terraform/aws-managed/ と IaC/terraform/oss/）"
for base in "${TF_BASES[@]}"; do
  # IaC/terraform/oss の lock はマネージド版の lock へのシンボリックリンク。init が lock を書くとリンクが実ファイルに置き換わる（ops/common.sh の tf_init_root）ので、
  # IaC/terraform/oss は -lockfile=readonly で読むだけにする。この PC のハッシュは先に回る IaC/terraform/aws-managed/ のルートが足す（TF_BASES の順を入れ替えても lock は壊れず、init で止まる）
  LOCK=""; if [ "$base" = IaC/terraform/oss ]; then LOCK=-lockfile=readonly; fi
  for r in "${ROOTS[@]}"; do
    terraform -chdir="$base/$r" init -backend=false -input=false ${LOCK:+"$LOCK"} >/dev/null \
      || die "$base/$r の init が失敗した${LOCK:+（lock file のエラーなら、この PC のハッシュがマネージド版の lock に無い。先に terraform -chdir=IaC/terraform/aws-managed/$r init -backend=false で足す）}"
    terraform -chdir="$base/$r" validate >/dev/null || die "$base/$r の validate が失敗した（terraform -chdir=$base/$r validate で中身を見る）"
    echo "$base/$r  OK"
  done
done

log "3. スクリプトの構文"
# .sh は git が追跡している全部を 1 つずつ見る（名指しにすると、ファイルを足したときに検査から漏れる。追跡していない .sh は見ない）
SH=$(git ls-files '*.sh')
[ -n "$SH" ] || die "git ls-files で .sh が取れない（リポジトリの中で打つ）"
while IFS= read -r f; do bash -n "$f" || die "$f に構文エラーがある"; done <<<"$SH"
echo "bash -n: $(grep -c . <<<"$SH") 本"
if command -v python3 >/dev/null; then PY=(python3); else PY=(uv run --python 3.13 python); fi
# .py は名指しにせず全部見る（名指しにすると、ファイルを足したときに検査から漏れる）
find app docker docs ops tests -name '*.py' -not -path '*/__pycache__/*' -print0 |
  xargs -0 "${PY[@]}" -c 'import ast, sys
for f in sys.argv[1:]:
    ast.parse(open(f, encoding="utf-8").read(), f)'
echo "構文エラーなし"

log "4. 模擬テスト"
if command -v uv >/dev/null; then
  # tests/ の test_*.py を全部（名指しで並べると、足したテストを入れ忘れる。tests/test_oss.py がこの glob であることを見る）
  for t in tests/test_*.py; do
    # test_nautobot は app/dashboard/topology_view.py を読むので gradio と pandas（web のグループ）も要る
    uv run --group dev --group web python "$t" || die "$t が失敗した"
  done
else
  echo "uv が無いので飛ばす（docs/development.md の「手元で確かめる」の通り uv sync --group dev --group web を入れてから打つ）"
fi

# 旧名はこのファイルにも字面で書かない（書くとこのファイルが grep に掛かる）。'net''ops' は bash が 1 語につなぐ
OLD_NAME='net''ops'
log "5. 旧名 $OLD_NAME が戻っていない（docs/cycles と docs/verification は記録なので見ない。cycle 019）"
# 残ってよいのは tables.tf の moved（古い state のアドレスは字面で書くしかない。IaC/terraform/oss のはそのシンボリックリンク）と、
# それを見る tests/test_analytics.py の 1 check だけ。moved を消したら ALLOWED と 5 行もここで直す
HITS=$(git ls-files -z | grep -z -v -e '^docs/cycles/' -e '^docs/verification/' | xargs -0 grep -l -i "$OLD_NAME" -- 2>/dev/null | LC_ALL=C sort || true)
ALLOWED=$'IaC/terraform/aws-managed/pipeline/analytics/tables.tf\nIaC/terraform/oss/pipeline/analytics/tables.tf\ntests/test_analytics.py'
[ "$HITS" = "$ALLOWED" ] || die "$OLD_NAME が残っている（許すのは tables.tf の moved と test_analytics の check だけ）: $(printf '%s' "$HITS" | tr '\n' ' ')"
n=$(grep -c -i "$OLD_NAME" IaC/terraform/aws-managed/pipeline/analytics/tables.tf tests/test_analytics.py | awk -F: '{s+=$2} END{print s}')
[ "$n" -eq 5 ] || die "tables.tf と test_analytics.py の $OLD_NAME が 5 行でない（$n 行）。moved 3 行と check 2 行以外に増えている"
echo "$OLD_NAME なし（許した 3 ファイル 5 行だけ）"

printf '\nすべて通過\n'
