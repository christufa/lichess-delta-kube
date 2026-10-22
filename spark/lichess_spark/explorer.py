"""lake.gold.position_moves: the opening explorer (#65).

For each month: from every position reached in a game's first MAX_PLIES plies, which move was
played next, how often and how it ended, by speed and rating band (the players' average rating).

    month, position, move, speed, rating_band, games, white_wins, draws, black_wins,
    rated_games, rating_sum, example_game_id, example_rating   (+ lineage)

- position: "<piece placement> <side to move> <castling>" (the first three FEN fields; no
  en-passant square, no counters). Move orders that reach the same position are one row, as on
  Lichess's explorer. The API looks a position up by the same three fields of the FEN the board
  sends (`position_key`).
- move: SAN as in the game.
- Only (move sequence, move) pairs played at least MIN_GAMES times in the month are kept,
  before they're merged by position: past ~ply 12 nearly every game is unique (measured on
  2014-06: 85 % of moves in plies 1-8 are in pairs played 5+ times, 2 % in plies 13-16), and
  that tail would be most of the table and never looked up.
- rating_band: lower bound of RATING_BANDS (0 = under 1200); null when a rating is missing.
  The average rating is rating_sum / rated_games, so it can be summed across rows.
- example_game_id: the highest-rated game (by average rating) in the row.

Input: raw.games for the moves (curated doesn't keep them), joined on (game_id, _source_file)
with curated.games for speed, result, ratings and is_duplicate (each game counted once). The
month is pinned to one curated build (_source_run_id), so --pending rebuilds it whenever
curated does, like the other gold tables.

Two passes over the month's (cached) move lists, so the unique tail is never shuffled with its
details: 1) count each (sequence, move) pair by a 64-bit hash of the sequence; 2) aggregate the
pairs that reach MIN_GAMES by speed and band. Each kept sequence is then replayed once with
python-chess (GPL, in this batch image only) to get its position.
"""
from __future__ import annotations

import uuid

from . import curated, raw, wap
from .wap import Check

TABLE = "lake.gold.position_moves"
MAX_PLIES = 16
MIN_GAMES = 5
RATING_BANDS = (1200, 1600, 2000, 2400)  # lower bounds above the first band (0 = under 1200)
BUCKETS = 16
TARGET_FILE_BYTES = 256 * 1024 * 1024
LAYOUT = "WRITE DISTRIBUTED BY PARTITION LOCALLY ORDERED BY position, move"
LINEAGE = wap.SOURCE_RUN_ID
START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq"
COMMENT = (f"Opening explorer: per month, position (first three FEN fields), next move (SAN), speed and "
           f"rating band, from the first {MAX_PLIES} plies of each game; (move sequence, move) pairs played "
           f"at least {MIN_GAMES} times in the month. Each game once.")
NOT_MOVES = ("1-0", "0-1", "1/2-1/2", "*")
# Comments ({ [%clk ...] [%eval ...] }), move numbers (1. and 1...), NAGs and ?!/!! annotations.
NOISE = r"\{[^}]*\}|\d+\.(\.\.)?|\$\d+|[?!]+"


def position_key(fen: str) -> str:
    """The table's key for a FEN: piece placement, side to move, castling."""
    return " ".join(fen.split()[:3])


def replay(moves: list[str]) -> str | None:
    """The position after `moves` (SAN), or None if one of them isn't legal."""
    import chess

    board = chess.Board()
    try:
        for san in moves:
            board.push_san(san)
    except ValueError:
        return None
    return position_key(board.fen())


def rating_band(avg: float | None) -> int | None:
    if avg is None:
        return None
    return max([0, *[b for b in RATING_BANDS if avg >= b]])


def evaluate(stats: dict, unique_games: int) -> list[Check]:
    """Checks for one month (pure, unit-tested)."""
    n = lambda key: int(stats.get(key) or 0)  # noqa: E731
    pairs, unparsed = n("kept_pairs"), n("unparsed_pairs")
    return [
        Check("not_empty", n("rows") > 0, n("rows"), None),
        Check("games_joined_once", n("joined_games") == unique_games, n("joined_games"), unique_games,
              "each unique curated game found once in raw by (game_id, _source_file)"),
        Check("start_not_overcounted", n("start_games") <= unique_games, n("start_games"), unique_games,
              "games through the start position can't exceed the month's games"),
        Check("results_within_games", n("results_over_games") == 0, n("results_over_games"), 0,
              "white + draws + black <= games in every row"),
        Check("moves_replay", unparsed * 100 <= pairs, unparsed, pairs // 100,
              "at most 1 % of kept move sequences fail to replay"),
        Check("start_coverage", n("start_games") * 100 >= unique_games * 98, n("start_games"), unique_games,
              "first moves played fewer than MIN_GAMES times drop out; normally < 2 %", blocking=False),
    ]


# ---------------------------------------------------------------------------- Spark side

def moves_column(F, movetext):
    """The first MAX_PLIES moves (SAN) of a PGN movetext, as an array."""
    cleaned = F.regexp_replace(F.regexp_replace(movetext, NOISE, " "), r"\s+", " ")
    tokens = F.filter(F.split(F.trim(cleaned), " "),
                      lambda m: (m != F.lit("")) & ~m.isin(*NOT_MOVES))
    return F.slice(tokens, 1, MAX_PLIES)


def _band(F, avg):
    band = F.when(avg.isNull(), F.lit(None).cast("int"))
    for bound in sorted(RATING_BANDS, reverse=True):
        band = band.when(avg >= bound, F.lit(bound))
    return band.otherwise(F.lit(0))


def month_games(raw_rows, curated_rows):
    """One row per unique game: moves (array), speed, result, average rating, band, game_id."""
    from pyspark.sql import functions as F

    moves = raw_rows.select(
        F.nullif(F.regexp_extract("Site", curated.SITE_PATTERN, 1), F.lit("")).alias("game_id"),
        "_source_file", moves_column(F, F.col("movetext")).alias("moves"))
    games = (curated_rows.where("NOT is_duplicate")
             .select("game_id", "_source_file", "speed", "result",
                     ((F.col("white_elo") + F.col("black_elo")) / 2).alias("avg_rating")))
    return (moves.join(games, ["game_id", "_source_file"])
                 .withColumn("rating_band", _band(F, F.col("avg_rating")))
                 .drop("_source_file"))


def _pairs(games):
    """Every (sequence before the move, move) of every game: one row per game and ply."""
    from pyspark.sql import functions as F

    return (games.select("*", F.posexplode("moves").alias("ply", "move"))
                 .withColumn("prefix", F.expr("array_join(slice(moves, 1, ply), ' ')"))
                 .withColumn("prefix_hash", F.xxhash64("prefix")))


def build(spark, games):
    """games (month_games) → the month's rows of TABLE (without month/lineage columns), and
    stats for the checks that only the build sees."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    # Pass 1: which (sequence, move) pairs reach MIN_GAMES (hash + move only: small rows).
    kept = (_pairs(games).groupBy("prefix_hash", "move").count()
            .where(F.col("count") >= MIN_GAMES).drop("count"))

    # Pass 2: details for the kept pairs only, by speed and band.
    cells = (_pairs(games).join(kept, ["prefix_hash", "move"])
             .groupBy("prefix", "move", "speed", "rating_band")
             .agg(F.count("*").alias("games"),
                  F.count_if(F.col("result") == "white").alias("white_wins"),
                  F.count_if(F.col("result") == "draw").alias("draws"),
                  F.count_if(F.col("result") == "black").alias("black_wins"),
                  F.count("avg_rating").alias("rated_games"),
                  F.sum("avg_rating").alias("rating_sum"),
                  F.max_by("game_id", "avg_rating").alias("example_game_id"),
                  F.max("avg_rating").alias("example_rating"))
             .cache())

    # Each kept sequence → its position, replayed once (vectorized UDF over distinct sequences).
    @F.pandas_udf(StringType())
    def to_position(prefixes):
        return prefixes.map(lambda p: replay(p.split()) if p else START)

    positions = (cells.select("prefix").distinct()
                 .withColumn("position", to_position("prefix")).cache())
    kept_pairs = cells.select("prefix", "move").distinct().count()
    unparsed = (cells.select("prefix", "move").distinct()
                .join(positions.where("position IS NULL"), "prefix").count())

    # Merge move orders that reach the same position.
    rows = (cells.join(positions.where("position IS NOT NULL"), "prefix")
            .groupBy("position", "move", "speed", "rating_band")
            .agg(F.sum("games").alias("games"), F.sum("white_wins").alias("white_wins"),
                 F.sum("draws").alias("draws"), F.sum("black_wins").alias("black_wins"),
                 F.sum("rated_games").alias("rated_games"), F.sum("rating_sum").alias("rating_sum"),
                 F.max_by("example_game_id", "example_rating").alias("example_game_id"),
                 F.max("example_rating").alias("example_rating")))
    return rows, {"kept_pairs": kept_pairs, "unparsed_pairs": unparsed}


def ensure_table(spark, sample_df) -> None:
    spark.sql("CREATE NAMESPACE IF NOT EXISTS lake.gold")
    if not wap.table_exists(spark, TABLE):
        ddl = sample_df._jdf.schema().toDDL()
        spark.sql(f"""
            CREATE TABLE {TABLE} ({ddl})
            USING iceberg
            PARTITIONED BY (month, bucket({BUCKETS}, position))
            TBLPROPERTIES ('format-version' = '2', 'write.parquet.compression-codec' = 'zstd',
                           'write.target-file-size-bytes' = '{TARGET_FILE_BYTES}',
                           'comment' = '{COMMENT}')""")  # COMMENT has no quotes
        spark.sql(f"ALTER TABLE {TABLE} {LAYOUT}")
    wap.ensure_main(spark, TABLE, sample_df.schema)
    wap.ensure_dq_table(spark)


def branch_stats(spark, branch: str, month: str) -> dict:
    return spark.sql(f"""
        SELECT count(*) AS rows,
               sum(CASE WHEN position = '{START}' THEN games END) AS start_games,
               count_if(white_wins + draws + black_wins > games) AS results_over_games
        FROM {TABLE} VERSION AS OF '{branch}'
        WHERE month = '{month}'""").first().asDict()


def pending_months(spark) -> list[str]:
    """curated months not built into the explorer yet, or rebuilt since."""
    return wap.pending(spark, curated.TABLE, TABLE)


def build_month(spark, month: str, run_id: str | None = None) -> list[Check]:
    from pyspark import StorageLevel
    from pyspark.sql import functions as F

    run_id = run_id or uuid.uuid4().hex
    lineage, curated_rows = wap.pinned_month(spark, curated.TABLE, month)
    _, raw_rows = wap.pinned_month(spark, raw.TABLE, month)
    unique_games = curated_rows.where("NOT is_duplicate").count()
    games = month_games(raw_rows, curated_rows).persist(StorageLevel.MEMORY_AND_DISK)
    joined = games.count()

    rows, stats = build(spark, games)
    df = (rows.select(F.lit(month).alias("month"), "*")
              .withColumn(LINEAGE, F.lit(lineage).cast("string"))
              .withColumn("_run_id", F.lit(run_id))
              .withColumn("_built_at", F.current_timestamp()))
    ensure_table(spark, df)
    audit = lambda branch: evaluate(  # noqa: E731
        {**branch_stats(spark, branch, month), **stats, "joined_games": joined}, unique_games)
    checks = wap.publish(spark, TABLE, month, df, audit, run_id)
    games.unpersist()
    spark.catalog.clearCache()
    return checks
