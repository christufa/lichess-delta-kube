"""PGN row conversion for Spark workers."""
import json
import re
from datetime import datetime, timezone

import chess
import chess.pgn

_CLK_RE     = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")
_EVAL_RE    = re.compile(r"\[%eval (#?-?[\d.]+)\]")
_ANN_RE     = re.compile(r"\[%\w+[^\]]*\]")

_NAG_SYMBOLS: dict[int, str] = {1: "!", 2: "?", 3: "!!", 4: "??", 5: "!?", 6: "?!"}


def _parse_clk(comment: str) -> int | None:
    m = _CLK_RE.search(comment)
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + round(float(m.group(3)))


def _parse_eval(comment: str) -> tuple[int | None, int | None]:
    """Returns (eval_cp, eval_mate); one will always be None."""
    m = _EVAL_RE.search(comment)
    if not m:
        return None, None
    v = m.group(1)
    if v.startswith("#"):
        return None, int(v[1:])
    return round(float(v) * 100), None


def _parse_time_control(tc: str) -> tuple[int | None, int | None]:
    if not tc or tc in ("-", "?"):
        return None, None
    m = re.match(r"(\d+)(?:\+(\d+))?", tc)
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2) or 0)


def _int_or_none(s: str | None) -> int | None:
    if not s or s == "?":
        return None
    try:
        return int(str(s).lstrip("+"))
    except (ValueError, TypeError):
        return None


def _clean_comment(comment: str) -> str | None:
    """Strip [%clk ...] / [%eval ...] annotations; return remaining human text or None."""
    s = _ANN_RE.sub("", comment).strip()
    return s or None


def _game_to_row(game: chess.pgn.Game) -> dict:
    h = game.headers

    site    = h.get("Site") or ""
    game_id = site.rstrip("/").split("/")[-1] if site else None

    utc_date = h.get("UTCDate") or h.get("Date") or "?"
    utc_time = h.get("UTCTime") or "00:00:00"
    played_at = None
    if "?" not in utc_date:
        try:
            played_at = datetime.strptime(
                f"{utc_date} {utc_time}", "%Y.%m.%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    tc = h.get("TimeControl") or ""
    init_secs, inc_secs = _parse_time_control(tc)
    initial_fen = h.get("FEN") if h.get("SetUp") == "1" else None

    board = game.board()
    moves: list[dict] = []
    prev_clk: dict[str, int | None] = {"w": init_secs, "b": init_secs}
    inc = inc_secs or 0

    node = game
    while node.variations:
        node  = node.variations[0]
        color = "w" if board.turn == chess.WHITE else "b"
        san   = board.san(node.move)
        uci   = node.move.uci()
        board.push(node.move)

        comment  = node.comment or ""
        clk      = _parse_clk(comment)
        eval_cp, eval_mate = _parse_eval(comment)

        time_spent = None
        if clk is not None and prev_clk[color] is not None:
            time_spent = prev_clk[color] - clk + inc
        prev_clk[color] = clk

        nags = [_NAG_SYMBOLS.get(n, n) for n in sorted(node.nags)] if node.nags else None

        moves.append({
            "n":          node.ply(),
            "color":      color,
            "san":        san,
            "uci":        uci,
            "fen":        board.fen(),
            "clk":        clk,
            "time_spent": time_spent,
            "eval_cp":    eval_cp,
            "eval_mate":  eval_mate,
            "nags":       nags,
            "comment":    _clean_comment(comment),
        })

    return {
        "game_id":           game_id,
        "variant":           (h.get("Variant") or "standard").lower(),
        "event":             h.get("Event") or None,
        "site":              site or None,
        "white_username":    h.get("White") or None,
        "black_username":    h.get("Black") or None,
        "white_elo":         _int_or_none(h.get("WhiteElo")),
        "black_elo":         _int_or_none(h.get("BlackElo")),
        "white_rating_diff": _int_or_none(h.get("WhiteRatingDiff")),
        "black_rating_diff": _int_or_none(h.get("BlackRatingDiff")),
        "white_title":       h.get("WhiteTitle") or None,
        "black_title":       h.get("BlackTitle") or None,
        "white_team":        h.get("WhiteTeam") or None,
        "black_team":        h.get("BlackTeam") or None,
        "result":            h.get("Result") or None,
        "termination":       h.get("Termination") or None,
        "played_at":         played_at,
        "time_control":      tc or None,
        "initial_time_secs": init_secs,
        "increment_secs":    inc_secs,
        "eco":               h.get("ECO") or None,
        "opening":           h.get("Opening") or None,
        "initial_fen":       initial_fen,
        "ply_count":         len(moves),
        "pgn":               str(game),
        "moves":             json.dumps(moves, separators=(",", ":")),
    }
