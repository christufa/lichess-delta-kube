# Spark 4.1 + Iceberg 1.12 + ABFS (hadoop-azure) for the Lichess lake.
#   - iceberg-spark-runtime: Iceberg tables, procedures (add_files, …), REST catalog client
#   - iceberg-azure-bundle:  Iceberg's ADLS FileIO (used with credentials vended by the catalog)
#   - hadoop-azure:          abfss:// for plain Parquet, with WorkloadIdentityTokenProvider
# Versions must match: Spark 4.1.x bundles Hadoop 3.4.2; Iceberg 1.12 ships a Spark 4.1 runtime.
#
# Two images from this file:
#   default            lichess-spark     jobs (spark-job) and notebook executors
#   --target notebook  lichess-notebook  the same + JupyterLab (jupyter workflow)
ARG SPARK_IMAGE=apache/spark:4.1.3-scala2.13-java21-python3-ubuntu
FROM ${SPARK_IMAGE} AS job

ARG ICEBERG_VERSION=1.12.0
ARG HADOOP_VERSION=3.4.2
ARG MAVEN=https://repo1.maven.org/maven2

USER root
ADD ${MAVEN}/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/${ICEBERG_VERSION}/iceberg-spark-runtime-4.1_2.13-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/iceberg/iceberg-azure-bundle/${ICEBERG_VERSION}/iceberg-azure-bundle-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/hadoop/hadoop-azure/${HADOOP_VERSION}/hadoop-azure-${HADOOP_VERSION}.jar /opt/spark/jars/
RUN chmod 0644 /opt/spark/jars/*.jar

# pandas + pyarrow on every node: toPandas() with Arrow and pandas UDFs.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore PIP_DISABLE_PIP_VERSION_CHECK=1
RUN (python3 -m pip --version >/dev/null 2>&1 || \
      (apt-get update && apt-get install -y --no-install-recommends python3-pip && rm -rf /var/lib/apt/lists/*)) \
 && python3 -m pip install "pandas>=2.2,<3" "pyarrow>=18"

# Our code: the session helper as an importable package, jobs as plain scripts.
COPY lichess_spark /usr/lib/python3/dist-packages/lichess_spark
COPY jobs /opt/lichess/jobs
COPY tests /opt/lichess/tests
COPY conf /opt/lichess/conf

USER spark

# --- notebook: JupyterLab whose kernel is a Spark driver (lichess_spark.notebook) ---
FROM job AS notebook
USER root
RUN python3 -m pip install "jupyterlab>=4.4,<5" ipykernel "matplotlib>=3.9" \
 && ln -s "$(ls /opt/spark/python/lib/py4j-*-src.zip | head -1)" /opt/spark/python/lib/py4j-src.zip
# pyspark from the Spark install (same version as the executors), importable by the kernel.
ENV PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-src.zip \
    PYSPARK_PYTHON=python3
COPY notebooks /opt/lichess/notebooks
COPY bin/start-jupyter.sh /opt/lichess/bin/start-jupyter.sh
USER spark
EXPOSE 8888 4040
ENTRYPOINT []
CMD ["/opt/lichess/bin/start-jupyter.sh"]

# Default target: the job image.
FROM job
