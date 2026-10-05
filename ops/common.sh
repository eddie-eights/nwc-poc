# ops/up.sh・ops/down.sh と、OSS 版（005）の oss/ops/up.sh・oss/ops/down.sh が読む共通の関数（画面の出し方と terraform の打ち方）。
# 読む側は、先に REGION を決め、ops/deploy-env.sh を読んでリポジトリの直下へ cd しておく。PREFIX は tf_use_cli_credentials を呼ぶ前に決める。
# TF_DIR は terraform のルートを置いたディレクトリ（既定 terraform。OSS 版は oss/terraform）。
# OPS_DIR は案内に出す up.sh / down.sh のディレクトリと、ops/up.sh が作った SSM のパラメータのタグ ManagedBy=<OPS_DIR>/up.sh（既定 ops。OSS 版は oss/ops）。
# どちらも、このファイルを読んだあとに書き換えてよい（関数は呼ばれたときに読む）。
TF_DIR="${TF_DIR:-terraform}"
OPS_DIR="${OPS_DIR:-ops}"

log()  { printf '\n\033[1;34m== %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mNG: %s\033[0m\n' "$*" >&2; exit 1; }
# terraform に渡す認証情報。もとは terraform/agent の opensearch provider（古い AWS SDK の Go v1）が `aws login` で入ったプロファイル
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
