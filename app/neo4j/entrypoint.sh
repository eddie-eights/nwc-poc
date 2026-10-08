#!/bin/bash
# OSS 版の Neo4j のタスクの入口（docker/images/neo4j/Dockerfile）。パスワードを NEO4J_AUTH に直してから、公式イメージの entrypoint を同じ形で呼ぶ。
# 公式の entrypoint は NEO4J_ で始まる環境変数を neo4j.conf の設定に書き写す（NEO4J_AUTH などの決まった名前を除く）。
# パスワードを NEO4J_ で始まる名前で渡すと設定のファイルに平文で残り、知らない設定として起動も止まるので、ECS の secrets は
# NEO4J_ で始まらない GRAPH_PASSWORD に入れ（値は SSM の SecureString。IaC/terraform/oss/pipeline/graph の neo4j.tf）、ここで NEO4J_AUTH を作って消す。
# ユーザーは neo4j で固定（公式の entrypoint が neo4j 以外を受けない）。パスワードは 8 文字以上で / を含まないこと（同じく公式の entrypoint の決まり）
set -euo pipefail

if [ -n "${GRAPH_PASSWORD:-}" ]; then
  export NEO4J_AUTH="neo4j/${GRAPH_PASSWORD}"
elif [ -z "${NEO4J_AUTH:-}" ]; then
  # 何も無いと公式の既定（neo4j/neo4j で、最初の接続でパスワードの変更を求める）になり、アプリがつなげない。黙って起動しない
  echo "GRAPH_PASSWORD (or NEO4J_AUTH) is not set. Pass the SSM SecureString <prefix>/neo4j-password as GRAPH_PASSWORD." >&2
  exit 1
fi
unset GRAPH_PASSWORD

exec tini -g -- /startup/docker-entrypoint.sh "$@"
