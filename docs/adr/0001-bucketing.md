# ADR 0001: Bucketing for curated tables

- **Status:** Proposed
- **Date:** 2026-10-19
- **Issue:** #8

## Context

`curated.games` has one row per game; `curated.player_games` has two per game, one per player.
Both are partitioned by `month`, so monthly builds replace exactly one partition
(write-audit-publish). The question is how to lay out the rows *inside* a month so the
recurring reads are cheap:

- **By player:** a player's history, head-to-head, per-player aggregates. These read
  `player_games` and group or join on the player.
- **By game:** look up one game, or join `player_games` back to `games` for movetext. These
  are point lookups, not large joins: `player_games` already carries everything the
  dashboard and gold tables need (ratings, result, speed, opening).

Iceberg can bucket a table on a column (`bucket(N, col)`). Spark then plans
**storage-partitioned joins** (SPJ) between tables with compatible buckets without a shuffle.
Lookups on the bucketed column read 1/N of a month.

Measured on `lake.raw.games` (Trino, 2026-10-19):

| Month | Games | Player rows | Distinct players | Busiest player |
|---|---|---|---|---|
| 2015-12 | 4.16 M | 8.3 M | 81 k | 9.2 k rows (0.11 %) |
| 2026-09 | 89.6 M | 179 M | 2.0 M | 34 k rows (0.02 %, the `maia1` bot) |

## Options considered

1. **No bucketing; sort by key within the month.** Simplest. Min/max statistics still prune
   files for point lookups on the sort key. But no SPJ, and a sort only serves one key.
2. **`player_games` bucketed by player; `games` sorted by `game_id`.** Player reads and
   player-keyed joins skip the shuffle; game lookups prune on `game_id` statistics. A join
   between `games` and `player_games` on `game_id` still shuffles, and that join isn't a
   recurring one.
3. **Both bucketed by `game_id`.** SPJ for the `games` ↔ `player_games` join, which we
   don't need, at the cost of every player-keyed query, which we do need.

## Decision

Option 2:

- `curated.player_games`: `PARTITIONED BY (month, bucket(16, player_id))`, sorted by
  `player_id, played_at`. `player_id` is the lower-cased username, since Lichess
  usernames are case-insensitive. The username as written stays in `player`.
- `curated.games`: `PARTITIONED BY (month)`, sorted by `game_id` (as built in #25). No bucket.
- Any future player-keyed curated or gold table uses `bucket(16, player_id)` too, so it can
  join `player_games` without a shuffle.

**N = 16.** A `player_games` row is about 12 short columns; estimate 20–30 bytes per row
compressed. For 2026-09 that's 180 M × ~25 B ≈ 4.5 GB, so 16 buckets give ≈ 280 MB per file.
That's inside the 256 MB–1 GB target, with room for Lichess to grow about 3× before files
reach 1 GB. The estimate is checked after the first build (see Evidence) and N is revisited
if files fall outside 128 MB–1 GB for recent months. Early months (2013: 0.25 M rows) give
small files; compaction (#63) merges them.

**Skew** is not a problem: the busiest account is 0.02–0.1 % of a month, against 6.25 % per
bucket. If it ever is, partially clustered distribution (below) splits the large side.

Spark settings for SPJ (Spark 4.1, Iceberg 1.12), set on the jobs that need them:

```
spark.sql.sources.v2.bucketing.enabled=true
spark.sql.sources.v2.bucketing.pushPartValues.enabled=true
spark.sql.iceberg.planning.preserve-data-grouping=true
spark.sql.requireAllClusterKeysForCoPartition=false
spark.sql.sources.v2.bucketing.partiallyClusteredDistribution.enabled=true
```

## Consequences

- Player queries on `player_games` read 1/16 of each month; `games` stays simple.
- Changing N later means rewriting `player_games` (partition spec evolution only applies to
  new data). Rewriting is cheap now (38 months) and expensive after the full backfill, so
  check the file sizes before #24.
- Joining `player_games` to `games` on `game_id` shuffles. That's acceptable for occasional
  lookups; gold tables are built from `player_games` and `games` separately.

## Evidence

- The skew measurements above (`approx_distinct`, `approx_most_frequent` on `lake.raw.games`).
- **To fill in after the first build:** bytes per row and file sizes of `player_games`
  for 2026-09 (`SELECT partition, file_count, total_data_file_size_in_bytes FROM
  lake.curated."player_games$partitions"`).
