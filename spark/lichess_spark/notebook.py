"""SparkSession for notebooks.

In the cluster (the jupyter workflow, deploy/lichess/workflowtemplate-jupyter.yaml):
  Spark client mode on Kubernetes. The notebook kernel is the driver; executors start as
  pods on the spot batch nodes when a query needs them and are removed after a few idle
  minutes (dynamic allocation), so an open notebook doesn't keep E8s nodes running.
  Executors sign in to the lake with workload identity, like the jobs.

Anywhere else (your PC): local mode, using the SAS from ingest/.env.

    from lichess_spark.notebook import notebook_spark
    spark = notebook_spark()
"""
from __future__ import annotations

import os

from .session import get_spark

EXECUTOR_TEMPLATE = "/opt/lichess/conf/executor-pod-template.yaml"
EXECUTOR_SA = "lichess-jobs"


def in_cluster(env=None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("KUBERNETES_SERVICE_HOST") and env.get("LICHESS_EXECUTOR_IMAGE"))


def k8s_client_conf(env=None, max_executors: int | None = None) -> dict[str, str]:
    """Client-mode settings. Needs POD_NAME, POD_IP and LICHESS_EXECUTOR_IMAGE (set by the workflow)."""
    env = os.environ if env is None else env
    missing = [k for k in ("POD_NAME", "POD_IP", "LICHESS_EXECUTOR_IMAGE") if not env.get(k)]
    if missing:
        raise RuntimeError(f"not set: {', '.join(missing)} (expected inside the jupyter workflow pod)")
    n_max = max_executors or int(env.get("LICHESS_MAX_EXECUTORS", "4"))
    return {
        "spark.master": "k8s://https://kubernetes.default.svc:443",
        "spark.submit.deployMode": "client",
        "spark.kubernetes.namespace": env.get("POD_NAMESPACE", "lichess"),
        # Executors are owned by this pod: stopping the notebook removes them.
        "spark.kubernetes.driver.pod.name": env["POD_NAME"],
        "spark.driver.host": env["POD_IP"],
        "spark.driver.bindAddress": "0.0.0.0",
        "spark.driver.port": "7078",
        "spark.driver.blockManager.port": "7079",
        "spark.driver.memory": env.get("LICHESS_DRIVER_MEMORY", "2g"),
        "spark.kubernetes.container.image": env["LICHESS_EXECUTOR_IMAGE"],
        "spark.kubernetes.container.image.pullPolicy": "Always",
        # Spot batch nodes, workload identity label and service account (Spark has no
        # settings for tolerations, so they come from a pod template).
        "spark.kubernetes.executor.podTemplateFile": EXECUTOR_TEMPLATE,
        "spark.kubernetes.authenticate.executor.serviceAccountName": EXECUTOR_SA,
        # Same executor shape as spark-job: one per Standard_E8s_v5.
        "spark.executor.cores": "6",
        "spark.kubernetes.executor.request.cores": "5500m",
        "spark.executor.memory": "40g",
        "spark.executor.memoryOverhead": "6g",
        # Executors come and go with the work.
        "spark.dynamicAllocation.enabled": "true",
        "spark.dynamicAllocation.shuffleTracking.enabled": "true",
        "spark.dynamicAllocation.minExecutors": "0",
        "spark.dynamicAllocation.maxExecutors": str(n_max),
        "spark.dynamicAllocation.executorIdleTimeout": "300s",
        "spark.dynamicAllocation.shuffleTracking.timeout": "1800s",
        "spark.kubernetes.executor.deleteOnTermination": "true",
        # The Spark UI listens on the pod IP (Spark has no separate bind address for it):
        # read-only, so other pods can't kill jobs from it.
        "spark.ui.killEnabled": "false",
    }


def notebook_conf(env=None, max_executors: int | None = None) -> dict[str, str]:
    env = os.environ if env is None else env
    base = {
        "spark.sql.execution.arrow.pyspark.enabled": "true",  # fast toPandas()
        "spark.sql.adaptive.enabled": "true",
    }
    if in_cluster(env):
        return {**base, **k8s_client_conf(env, max_executors)}
    return {**base, "spark.master": env.get("SPARK_MASTER", "local[*]")}


def notebook_spark(app_name: str = "notebook", max_executors: int | None = None,
                   extra: dict[str, str] | None = None, env=None):
    return get_spark(app_name, {**notebook_conf(env, max_executors), **(extra or {})}, env)
