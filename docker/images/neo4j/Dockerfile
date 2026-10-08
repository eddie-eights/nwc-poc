# OSS 版（cycle 005）の Neo4j のイメージ。Neptune Analytics の代わりに ECS のタスク 1 つで動かす（IaC/terraform/oss/pipeline/graph の neo4j.tf）。
# Neo4j Community Edition に GDS（Graph Data Science）を足す。GDS の jar は公式イメージの products/ に入っている
# （neo4j:2026.09.0-community には neo4j-graph-data-science-2026.09.0.jar）ので plugins/ に写すだけで、起動時のダウンロード（NEO4J_PLUGINS）は使わない。
# 閉域のタスクは外へ取りに行けない。組み合わせは oss/compose/neo4j/Dockerfile で確かめたものと同じ。
# パスワードは ECS の secrets で GRAPH_PASSWORD に入れ、entrypoint.sh が NEO4J_AUTH に直してから公式の entrypoint を呼ぶ（理由は entrypoint.sh）。
# build の context は app/neo4j/（OSS 版の ops/up.sh がタグを中身から作る: <Neo4j の版>-<app/neo4j/ とこの Dockerfile のハッシュ>）
ARG NEO4J_VERSION=2026.09.0
FROM neo4j:${NEO4J_VERSION}-community
RUN cp /var/lib/neo4j/products/neo4j-graph-data-science-*.jar /var/lib/neo4j/plugins/
ENV NEO4J_dbms_security_procedures_unrestricted=gds.*
COPY --chmod=0755 entrypoint.sh /startup/nwc-entrypoint.sh
ENTRYPOINT ["/startup/nwc-entrypoint.sh"]
CMD ["neo4j"]
