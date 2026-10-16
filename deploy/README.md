# deploy

Everything in the cluster after Terraform's starting set (`infra/gitops`) is managed by
**Argo CD** from this folder.

```text
deploy/
  apps/                 synced by the root Application ("app-of-apps")
    project-lichess.yaml  AppProject for this repo
    …                     one Application per component (Argo Workflows, workflow templates, …)
```

Merging to `main` is the deployment: Argo CD polls the repo every few minutes and
applies the difference (auto-sync with prune and self-heal).

## What lives where

| | Managed by | Why |
|---|---|---|
| Argo CD itself, root Application | Terraform (`infra/gitops`) | Something has to install the installer |
| `lichess` namespace, `lichess-jobs` service account, `lichess-config` ConfigMap | Terraform (`infra/gitops`) | They carry deployment-specific IDs (identity client ID, lake and ACR names) that stay out of this public repo |
| Everything else | Argo CD, from `deploy/` | GitOps: the repo is the source of truth |

## UIs

Private for now, reached through the AKS API (your IP + Entra sign-in):

```powershell
kubectl port-forward -n argocd svc/argocd-server 8080:443     # https://localhost:8080
```

The first login is `admin` with the password from `argocd-initial-admin-secret`.
Change it, then delete that secret. Public URLs with Entra SSO: #45.
