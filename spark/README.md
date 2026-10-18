# spark

PySpark jobs for the Lichess lake, and the image they run in.

| | |
|---|---|
| Spark | 4.1.3 (Scala 2.13, Java 21), image `apache/spark:4.1.3-scala2.13-java21-python3-ubuntu` |
| Iceberg | 1.12.0: `iceberg-spark-runtime-4.1_2.13`, `iceberg-azure-bundle` |
| ADLS | `hadoop-azure` 3.4.2 (matches Spark's Hadoop), workload identity via `WorkloadIdentityTokenProvider` |

```text
spark/
  Dockerfile          lichess-spark (jobs, executors); --target notebook: lichess-notebook (+ JupyterLab)
  lichess_spark/      get_spark(): SparkSession configured from the environment (lake, credentials, spot)
                      notebook_spark(): the same for notebooks (client mode in the cluster, local on a PC)
  jobs/               one PySpark script per job (mainApplicationFile local:///opt/lichess/jobs/<job>.py)
  notebooks/          starter notebooks, copied into JupyterLab (commit them without outputs)
  conf/               executor pod template for notebook sessions (spot nodes, workload identity)
  bin/                start-jupyter.sh
  tests/              unit tests (pytest) + local_smoke.py / local_notebook.py (Spark local mode, run in CI)
```

## Running jobs on AKS

Jobs run as SparkApplications through the `spark-job` WorkflowTemplate
(`deploy/lichess/`): the driver on the system node, executors on spot batch nodes,
both signed in to the lake with workload identity. No secrets anywhere.

```powershell
kubectl create -n lichess -f deploy/examples/run-spark-smoke.yaml
kubectl get sparkapplications -n lichess
```

## Notebooks (JupyterLab on AKS)

The `jupyter` workflow runs JupyterLab on the system node. Its kernel is a Spark driver in
client mode: executors start on the spot batch nodes when a query needs them (the first
waits ~3–5 min for a node) and are removed after 5 idle minutes, so an open notebook
costs only the system node you already pay for.

```powershell
kubectl create -n lichess -f deploy/examples/run-jupyter.yaml
kubectl port-forward -n lichess svc/jupyter 8888 4040
```

Open http://localhost:8888 and start with `examples/getting-started.ipynb`. There is no token:
JupyterLab listens on 127.0.0.1 inside its pod, so the only way in is a port-forward, which
needs your Entra sign-in and RBAC on the cluster.
The Spark UI is at http://localhost:4040 while a session is running.

```python
from lichess_spark import dest_root, notebook_spark
spark = notebook_spark()            # max_executors=2 to stay smaller
games = spark.read.option("recursiveFileLookup", "true").parquet(f"{dest_root()}/raw/games")
```

- **VS Code:** open a notebook from the repo, then *Select Kernel → Existing Jupyter Server →*
  `http://localhost:8888`. The file stays in your repo and the kernel runs in the cluster.
- **Stopping:** JupyterLab stops itself after `hours` (default 8). To stop sooner:
  `kubectl delete workflow -n lichess -l workflows.argoproj.io/workflow-template=jupyter`.
  Its executors go with it. Only one runs at a time.
- **Files:** the JupyterLab home is a 16 GiB disk (`jupyter-work`) that survives restarts but not
  `terraform destroy`. Commit notebooks you want to keep, without outputs (the repo is public):
  `pip install nbstripout; nbstripout --install` once, and CI checks it.

## Building the images

CI builds, tests, scans, signs and pushes `lichess-spark` and `lichess-notebook` on every
merge to main (`.github/workflows/images.yml`), tagged `:<commit sha>` and `:main`.
Workflows default to `image-tag=main`; pass a commit SHA to pin a run to an exact build.

Manual fallback (ACR Tasks, from the repo root):

```powershell
$acr = (terraform -chdir=infra/compute output -raw acr_login_server).Split('.')[0]
$sha = git rev-parse --short HEAD
az acr build -r $acr -t "lichess-spark:main" -t "lichess-spark:$sha" spark
az acr build -r $acr --target notebook -t "lichess-notebook:main" -t "lichess-notebook:$sha" spark
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
