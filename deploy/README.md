# deploy

Everything in the cluster after Terraform's starting set (`infra/gitops`) is managed by
**Argo CD** from this folder.

```text
deploy/
  apps/                       synced by the root Application ("app-of-apps")
    project-lichess.yaml        AppProject for this repo
    argo-workflows.yaml         Argo Workflows (Helm chart), wave 0
    spark-operator.yaml         Kubeflow Spark Operator (Helm chart), wave 0
    lichess.yaml                the pipeline's own objects from deploy/lichess, wave 1
  lichess/                    WorkflowTemplates / CronWorkflows (namespace lichess)
    workflowtemplate-lake-smoke.yaml    end-to-end check: identity → lake
    workflowtemplate-ingest-month.yaml  ingest months on a spot node
    workflowtemplate-ingest-backfill.yaml  fan out ingest-month over a range, 4 at a time
    workflowtemplate-spark-job.yaml     run a PySpark job (spark/jobs) as a SparkApplication
    workflowtemplate-jupyter.yaml       JupyterLab on demand; the kernel drives Spark executors on spot
    jupyter.yaml                        its disk (PVC jupyter-work) and port-forward Service
    rbac-spark-submit.yaml              lets workflows create SparkApplications
  examples/                   not synced; submit by hand
    run-lake-smoke.yaml
    run-ingest-month.yaml
    run-ingest-backfill.yaml
    run-spark-smoke.yaml
    run-jupyter.yaml
```

Merging to `main` is the deployment: Argo CD polls the repo every few minutes and
applies the difference (auto-sync with prune and self-heal).

## What lives where

| | Managed by | Why |
|---|---|---|
| Argo CD itself, root Application | Terraform (`infra/gitops`) | Something has to install the installer |
| `lichess` namespace, `lichess-jobs` service account, `lichess-config` and `artifact-repositories` ConfigMaps | Terraform (`infra/gitops`) | They carry deployment-specific IDs (identity client ID, lake and ACR names) that stay out of this public repo |
| Everything else | Argo CD, from `deploy/` | GitOps: the repo is the source of truth |

## UIs

Private for now, reached through the AKS API (your IP + Entra sign-in):

```powershell
kubectl port-forward -n argocd svc/argocd-server 8080:443               # Argo CD        https://localhost:8080
kubectl port-forward -n argo   svc/argo-workflows-server 2746:2746      # Argo Workflows http://localhost:2746
```

## Workflows

Every workflow in `lichess` runs as `lichess-jobs` on the spot batch pool, with
workload identity (no secrets); see `workflowDefaults` in `apps/argo-workflows.yaml`.
Logs and outputs are archived to the lake under `argo/YYYY/MM/<workflow>/`.

```powershell
kubectl create -n lichess -f deploy/examples/run-lake-smoke.yaml      # end-to-end check
kubectl create -n lichess -f deploy/examples/run-ingest-month.yaml    # ingest 2026-09
kubectl create -n lichess -f deploy/examples/run-ingest-backfill.yaml # 2017–2026, 4 months at a time
kubectl get workflows -n lichess
```

### The ingest image

`ingest-month` runs `lichess-ingest:<image-tag>` (default `main`) from the cluster's
ACR. Until CI does it (#20), build and push it with ACR Tasks (builds in Azure, no
local Docker push), from the repo root:

```powershell
$acr = terraform -chdir=infra/compute output -raw acr_login_server
$sha = git rev-parse --short HEAD
az acr build -r $acr.Split('.')[0] --target runtime -t "lichess-ingest:main" -t "lichess-ingest:$sha" ingest
```

Each month's run record (timings, download speed, row counts) is stored in the lake
under `bootstrap/_state/runs/<run_id>/<month>.json`.

### Downloads and parallel runners

- **Torrents:** the image ships `aria2c`, so months are downloaded from Lichess's
  per-month `.torrent` (many peers, and what Lichess asks bulk downloaders to use).
  A swarm that makes no progress for 5 minutes falls back to HTTPS. Either way the
  file is checked against Lichess's sha256 before parsing.
- **Claims:** before a month starts, the runner creates
  `bootstrap/_state/claims/raw_games/<month>.json` (atomic create-if-absent). Other
  runners skip claimed months, so the PC, `ingest-month` and `ingest-backfill` can run
  at the same time. Claims are released when the month finishes; a crashed runner's
  claim expires after 8 h (`--claim-ttl-hours`).

The first login is `admin` with the password from `argocd-initial-admin-secret`.
Change it, then delete that secret. Public URLs with Entra SSO: #45.
