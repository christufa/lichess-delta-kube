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
  CI, not on the PC. The PC's Node is older than `web/` needs: run its unit tests with
  `docker build --target test web` (Playwright works locally).
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
| `infra/compute` | ACR, AKS (system pool autoscaling 1–3, spot `batch` pool 0–4), Key Vault, identities (jobs, CI, ESO, Kyverno, Grafana, Lakekeeper, Trino, chat), Managed Prometheus + alerts |
| `infra/gitops` | Argo CD (+ Entra SSO via workload identity), cluster facts, namespaces/SAs, Cloudflare Tunnel + Access + DNS, Entra apps (Argo CD, MCP, Chat, Claude API audience) (needs `CLOUDFLARE_API_TOKEN`) |
| `deploy/apps` | Argo CD apps (root app-of-apps); ApplicationSets where values come from facts |
| `deploy/lichess` | WorkflowTemplates: ingest-month (land → raw → curated → gold), ingest-backfill, monthly, spark-job, jupyter, lakekeeper-bootstrap, trino-smoke; CronWorkflows lichess-monthly (daily), lake-maintenance (weekly) |
| `deploy/examples` | `kubectl create -n lichess -f …` runs (not synced) |
| `deploy/charts/platform` | ClusterSecretStore, Kyverno image signature policy |
| `deploy/network-policies` | Cilium default-deny ingress per namespace + explicit allows |
| `ingest/` | lichess-ingest (torrent/HTTPS download, parse to Parquet, claims, manifests) |
| `spark/` | Spark 4.1 + Iceberg 1.12 image, `lichess_spark` (session, catalog `lake`, `wap` write-audit-publish + lineage, `raw`, `curated`, `player_games`, `gold`, `maintenance`), jobs, notebook image |
| `api/` | chess-api: read-only FastAPI over `lake.gold` via Trino; `openapi.json` is the contract |
| `web/` | chess-web: the public dashboard (React 19, Vite, TanStack Router/Query, Tailwind v4 + shadcn/ui, ECharts), client generated from `api/openapi.json`, the chat panel (MSAL redirect sign-in, AI SDK 7), nginx image |
| `mcp-server/` | chess-mcp: the lakehouse MCP server (Python MCP SDK, streamable HTTP, Entra OAuth, sqlglot read-only guard, audit log) at `chess-mcp.<domain>/mcp` |
| `chat/` | chess-chat: the chat backend (Pydantic AI, Claude by Anthropic Workload Identity Federation, MCP tools only, $5/day spend cap in SQLite) at `/api/v1/chat` |
| `docs/adr` | decisions: 0001 bucketing (player_games `bucket(16, player_id)`), 0002 data architecture, 0003 storage layout, 0004 implementation stack, 0005 Spark version |

## Current state (2026-10-21)

Live:
- **Platform:**
  - GitOps: Argo CD and Argo Workflows.
  - Signed CI images: GitHub OIDC, SBOM, Trivy, cosign, Renovate. Kyverno verifies digests and denies unsigned `lichess-*` images.
  - Secrets: Key Vault + ESO.
  - Pod security: Pod Security Admission `restricted` on the app namespaces, and Kyverno PSS in audit mode.
  - Cilium network policies.
  - Cloudflare Tunnel + Access (`chess-cd`, `chess-flows`, `chess-lab`, `chess-grafana`).
  - Monitoring: Managed Prometheus, Grafana and email alerts (incl. WorkflowFailed).
  - Azure cost exported daily to `lake.ops.azure_cost`.
- **Query side:**
  - Lakekeeper: K8s-token auth, OpenFGA.
  - Trino: JWT sign-in, private-CA TLS via trust-manager, read-only rules.
- **Public on `chess.generops.dev`:**
  - The dashboard (`web/`, #67): Overview and Openings.
  - The data API at `/api/v1` (#66).
- **Behind an Entra app role** (only the owner + `mcp_extra_users`):
  - The MCP server at `chess-mcp.<domain>/mcp` (#68), for Claude's connector, VS Code and similar clients.
  - The chat panel on the dashboard (#69), backed by `chess-chat`, which reads the lake only through the MCP server.

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

The chat's sign-ins, all without secrets (`chat/README.md`):
- **Browser to chat:** MSAL, PKCE, scope `Chat.Access` + role `Chat.User`.
- **Chat to MCP:** the managed identity, app-only, role `Lakehouse.Query`. The MCP app requests the `idtyp` optional claim to tell app-only tokens from user tokens.
- **Chat to Claude:** Anthropic Workload Identity Federation (Entra token for the "Claude API audience" app). Set up in the Claude Console:
  - the issuer's max JWT lifetime is 25 h, because managed identity tokens last 24 h + 5 min;
  - the exchange needs the workspace ID;
  - rejected exchanges show their reason only in the Console's Authentication history.

Next, all by choice, none urgent:
- Chat evals: a golden question set graded against the gold tables, run on demand or nightly.
- Per-user attribution in the MCP audit for chat queries; it currently records the chat's identity.
- The opening explorer `gold.position_moves` (#65) and per-position data (#29).
- Backfill (#24) and teardown (#48).
- Trino worker autoscaling (KEDA).
- Catalog recovery: the two CNPG Postgres clusters (Lakekeeper catalog, OpenFGA) have no backups, by
  decision. Tables come back from the lake instead: a weekly inventory + drill and a restore job
  (`spark/jobs/catalog_recovery.py`, `docs/runbooks/catalog-recovery.md`, #125).

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
