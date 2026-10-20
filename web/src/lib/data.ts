import type { RatingBucket, Speed, SummaryRow } from "@/api/generated";
import { formatPercent } from "./format";
import { SPEEDS } from "./speeds";

// Pure reshaping of API responses for the charts and tables (unit-tested).

export interface Results {
  white: number;
  draws: number;
  black: number;
  unfinished: number;
  games: number;
}

interface WithResults {
  white_wins: number;
  draws: number;
  black_wins: number;
  unfinished: number;
  games: number;
}

export function resultsOf(row: WithResults): Results {
  return {
    white: row.white_wins,
    draws: row.draws,
    black: row.black_wins,
    unfinished: row.unfinished,
    games: row.games,
  };
}

export interface SpeedSeries {
  months: string[];
  series: { speed: Speed; values: number[] }[];
}

/** Months present in the rows, in order. */
export function monthsOf(rows: readonly SummaryRow[]): string[] {
  return [...new Set(rows.map((r) => r.month))].sort();
}

/** One series per speed that has games: `metric` for each month (0 where it has no row). */
export function seriesBySpeed(rows: readonly SummaryRow[], metric: "games" | "players"): SpeedSeries {
  const months = monthsOf(rows);
  const index = new Map(months.map((m, i) => [m, i]));
  const bySpeed = new Map<string, number[]>(SPEEDS.map((s) => [s, months.map(() => 0)]));
  for (const row of rows) {
    const values = row.speed === null ? undefined : bySpeed.get(row.speed);
    const i = index.get(row.month);
    if (values && i !== undefined) values[i] = row[metric];
  }
  return {
    months,
    series: SPEEDS.map((speed) => ({ speed, values: bySpeed.get(speed) ?? [] })).filter((s) =>
      s.values.some((v) => v > 0),
    ),
  };
}

/** Each speed's share of the month's games (0–1); a month's shares add up to 1. */
export function speedShares(rows: readonly SummaryRow[]): SpeedSeries {
  const { months, series } = seriesBySpeed(rows, "games");
  const totals = months.map((_, i) => series.reduce((sum, s) => sum + (s.values[i] ?? 0), 0));
  return {
    months,
    series: series.map((s) => ({
      speed: s.speed,
      values: s.values.map((v, i) => {
        const total = totals[i] ?? 0;
        return total > 0 ? v / total : 0;
      }),
    })),
  };
}

/** Results summed over the range: every game (`all`), then each speed with games. */
export function resultsBySpeed(rows: readonly SummaryRow[]): { speed: Speed | "all"; results: Results }[] {
  const out: { speed: Speed | "all"; results: Results }[] = [];
  for (const speed of ["all", ...SPEEDS] as const) {
    const total: Results = { white: 0, draws: 0, black: 0, unfinished: 0, games: 0 };
    for (const row of rows) {
      if (row.speed !== speed) continue;
      total.white += row.white_wins;
      total.draws += row.draws;
      total.black += row.black_wins;
      total.unfinished += row.unfinished;
      total.games += row.games;
    }
    if (total.games > 0) out.push({ speed, results: total });
  }
  return out;
}

export interface MonthKpis {
  month: string;
  games: number;
  players: number;
  avgRating: number | null;
  botShare: number;
  /** Change in games against the previous month with data; null for the first month. */
  gamesChange: number | null;
  previousMonth: string | null;
}

/** Headline numbers for one month (every speed: the `all` rows). */
export function monthKpis(rows: readonly SummaryRow[], month: string): MonthKpis | undefined {
  const all = rows.filter((r) => r.speed === "all").sort((a, b) => a.month.localeCompare(b.month));
  const i = all.findIndex((r) => r.month === month);
  const row = all[i];
  if (!row) return undefined;
  const prev = i > 0 ? all[i - 1] : undefined;
  return {
    month,
    games: row.games,
    players: row.players,
    avgRating: row.avg_rating,
    botShare: row.games > 0 ? row.bot_games / row.games : 0,
    gamesChange: prev && prev.games > 0 ? row.games / prev.games - 1 : null,
    previousMonth: prev?.month ?? null,
  };
}

/**
 * One speed's rated buckets in order, counted in player-games: each game counts once per
 * player, at that player's rating in that game. These add up (to twice the games); distinct
 * players per bucket don't, because a player whose rating moves appears in several buckets.
 */
export function ratingHistogram(
  rows: readonly RatingBucket[],
  speed: Speed,
): { buckets: { rating: number; playerGames: number }[]; unratedPlayerGames: number } {
  const own = rows.filter((r) => r.speed === speed);
  const buckets = own
    .flatMap((r) => (r.rating_bucket === null ? [] : [{ rating: r.rating_bucket, playerGames: r.player_games }]))
    .sort((a, b) => a.rating - b.rating);
  const unratedPlayerGames = own.filter((r) => r.rating_bucket === null).reduce((s, r) => s + r.player_games, 0);
  return { buckets, unratedPlayerGames };
}

/** The bucket holding the median player-game, or null without data. */
export function medianBucket(buckets: readonly { rating: number; playerGames: number }[]): number | null {
  const total = buckets.reduce((s, b) => s + b.playerGames, 0);
  if (total === 0) return null;
  let seen = 0;
  for (const b of buckets) {
    seen += b.playerGames;
    if (seen * 2 >= total) return b.rating;
  }
  return null;
}

/** "White wins 49.8%, draws 3.9%, black wins 45.1%" (+ unfinished when there are any). */
export function describeResults(r: Results): string {
  const parts = [
    `White wins ${formatPercent(r.white, r.games)}`,
    `draws ${formatPercent(r.draws, r.games)}`,
    `black wins ${formatPercent(r.black, r.games)}`,
  ];
  if (r.unfinished > 0) parts.push(`unfinished ${formatPercent(r.unfinished, r.games)}`);
  return parts.join(", ");
}
