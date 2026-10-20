import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { ChartOption } from "@/lib/chart-theme";
import { cn } from "@/lib/utils";

// Only what the dashboard draws, so the bundle stays small.
echarts.use([LineChart, BarChart, GridComponent, LegendComponent, TooltipComponent, SVGRenderer]);

interface EChartProps {
  option: ChartOption;
  /** What the chart shows, for screen readers (the data table below it has the numbers). */
  label: string;
  className?: string;
}

/** An ECharts chart that follows its container's size. */
export function EChart({ option, label, className }: EChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);

  useEffect(() => {
    if (!el.current) return;
    const instance = echarts.init(el.current, null, { renderer: "svg" });
    chart.current = instance;
    const observer = new ResizeObserver(() => {
      instance.resize();
    });
    observer.observe(el.current);
    return () => {
      observer.disconnect();
      instance.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  return <div ref={el} role="img" aria-label={label} className={cn("h-72 w-full", className)} />;
}
