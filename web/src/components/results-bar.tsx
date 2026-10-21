import type { CSSProperties } from "react";
import { Swatch } from "@/components/common";
import { describeResults, type Results } from "@/lib/data";
import { formatPercent } from "@/lib/format";
import { cn } from "@/lib/utils";

const PARTS = [
  { key: "white", label: "White wins", color: "var(--result-white)" },
  { key: "draws", label: "Draws", color: "var(--result-draw)" },
  { key: "black", label: "Black wins", color: "var(--result-black)" },
  { key: "unfinished", label: "Unfinished", color: "var(--result-unfinished)" },
] as const;

/** White / draw / black (/ unfinished) as one 100% bar, with the split as text. */
export function ResultsBar({
  results,
  className,
  showLabels = true,
  delay = 0,
}: {
  results: Results;
  className?: string;
  showLabels?: boolean;
  /** When the bar starts growing in, in ms. */
  delay?: number;
}) {
  const { games } = results;
  return (
    <div className={cn("min-w-0", className)}>
      <div
        className="ring-border animate-grow-x flex h-3 w-full gap-[2px] overflow-hidden rounded-full ring-1"
        style={{ "--delay": `${delay}ms` } as CSSProperties}
        role="img"
        aria-label={describeResults(results)}
      >
        {PARTS.map((p) =>
          results[p.key] > 0 ? (
            <div key={p.key} style={{ width: `${(results[p.key] / games) * 100}%`, backgroundColor: p.color }} />
          ) : null,
        )}
      </div>
      {showLabels && (
        <div aria-hidden="true" className="text-muted-foreground mt-1 flex justify-between text-xs tabular-nums">
          <span>{formatPercent(results.white, games)}</span>
          <span>{formatPercent(results.draws, games)}</span>
          <span>{formatPercent(results.black, games)}</span>
        </div>
      )}
    </div>
  );
}

export function ResultsLegend({ className }: { className?: string }) {
  return (
    <ul className={cn("text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs", className)} aria-label="Legend">
      {PARTS.map((p) => (
        <li key={p.key} className="flex items-center gap-1.5">
          <Swatch color={p.color} />
          {p.label}
        </li>
      ))}
    </ul>
  );
}
