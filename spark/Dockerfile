# OSS 版（cycle 005）の Spark のイメージ。EMR Serverless の代わりに ECS のタスクで spark-submit --master local[*] を打つ
# （oss/terraform/pipeline/analytics/spark.tf。マネージド版は使わない。マネージド版の EMR には ops/up.sh が snmp_sinks.py を S3 に置く）。
# 閉域で Maven に届かないので、jar はここで焼き込む（起動時の --packages は使わない）。
# jar は oss/compose/spark/Dockerfile で確かめた組み合わせ（Kafka・Iceberg・S3 Tables のカタログ）に、checkpoint を S3 に書く S3A を足す:
#   hadoop-aws はイメージの Hadoop（hadoop-client-api-3.3.4）と同じ版、aws-java-sdk-bundle は hadoop-aws 3.3.4 の pom が指す版。
# Kafka は PLAINTEXT なので、マネージド版の JAR_URLS にある MSK の IAM 認証の jar は入れない。
# build の context はこのディレクトリ（OSS 版の ops/up.sh がタグを中身から作る: <Spark の版>-<spark/ のハッシュ>）
ARG SPARK_VERSION=3.5.9
FROM apache/spark:${SPARK_VERSION}
ARG SPARK_VERSION
ARG MAVEN=https://repo1.maven.org/maven2
ARG ICEBERG_VERSION=1.10.1
ARG HADOOP_VERSION=3.3.4
ARG AWS_SDK_V1_VERSION=1.12.262
USER root
RUN cd /opt/spark/jars && for p in \
      org/apache/spark/spark-sql-kafka-0-10_2.12/$SPARK_VERSION/spark-sql-kafka-0-10_2.12-$SPARK_VERSION.jar \
      org/apache/spark/spark-token-provider-kafka-0-10_2.12/$SPARK_VERSION/spark-token-provider-kafka-0-10_2.12-$SPARK_VERSION.jar \
      org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar \
      org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar \
      org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/$ICEBERG_VERSION/iceberg-spark-runtime-3.5_2.12-$ICEBERG_VERSION.jar \
      org/apache/iceberg/iceberg-aws-bundle/$ICEBERG_VERSION/iceberg-aws-bundle-$ICEBERG_VERSION.jar \
      software/amazon/s3tables/s3-tables-catalog-for-iceberg-runtime/0.1.8/s3-tables-catalog-for-iceberg-runtime-0.1.8.jar \
      org/apache/hadoop/hadoop-aws/$HADOOP_VERSION/hadoop-aws-$HADOOP_VERSION.jar \
      com/amazonaws/aws-java-sdk-bundle/$AWS_SDK_V1_VERSION/aws-java-sdk-bundle-$AWS_SDK_V1_VERSION.jar; do \
      curl -fsSLO "$MAVEN/$p" || exit 1; \
    done
COPY snmp_sinks.py /opt/nwc/snmp_sinks.py
USER spark
