import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, LegendComponent, MarkAreaComponent, MarkLineComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { SVGRenderer } from "echarts/renderers";
import { useEffect, useRef } from "react";
import type { EChartProps } from "@/components/chart";
import { cn } from "@/lib/utils";

// Only what the dashboard draws, so the bundle stays small. Loaded lazily (chart.tsx).
echarts.use([
  LineChart,
  BarChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
  SVGRenderer,
]);

/** An ECharts chart that follows its container's size and first draws when scrolled into view. */
export default function EChart({ option, label, className }: EChartProps) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  const latest = useRef(option);

  useEffect(() => {
    latest.current = option;
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    let instance: echarts.ECharts | null = null;
    const resize = new ResizeObserver(() => {
      instance?.resize();
    });
    // Drawing on first sight plays the entry animation where it can be seen.
    const seen = new IntersectionObserver(
      (entries) => {
        if (instance || !entries.some((e) => e.isIntersecting)) return;
        instance = echarts.init(node, null, { renderer: "svg" });
        chart.current = instance;
        instance.setOption(latest.current, { notMerge: true });
        resize.observe(node);
        seen.disconnect();
      },
      { rootMargin: "0px 0px -10% 0px" },
    );
    seen.observe(node);
    return () => {
      seen.disconnect();
      resize.disconnect();
      instance?.dispose();
      chart.current = null;
    };
  }, []);

  return <div ref={el} role="img" aria-label={label} className={cn("h-72 w-full", className)} />;
}
