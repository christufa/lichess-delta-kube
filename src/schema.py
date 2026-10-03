"""Dataset columns shared by Delta insertion and HF export."""
# Match the existing HF columns; partition metadata stays in Delta only.
STRING_COLUMNS = "game_id variant event site white_username black_username white_title black_title white_team black_team result termination time_control eco opening initial_fen pgn moves".split()
INT_COLUMNS = "white_elo black_elo white_rating_diff black_rating_diff initial_time_secs increment_secs ply_count".split()
COLUMNS = STRING_COLUMNS + INT_COLUMNS + ["played_at"]
