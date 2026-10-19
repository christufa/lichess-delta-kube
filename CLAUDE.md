# CLAUDE.md: working on lichess-delta-kube

A self-run Azure data platform for the full Lichess game history: monthly PGN dumps →
ADLS Gen2 → Iceberg tables (Lakekeeper REST catalog) → Spark on AKS / Trino → a public
dashboard, an MCP server and a chat on `chess-*.generops.dev`. It's a portfolio project
("peak modern practices") and will be torn down at the end (#48).

## Rules that matter

- **The repo is public.** Never commit deployment-specific values: subscription/tenant IDs,
  storage/ACR/Key Vault names, IPs, emails, client IDs. They live in gitignored
  `*.tfvars` / `infra/state.tfbackend`, or reach the cluster as **cluster facts**
  (annotations on Argo CD's in-cluster cluster Secret, written by `infra/gitops/platform.tf`),
  consumed by ApplicationSets with the cluster generator. Never paste `terraform plan`
  output into issues/PRs. Run gitleaks on staged changes before committing.
- **IP firewall rules are set by hand** in Azure (lake, state account, AKS, Key Vault).
  Terraform ignores `ip_rules`; no IP scripts.
- **The owner works on Windows / PowerShell**: give PowerShell commands (quote
  `"-backend-config=../state.tfbackend"`; avoid `"\n"` inside jsonpath). Builds happen in
  CI, not on the PC.
- **Every change is a PR** to `main` (branch protection). Commits/PRs end with the
  attribution trailer. Merging is deployment: Argo CD syncs `deploy/`; Terraform is
  applied by the owner (`terraform -chdir=infra/<stack> plan -out main.tfplan` then
  `apply main.tfplan`). PR descriptions list the exact apply steps and how to verify.
- **Secrets**: human-provided values → Key Vault → External Secrets (ClusterSecretStore
  `azure-key-vault`). Generated in-cluster values → ESO Password generator or
  CloudNativePG app secrets. Azure access → workload identity (one managed identity per
  component, least privilege), never keys.

## Layout

| Path | What |
|---|---|
| `infra/bootstrap` | state account, project budget (local state) |
| `infra/platform` | lake (ADLS Gen2, HNS), VNet/subnet, lifecycle rule for landed Parquet |
| `infra/compute` | ACR, AKS (system pool autoscaling 1–3, spot `batch` pool 0–4), Key Vault, identities (jobs, CI, ESO, Kyverno, Grafana, Lakekeeper, Trino), Managed Prometheus + alerts |
| `infra/gitops` | Argo CD (+ Entra SSO via workload identity), cluster facts, namespaces/SAs, Cloudflare Tunnel + Access + DNS, Entra apps (needs `CLOUDFLARE_API_TOKEN`) |
| `deploy/apps` | Argo CD apps (root app-of-apps); ApplicationSets where values come from facts |
| `deploy/lichess` | WorkflowTemplates: ingest-month (land + publish), ingest-backfill, spark-job, jupyter, lakekeeper-bootstrap, trino-smoke |
| `deploy/examples` | `kubectl create -n lichess -f …` runs (not synced) |
| `deploy/charts/platform` | ClusterSecretStore, Kyverno image signature policy |
| `deploy/network-policies` | Cilium default-deny ingress per namespace + explicit allows |
| `ingest/` | lichess-ingest (torrent/HTTPS download, parse to Parquet, claims, manifests) |
| `spark/` | Spark 4.1 + Iceberg 1.12 image, `lichess_spark` (session, catalog `lake`, notebooks, raw WAP), jobs, notebook image |

## Current state (2026-10-19)

Live: GitOps (Argo CD, Workflows), signed CI images (GitHub OIDC, SBOM, Trivy, cosign,
Kyverno digest verification, Renovate), Key Vault + ESO, Kyverno PSS (audit),
Cilium network policies, Cloudflare Tunnel + Access (`chess-cd`, `chess-flows`,
`chess-lab`, `chess-grafana`), Managed Prometheus + Grafana + email alerts, Lakekeeper
(K8s-token auth, OpenFGA; `lichess-jobs` is instance admin), `lake.raw.games` with
write-audit-publish (`lake.ops.dq_results`), backfill pipeline ready (not run yet),
Trino (#64: JWT sign-in, private-CA TLS via trust-manager, read-only; `trino-smoke` passes).

In the lake: 2013-01 → 2015-12 and 2026-08/09 (38 months). 2016 was ingested on the PC
but isn't in `lake.raw.games`; check whether it was ever published.

Next, in order (issues in milestones Phase 4–7):
1. Curated tables `curated.games`, `curated.player_games` (#25, #26) with WAP checks (#27).
2. Gold tables + opening explorer (#65); Iceberg maintenance (#63).
3. Data API (FastAPI, #66), public dashboard (React+TS+Vite, #67) on `chess.generops.dev`.
4. MCP server (#68, Entra OAuth, app role `Lakehouse.Query`), model-agnostic chat (#69,
   Pydantic AI, app role `Chat.User`, evals).
5. Open loose ends: Kyverno audit → enforce (#62), cost export to the lake and traces
   (#31), CNPG backups to the lake, Trino worker autoscaling (KEDA), monthly CronWorkflow (#30),
   backfill (#24), teardown (#48).

## Conventions

- Pin versions (charts, images, actions by SHA); check the current release before adding
  one. Renovate keeps them fresh.
- New namespace → add it to the right AppProject (`lichess`, `platform-security`,
  `observability`) and give it a default-deny CiliumNetworkPolicy with explicit allows
  (webhook namespaces also allow the API server entities + kube-system).
- New Azure identity → `infra/compute` (identity, role, federated credential) → output →
  fact in `infra/gitops/platform.tf` → ApplicationSet reads it.
- CI must stay green: `.github/workflows/{ci,images}.yml` (unit tests, image smoke tests incl.
  local Spark/Iceberg runs, Trivy, `helm template` + kubeconform for `deploy/`).
