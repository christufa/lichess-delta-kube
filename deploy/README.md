# deploy

Everything in the cluster after Terraform's starting set (`infra/gitops`) is managed by
**Argo CD** from this folder.

```text
deploy/
  apps/                       synced by the root Application ("app-of-apps")
    project-lichess.yaml        AppProject for this repo
    argo-workflows.yaml         Argo Workflows (Helm chart), wave 0
    lichess.yaml                the pipeline's own objects from deploy/lichess, wave 1
  lichess/                    WorkflowTemplates / CronWorkflows (namespace lichess)
    workflowtemplate-lake-smoke.yaml
  examples/                   not synced; submit by hand
    run-lake-smoke.yaml
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
kubectl get workflows -n lichess
```

The first login is `admin` with the password from `argocd-initial-admin-secret`.
Change it, then delete that secret. Public URLs with Entra SSO: #45.
