# lichess-delta-kube

A data platform for the full [Lichess](https://lichess.org) game history:
monthly PGN dumps from [database.lichess.org](https://database.lichess.org/)
are landed in Azure Data Lake Storage, parsed into Iceberg tables, and modeled
for analysis. Batch work runs as containers on Azure Kubernetes Service.

> The previous Databricks pipeline (Hugging Face Parquet → Unity Catalog Delta)
> is preserved at the `databricks-final` tag.

## Status

Planning and measurement. See [docs/ROADMAP.md](docs/ROADMAP.md) for the data
flow, phases and layout, and the GitHub milestones for the issue-level plan.

Current work: the ingestion spike — [`spike/pgn_timing`](spike/pgn_timing)
times download, decompression, parsing and Iceberg commit for one real month.

## Decisions

Architecture decisions are recorded in [docs/adr/](docs/adr/).
