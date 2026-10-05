# 005: Neo4j Community Edition に GDS（Graph Data Science）を足したイメージ。
# GDS の jar は公式イメージの products/ に入っている（neo4j:2026.09.0-community には neo4j-graph-data-science-2026.09.0.jar）。
# plugins/ に写すだけで、起動時のダウンロード（NEO4J_PLUGINS）は使わない。ECS のタスクは外へ取りに行かなくてよい。
FROM neo4j:2026.09.0-community
RUN cp /var/lib/neo4j/products/neo4j-graph-data-science-*.jar /var/lib/neo4j/plugins/
ENV NEO4J_dbms_security_procedures_unrestricted=gds.*
