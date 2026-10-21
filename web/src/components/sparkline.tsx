import { useId } from "react";
import { cn } from "@/lib/utils";

/** A tiny trend line with a soft fill that draws itself in (decoration: the numbers are elsewhere). */
export function Sparkline({ values, color, className }: { values: readonly number[]; color: string; className?: string }) {
  const id = `spark-${useId().replace(/[^\w-]/g, "")}`;
  if (values.length < 2) return null;
  const min = Math.min(...values);
  const span = Math.max(...values) - min || 1;
  const points = values.map((v, i) => [(i / (values.length - 1)) * 200, 38 - ((v - min) / span) * 34] as const);
  const line = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  return (
    <svg viewBox="0 0 200 40" preserveAspectRatio="none" aria-hidden="true" className={cn("h-10 w-full overflow-visible", className)}>
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: color, stopOpacity: 0.3 }} />
          <stop offset="1" style={{ stopColor: color, stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      <path d={`${line} L200,40 L0,40 Z`} fill={`url(#${id})`} className="animate-in fade-in duration-1000" />
      <path
        d={line}
        pathLength={1}
        fill="none"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="sparkline-draw"
        style={{ stroke: color }}
      />
    </svg>
  );
}
