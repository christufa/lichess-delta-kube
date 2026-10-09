# Roadmap

Ingest the full Lichess game history directly from the
[Lichess database dumps](https://database.lichess.org/) into Iceberg tables on
Azure, replacing the previous Databricks/Hugging Face pipeline (tagged
`databricks-final`). A serving layer (UI/database) is out of scope for now.

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
| Ingestion spike | A single Azure VM in the target region |
| Production | Containers in ACR, one Kubernetes Job per month on AKS (batch pool scales to zero) |
| Large joins / positions | Spark on the same AKS cluster (Phase 5) |

## Milestones

| Phase | Milestone | Outcome |
|---|---|---|
| 0 | Foundations & decisions | Data architecture, storage, catalog and stack ADRs; region, quotas, budget; repo restructured |
| 1 | Ingestion spike | Per-stage timings for one real month and a backfill estimate |
| 2 | Platform | Terraform for ADLS, ACR, Postgres catalog, minimal AKS; CI images |
| 3 | Landing & raw | Downloader, streaming parser, `raw.games` commits, full backfill |
| 4 | Curated | `curated.games`, `curated.player_games`, data quality, bucketing |
| 5 | Spark & heavy transforms | Spark on AKS for large joins and per-position data |
| 6 | Operations | Monthly orchestration, monitoring, cost reporting, Databricks retired |

## Repository layout

```text
docs/        roadmap and architecture decision records (docs/adr/)
spike/       throwaway measurement tools (spike/pgn_timing)
ingest/      production parser (planned: pgn2parquet)
jobs/        Python jobs: landing, Iceberg commits, curated builds (planned)
infra/       Terraform (planned)
deploy/      Kubernetes manifests (planned)
```
