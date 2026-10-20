# deploy

Everything in the cluster after Terraform's starting set (`infra/gitops`) is managed by
**Argo CD** from this folder.

```text
deploy/
  apps/                       synced by the root Application ("app-of-apps")
    project-lichess.yaml        AppProject for this repo
    project-platform.yaml       AppProject that may only create network policies
    argo-workflows.yaml         Argo Workflows (Helm chart), wave 0
    spark-operator.yaml         Kubeflow Spark Operator (Helm chart), wave 0
    external-secrets.yaml       External Secrets Operator (Key Vault → Secrets), wave 0
    kyverno.yaml                Kyverno (ApplicationSet: identity from cluster facts), wave 0
    kyverno-policies.yaml       Pod Security Standards (restricted, audit), wave 1
    platform.yaml               ApplicationSet → charts/platform with cluster facts, wave 1
    lichess.yaml                the pipeline's own objects from deploy/lichess, wave 1
    cloudflared.yaml            Cloudflare Tunnel connector for chess-*.generops.dev, wave 1
    cnpg.yaml                   CloudNativePG operator (Postgres), wave 0
    lakekeeper-db.yaml          Postgres for the catalog and OpenFGA + encryption key (deploy/lakekeeper), wave 1
    lakekeeper.yaml             Lakekeeper, the Iceberg REST catalog: Kubernetes token sign-in, OpenFGA permissions, wave 2
    grafana.yaml                Grafana for chess-grafana (ApplicationSet: identity, workspace URL from facts), wave 1
    project-observability.yaml  AppProject that may only create PodMonitors/ServiceMonitors
    monitoring.yaml             PodMonitors for Azure Managed Prometheus (deploy/monitoring), wave 2
    cert-manager.yaml           cert-manager (in-cluster TLS), wave 0
    trust-manager.yaml          distributes the private CA as ConfigMap internal-ca, wave 1
    pki.yaml                    the private CA (deploy/pki), wave 2
    trino.yaml                  Trino over the lake (ApplicationSet: identity, OIDC issuer from facts), wave 3
    network-policies.yaml       default-deny ingress everywhere (deploy/network-policies), wave 2
  cloudflared/                cloudflared Deployment (routes and Access live in infra/gitops/edge.tf)
  lakekeeper/                 CloudNativePG clusters for the catalog and OpenFGA, generated encryption key
  charts/platform/            ClusterSecretStore, image signature policy (values from cluster facts)
  monitoring/                 PodMonitors (Argo Workflows, Argo CD, cloudflared)
  pki/                        private CA: ClusterIssuers, root CA, trust-manager Bundle
  network-policies/           one CiliumNetworkPolicy per namespace (+ scrape and cloudflared allows)
  lichess/                    WorkflowTemplates / CronWorkflows (namespace lichess)
    workflowtemplate-lake-smoke.yaml    end-to-end check: identity → lake
    workflowtemplate-ingest-month.yaml  ingest months on a spot node, publish into lake.raw.games, build curated + gold
    workflowtemplate-ingest-backfill.yaml  fan out ingest-month over a range, 4 at a time
    workflowtemplate-spark-job.yaml     run a PySpark job (spark/jobs) as a SparkApplication
    cronworkflow-lake-maintenance.yaml  weekly Iceberg maintenance on every lake table (Sundays 03:00 UTC)
    workflowtemplate-monthly.yaml       the monthly pipeline: new Lichess months → raw → curated → gold → maintenance
    cronworkflow-lichess-monthly.yaml   runs `monthly` daily (06:00 UTC); a no-op until Lichess publishes a new month
    workflowtemplate-jupyter.yaml       JupyterLab on demand; the kernel drives Spark executors on spot
    workflowtemplate-lakekeeper-bootstrap.yaml  catalog setup: bootstrap, warehouse "lichess", read-only grants
    workflowtemplate-trino-smoke.yaml   Trino end to end: TLS, JWT sign-in, catalog, lake reads
    jupyter.yaml                        its disk (PVC jupyter-work) and port-forward Service
    rbac-spark-submit.yaml              lets workflows create SparkApplications
  examples/                   not synced; submit by hand
    run-lake-smoke.yaml
    run-ingest-month.yaml
    run-ingest-backfill.yaml
    run-spark-smoke.yaml
    run-jupyter.yaml
    netpol-check.yaml                   proves lichess is closed to other namespaces
    run-lakekeeper-bootstrap.yaml
    run-catalog-smoke.yaml              Spark creates/writes/reads an Iceberg table through Lakekeeper
    run-register-raw-games.yaml         publish landed months into lake.raw.games (write-audit-publish)
    run-curated-games.yaml              build lake.curated.games from raw (write-audit-publish)
    run-player-games.yaml               build lake.curated.player_games from curated.games
    run-gold.yaml                       build the gold tables from curated
    run-maintain-tables.yaml            run the Iceberg maintenance now (first run: orphans dry run)
    run-monthly.yaml                    run the monthly pipeline now
    run-trino-smoke.yaml
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

### Images

Workflows run images from the cluster's ACR: `lichess-ingest`, `lichess-spark`,
`lichess-notebook`, tag `<image-tag>` (default `main`). CI builds them on every merge to
main (`.github/workflows/images.yml`): tests inside the image, Trivy scan, push as
`:<commit sha>` + `:main` with SBOM and provenance, cosign keyless signature. Pass a
commit SHA as `image-tag` to pin a run to an exact build.

Manual fallback (ACR Tasks, builds in Azure), from the repo root:

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
