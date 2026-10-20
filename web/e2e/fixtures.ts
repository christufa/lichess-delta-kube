import type { Page, Route } from "@playwright/test";
import type {
  Months,
  Openings,
  RatingBucket,
  RatingDistribution,
  Speed,
  Summary,
  SummaryRow,
} from "../src/api/generated/types.gen";

// A made-up but lichess-shaped data set, so the tests run without the API (or Trino).

export const MONTHS: string[] = [
  ...Array.from({ length: 36 }, (_, i) => `${2013 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}`),
  "2026-08",
  "2026-09",
];

const SHARE: Record<Speed, number> = {
  ultrabullet: 0.01,
  bullet: 0.28,
  blitz: 0.45,
  rapid: 0.12,
  classical: 0.12,
  correspondence: 0.02,
};

function row(month: string, speed: string, games: number): SummaryRow {
  const white = Math.round(games * 0.498);
  const black = Math.round(games * 0.462);
  const draws = Math.round(games * 0.036);
  return {
    month,
    speed,
    games,
    players: Math.round(games * 0.11),
    white_wins: white,
    black_wins: black,
    draws,
    unfinished: games - white - black - draws,
    bot_games: Math.round(games * 0.004),
    avg_rating: 1550 + MONTHS.indexOf(month) * 2,
  };
}

export function summary(from: string, to: string): Summary {
  const rows: SummaryRow[] = [];
  MONTHS.filter((m) => m >= from && m <= to).forEach((month) => {
    const total = 100_000 + MONTHS.indexOf(month) * 150_000;
    rows.push(row(month, "all", total));
    for (const [speed, share] of Object.entries(SHARE)) rows.push(row(month, speed, Math.round(total * share)));
  });
  return { from_month: from, to_month: to, rows };
}

export function ratingDistribution(month: string, speed: Speed): RatingDistribution {
  const rows: RatingBucket[] = Array.from({ length: 40 }, (_, i) => {
    const bucket = 600 + i * 50;
    const players = Math.round(20_000 * Math.exp(-(((bucket - 1550) / 300) ** 2)));
    return { speed, rating_bucket: bucket, players, player_games: players * 9 };
  });
  rows.push({ speed, rating_bucket: null, players: 1234, player_games: 2000 });
  return { month, bucket_width: 50, rows };
}

const OPENING_NAMES = [
  ["B01", "Scandinavian Defense"],
  ["C00", "French Defense: Knight Variation"],
  ["A00", "Van't Kruijs Opening"],
  ["C20", "King's Pawn Game: Wayward Queen Attack"],
  ["D00", "Queen's Pawn Game: Accelerated London System"],
  ["B00", "Owen Defense"],
  ["C50", "Italian Game"],
  ["A40", "Horwitz Defense"],
  ["C44", "Scotch Game"],
  ["B20", "Sicilian Defense: Bowdler Attack"],
] as const;

export function openings(from: string, to: string, speed: Speed | null, band: number | null, limit: number): Openings {
  const list = OPENING_NAMES.slice(0, limit).map(([eco, opening], i) => {
    const games = 90_000 - i * 7_000;
    const white = Math.round(games * (0.47 + i * 0.005));
    const draws = Math.round(games * 0.04);
    const unfinished = Math.round(games * 0.005);
    return { eco, opening, games, white_wins: white, draws, black_wins: games - white - draws - unfinished, unfinished };
  });
  return {
    from_month: from,
    to_month: to,
    speed,
    rating_band: band,
    band_width: 200,
    total_games: 1_250_000,
    openings: list,
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

/** Answers /api/v1/* like the real API; `fail` makes the matching endpoints return 503. */
export async function mockApi(page: Page, opts: { fail?: RegExp } = {}) {
  await page.route("**/api/v1/**", (route) => {
    const url = new URL(route.request().url());
    const q = url.searchParams;
    if (opts.fail?.test(url.pathname)) {
      return json(route, { detail: "Trino is unavailable" }, 503);
    }
    switch (url.pathname) {
      case "/api/v1/months":
        return json(route, { months: MONTHS } satisfies Months);
      case "/api/v1/summary":
        return json(route, summary(q.get("from") ?? "", q.get("to") ?? ""));
      case "/api/v1/rating-distribution":
        return json(route, ratingDistribution(q.get("month") ?? "", (q.get("speed") ?? "blitz") as Speed));
      case "/api/v1/openings":
        return json(
          route,
          openings(
            q.get("from") ?? "",
            q.get("to") ?? "",
            (q.get("speed") as Speed | null) ?? null,
            q.has("rating_band") ? Number(q.get("rating_band")) : null,
            Number(q.get("limit") ?? 20),
          ),
        );
      default:
        return json(route, { detail: "Not Found" }, 404);
    }
  });
}
