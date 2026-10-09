# lab の材料（イメージの版・containerlab の rpm・S3 に置く lab/）。ops/up.sh（IaC/terraform/aws-managed/pipeline/lab と stream の Telegraf）と
# ops/lab-debug.sh（IaC/cloudformation/lab-debug.yaml のデバッグ用の EC2。up.sh とは別のスタックで、バケットと ECR もスタックが持つ）が source する。版と作り方をここ 1 か所にして、2 つの EC2 がずれないようにする。
# 呼ぶ側が REGION と PY（python の起動の配列）を先に決めておく。
#
# SRLINUX_TAG / MULTITOOL_TAG / TREX_TAG は上流の版（docker/compose/.env.example の手元の lab もこの版を引く）。ECR に置くタグはその後ろに -$LAB_ARCH を付けた
# *_ECR_TAG で、IaC/terraform/aws-managed/pipeline/lab の変数の既定値（*_image_tag）と IaC/cloudformation/lab-debug.yaml のパラメータの既定値はこちら。
# CONTAINERLAB_VERSION は同じ 2 つの containerlab_version / ContainerlabVersion に、TELEGRAF_VERSION は docker/images/telegraf/Dockerfile の ARG の既定値に合わせてある。
# 変えるときは全部を変える（tests/test_lab_debug.py が見る）。lab の EC2 は x86_64（TRex が amd64 だけのため）なので、lab のイメージと rpm は amd64 を引く
SRLINUX_TAG=26.7.2   # ghcr.io/nokia/srlinux はマルチアーキ。amd64 を引く
MULTITOOL_TAG=v0.10.0
TREX_TAG=2.41        # trexcisco/trex は amd64 だけ（latest = 2.41）
# ECR のタグにアーキを入れるのは、2026-10-08 より前（lab の EC2 が arm64 だったころ）に上流の版そのままのタグで置いた arm64 の写しが
# KEEP_ECR=1 で残っていても、名前がぶつからないようにするため（タグがあれば写しを飛ばすので、同じ名前だと x86_64 の EC2 が arm64 を引いて起きない。
# リポジトリは IMMUTABLE なので同じタグへ上書きもできない）。前の arm64 のタグは使われずに残るだけ
LAB_ARCH=amd64
SRLINUX_ECR_TAG="$SRLINUX_TAG-$LAB_ARCH"
MULTITOOL_ECR_TAG="$MULTITOOL_TAG-$LAB_ARCH"
TREX_ECR_TAG="$TREX_TAG-$LAB_ARCH"
CONTAINERLAB_VERSION=0.79.0
TELEGRAF_VERSION=1.40.1
CONTAINERLAB_RPM="containerlab_${CONTAINERLAB_VERSION}_linux_amd64.rpm"
SRLINUX_UPSTREAM=ghcr.io/nokia/srlinux
MULTITOOL_UPSTREAM=ghcr.io/srl-labs/network-multitool
TREX_UPSTREAM=trexcisco/trex
# lab の SR Linux が送る syslog の形式。ops/up.sh の SYSLOG_STANDARD（stream の syslog-ng が受ける形式。既定は本番の Cisco に合わせた RFC3164）がこれと違えば up.sh が注意を出す。
# app/containerlab/lab.sh の LOG_STANDARD（デバッグ用の EC2 の Telegraf に渡す）と同じ
LAB_SYSLOG_STANDARD=RFC5424
# containerlab が SR Linux 全台に入れる既定の認証情報（lab だけの公開既定値で、実機の値ではない）。ops/up.sh が SSM の
# /<接頭辞>/gnmic/gnmi-username・gnmi-password の最初の値にする（実機を足すなら SSM の値を書き換える）。
# docker/compose/compose.yaml の gnmic の GNMI_USERNAME / GNMI_PASSWORD と同じ。SNMP の community は cycle 013 で SNMP のポーリングをやめたので持たない
LAB_GNMI_USERNAME=admin
LAB_GNMI_PASSWORD='NokiaSrl1!'

cfn_stack_status() {  # cfn_stack_status <スタック名>  状態を出す。無ければ空。読めない（認証切れ・スロットリングなど）なら理由を stderr に出して 1
  # 「無い」と「読めない」を分ける（読めないのを無いと扱うと、ops/lab-debug.sh down が消さずに終わり、up が器を作り直そうとする）
  local out
  if out=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$1" --query 'Stacks[0].StackStatus' --output text 2>&1); then
    printf '%s\n' "$out"
    return 0
  fi
  case "$out" in *"does not exist"*) return 0 ;; esac
  printf '%s\n' "$out" >&2
  return 1
}
ecr_has() {  # ecr_has <リポジトリ名> <タグ>
  aws ecr describe-images --region "$REGION" --repository-name "$1" --image-ids imageTag="$2" >/dev/null 2>&1
}
fetch() {  # fetch <URL> <ファイル名>  展開したフォルダの直下に無いときだけ取る。翌日からは取り直さない
  if [ -s "$2" ]; then echo "$2: 手元にあるので取らない"; return 0; fi
  curl -fL --retry 3 -o "$2.part" "$1" || { rm -f "$2.part"; return 1; }
  mv "$2.part" "$2"
}
dir_tag() {  # dir_tag <版> <ディレクトリ> [ファイル...]  "<版>-<ディレクトリの中身のハッシュ 12 桁>"。ECR のタグは上書きできないので、中身を変えたら別のタグにする
  # 3 つ目からのファイル（ディレクトリの外にある docker/images/<名前>/Dockerfile）は、リポジトリの根からの相対パスと中身をディレクトリの後ろに足す。
  # Dockerfile だけ変えてもタグが変わるように、呼ぶ側は build の -f と同じファイルを渡す。リポジトリの直下で呼ぶ
  "${PY[@]}" - "$@" <<'PY'
import hashlib, os, sys
ver, root, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
if not os.path.isdir(root):
    sys.exit(f"dir_tag: {root} がディレクトリでない")
h = hashlib.sha256()
for d, dirs, files in os.walk(root):
    dirs[:] = sorted(x for x in dirs if x != "__pycache__")
    for f in sorted(files):
        if f == ".DS_Store":
            continue
        path = os.path.join(d, f)
        h.update(os.path.relpath(path, root).encode() + b"\0")
        with open(path, "rb") as fh:
            h.update(fh.read())
for path in extra:
    h.update(os.path.relpath(path).encode() + b"\0")
    with open(path, "rb") as fh:
        h.update(fh.read())
print(f"{ver}-{h.hexdigest()[:12]}")
PY
}
mirror_image() {  # mirror_image <上流のイメージ:タグ> <ECR のイメージ:タグ> [platform]  上流のイメージを ECR に置き直す（EC2 は VPC の中から ECR しか引けない）
  # platform の既定は linux/arm64（Fargate）。lab の EC2 に載るものは linux/amd64 を渡す
  docker pull --platform "${3:-linux/arm64}" "$1"
  docker tag "$1" "$2"
  docker push "$2"
}
mirror_lab_images() {  # mirror_lab_images <レジストリ> <接頭辞>  lab の 3 つ（SR Linux 約 1 GB、linux kind の既定の multitool、TRex）のうち ECR に無いタグだけ。lab の EC2 は x86_64 なので amd64
  if ecr_has "$2-lab-srlinux" "$SRLINUX_ECR_TAG"; then echo "lab-srlinux:$SRLINUX_ECR_TAG はある"
  else mirror_image "$SRLINUX_UPSTREAM:$SRLINUX_TAG" "$1/$2-lab-srlinux:$SRLINUX_ECR_TAG" "linux/$LAB_ARCH" || return 1; fi
  if ecr_has "$2-lab-multitool" "$MULTITOOL_ECR_TAG"; then echo "lab-multitool:$MULTITOOL_ECR_TAG はある"
  else mirror_image "$MULTITOOL_UPSTREAM:$MULTITOOL_TAG" "$1/$2-lab-multitool:$MULTITOOL_ECR_TAG" "linux/$LAB_ARCH" || return 1; fi
  if ecr_has "$2-lab-trex" "$TREX_ECR_TAG"; then echo "lab-trex:$TREX_ECR_TAG はある"
  else mirror_image "$TREX_UPSTREAM:$TREX_TAG" "$1/$2-lab-trex:$TREX_ECR_TAG" "linux/$LAB_ARCH" || return 1; fi
}
telegraf_tag() {  # telegraf_tag  app/telegraf/ の中身と docker/images/telegraf/Dockerfile からタグを作る（stream の ECS もデバッグ用の EC2 もこのタグを引く）
  dir_tag "$TELEGRAF_VERSION" app/telegraf docker/images/telegraf/Dockerfile
}
build_telegraf() {  # build_telegraf <ECR のイメージ:タグ> [platform]  COPY だけなので PC のアーキと違っても QEMU は要らない
  # platform の既定は linux/arm64（stream の Fargate）。デバッグ用の EC2（x86_64 の lab と同じホスト）は linux/amd64 を渡す
  docker buildx build --platform "${2:-linux/arm64}" --build-arg "TELEGRAF_VERSION=$TELEGRAF_VERSION" -t "$1" --push -f docker/images/telegraf/Dockerfile app/telegraf/
}
upload_lab() {  # upload_lab <バケット>  lab の EC2 は起動のたびに s3://<バケット>/lab/ を読む（app/containerlab/setup.sh）。リポジトリの直下で呼ぶ
  fetch "https://github.com/srl-labs/containerlab/releases/download/v$CONTAINERLAB_VERSION/$CONTAINERLAB_RPM" "$CONTAINERLAB_RPM" \
    || { echo "containerlab の rpm が取れない（社内 PC なら docs/setup.md「社内 PC の CA」）" >&2; return 1; }
  # --delete: 手元で消した・改名したファイル（srlinux/*.cli など）を S3 からも消す。EC2 も --delete で読むので、残すと古い機器名が戻る。
  # --exclude に当たるものは送らず、S3 側でも消さない（aws s3 sync の --delete の説明）。rpm は下の cp で置くので、ここで除いて消させない
  aws s3 sync --only-show-errors --delete app/containerlab/ "s3://$1/lab/" --exclude "splab.clab.yml" --exclude "__pycache__/*" --exclude "*.DS_Store" --exclude "$CONTAINERLAB_RPM" || return 1
  aws s3 cp --only-show-errors "$CONTAINERLAB_RPM" "s3://$1/lab/"
}
