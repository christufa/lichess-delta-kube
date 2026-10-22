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
FROM ghcr.io/astral-sh/uv:0.13.4 AS uv
FROM ${SPARK_IMAGE} AS job

# renovate: datasource=maven depName=org.apache.iceberg:iceberg-spark-runtime-4.1_2.13
ARG ICEBERG_VERSION=1.12.0
# renovate: datasource=maven depName=org.apache.hadoop:hadoop-azure
ARG HADOOP_VERSION=3.4.2
ARG MAVEN=https://repo1.maven.org/maven2

USER root
# OS security updates on top of the base image (CI fails on fixable critical CVEs).
RUN apt-get update && apt-get -y upgrade && rm -rf /var/lib/apt/lists/*
# Derby only backs Spark's embedded Hive metastore, which we don't use (Iceberg REST catalog).
RUN rm -f /opt/spark/jars/derby-*.jar

ADD ${MAVEN}/org/apache/iceberg/iceberg-spark-runtime-4.1_2.13/${ICEBERG_VERSION}/iceberg-spark-runtime-4.1_2.13-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/iceberg/iceberg-azure-bundle/${ICEBERG_VERSION}/iceberg-azure-bundle-${ICEBERG_VERSION}.jar /opt/spark/jars/
ADD ${MAVEN}/org/apache/hadoop/hadoop-azure/${HADOOP_VERSION}/hadoop-azure-${HADOOP_VERSION}.jar /opt/spark/jars/
RUN chmod 0644 /opt/spark/jars/*.jar

# Python for PySpark (the driver, the executors and the notebook kernel all run this image):
# CPython 3.12 from python-build-standalone, installed by uv into /opt/python. The base image is
# Ubuntu 22.04, whose own python3 is 3.10; it stays for apt, ours comes first on PATH and is the
# one Spark runs (PYSPARK_PYTHON). uv is mounted for the build only, not left in the image.
ARG PYTHON_VERSION=3.12.12
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_NO_CACHE=1
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv python install "$PYTHON_VERSION" \
 && ln -s "$(dirname "$(dirname "$(uv python find "$PYTHON_VERSION")")")" /opt/py \
 && /opt/py/bin/python3 --version
ENV PATH=/opt/py/bin:$PATH \
    PYSPARK_PYTHON=/opt/py/bin/python3 \
    PYSPARK_DRIVER_PYTHON=/opt/py/bin/python3

# pandas + pyarrow on every node: toPandas() with Arrow and pandas UDFs. python-chess replays
# move sequences into positions for the opening explorer (lichess_spark.explorer); GPL-3.0,
# used only inside this batch image, never shipped to users.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv pip install --python "$(readlink -f /opt/py/bin/python3)" --system --break-system-packages \
      "pandas>=2.2,<3" "pyarrow>=18" "chess==1.11.2"

# Our code: the session helper as an importable package (a .pth file puts /opt/lichess/python on
# the interpreter's path), jobs as plain scripts.
COPY lichess_spark /opt/lichess/python/lichess_spark
RUN echo /opt/lichess/python > "$(/opt/py/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')/lichess.pth"
COPY jobs /opt/lichess/jobs
COPY tests /opt/lichess/tests
COPY conf /opt/lichess/conf

USER spark

# --- notebook: JupyterLab whose kernel is a Spark driver (lichess_spark.notebook) ---
FROM job AS notebook
USER root
# pillow>=13 (via matplotlib): 12.x has critical decoder/encoder vulnerabilities (2026-10), and
# 13 needs Python >= 3.11, one reason the image runs 3.12.
RUN --mount=from=uv,source=/uv,target=/usr/local/bin/uv \
    uv pip install --python "$(readlink -f /opt/py/bin/python3)" --system --break-system-packages \
      "jupyterlab>=4.4,<5" ipykernel "matplotlib>=3.9" "pillow>=13" \
 && ln -s "$(ls /opt/spark/python/lib/py4j-*-src.zip | head -1)" /opt/spark/python/lib/py4j-src.zip
# pyspark from the Spark install (same version as the executors), importable by the kernel.
ENV PYTHONPATH=/opt/spark/python:/opt/spark/python/lib/py4j-src.zip
COPY notebooks /opt/lichess/notebooks
COPY bin/start-jupyter.sh /opt/lichess/bin/start-jupyter.sh
# Built from Windows (az acr build uploads the working copy), the script arrives without
# the executable bit and possibly with CRLF line endings: fix both.
RUN sed -i 's/\r$//' /opt/lichess/bin/start-jupyter.sh && chmod 0755 /opt/lichess/bin/start-jupyter.sh
USER spark
EXPOSE 8888 4040
ENTRYPOINT []
CMD ["/opt/lichess/bin/start-jupyter.sh"]

# Default target: the job image.
FROM job
