# ops/up.sh・ops/down.sh と、OSS 版（005）の ops/oss/up.sh・ops/oss/down.sh が読む共通の関数（画面の出し方と terraform の打ち方）。
# 読む側は、先に REGION を決め、ops/deploy-env.sh を読んでリポジトリの直下へ cd しておく。PREFIX は tf_use_cli_credentials を呼ぶ前に決める。
# TF_DIR は terraform のルートを置いたディレクトリ（既定 IaC/terraform/aws-managed。OSS 版は IaC/terraform/oss）。
# OPS_DIR は案内に出す up.sh / down.sh のディレクトリと、ops/up.sh が作った SSM のパラメータのタグ ManagedBy=<OPS_DIR>/up.sh（既定 ops。OSS 版は ops/oss）。
# どちらも、このファイルを読んだあとに書き換えてよい（関数は呼ばれたときに読む）。
TF_DIR="${TF_DIR:-IaC/terraform/aws-managed}"
OPS_DIR="${OPS_DIR:-ops}"

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mNG: %s\033[0m\n' "$*" >&2; exit 1; }
# terraform に渡す認証情報。もとは IaC/terraform/aws-managed/agent の opensearch provider（古い AWS SDK の Go v1）が `aws login` で入ったプロファイル
# （login_session）を読めずに NoCredentialProviders で落ちたための回避（provider は 2026-09-28 に外した。aws provider が
# login_session を読めるかは確かめていないので残す）。鍵が環境変数に無い（= プロファイルから読む）ときは、AWS CLI から
# 資格情報を受け取る credential_process だけのプロファイルを一時ファイルに書き、terraform にはそちらを読ませる
# （AWS CLI ユーザーガイド「Sharing Login credentials as process credentials」の形。15 分ごとの更新は CLI が続ける）。
# 一時ファイル TF_AWS_CONFIG は、読む側の EXIT の trap で消す
TF_AWS_CONFIG=""
TF_AWS_ENV=()
tf_use_cli_credentials() {
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ]; then  # 鍵が環境変数にあるときはそのまま渡す
    echo "terraform の認証情報: 環境変数の鍵"
    return 0
  fi
  local profile_opt=""
  if [ -n "${AWS_PROFILE:-}" ]; then profile_opt="--profile $(printf '%q' "$AWS_PROFILE")"; fi
  TF_AWS_CONFIG=$(mktemp "${TMPDIR:-/tmp}/$PREFIX-aws-config.XXXXXX") || die "一時ファイルを作れない（TMPDIR）"
  printf '[profile %s-terraform]\ncredential_process = env -u AWS_PROFILE AWS_CONFIG_FILE=%q aws configure export-credentials %s --format process\n' \
    "$PREFIX" "${AWS_CONFIG_FILE:-$HOME/.aws/config}" "$profile_opt" >"$TF_AWS_CONFIG"
  TF_AWS_ENV=(AWS_CONFIG_FILE="$TF_AWS_CONFIG" AWS_PROFILE="$PREFIX-terraform")
  echo "terraform の認証情報: プロファイル ${AWS_PROFILE:-（既定）} を AWS CLI 経由（credential_process）で渡す"
}
tf() {  # tf <ルート> <terraform のサブコマンドと引数…>
  local root="$1"; shift
  env ${TF_AWS_ENV[@]+"${TF_AWS_ENV[@]}"} terraform -chdir="$TF_DIR/$root" "$@"
}
# emr_cancel_jobs <EMR Serverless のアプリケーション ID> <待つ回数（5 秒ごと）>
#   動いている・待っている（SUBMITTED / PENDING / SCHEDULED / RUNNING / QUEUED）Spark のジョブを名前で絞らずに全部 cancel し、
#   止まる（CANCELLING も抜ける）まで待つ。止め切れたら 0。待ち切れなければ残ったジョブの id を EMR_JOBS_LEFT に入れて 1 を返す
#   （止まるか進むかは呼ぶ側が決める）。アプリケーションは止めない。
#   ops/up.sh の手順 7-4（アプリの上限やサブネットを変える前）、ops/down.sh（アプリを止めて消す前）、ops/stop-spark.sh（ジョブだけ止める）が呼ぶ。
#   手順 7-5 は SpecHash で選んだジョブだけを止めるので、ここは使わない。REGION を使う
EMR_JOBS_LEFT=""
emr_cancel_jobs() {
  local app="$1" loops="$2" runs id i
  runs=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$app" \
    --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED --query 'jobRuns[].id' --output text 2>/dev/null || true)
  [ "$runs" != None ] || runs=""
  [ -n "$runs" ] || echo "動いている Spark のジョブは無い"
  for id in $runs; do
    echo "Spark のジョブ $id を止める"
    aws emr-serverless cancel-job-run --region "$REGION" --application-id "$app" --job-run-id "$id" >/dev/null || true
  done
  EMR_JOBS_LEFT=""
  for i in $(seq 1 "$loops"); do
    EMR_JOBS_LEFT=$(aws emr-serverless list-job-runs --region "$REGION" --application-id "$app" \
      --states SUBMITTED PENDING SCHEDULED RUNNING QUEUED CANCELLING --query 'jobRuns[].id' --output text 2>/dev/null || true)
    [ "$EMR_JOBS_LEFT" != None ] || EMR_JOBS_LEFT=""
    [ -n "$EMR_JOBS_LEFT" ] || return 0
    sleep 5
  done
  return 1
}
# tf_init_root <ルート>  マネージド版は「init -input=false」のまま。OSS 版（005）は TF_INIT_LOCKFILE=readonly にして -lockfile=readonly を足す。
# IaC/terraform/oss/<ルート>/.terraform.lock.hcl はマネージド版の lock へのシンボリックリンクで、init が lock を書き換える場面
# （その PC の OS・CPU のハッシュが lock に無いとき）に、リンクが実ファイルに置き換わる。readonly なら書き換えずに止まる
tf_init_root() {
  local hint=""
  if [ -n "${TF_INIT_LOCKFILE:-}" ]; then
    hint="。上に「lock file」のエラーが出ているなら、この PC の OS・CPU のハッシュが lock に無い（-lockfile=$TF_INIT_LOCKFILE では書き足さない）。先にマネージド版のルートを init して lock に足す: terraform -chdir=IaC/terraform/aws-managed/$1 init -input=false のあと、もう一度打つ"
  fi
  tf "$1" init -input=false ${TF_INIT_LOCKFILE:+"-lockfile=$TF_INIT_LOCKFILE"} >/dev/null \
    || die "$TF_DIR/$1 の init に失敗した（provider の取得。社内 PC は docs/setup.md「社内 PC の CA」）$hint"
}
