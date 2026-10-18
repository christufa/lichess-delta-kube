# lichess-delta-kube

A data platform for the full [Lichess](https://lichess.org) game history:
monthly PGN dumps from [database.lichess.org](https://database.lichess.org/)
are landed in Azure Data Lake Storage, parsed into Iceberg tables, and modeled
for analysis. Batch work runs as containers on Azure Kubernetes Service.

## Status

Running on AKS: GitOps (Argo CD), ingest workflows (Argo Workflows), Spark with
JupyterLab, signed CI images, Key Vault + External Secrets and Kyverno policies.
Next: network policies, the public entry on `chess-*.generops.dev`, the Iceberg
catalog and tables, then Trino, the dashboard and the chat on the data.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the data flow, phases and layout, and
the GitHub milestones for the issue-level plan.

## Decisions

Architecture decisions are recorded in [docs/adr/](docs/adr/).
