"""Notebook session in local mode, run with plain python3 in the notebook image (CI):
pyspark importable from the Spark install, lichess_spark.notebook_spark, Arrow toPandas.
"""
import os
import tempfile

os.environ["LICHESS_DEST"] = "file://" + tempfile.mkdtemp()
from lichess_spark import notebook_spark  # noqa: E402

spark = notebook_spark("ci-notebook", extra={"spark.master": "local[2]"})
assert spark.range(1000).count() == 1000
pdf = spark.range(10).selectExpr("id", "id * 2 AS double").toPandas()
assert len(pdf) == 10 and int(pdf["double"].sum()) == 90
print("notebook session ok: Spark", spark.version)
spark.stop()
