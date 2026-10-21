# lichess-delta-kube

A self-run data platform for the [Lichess open database](https://database.lichess.org/): every
month of rated standard games is downloaded, checked, and built into Apache Iceberg tables on
Azure, then served through a public API and dashboard. Everything runs on Azure Kubernetes
Service, deployed from this repository with GitOps.

**Live:** **https://chess.generops.dev** (dashboard) · **https://chess.generops.dev/api/docs** (API)

> **About access.** Only the *public* endpoints below are open to everyone. The *private* ones
> (Argo CD, Argo Workflows, Grafana, JupyterLab, and the MCP server and chat once they ship)
> sit behind a Microsoft Entra ID sign-in and let in **only accounts the owner has assigned**.
> If you're not one of them you'll see a Microsoft or Cloudflare sign-in page and then be
> refused: that's expected, not an outage. The *internal* services exist only inside the
> cluster and can't be reached from the internet at all.

## What's in the lake

38 months so far (2013-01 → 2015-12 and 2026-08/09; the full history, 8.2 billion games, is a
planned backfill). Each layer is built a month at a time from the one before and published only
if its checks pass ([ADR 0002](docs/adr/0002-data-architecture.md)).

| Table | One row per | Rows now |
|---|---|---|
| `raw.games` | game: every PGN header tag as text, plus the moves | 226,572,280 |
| `curated.games` | game, typed: id, UTC start time, ratings, speed, result, opening, … (nothing filtered; repeated ids flagged) | 226,572,280 |
| `curated.player_games` | player in a game (two per game): rating, opponent, outcome | 453,144,560 |
| `gold.monthly_summary` | month × speed: games, players, results, bot games, average rating | 230 |
| `gold.openings` | month × speed × opening × 200-point rating band: games and results | 1.36 M |
| `gold.rating_distribution` | month × speed × 50-point rating bucket: player-games and players | 6,971 |
| `ops.dq_results` | data-quality check of any build | grows |
| `ops.azure_cost` | day × resource group × service: Azure cost of this project | grows |

## Endpoints

### Public: no sign-in

**Dashboard**: https://chess.generops.dev, a static React app; all its data comes from the API
below.

| Page | URL | Query parameters (all optional, kept in the URL so views can be shared) |
|---|---|---|
| Overview | [`/`](https://chess.generops.dev/) | `from`, `to` (YYYY-MM): range of the charts · `metric` (`players` instead of games) · `speed` (`ultrabullet` … `correspondence`) and `month`: the rating distribution shown |
| Openings | [`/openings`](https://chess.generops.dev/openings) | `from`, `to` (YYYY-MM) · `speed` · `band` (lower bound of a 200-point rating band, 400–2800) |

**Data API**: `https://chess.generops.dev/api`, read-only JSON over the gold tables
([api/](api/README.md)).

| Endpoint | Parameters | Returns |
|---|---|---|
| [`GET /api/v1/months`](https://chess.generops.dev/api/v1/months) | none | the months that have data, oldest first |
| [`GET /api/v1/summary`](https://chess.generops.dev/api/v1/summary?from=2026-08&to=2026-09&speed=all) | `from`, `to` (YYYY-MM, required) · `speed` (repeatable: `all`, `ultrabullet`, `bullet`, `blitz`, `rapid`, `classical`, `correspondence`; empty = all of them) | per month and speed: games, players, white wins, draws, black wins, unfinished, bot games, average rating |
| [`GET /api/v1/rating-distribution`](https://chess.generops.dev/api/v1/rating-distribution?month=2026-09&speed=blitz) | `month` (required) · `speed` (repeatable) | per speed and 50-point rating bucket: player-games and players |
| [`GET /api/v1/openings`](https://chess.generops.dev/api/v1/openings?from=2026-09&to=2026-09&speed=blitz&limit=10) | `from`, `to` (required) · `speed` · `rating_band` (0–4000, a multiple of 200) · `limit` (1–100, default 20) | the most played openings over the range, with results, plus the selection's total games |
| [`GET /api/docs`](https://chess.generops.dev/api/docs) | none | interactive documentation (Swagger UI) |
| [`GET /api/openapi.json`](https://chess.generops.dev/api/openapi.json) | none | the OpenAPI spec: the contract the dashboard's client is generated from |

Errors: `400` for a range whose `from` is after `to`, `422` for an invalid parameter, `429` (with
`Retry-After`) above about 5 requests a second per IP with bursts of 60, `503` if the data store
is unavailable. Responses are cached for an hour and carry an `ETag`; the data changes monthly.
The API's health probes are deliberately not routed publicly; `https://chess.generops.dev/healthz`
is the dashboard's web server answering `ok`, not the API.

### Private: owner's Entra sign-in, through Cloudflare Access

Visitors without an assigned account are refused (see *About access* above).

| Service | URL | What it's for |
|---|---|---|
| Argo CD | https://chess-cd.generops.dev | what's deployed from git, sync status, rollbacks |
| Argo Workflows | https://chess-flows.generops.dev | pipeline runs: ingest, builds, maintenance, smoke tests, with logs |
| Grafana | https://chess-grafana.generops.dev | cluster, workflow and Spark metrics (Azure Managed Prometheus) |
| JupyterLab | https://chess-lab.generops.dev | notebooks with a Spark session on the lake; **only up while a notebook workflow runs** |

**In progress:** two more private endpoints, each requiring an Entra token with an app role that
only assigned accounts get:
- **MCP server** (#68), for MCP clients such as Claude: `https://chess-mcp.generops.dev/mcp`, app role `Lakehouse.Query`.
- **Chat on the data** (#69), a panel in the dashboard: everyone can see it, only `Chat.User` accounts can send.

### Internal: inside the cluster only, not reachable from the internet

| Service | What it does |
|---|---|
| Trino (`trino.trino:8443`) | SQL over the lake; JWT sign-in with Kubernetes service-account tokens, TLS from a private CA, per-user rules (the API may read `gold` only) |
| Lakekeeper (`lakekeeper.lakekeeper:8181`) | Iceberg REST catalog `lake`; vends short-lived storage credentials per table |
| Spark (operator + jobs) | builds every table; drivers on the system pool, executors on spot nodes |
| Argo Workflows controller | runs the pipeline (below) |
| CloudNativePG | Postgres for Lakekeeper |
| Kyverno, External Secrets, cert-manager, trust-manager, cloudflared | policy, secrets from Key Vault, private CA, the outbound tunnel all public traffic arrives through |

## What runs on its own

| When | What |
|---|---|
| Daily 06:00 UTC | `lichess-monthly`: if Lichess has published a new month, ingest it and build raw → curated → gold → maintenance; refresh Azure costs |
| Sundays 03:00 UTC | `lake-maintenance`: compact small files, expire snapshots older than 7 days, remove orphan files |
| Every merge to `main` | CI builds, tests, scans and signs the images; new API and dashboard builds roll out by themselves (signed digests on the `deploy-state` branch) |

A failed workflow sends an email alert. A month whose checks fail never reaches the tables.

## How it's built

Monthly dump → `lichess-ingest` (torrent download, SHA-256 check, Arrow parsing into Parquet) →
`raw` → `curated` → `gold` (PySpark with write-audit-publish on Iceberg branches) → Trino → API
and dashboard, all through a Cloudflare Tunnel (no public IP or open port on the cluster).

| Path | Component |
|---|---|
| [`ingest/`](ingest/README.md) | `lichess-ingest`: download, verify, parse PGN into Parquet, month claims and manifests |
| [`spark/`](spark/README.md) | Spark 4.1 + Iceberg 1.12 image and jobs: raw, curated, player_games, gold, maintenance, costs |
| [`api/`](api/README.md) | `chess-api`: the FastAPI data API |
| [`web/`](web/README.md) | `chess-web`: the React dashboard, served by unprivileged nginx |
| [`deploy/`](deploy/README.md) | everything Argo CD syncs: apps, workflow templates, CronWorkflows, charts, network policies |
| [`infra/`](infra/README.md) | Terraform in four stacks: bootstrap, platform (lake, network), compute (AKS, ACR, Key Vault, identities, monitoring), gitops (Argo CD, Cloudflare, Entra) |
| [`docs/adr/`](docs/adr/) | architecture decisions: data architecture, storage, stack, Spark version, bucketing |

**Security:**
- No secrets in git, and no storage keys: every component signs in to Azure with its own workload identity.
- Every image is signed in CI, and Kyverno admits only signed ones.
- Pod Security `restricted` in the app namespaces.
- Default-deny network policies.
- Read-only database users per service.

**Cost:** about $5–6 a day while the cluster runs, almost all of it VMs. The lake itself costs
about $2 a month today (~$85 for the full history).

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phases and the GitHub milestones for the plan.
