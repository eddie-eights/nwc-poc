# 005: EMR を使わない Spark（ECS のタスクで spark-submit --master local[*]）のイメージの試し。
# jar は ops/up.sh の JAR_URLS と同じもの（MSK の IAM 認証の jar だけ外す。compose の Kafka は PLAINTEXT）に、
# EMR が自分で入れている Iceberg（iceberg-spark-runtime と、S3FileIO と SigV4 が使う AWS SDK の iceberg-aws-bundle）を足す。
# Spark は 3.5.9 なので spark-sql-kafka も 3.5.9（kafka-clients と commons-pool2 の版は spark-parent_2.12-3.5.9.pom の kafka.version / commons-pool2.version）
FROM apache/spark:3.5.9
USER root
ARG MAVEN=https://repo1.maven.org/maven2
ARG SPARK_VERSION=3.5.9
ARG ICEBERG_VERSION=1.10.1
RUN cd /opt/spark/jars && for p in \
      org/apache/spark/spark-sql-kafka-0-10_2.12/$SPARK_VERSION/spark-sql-kafka-0-10_2.12-$SPARK_VERSION.jar \
      org/apache/spark/spark-token-provider-kafka-0-10_2.12/$SPARK_VERSION/spark-token-provider-kafka-0-10_2.12-$SPARK_VERSION.jar \
      org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar \
      org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar \
      org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/$ICEBERG_VERSION/iceberg-spark-runtime-3.5_2.12-$ICEBERG_VERSION.jar \
      org/apache/iceberg/iceberg-aws-bundle/$ICEBERG_VERSION/iceberg-aws-bundle-$ICEBERG_VERSION.jar \
      software/amazon/s3tables/s3-tables-catalog-for-iceberg-runtime/0.1.8/s3-tables-catalog-for-iceberg-runtime-0.1.8.jar; do \
      curl -fsSLO "$MAVEN/$p" || exit 1; \
    done
USER spark
