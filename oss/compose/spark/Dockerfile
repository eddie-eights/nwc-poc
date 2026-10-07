# 005: EMR を使わない Spark（ECS のタスクで spark-submit --master local[*]）のイメージの試し。
# jar は ops/up.sh の JAR_URLS と同じもの（MSK の IAM 認証の jar だけ外す。compose の Kafka は PLAINTEXT）に、
# EMR が自分で入れている Iceberg（iceberg-spark-runtime と、S3FileIO と SigV4 が使う AWS SDK の iceberg-aws-bundle）を足す。
# Spark は 3.5.9 なので spark-sql-kafka も 3.5.9（kafka-clients と commons-pool2 の版は spark-parent_2.12-3.5.9.pom の kafka.version / commons-pool2.version）。
# Iceberg は 1.12.0（EMR 7.14.0 が入れているのは 1.10.1-amzn-1）。1.12 のクラスは Java 17 向けなので、FROM は Java 17 のイメージ（Python 3 入り）。
# jar の sha256 は spark/Dockerfile と同じ値（tests/test_oss.py が突き合わせる）
FROM apache/spark:3.5.9-java17-python3
USER root
ARG MAVEN=https://repo1.maven.org/maven2
ARG SPARK_VERSION=3.5.9
ARG ICEBERG_VERSION=1.12.0
RUN cd /opt/spark/jars && for e in \
      fed393dcc3ef70611fe3d4f35ae78a8ea7c55301fd4cdd66c5dc97fcfaa5a00f:org/apache/spark/spark-sql-kafka-0-10_2.12/$SPARK_VERSION/spark-sql-kafka-0-10_2.12-$SPARK_VERSION.jar \
      e82612e6070438c5ea48b08c74f367a6d214a390bdf6dcf3ff316dca592ddda5:org/apache/spark/spark-token-provider-kafka-0-10_2.12/$SPARK_VERSION/spark-token-provider-kafka-0-10_2.12-$SPARK_VERSION.jar \
      9d0a9058c8ede79db6e54ae378ed16fe8d1bead2894635a4aa6aa0b7981668c6:org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar \
      ea0505ee7515e58b1ac0e686e4d1a5d9f7d808e251a61bc371aa0595b9963f83:org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar \
      8dd8c90e54ed1a20f2461309d047a255ee56382eb7c0a46162b27a4936fd86dc:org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/$ICEBERG_VERSION/iceberg-spark-runtime-3.5_2.12-$ICEBERG_VERSION.jar \
      dd7e49ffa4ac9b215bed846581991259e3aec9bb2fa08d6cec029c46cd66d957:org/apache/iceberg/iceberg-aws-bundle/$ICEBERG_VERSION/iceberg-aws-bundle-$ICEBERG_VERSION.jar \
      3c9f6db7aa3f1a64297644598ece21d9a9b1e7a56c52eca6e5dcca59519cbf4d:software/amazon/s3tables/s3-tables-catalog-for-iceberg-runtime/0.1.8/s3-tables-catalog-for-iceberg-runtime-0.1.8.jar; do \
      p=${e#*:}; curl -fsSLO "$MAVEN/$p" && echo "${e%%:*}  ${p##*/}" | sha256sum -c --quiet - || exit 1; \
    done
USER spark
