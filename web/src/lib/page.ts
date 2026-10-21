import { useEffect } from "react";
import { formatMonth, nextMonth } from "./format";

/** Sets the tab title for a page. */
export function usePageTitle(title: string) {
  useEffect(() => {
    document.title = `${title} · Lichess lakehouse`;
  }, [title]);
}

/** The contiguous runs of months with data: "Jan 2013 – Dec 2015, Aug 2026 – Sep 2026". */
export function coverage(months: readonly string[]): string {
  const runs: [string, string][] = [];
  for (const m of months) {
    const last = runs.at(-1);
    if (last && nextMonth(last[1]) === m) last[1] = m;
    else runs.push([m, m]);
  }
  return runs.map(([a, b]) => (a === b ? formatMonth(a) : `${formatMonth(a)} – ${formatMonth(b)}`)).join(", ");
}
