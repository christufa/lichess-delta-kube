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
| `deploy/lichess` | WorkflowTemplates: ingest-month (land → raw → curated → gold), ingest-backfill, monthly, spark-job, jupyter, lakekeeper-bootstrap, trino-smoke; CronWorkflows lichess-monthly (daily), lake-maintenance (weekly) |
| `deploy/examples` | `kubectl create -n lichess -f …` runs (not synced) |
| `deploy/charts/platform` | ClusterSecretStore, Kyverno image signature policy |
| `deploy/network-policies` | Cilium default-deny ingress per namespace + explicit allows |
| `ingest/` | lichess-ingest (torrent/HTTPS download, parse to Parquet, claims, manifests) |
| `spark/` | Spark 4.1 + Iceberg 1.12 image, `lichess_spark` (session, catalog `lake`, `wap` write-audit-publish + lineage, `raw`, `curated`, `player_games`, `gold`, `maintenance`), jobs, notebook image |
| `docs/adr` | decisions; 0001 bucketing (player_games: `bucket(16, player_id)`) |

## Current state (2026-10-20)

Live: GitOps (Argo CD, Workflows), signed CI images (GitHub OIDC, SBOM, Trivy, cosign,
Kyverno digest verification, Renovate), Key Vault + ESO, Kyverno PSS (audit),
Cilium network policies, Cloudflare Tunnel + Access (`chess-cd`, `chess-flows`,
`chess-lab`, `chess-grafana`), Managed Prometheus + Grafana + email alerts (incl.
WorkflowFailed), Lakekeeper (K8s-token auth, OpenFGA), Trino (#64: JWT sign-in, private-CA
TLS via trust-manager, read-only rules; `trino-smoke` passes).

The lake (catalog `lake`; every layer built a month at a time with write-audit-publish,
checks in `lake.ops.dq_results`):
- `raw.games` → `curated.games` (typed, nothing filtered, `is_duplicate`) →
  `curated.player_games` (2 rows/game, `bucket(16, player_id)`) → `gold.monthly_summary`,
  `gold.openings`, `gold.rating_distribution` (each game once).
- Lineage by run id: each derived row has `_source_run_id` (the source month's `_run_id`);
  `--pending` rebuilds a month only when its source was rebuilt, never after compaction.
- 38 months: 2013-01 → 2015-12 and 2026-08/09. 2016 and the rest were never ingested; the
  full backfill (#24) is deliberately deferred.
- Daily `lichess-monthly` brings each new Lichess month (from 2026-08 on) through every
  layer; weekly `lake-maintenance` compacts, expires snapshots, removes orphans.
- Known data quirk: in 2013–2015 a few hundred to ~8.7k games per month start in the last
  1–2 hours of the previous month (old dumps cut at European midnight, not UTC); counted
  by the non-blocking `played_at_in_month` check, not dropped.

Next (issues in milestones Phase 4–7):
1. Data API (FastAPI, #66; in progress), then the public dashboard (React+TS+Vite, #67)
   on `chess.generops.dev`.
2. Kyverno audit → enforce (#62), cost export to the lake (#31), ADRs for decisions already
   made (#5, #10, #15, #7).
3. Later, by choice: opening explorer `gold.position_moves` (#65), per-position data (#29),
   MCP server (#68) and chat (#69), backfill (#24), teardown (#48). Also open: CNPG backups
   to the lake, Trino worker autoscaling (KEDA).

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
