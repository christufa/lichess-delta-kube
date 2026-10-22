import { Chess } from "chess.js";
import type { Speed } from "@/api/generated";
import { parseMonth, parseSpeed } from "./search";

// The opening explorer's state lives in the URL like the other pages: the line played from the
// start (?moves=e4,c5,Nf3), so any position can be shared, plus the filters. The board's FEN is
// derived from the moves; the API keys positions on the FEN's first three fields.

/** The explorer table's rating bands (players' average rating): lower bounds, 0 = under 1200. */
export const EXPLORER_BANDS = [0, 1200, 1600, 2000, 2400] as const;
export type ExplorerBand = (typeof EXPLORER_BANDS)[number];

export function formatExplorerBand(band: ExplorerBand): string {
  if (band === 0) return "Under 1200";
  if (band === 2400) return "2400+";
  return `${band}–${band + 399}`;
}

export function parseExplorerBand(value: unknown): ExplorerBand | undefined {
  const n = typeof value === "string" && value !== "" ? Number(value) : value;
  return EXPLORER_BANDS.find((b) => b === n);
}

/** The data covers each game's first 16 plies. */
export const MAX_PLIES = 16;

export interface Line {
  /** The legal moves (SAN, as chess.js writes them) from the start. */
  moves: string[];
  fen: string;
  turn: "w" | "b";
}

/** Replay moves from the start, stopping at the first one that isn't legal. */
export function replay(moves: readonly string[]): Line {
  const chess = new Chess();
  const played: string[] = [];
  for (const san of moves) {
    try {
      played.push(chess.move(san).san);
    } catch {
      break;
    }
  }
  return { moves: played, fen: chess.fen(), turn: chess.turn() };
}

/** The line after one more move (SAN, or a from/to drag; pawns promote to a queen), or null if illegal. */
export function extend(
  moves: readonly string[],
  move: string | { from: string; to: string },
): string[] | null {
  const chess = new Chess();
  for (const san of moves) chess.move(san);
  try {
    const m = chess.move(typeof move === "string" ? move : { ...move, promotion: "q" });
    return [...moves, m.san];
  } catch {
    return null;
  }
}

/** "1. e4 c5 2. Nf3": the line in move-number notation, one entry per ply. */
export function numbered(moves: readonly string[]): { ply: number; label: string; san: string }[] {
  return moves.map((san, i) => ({
    ply: i + 1,
    san,
    label: i % 2 === 0 ? `${i / 2 + 1}. ${san}` : san,
  }));
}

export function parseMoves(value: unknown): string[] {
  if (typeof value !== "string" || value === "") return [];
  return replay(value.split(/[,\s]+/).filter(Boolean)).moves;
}

export interface ExplorerSearch {
  moves?: string;
  from?: string;
  to?: string;
  speed?: Speed;
  band?: ExplorerBand;
}

export function validateExplorerSearch(search: Record<string, unknown>): ExplorerSearch {
  const moves = parseMoves(search.moves);
  return {
    moves: moves.length > 0 ? moves.join(",") : undefined,
    from: parseMonth(search.from),
    to: parseMonth(search.to),
    speed: parseSpeed(search.speed),
    band: parseExplorerBand(search.band),
  };
}
