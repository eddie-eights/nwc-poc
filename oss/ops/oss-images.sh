# OSS 版（cycle 005「マネージドを OSS に置き換えた環境を作る」）のイメージの名前と版。oss/ops/up.sh が source する。
# 版をここ 1 か所に持つ（ops/lab-common.sh と同じ考え方）。正は oss/compose/（手元で起こして確かめた版）で、
# 公開イメージは compose.yaml の image:、ビルドするもの（spark / neo4j）は oss/compose/<名前>/Dockerfile の FROM に合わせてある。
# 変えるときは compose と一緒に変える（tests/test_oss_ops.py が見る）。
# 先に ops/lab-common.sh を読み、REGION と PY を決めておく（ecr_has / mirror_image / dir_tag を使う）。
#
# ECR のリポジトリは oss/terraform/base/ecr（terraform/base/ecr/main.tf の oss_repositories）が <接頭辞>-<名前> で作る。
# Kafbat UI はマネージド版と同じリポジトリ <接頭辞>-kafka-ui（pipeline_repositories）。
OSS_KAFKA_IMAGE=apache/kafka
OSS_KAFKA_TAG=4.3.1                  # oss/terraform/pipeline/stream/kafka.tf の kafka_image_tag の既定値とも同じ
OSS_KAFKA_UI_IMAGE=ghcr.io/kafbat/kafka-ui
OSS_KAFKA_UI_TAG=v1.5.0              # マネージド版の ops/up.sh の KAFKA_UI_TAG と、terraform/pipeline/stream の kafka_ui_image_tag の既定値とも同じ
OSS_OPENSEARCH_IMAGE=opensearchproject/opensearch
OSS_OPENSEARCH_TAG=3.9.0
OSS_VM_TAG=v1.153.0-cluster          # VictoriaMetrics のクラスター版の 3 つ（victoriametrics/vmstorage・vminsert・vmselect）は同じ版
OSS_SPARK_VERSION=3.5.9              # oss/compose/spark/Dockerfile の FROM apache/spark:<版>。タグは dir_tag で中身のハッシュを足す
OSS_NEO4J_VERSION=2026.09.0-community  # oss/compose/neo4j/Dockerfile の FROM neo4j:<版>（GDS は公式イメージの products/ から写す）
# ECR に写すもの（terraform/base/ecr の oss_repositories と同じ 7 つ）
OSS_IMAGES="kafka opensearch vmstorage vminsert vmselect spark neo4j"

oss_image_tag() {  # oss_image_tag <名前>  ECR に置くタグ。知らない名前なら 1
  case "$1" in
    kafka) echo "$OSS_KAFKA_TAG" ;;
    opensearch) echo "$OSS_OPENSEARCH_TAG" ;;
    vmstorage|vminsert|vmselect) echo "$OSS_VM_TAG" ;;
    spark) dir_tag "$OSS_SPARK_VERSION" oss/compose/spark ;;  # ECR のタグは上書きできないので、Dockerfile を変えたら別のタグにする
    neo4j) dir_tag "$OSS_NEO4J_VERSION" oss/compose/neo4j ;;
    *) return 1 ;;
  esac
}
oss_image_upstream() {  # oss_image_upstream <名前>  写す元（ビルドするものは空）
  case "$1" in
    kafka) echo "$OSS_KAFKA_IMAGE" ;;
    opensearch) echo "$OSS_OPENSEARCH_IMAGE" ;;
    vmstorage|vminsert|vmselect) echo "victoriametrics/$1" ;;
    spark|neo4j) echo "" ;;
    *) return 1 ;;
  esac
}
# mirror_oss_images <レジストリ> <接頭辞> <名前…>  ECR に無いタグだけ置く。公開イメージは arm64 を引いて写し（mirror_image）、
# spark と neo4j は oss/compose/<名前>/ を arm64 でビルドして push する。リポジトリの直下で呼ぶ。docker login は呼ぶ側が済ませる
mirror_oss_images() {
  local reg="$1" prefix="$2" name tag upstream; shift 2
  for name in "$@"; do
    tag=$(oss_image_tag "$name") || { echo "知らないイメージ: $name（oss/ops/oss-images.sh の OSS_IMAGES: $OSS_IMAGES）" >&2; return 1; }
    if ecr_has "$prefix-$name" "$tag"; then echo "$name:$tag はある"; continue; fi
    upstream=$(oss_image_upstream "$name")
    if [ -n "$upstream" ]; then
      mirror_image "$upstream:$tag" "$reg/$prefix-$name:$tag" || return 1
    else
      docker buildx build --platform linux/arm64 -t "$reg/$prefix-$name:$tag" --push "oss/compose/$name" || return 1
    fi
  done
}
