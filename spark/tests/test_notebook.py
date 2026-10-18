import pytest

from lichess_spark.notebook import EXECUTOR_TEMPLATE, in_cluster, k8s_client_conf, notebook_conf

POD = {
    "KUBERNETES_SERVICE_HOST": "10.0.0.1",
    "LICHESS_EXECUTOR_IMAGE": "acr.example/lichess-spark:main",
    "POD_NAME": "jupyter-abc-jupyter-123",
    "POD_IP": "10.244.0.7",
    "POD_NAMESPACE": "lichess",
}


def test_local_outside_cluster():
    conf = notebook_conf({})
    assert conf["spark.master"] == "local[*]"
    assert not any(k.startswith("spark.kubernetes") for k in conf)


def test_local_master_override():
    assert notebook_conf({"SPARK_MASTER": "local[2]"})["spark.master"] == "local[2]"


def test_needs_executor_image_to_count_as_cluster():
    assert not in_cluster({"KUBERNETES_SERVICE_HOST": "10.0.0.1"})  # e.g. a job pod
    assert in_cluster(POD)


def test_client_mode_in_cluster():
    conf = notebook_conf(POD)
    assert conf["spark.master"].startswith("k8s://")
    assert conf["spark.submit.deployMode"] == "client"
    assert conf["spark.driver.host"] == "10.244.0.7"
    assert conf["spark.kubernetes.driver.pod.name"] == "jupyter-abc-jupyter-123"
    assert conf["spark.kubernetes.container.image"] == "acr.example/lichess-spark:main"
    assert conf["spark.kubernetes.executor.podTemplateFile"] == EXECUTOR_TEMPLATE
    assert conf["spark.dynamicAllocation.minExecutors"] == "0"
    assert conf["spark.dynamicAllocation.maxExecutors"] == "4"
    assert conf["spark.ui.killEnabled"] == "false"


def test_max_executors():
    assert k8s_client_conf({**POD, "LICHESS_MAX_EXECUTORS": "2"})["spark.dynamicAllocation.maxExecutors"] == "2"
    assert k8s_client_conf(POD, max_executors=1)["spark.dynamicAllocation.maxExecutors"] == "1"


def test_missing_pod_env():
    with pytest.raises(RuntimeError, match="POD_IP"):
        k8s_client_conf({k: v for k, v in POD.items() if k != "POD_IP"})
