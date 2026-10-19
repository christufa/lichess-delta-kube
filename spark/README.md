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

Open **https://chess-lab.generops.dev** (Microsoft sign-in through Cloudflare Access) or, with
the port-forward, http://localhost:8888, and start with `examples/getting-started.ipynb`.
There is no token: JupyterLab listens on 127.0.0.1 inside its pod, and the only ways in are the
port-forward (your Entra sign-in + RBAC) and a cloudflared sidecar whose tunnel only accepts
requests that passed Cloudflare Access. chess-lab is only up while a notebook runs.
The Spark UI is at http://localhost:4040 while a session is running.

```python
from lichess_spark import dest_root, notebook_spark
spark = notebook_spark()            # max_executors=2 to stay smaller
games = spark.read.option("recursiveFileLookup", "true").parquet(f"{dest_root()}/raw/games")
spark.sql("SHOW NAMESPACES IN lake").show()   # Iceberg tables through Lakekeeper
```

In the cluster every session also has the Iceberg catalog **`lake`** (Lakekeeper, REST,
default catalog): `spark.table("lake.ops.catalog_smoke")`, `CREATE TABLE lake.raw.… USING iceberg`.
Lakekeeper vends a SAS token scoped to each table; set `LICHESS_CATALOG_URI` to use it elsewhere.

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

## raw.games (write-audit-publish)

`jobs/register_raw_games.py` turns landed Parquet (`raw/games/month=…`, from lichess-ingest)
into the Iceberg table **`lake.raw.games`**, one month at a time:

1. write the month into a private branch (`audit_YYYY_MM`), replacing that month there;
2. audit the branch: row count = the month's manifest, `Site` present, `Result` valid,
   `month` column consistent;
3. all pass: fast-forward `main` to the branch. Any failure: `main` is untouched.

Readers never see an unchecked month; every check is stored in `lake.ops.dq_results`.
`--pending` publishes every landed month that isn't in the table yet; re-publishing a month
replaces it. The branch-and-publish steps live in `lichess_spark/wap.py`, shared with curated.

## curated.games

`jobs/build_curated_games.py` builds **`lake.curated.games`** from `lake.raw.games`, a month
at a time, with the same write-audit-publish. **Nothing is filtered**: every raw row becomes
one typed row (`game_id`, `played_at` in UTC, ratings as integers, base/increment and speed
class, normalized `result`, titles and `*_is_bot`, tournament/swiss id, lineage). What's
unusual is kept and counted, not dropped:

| Check | Blocks publishing? |
|---|---|
| curated rows = raw rows; `game_id` present; time control parseable; `played_at` present; month consistent; not empty | yes |
| `game_id` unique (extra copies get `is_duplicate`); ratings in 400–4000; result decided (`*` → null); termination known; `played_at` in the month | no, counted in `dq_results` |

Each row records the raw snapshot it came from (`_raw_snapshot_id`); `--pending` builds raw
months that aren't in curated yet or were republished in raw since.

## curated.player_games and gold

`jobs/build_player_games.py` turns each curated game into **two rows**, one from each
player's side (`player`, lower-cased `player_id`, `color`, opponent, ratings, `outcome`
win/draw/loss, `score`). Like `games`, nothing is filtered. Layout follows
[ADR 0001](../docs/adr/0001-bucketing.md): partitioned by `month` and
`bucket(16, player_id)`, sorted by `player_id, played_at`. Blocking checks: exactly two rows
per game, one white and one black, player present.

`jobs/build_gold.py` builds the dashboard's small tables, counting each game once
(`NOT is_duplicate`):

| Table | Grain | Measures |
|---|---|---|
| `lake.gold.monthly_summary` | month × speed (`all` = every speed) | games, players, white wins/draws/black wins/unfinished, bot games, average rating |
| `lake.gold.openings` | month × speed × ECO/opening × rating band (200) | games and results |
| `lake.gold.rating_distribution` | month × speed × rating bucket (50) | player-games, players |

Each gold table's totals must reconcile with curated (games = unique curated games,
player-games = twice that) before it publishes.

Each layer records the source snapshot it was built from, so `--pending` cascades:
raw → `curated.games` → `curated.player_games` → gold. `ingest-month` (and so
`ingest-backfill`) runs all three builds after publishing raw.

## Adding a job

1. Add `jobs/<name>.py`. Build the session with `lichess_spark.get_spark("<name>")` and paths from `dest_root()`.
2. Rebuild the image, then submit `spark-job` with `job=<name>` and `args` as a JSON list.
