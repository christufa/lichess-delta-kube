# Spark 4.1 + Iceberg 1.12 + ABFS (hadoop-azure) for the Lichess lake.
#   - iceberg-spark-runtime: Iceberg tables, procedures (add_files, …), REST catalog client
#   - iceberg-azure-bundle:  Iceberg's ADLS FileIO (used with credentials vended by the catalog)
#   - hadoop-azure:          abfss:// for plain Parquet, with WorkloadIdentityTokenProvider
# Versions must match: Spark 4.1.x bundles Hadoop 3.4.2; Iceberg 1.12 ships a Spark 4.1 runtime.
ARG SPARK_IMAGE=apache/spark:4.1.3-scala2.13-java21-python3-ubuntu
FROM ${SPARK_IMAGE}

ARG ICEBERG_VERSION=1.12.0
ARG HADOOP_VERSION=3.4.2
ARG MAVEN=https://repo1.maven.org/maven2

USER root
ADD ${MAVEN}/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/${ICEBERG_VERSION}/iceberg-spark-runtime-4.1_2.13-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/iceberg/iceberg-azure-bundle/${ICEBERG_VERSION}/iceberg-azure-bundle-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/hadoop/hadoop-azure/${HADOOP_VERSION}/hadoop-azure-${HADOOP_VERSION}.jar /opt/spark/jars/
RUN chmod 0644 /opt/spark/jars/*.jar

# Our code: the session helper as an importable package, jobs as plain scripts.
COPY lichess_spark /usr/lib/python3/dist-packages/lichess_spark
COPY jobs /opt/lichess/jobs
COPY tests /opt/lichess/tests

USER spark
