# ADR 0003: ADLS Gen2 storage layout and access model

- **Status:** Accepted
- **Date:** 2026-10-20
- **Issue:** #10

## Context

All data lives in Azure Data Lake Storage Gen2. Writers: the ingest pods, the Spark jobs, and
Lakekeeper, which vends table-scoped credentials. Readers: Spark, Trino, notebooks. The repo is
public, the cluster is a personal one, and the source data is public and can always be fetched
again (ADR 0002). So the priorities are no stored secrets, least privilege per component, and
low cost. Durability matters less.

## Options considered

1. **Several containers** (`landing`, `warehouse`, `staging`, `logs`), as the issue proposed.
   Clear separation, but more firewall and RBAC surface, and Lakekeeper's warehouse would
   still sit in one of them.
2. **One container with prefixes.** One RBAC and firewall scope; lifecycle rules work by prefix
   anyway.
3. **Account keys or SAS in Kubernetes Secrets.** Simple, but a secret to rotate and leak.
4. **Entra ID only: workload identity for pods, user-delegation SAS or `az login` for people.**

## Decision

- **One storage account**: Standard, **LRS** (the source is public, so it can always be
  re-ingested), Hot tier, **hierarchical namespace** on. **Shared keys disabled**, so only
  Entra ID and user-delegation SAS work. Soft delete for blobs and containers (7 days).
- **Network**: firewall default Deny; allowed are the AKS subnet (service endpoint) and the
  owner's IP. IP rules are set by hand in Azure; Terraform ignores `ip_rules`.
- **One private container**, laid out by prefix under the deployment's root prefix:

  | Prefix | Contents | Written by | Lifecycle |
  |---|---|---|---|
  | `raw/games/month=YYYY-MM/` | landed Parquet (ingest output) | lichess-ingest | deleted after 14 days (published into `raw.games` long before) |
  | `_state/raw_games/YYYY-MM.json` | per-month manifests (count, checksum, run) | lichess-ingest | kept |
  | `_state/claims/` | month claims so parallel runners never ingest the same month | lichess-ingest | released when done |
  | `_state/runs/` | per-run ingest logs | lichess-ingest | kept |
  | Lakekeeper table locations | Iceberg data and metadata for every table in warehouse `lichess` | Spark, through Lakekeeper | Iceberg maintenance (#63): compaction, snapshot expiry, orphan removal |
  | `spark/fallback/` | shuffle blocks of decommissioned spot executors | Spark | transient |

- **Access**, one identity per component, Azure RBAC on the account:

  | Identity | Used by | Role |
  |---|---|---|
  | jobs | ingest pods, Spark jobs, notebooks (SA `lichess-jobs`) | Storage Blob Data Contributor |
  | Lakekeeper | the catalog: vends per-table SAS tokens to Spark | Storage Blob Data Contributor + Storage Blob Delegator |
  | Trino | reads table files with its own identity | Storage Blob Data Reader |

  Pods sign in with AKS workload identity (federated credentials), with no secrets in the
  cluster. People use `az login` or a short-lived user-delegation SAS.

## Consequences

- Nothing in git or in the cluster can unlock the lake. Every access is an Entra identity
  with a role, visible in the activity log.
- LRS means a regional outage loses the copy; recovery is re-ingesting from Lichess
  (estimated ~15 h for the full history with 4 parallel spot pods, per #24; ADR 0002).
- One container keeps the firewall and RBAC simple. Separation is by prefix and by Iceberg
  table, not by container.

## Evidence

Measured sizes (2026-10-20, 38 months, 226.6 M games), and a full-history estimate scaled to
8.22 B games:

| Data | Now | Per game | Full history |
|---|---|---|---|
| `raw.games` (movetext dominates) | 91.8 GB | ~405 B | ~3.3 TB |
| `curated.games` | 7.8 GB | ~35 B | ~0.29 TB |
| `curated.player_games` | 14.9 GB | ~66 B | ~0.54 TB |
| gold + ops | < 0.1 GB | — | < 0.01 TB |
| **Total** | **~114 GB** | ~505 B | **~4.2 TB** |

Cost at about $0.02 per GB-month (Hot, LRS; check current regional pricing): **~$2.30/month
now** and **~$85/month for the full history**. Moving `raw.games`' data files to the Cool tier
(about half the price, and rarely read once curated is built) would bring the full history to
roughly $50/month. That's an option for after the backfill (#24), not needed now. Landed
Parquet adds at most 14 days of one month's output; Iceberg snapshots at most 7 days of
rewritten files.
