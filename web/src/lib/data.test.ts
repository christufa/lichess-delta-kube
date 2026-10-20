import { describe, expect, it } from "vitest";
import type { RatingBucket, SummaryRow } from "@/api/generated";
import {
  describeResults,
  medianBucket,
  monthKpis,
  ratingHistogram,
  resultsBySpeed,
  seriesBySpeed,
  speedShares,
} from "./data";

function row(month: string, speed: string | null, games: number, extra: Partial<SummaryRow> = {}): SummaryRow {
  return {
    month,
    speed,
    games,
    players: games / 10,
    white_wins: games / 2,
    draws: 0,
    black_wins: games / 2,
    unfinished: 0,
    bot_games: 0,
    avg_rating: 1500,
    ...extra,
  };
}

const rows: SummaryRow[] = [
  row("2015-01", "all", 300),
  row("2015-01", "blitz", 200),
  row("2015-01", "bullet", 100),
  row("2015-02", "all", 400, { bot_games: 4 }),
  row("2015-02", "blitz", 400),
  row("2015-02", null, 7), // unparseable time control: not a speed
];

describe("seriesBySpeed", () => {
  it("has one series per speed with data, in speed order, zero-filled", () => {
    const { months, series } = seriesBySpeed(rows, "games");
    expect(months).toEqual(["2015-01", "2015-02"]);
    expect(series).toEqual([
      { speed: "bullet", values: [100, 0] },
      { speed: "blitz", values: [200, 400] },
    ]);
  });

  it("reads the chosen metric", () => {
    expect(seriesBySpeed(rows, "players").series[1]?.values).toEqual([20, 40]);
  });
});

describe("speedShares", () => {
  it("makes each month add up to 1", () => {
    const { series } = speedShares(rows);
    expect(series.map((s) => s.values[0])).toEqual([1 / 3, 2 / 3]);
    expect(series.map((s) => s.values[1])).toEqual([0, 1]);
  });
});

describe("resultsBySpeed", () => {
  it("sums the range, all first, skipping speeds without games", () => {
    const out = resultsBySpeed(rows);
    expect(out.map((r) => r.speed)).toEqual(["all", "bullet", "blitz"]);
    expect(out[0]?.results).toEqual({ white: 350, draws: 0, black: 350, unfinished: 0, games: 700 });
  });
});

describe("monthKpis", () => {
  it("compares with the previous month that has data", () => {
    const k = monthKpis(rows, "2015-02");
    expect(k).toMatchObject({ games: 400, players: 40, botShare: 0.01, previousMonth: "2015-01" });
    expect(k?.gamesChange).toBeCloseTo(1 / 3);
  });

  it("has no change for the first month and nothing for an unknown one", () => {
    expect(monthKpis(rows, "2015-01")?.gamesChange).toBeNull();
    expect(monthKpis(rows, "2020-01")).toBeUndefined();
  });
});

describe("ratingHistogram", () => {
  const buckets: RatingBucket[] = [
    { speed: "blitz", rating_bucket: 1550, players: 30, player_games: 0 },
    { speed: "blitz", rating_bucket: 1500, players: 10, player_games: 0 },
    { speed: "blitz", rating_bucket: null, players: 5, player_games: 0 },
    { speed: "bullet", rating_bucket: 1500, players: 99, player_games: 0 },
  ];

  it("keeps one speed's rated buckets in order and counts the unrated", () => {
    expect(ratingHistogram(buckets, "blitz")).toEqual({
      buckets: [
        { rating: 1500, players: 10 },
        { rating: 1550, players: 30 },
      ],
      unratedPlayers: 5,
    });
  });

  it("finds the median player's bucket", () => {
    expect(medianBucket(ratingHistogram(buckets, "blitz").buckets)).toBe(1550);
    expect(medianBucket([])).toBeNull();
  });
});

describe("describeResults", () => {
  it("says the split, and unfinished games only when there are some", () => {
    expect(describeResults({ white: 50, draws: 10, black: 40, unfinished: 0, games: 100 })).toBe(
      "White wins 50.0%, draws 10.0%, black wins 40.0%",
    );
    expect(describeResults({ white: 50, draws: 10, black: 39, unfinished: 1, games: 100 })).toContain("unfinished 1.0%");
  });
});
