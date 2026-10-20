# ADR 0005: Spark version and native execution engine

- **Status:** Accepted
- **Date:** 2026-10-20
- **Issue:** #7

## Context

Spark builds the Iceberg tables (ADR 0004). The issue asked for a Spark version and a native
execution engine (Gluten/Velox or DataFusion Comet), with a compatibility matrix across
Spark, the engines, Iceberg, Celeborn and hadoop-azure. What constrains it:

- Iceberg ships a Spark runtime per Spark minor version. As of 2026-10, Iceberg 1.12's newest
  is Spark 4.1; there is no 4.2 runtime yet.
- `hadoop-azure` (ABFS with workload identity) must match the Hadoop that Spark bundles:
  Spark 4.1.x bundles Hadoop 3.4.2.
- Native engines lag new Spark versions and fall back to the JVM for unsupported operators.
  They pay off only on heavy scans and joins, which we can't judge without a benchmark of
  our own jobs.

## Options considered

1. **Spark 3.5.x + a native engine now.** Broadest engine support, but an older Spark, and
   tuning an engine without a baseline.
2. **Spark 4.1 on the JVM; decide on a native engine with a benchmark later.**
3. **Spark 4.2.** No Iceberg runtime for it yet.

## Decision

Option 2, pinned in `spark/Dockerfile` (Renovate tracks the Maven artifacts):

| Component | Version |
|---|---|
| Spark | 4.1.3 (Scala 2.13, Java 21), image `apache/spark:4.1.3-scala2.13-java21-python3-ubuntu` |
| Iceberg | 1.12.0 (`iceberg-spark-runtime-4.1_2.13`, `iceberg-azure-bundle`) |
| hadoop-azure | 3.4.2 (matches Spark's bundled Hadoop) |
| Spark operator | Kubeflow `spark-operator` chart 2.5.2 (`SparkApplication` v1beta2) |
| Shuffle | Spark's own, with executor decommissioning and fallback storage in the lake; no Celeborn |
| Native engine | none for now |

## Consequences

- Current Spark and Iceberg features: branches and `fast_forward` (write-audit-publish),
  storage-partitioned joins (ADR 0001), `rewrite_data_files` with sort.
- No Celeborn to run: spot evictions are handled by decommissioning (shuffle blocks move to
  other executors or to `spark/fallback/` in the lake).
- **Revisit** when (a) Iceberg ships a Spark 4.2 runtime, or (b) a job becomes slow enough to
  matter: the heaviest planned ones are the opening explorer (#65) and per-position data
  (#29). Then benchmark Gluten and Comet against the JVM baseline below on that job before
  choosing.

## Evidence

JVM baseline, 38 months (226.6 M games), 2 spot executors × 6 cores:

| Job | Time |
|---|---|
| `build_curated_games` | 16 min |
| `build_player_games` (453 M rows, bucketed, sorted) | 20 min |
| `build_gold` (three tables, with reconciliation) | ~15–20 min |
| `rewrite_data_files` on `player_games` (13.6 GB) | ~10 min |

All builds run the CI smoke tests (`spark/tests/local_*.py`) in the same image in local mode.
