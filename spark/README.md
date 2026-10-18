# spark

PySpark jobs for the Lichess lake, and the image they run in.

| | |
|---|---|
| Spark | 4.1.3 (Scala 2.13, Java 21), image `apache/spark:4.1.3-scala2.13-java21-python3-ubuntu` |
| Iceberg | 1.12.0: `iceberg-spark-runtime-4.1_2.13`, `iceberg-azure-bundle` |
| ADLS | `hadoop-azure` 3.4.2 (matches Spark's Hadoop), workload identity via `WorkloadIdentityTokenProvider` |

```text
spark/
  Dockerfile          base image + Iceberg/Azure jars + our code
  lichess_spark/      get_spark(): SparkSession configured from the environment (lake, credentials, spot)
  jobs/               one PySpark script per job (mainApplicationFile local:///opt/lichess/jobs/<job>.py)
  tests/              unit tests (pytest) + local_smoke.py (Spark local mode, run in CI)
```

## Running jobs on AKS

Jobs run as SparkApplications through the `spark-job` WorkflowTemplate
(`deploy/lichess/`): the driver on the system node, executors on spot batch nodes,
both signed in to the lake with workload identity. No secrets anywhere.

```powershell
kubectl create -n lichess -f deploy/examples/run-spark-smoke.yaml
kubectl get sparkapplications -n lichess
```

## Building the image

Until CI pushes images (#20), build in Azure with ACR Tasks, from the repo root:

```powershell
$acr = terraform -chdir=infra/compute output -raw acr_login_server
$sha = git rev-parse --short HEAD
az acr build -r $acr.Split('.')[0] -t "lichess-spark:main" -t "lichess-spark:$sha" spark
```

## Running locally

Same code on your PC against the lake, with the SAS from `ingest/.env`
(`AZURE_STORAGE_SAS_TOKEN`) and `LICHESS_DEST`, inside the image:

```powershell
docker build -t lichess-spark:local spark
docker run --rm -it --env-file ingest/.env --entrypoint /opt/spark/bin/spark-submit lichess-spark:local `
  --master 'local[*]' --driver-memory 32g /opt/lichess/jobs/smoke_raw_counts.py
```

## Adding a job

1. Add `jobs/<name>.py`. Build the session with `lichess_spark.get_spark("<name>")` and paths from `dest_root()`.
2. Rebuild the image, then submit `spark-job` with `job=<name>` and `args` as a JSON list.
