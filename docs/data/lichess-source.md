# Lichess source: standard rated games

Inventory of the monthly dumps at <https://database.lichess.org/standard/>,
taken 2026-10-10 (issue #17). Per-month numbers are in
[`lichess-standard-inventory.csv`](lichess-standard-inventory.csv).

## Totals

| | |
|---|---|
| Months | 165 (2013-01 → 2026-09), one file per month, not cumulative |
| Games | 8,220,312,882 |
| Compressed size | 2.60 TB (`.pgn.zst`) |
| Largest month | 2023-03: 34.9 GB, 108.2M games |
| Recent months | ~28–31 GB and ~85–95M games each |

| Year | Games | Compressed | Bytes/game |
|---|---|---|---|
| 2013 | 3.4M | 0.5 GB | 151 |
| 2014 | 11.6M | 2.1 GB | 182 |
| 2015 | 30.1M | 5.6 GB | 188 |
| 2016 | 78.5M | 14.1 GB | 179 |
| 2017 | 148.1M | 41.3 GB | 279 |
| 2018 | 265.8M | 80.4 GB | 302 |
| 2019 | 436.8M | 128.6 GB | 294 |
| 2020 | 813.9M | 242.5 GB | 298 |
| 2021 | 1,123.0M | 363.1 GB | 323 |
| 2022 | 1,093.1M | 353.5 GB | 323 |
| 2023 | 1,181.3M | 382.9 GB | 324 |
| 2024 | 1,113.2M | 362.4 GB | 326 |
| 2025 | 1,114.2M | 362.9 GB | 326 |
| 2026 (Jan–Sep) | 807.4M | 263.4 GB | 326 |

Three quarters of the data is 2020 onward.

## Published files

| File | Use |
|---|---|
| `list.txt` | Download URLs, newest first. **Use this for discovery.** |
| `counts.txt` | Game count per file (`<file> <games>`); lines are not in date order, so sort when reading |
| `sha256sums.txt` | `<sha256>  <file>` per line, all 165 files; `pgn-timing download` verifies against it |
| `*.pgn.zst.torrent` | A torrent for every month |

Validation: the spike's 2025-09 run matched both the published size
(28,334,624,323 bytes) and game count (87,049,890) exactly.

## Things to handle

- **Stray file:** the directory listing also contains
  `lichess_db_standard_rated_2024-10.pgn.zst.zst` (1.29 GB). Don't scrape the
  directory; take file names from `list.txt`.
- **Clock comments from 2017-04:** bytes per game jump from ~190 to ~307 between
  2017-03 and 2017-04, consistent with `[%clk]` comments appearing in movetext.
  Movetext before that has no clocks; downstream parsing must not assume them.
- **Tag set over time:** 2025-09 had no tags outside the parser's known list.
  Older months may differ; the pipeline should record `unknown_tags` per month
  (the spike already reports them) and fail loudly on schema surprises.
- **Rate limits:** none documented. Downloads are large (~30 GB/month recently);
  stay polite (a few concurrent downloads) or use the torrents for a bulk backfill.

## Bootstrap estimates (from the spike)

Using the 2025-09 measurements on the i9 workstation (parse ~13.6 s per
compressed GB, Parquet ≈ 1.42 × compressed size, download 11.9 MB/s):

| | Estimate |
|---|---|
| Download all months | ~61 h (~2.5 days) at 11.9 MB/s |
| Parse all months, one at a time | ~10 h |
| Parquet output (current spike settings) | ~3.7 TB |
| Disk if keeping source + Parquet | ~6.3 TB |

Download dominates. Parsing can overlap with downloading the next month, so a
full local backfill is roughly the download time. The Parquet size should drop
once file size and compression are tuned (see #16 findings).
