import type { Speed } from "@/api/generated";
import { isSpeed } from "./speeds";

// URL search params are the page state, so a view can be shared or bookmarked. Anything
// invalid is dropped and the page falls back to its default.

const MONTH = /^\d{4}-(0[1-9]|1[0-2])$/;

export function parseMonth(value: unknown): string | undefined {
  return typeof value === "string" && MONTH.test(value) ? value : undefined;
}

export function parseSpeed(value: unknown): Speed | undefined {
  return isSpeed(value) ? value : undefined;
}

/** Lower bounds of the 200-point rating bands offered: 400 … 2800. */
export const RATING_BANDS = Array.from({ length: 13 }, (_, i) => 400 + i * 200);

export function parseBand(value: unknown): number | undefined {
  const n = typeof value === "string" && value !== "" ? Number(value) : value;
  return typeof n === "number" && Number.isInteger(n) && n >= 0 && n <= 4000 && n % 200 === 0
    ? n
    : undefined;
}

export type Metric = "games" | "players";

export interface OverviewSearch {
  from?: string;
  to?: string;
  metric?: Metric;
  month?: string;
  speed?: Speed;
}

export function validateOverviewSearch(search: Record<string, unknown>): OverviewSearch {
  return {
    from: parseMonth(search.from),
    to: parseMonth(search.to),
    metric: search.metric === "players" ? "players" : undefined,
    month: parseMonth(search.month),
    speed: parseSpeed(search.speed),
  };
}

export interface OpeningsSearch {
  from?: string;
  to?: string;
  speed?: Speed;
  band?: number;
}

export function validateOpeningsSearch(search: Record<string, unknown>): OpeningsSearch {
  return {
    from: parseMonth(search.from),
    to: parseMonth(search.to),
    speed: parseSpeed(search.speed),
    band: parseBand(search.band),
  };
}

/**
 * A month range within the months that have data: unknown months fall back to the
 * defaults (the last `defaultSpan` months), and a reversed range is swapped (the API
 * answers 400 to from > to).
 */
export function resolveRange(
  months: readonly string[],
  from: string | undefined,
  to: string | undefined,
  defaultSpan: number,
): { from: string; to: string } {
  const last = months.at(-1) ?? "";
  const defaultFrom = months[Math.max(0, months.length - defaultSpan)] ?? last;
  const f = from && months.includes(from) ? from : defaultFrom;
  const t = to && months.includes(to) ? to : last;
  return f <= t ? { from: f, to: t } : { from: t, to: f };
}

/** `month` if it has data, else the latest month. */
export function resolveMonth(months: readonly string[], month: string | undefined): string {
  return month && months.includes(month) ? month : (months.at(-1) ?? "");
}
