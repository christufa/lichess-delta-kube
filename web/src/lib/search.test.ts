import { describe, expect, it } from "vitest";
import { formatBand, formatCompact, formatMonth, formatPercent, monthOptions } from "./format";
import { coverage } from "./page";
import { parseBand, resolveMonth, resolveRange, validateOpeningsSearch, validateOverviewSearch } from "./search";

const months = ["2015-10", "2015-11", "2015-12", "2026-08", "2026-09"];

describe("search params", () => {
  it("keeps valid values and drops the rest", () => {
    expect(validateOverviewSearch({ from: "2015-10", to: "2015-13", metric: "players", speed: "blitz" })).toEqual({
      from: "2015-10",
      to: undefined,
      metric: "players",
      month: undefined,
      speed: "blitz",
    });
    expect(validateOverviewSearch({ metric: "games", speed: "turbo" })).toMatchObject({ metric: undefined, speed: undefined });
    expect(validateOpeningsSearch({ band: "1400" }).band).toBe(1400);
  });

  it("accepts only 200-point rating bands", () => {
    expect(parseBand(1400)).toBe(1400);
    expect(parseBand("1500")).toBeUndefined();
    expect(parseBand(-200)).toBeUndefined();
    expect(parseBand("")).toBeUndefined();
  });

  it("resolves a range within the months with data", () => {
    expect(resolveRange(months, undefined, undefined, 2)).toEqual({ from: "2026-08", to: "2026-09" });
    expect(resolveRange(months, undefined, undefined, 99)).toEqual({ from: "2015-10", to: "2026-09" });
    expect(resolveRange(months, "2015-11", "2015-12", 1)).toEqual({ from: "2015-11", to: "2015-12" });
    expect(resolveRange(months, "2020-01", "2015-11", 1)).toEqual({ from: "2015-11", to: "2026-09" });
  });

  it("swaps a reversed range (the API would answer 400)", () => {
    expect(resolveRange(months, "2026-09", "2015-10", 1)).toEqual({ from: "2015-10", to: "2026-09" });
  });

  it("falls back to the latest month", () => {
    expect(resolveMonth(months, "2015-11")).toBe("2015-11");
    expect(resolveMonth(months, "2016-01")).toBe("2026-09");
  });
});

describe("format", () => {
  it("formats months, numbers and bands", () => {
    expect(formatMonth("2015-12")).toBe("Dec 2015");
    expect(formatCompact(12_345_678)).toBe("12.3M");
    expect(formatPercent(1, 3)).toBe("33.3%");
    expect(formatPercent(1, 0)).toBe("–");
    expect(formatBand(1400)).toBe("1400–1599");
    expect(monthOptions(["2015-11", "2015-12"])[0]).toEqual({ value: "2015-12", label: "Dec 2015" });
  });

  it("describes the months loaded as contiguous runs", () => {
    expect(coverage(months)).toBe("Oct 2015 – Dec 2015, Aug 2026 – Sep 2026");
    expect(coverage(["2015-12"])).toBe("Dec 2015");
  });
});
