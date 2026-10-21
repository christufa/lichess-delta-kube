import { lazy, Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChartOption } from "@/lib/chart-theme";
import { cn } from "@/lib/utils";

// ECharts is most of the dashboard's JavaScript, so it loads in its own chunk while the
// page around it is already on screen.
const LazyEChart = lazy(() => import("@/components/echart"));

export interface EChartProps {
  option: ChartOption;
  /** What the chart shows, for screen readers (the data table below it has the numbers). */
  label: string;
  className?: string;
}

export function EChart(props: EChartProps) {
  return (
    <Suspense
      fallback={
        <div role="img" aria-label={props.label} className={cn("h-72 w-full", props.className)}>
          <Skeleton className="h-full w-full" />
        </div>
      }
    >
      <LazyEChart {...props} />
    </Suspense>
  );
}
