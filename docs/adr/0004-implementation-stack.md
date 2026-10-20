# ADR 0004: Implementation stack: parser, job runtime, orchestration, IaC

- **Status:** Accepted
- **Date:** 2026-10-20
- **Issue:** #15

## Context

The old pipeline was slow at parsing, so the issue proposed a Rust parser, Python jobs with
PyIceberg, one Kubernetes Job per month, and Terraform. Building it changed some of that.
This ADR records what was chosen and why.

## Options considered

- **Parser:** Rust (`zstd`, `arrow` crates) vs **Python with NumPy/Arrow vectorised
  parsing**. The spike (`spike/pgn_timing`) showed parsing isn't the bottleneck once headers
  are parsed with Arrow kernels on whole chunks instead of per game in Python: download is.
- **Table writes:** PyIceberg vs **Spark**. Curated and gold need joins, windows and
  aggregations over ~90 M games a month, and write-audit-publish needs Iceberg branches and
  procedures (`fast_forward`, `rewrite_data_files`). Spark has all of it.
- **Orchestration:** plain Kubernetes Jobs/CronJobs, Airflow, Dagster, or **Argo
  Workflows**. Argo is Kubernetes-native, adds no separate scheduler or database to run, and
  has fan-out (`withParam`), mutexes, retries and cron built in.
- **IaC:** **Terraform** vs Bicep. Terraform covers Azure, Entra ID and Cloudflare with one
  tool.

## Decision

| Concern | Choice |
|---|---|
| Download | `aria2c` torrent (falls back to HTTPS), resumable, SHA-256 verified |
| Parser | Python 3 + NumPy + PyArrow: game-aligned chunks, header tags as Arrow string columns, `movetext` kept whole; a pure-Python reference parser checks the fast one |
| Table builds | PySpark on Spark 4.1 with Iceberg 1.12 (ADR 0005), as `SparkApplication`s via the Kubeflow Spark operator |
| Interactive SQL | Trino over the same Iceberg catalog (#64) |
| Catalog | Lakekeeper (Iceberg REST) on CloudNativePG, with vended credentials |
| Runtime | AKS: a small on-demand system pool for drivers, controllers and Trino; a spot `batch` pool (0–4 nodes) for ingest pods and Spark executors |
| Orchestration | Argo Workflows: `ingest-month`, `ingest-backfill`, `monthly` (daily CronWorkflow), `spark-job`, `lake-maintenance` (weekly CronWorkflow) |
| Deployment | Argo CD (GitOps): every change is a PR, and merging deploys `deploy/` |
| IaC | Terraform in four stacks: `bootstrap` (state, budget), `platform` (lake, network), `compute` (ACR, AKS, Key Vault, identities, monitoring), `gitops` (Argo CD, cluster facts, Cloudflare, Entra apps) |
| Supply chain | Images built in GitHub Actions only (OIDC to Azure): SBOM, provenance, Trivy, cosign signatures; Kyverno verifies digests and signatures at admission; Renovate pins and updates |

## Consequences

- One language (Python) for ingest, Spark jobs, notebooks and the coming API. That's easy to
  maintain for one person, and fast enough where it matters.
- Spark costs a driver and executors per job (~1–2 minutes of start-up), acceptable for
  monthly batch work. Spot executors keep it cheap; decommissioning moves shuffle data off
  a reclaimed node.
- Argo Workflows is the only scheduler. Alerts on failed workflows come from Managed
  Prometheus (`WorkflowFailed`).
- Terraform is applied by the owner (plan, then apply). Argo CD applies everything under
  `deploy/` on merge.

## Evidence

- `spike/pgn_timing`: both parsers produce identical tables on a full month; the vectorised
  one is the default.
- Full builds for 38 months with 2 spot executors (6 cores each): `curated.games` 16 min,
  `curated.player_games` 20 min, gold ~15–20 min.
