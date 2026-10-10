# Temporal のサーバー（cycle 036）。WORKFLOW のタスクの temporal コンテナ。履歴は Nautobot の RDS for PostgreSQL（temporal / temporal_visibility）に書く。
# 公式の temporalio/server に、admin-tools の temporal-sql-tool（スキーマ）と temporal CLI（namespace と healthCheck）と psql（ロールと DB の作成）を足し、
# 入口を entrypoint-rds.sh（初期化してから /sbin/tini の子として公式の /etc/temporal/entrypoint.sh へ exec）に替える。worker のイメージは docker/images/temporal/（別物）。
# tini は entrypoint.sh が最後に exec する PID 1（temporal-server と namespace を作る背景のプロセスの親。抜けた子を回収する。cycle 039）。
# namespace.sh は entrypoint.sh が背景で起こす namespace の作成（/etc/temporal/namespace-rds.sh。master のパスワードを外したあとの env で動く。cycle 039）。
# 版の正は ops/up-common.sh の TEMPORAL_SERVER_VERSION と POSTGRES_MAJOR（--build-arg で渡す。ここの既定値も同じ値にしてある。tests/test_workflow.py が一致を見る）。
# ECS Fargate の ARM64 で動かす。RUN があるので、arm64 でない PC では buildx の QEMU が要る（ops/up.sh が確かめる）
ARG TEMPORAL_SERVER_VERSION=1.32.1
FROM temporalio/admin-tools:${TEMPORAL_SERVER_VERSION} AS tools
FROM temporalio/server:${TEMPORAL_SERVER_VERSION}
# RDS for PostgreSQL と同じ大版の psql（ops/up-common.sh の POSTGRES_MAJOR から --build-arg で渡す。RDS の版を変えたらそこだけ直す）
ARG POSTGRES_MAJOR=18
ARG POSTGRESQL_CLIENT_PACKAGE=postgresql${POSTGRES_MAJOR}-client
USER root
RUN apk add --no-cache ${POSTGRESQL_CLIENT_PACKAGE} tini
COPY --from=tools /usr/local/bin/temporal-sql-tool /usr/local/bin/temporal /usr/local/bin/
COPY --from=tools /etc/temporal/schema /etc/temporal/schema
COPY --chmod=755 entrypoint.sh /etc/temporal/entrypoint-rds.sh
COPY --chmod=755 namespace.sh /etc/temporal/namespace-rds.sh
# 埋め込みの設定テンプレートが読む dynamic config の既定のパス（無いと temporal-server が起動しない）
COPY dynamicconfig.yaml /etc/temporal/config/dynamicconfig/docker.yaml
USER temporal
ENTRYPOINT ["/etc/temporal/entrypoint-rds.sh"]
