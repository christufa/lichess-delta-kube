# ADR 0002: Data architecture: source, landing, raw, curated, gold

- **Status:** Accepted
- **Date:** 2026-10-20
- **Issue:** #5

## Context

The goal is queryable tables over the full Lichess game history: a public dashboard, an MCP
server and a chat read from them. The source publishes one compressed PGN dump per month,
with game counts and SHA-256 checksums alongside. A month is large: 2026-09 is 89.6 M games
and about 35 GB compressed. The whole history is 166 months and 8.22 B games (counts.txt,
2026-10). Each layer must be rebuildable a month at a time, and readers must never see a
half-written or unchecked month.

## Options considered

1. **Keep the `.pgn.zst` dumps in the lake as the landing layer** and parse from there.
   Gives a private replay point, but costs ~3+ TB of cold storage for files that Lichess
   already serves, checksummed, forever.
2. **Parse straight from the download; land Parquet plus a manifest.** Lichess (with its
   checksums) is the replay point, so a month can always be fetched again and verified.
3. **One wide table** (games with typed columns only). Simple, but player-centric queries
   (most of the dashboard) would need to unpivot white/black every time.

## Decision

Option 2, with these layers, all Apache Iceberg tables in one catalog (`lake`, Lakekeeper)
except the landing files:

| Layer | What | Rule |
|---|---|---|
| **Source** | `lichess_db_standard_rated_YYYY-MM.pgn.zst`, standard rated games only | The replay point. Downloads are verified against `sha256sums.txt`, and the parsed game count against `counts.txt`. |
| **Landing** | Parquet per month plus a manifest (`_state/raw_games/YYYY-MM.json`: games, checksum, run) | Written by `lichess-ingest`; transient (expires after 14 days, ADR 0003). |
| **Raw** | `raw.games`: every PGN header tag as a string, plus `movetext` and lineage (`_source_file`, `_ingested_at`, `_run_id`) | No cleanup and no business rules. Count = manifest. |
| **Curated** | `curated.games` (typed: `game_id`, UTC `played_at`, ratings, speed class, result…) and `curated.player_games` (two rows per game) | **Nothing filtered**: every raw row becomes a curated row. Oddities are flagged (`is_duplicate`) or counted by non-blocking checks. |
| **Gold** | `gold.monthly_summary`, `gold.openings`, `gold.rating_distribution` | Small aggregates for the dashboard and chat; each game counted once. |
| **Ops** | `ops.dq_results` | Every check of every build. |

Processing rules, the same in every layer:

- **Incremental by month**: partitioned by `month`; a build replaces exactly one month.
- **Write-audit-publish**: each month is written to an Iceberg branch, audited, and main is
  fast-forwarded only if every *blocking* check passes. Readers never see an unchecked month.
- **Lineage by run id**: every row carries the run that wrote it (`_run_id`); derived rows
  carry the source month's run id (`_source_run_id`). A month is rebuilt when its source was
  rebuilt, and never because of compaction or other maintenance.
- **Orchestration**: Argo Workflows; a daily CronWorkflow brings each new month through every
  layer (#30).

Open questions from the issue, answered:

- **Standard games only, or variants too?** Standard rated only. Variants are separate dumps
  with different tags (`Variant`, `FEN`); add them as their own source if ever needed.
- **Retention:** landed Parquet 14 days; Iceberg snapshots 7 days, keeping the last 3 (#63).
  No dump archive; see option 1.
- **`games` + `player_games` as the first curated target?** Yes, and they proved enough for
  the first gold tables. Per-position data (#29) and the opening explorer (#65) come later
  from `raw.movetext`.

## Consequences

- A month can always be rebuilt from Lichess. Re-ingesting one costs a download (minutes by
  torrent) instead of storage.
- Curated keeps data that a filter-first design would drop (unfinished games, duplicate ids,
  odd ratings), so gold and the API choose what to count, explicitly.
- Every layer's quality is queryable history (`ops.dq_results`), not just logs.

## Evidence

- 38 months built end to end (2013–2015, 2026-08/09): 226,572,280 games in raw and curated,
  453,144,560 player rows, and gold totals reconciling exactly. No month rejected.
- Counted, not blocking, in real data: 2026-09 has 220,454 unfinished games; 2013–2015
  months have games starting in the previous month's last 1–2 hours (old dumps cut at
  European midnight).
