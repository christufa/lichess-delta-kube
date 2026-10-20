# Roadmap

Ingest the full Lichess game history directly from the
[Lichess database dumps](https://database.lichess.org/) into Iceberg tables on
Azure, and serve it: a public dashboard with an opening explorer on
`chess.generops.dev`, a lakehouse MCP server and a chat on the data.

Work is tracked as GitHub milestones and issues; this page is the summary.

## Data flow

```text
Lichess .pgn.zst ─► landing (ADLS, unchanged) ─► raw.games (Iceberg) ─► curated.* (Iceberg)
```

| Layer | Contents | Rules |
|---|---|---|
| Landing | Monthly `.pgn.zst` files + manifests | Never modified; the replay point |
| Raw | Header tags as columns, `movetext` as a string, ingest metadata; partitioned by month | No cleanup, no move replay; atomic per-month commits |
| Curated | `games`, `player_games` (later `positions`) | Typed, deduplicated, quality-checked; incremental by month |

## Where it runs

| Stage | Where |
|---|---|
| Development | Locally, in Docker, on small early months |
| Ingestion spike | Done locally (results in #16) |
| Historical bootstrap | `ingest/` (`lichess-ingest`) on the workstation, month by month, writing straight to ADLS |
| Monthly updates | Argo CronWorkflow on AKS, one spot pod per month (batch pool scales to zero) |
| Large joins / positions | Spark on the same AKS cluster (Phase 5) |

## Milestones

| Phase | Milestone | Outcome |
|---|---|---|
| 0 | Foundations & decisions | Data architecture, storage, catalog and stack ADRs; region, quotas, budget; repo restructured |
| 1 | Ingestion spike | Per-stage timings for one real month and a backfill estimate |
| 2 | Platform | Terraform for ADLS, ACR, minimal AKS (done) |
| 2.5 | GitOps, orchestration & UIs | Argo CD, Argo Workflows, CI images, monthly CronWorkflow |
| 3 | Landing & raw | Downloader, streaming parser, `raw.games` commits, full backfill |
| 4 | Curated | `curated.games`, `curated.player_games`, data quality, bucketing |
| 5 | Spark & heavy transforms | Spark on AKS for large joins and per-position data |
| 6 | Operations | Monitoring, cost reporting, security baseline, table maintenance, teardown |
| 7 | Serving | Trino, gold tables + opening explorer, API, public dashboard, MCP server, chat |

## Repository layout

```text
docs/        roadmap and architecture decision records (docs/adr/)
spike/       throwaway measurement tools (spike/pgn_timing)
ingest/      lichess-ingest: download, verify, parse and land raw Parquet in ADLS
spark/       PySpark jobs and the Spark image (Spark 4.1, Iceberg 1.12, ABFS + workload identity)
api/         chess-api: the read-only data API over lake.gold (FastAPI; openapi.json is the contract)
infra/       Terraform: bootstrap (state, budget), platform (lake, network), compute (ACR, AKS, Key Vault, identities), gitops (Argo CD, cluster facts)
deploy/      Argo CD (GitOps): apps, charts/platform, workflow templates, examples
```
