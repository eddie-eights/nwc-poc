# OSS 版（cycle 005）の Spark のイメージ。EMR Serverless の代わりに ECS のタスクで spark-submit --master local[*] を打つ
# （IaC/terraform/oss/pipeline/analytics/spark.tf。マネージド版は使わない。マネージド版の EMR には ops/up.sh が snmp_sinks.py を S3 に置く）。
# 閉域で Maven に届かないので、jar はここで焼き込む（起動時の --packages は使わない）。
# jar は oss/compose/spark/Dockerfile で確かめた組み合わせ（Kafka・Iceberg・S3 Tables のカタログ）に、checkpoint を S3 に書く S3A を足す:
#   hadoop-aws はイメージの Hadoop（hadoop-client-api-3.3.4）と同じ版、aws-java-sdk-bundle は hadoop-aws 3.3.4 の pom が指す版。
# Kafka は PLAINTEXT なので、マネージド版の JAR_URLS にある MSK の IAM 認証の jar は入れない。
# build の context は app/spark/（OSS 版の ops/up.sh がタグを中身から作る: <Spark の版>-<app/spark/ のハッシュ>）
ARG SPARK_VERSION=3.5.9
# Iceberg 1.12 のクラスは Java 17 向け（class ファイルの版 61）なので、Java 11 の apache/spark:<版> ではなく Java 17 のもの（Python 3 入り）
FROM apache/spark:${SPARK_VERSION}-java17-python3
ARG SPARK_VERSION
ARG MAVEN=https://repo1.maven.org/maven2
ARG ICEBERG_VERSION=1.12.0
ARG HADOOP_VERSION=3.3.4
ARG AWS_SDK_V1_VERSION=1.12.262
USER root
# jar ごとに <sha256>:<Maven のパス>。取った jar の sha256 が合わなければビルドを止める（ミラーやキャッシュが壊れた jar を返しても通さない）。
# 版を変えたら sha256 も変える（Maven の <jar>.sha1 と突き合わせてから、shasum -a 256 の値を書く。2026-10-08 に 9 本とも突き合わせた）
RUN cd /opt/spark/jars && for e in \
      fed393dcc3ef70611fe3d4f35ae78a8ea7c55301fd4cdd66c5dc97fcfaa5a00f:org/apache/spark/spark-sql-kafka-0-10_2.12/$SPARK_VERSION/spark-sql-kafka-0-10_2.12-$SPARK_VERSION.jar \
      e82612e6070438c5ea48b08c74f367a6d214a390bdf6dcf3ff316dca592ddda5:org/apache/spark/spark-token-provider-kafka-0-10_2.12/$SPARK_VERSION/spark-token-provider-kafka-0-10_2.12-$SPARK_VERSION.jar \
      9d0a9058c8ede79db6e54ae378ed16fe8d1bead2894635a4aa6aa0b7981668c6:org/apache/kafka/kafka-clients/3.4.1/kafka-clients-3.4.1.jar \
      ea0505ee7515e58b1ac0e686e4d1a5d9f7d808e251a61bc371aa0595b9963f83:org/apache/commons/commons-pool2/2.11.1/commons-pool2-2.11.1.jar \
      8dd8c90e54ed1a20f2461309d047a255ee56382eb7c0a46162b27a4936fd86dc:org/apache/iceberg/iceberg-spark-runtime-3.5_2.12/$ICEBERG_VERSION/iceberg-spark-runtime-3.5_2.12-$ICEBERG_VERSION.jar \
      dd7e49ffa4ac9b215bed846581991259e3aec9bb2fa08d6cec029c46cd66d957:org/apache/iceberg/iceberg-aws-bundle/$ICEBERG_VERSION/iceberg-aws-bundle-$ICEBERG_VERSION.jar \
      3c9f6db7aa3f1a64297644598ece21d9a9b1e7a56c52eca6e5dcca59519cbf4d:software/amazon/s3tables/s3-tables-catalog-for-iceberg-runtime/0.1.8/s3-tables-catalog-for-iceberg-runtime-0.1.8.jar \
      53f9ae03c681a30a50aa17524bd9790ab596b28481858e54efd989a826ed3a4a:org/apache/hadoop/hadoop-aws/$HADOOP_VERSION/hadoop-aws-$HADOOP_VERSION.jar \
      873fe7cf495126619997bec21c44de5d992544aea7e632fdc77adb1a0915bae5:com/amazonaws/aws-java-sdk-bundle/$AWS_SDK_V1_VERSION/aws-java-sdk-bundle-$AWS_SDK_V1_VERSION.jar; do \
      p=${e#*:}; curl -fsSLO "$MAVEN/$p" && echo "${e%%:*}  ${p##*/}" | sha256sum -c --quiet - || exit 1; \
    done
COPY snmp_sinks.py /opt/nwc/snmp_sinks.py
USER spark
