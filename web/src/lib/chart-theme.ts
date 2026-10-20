import type { EChartsCoreOption } from "echarts/core";
import { useMemo, useSyncExternalStore } from "react";
import type { Speed } from "@/api/generated";
import { SPEED_COLOR_VARS, SPEEDS } from "./speeds";

export type ChartOption = EChartsCoreOption;

const darkQuery = "(prefers-color-scheme: dark)";

function subscribe(onChange: () => void) {
  const mql = window.matchMedia(darkQuery);
  mql.addEventListener("change", onChange);
  return () => {
    mql.removeEventListener("change", onChange);
  };
}

/** Light or dark, following the OS. */
export function useColorScheme(): "light" | "dark" {
  return useSyncExternalStore(
    subscribe,
    () => (window.matchMedia(darkQuery).matches ? "dark" : "light"),
    () => "light",
  );
}

/** The value of a CSS custom property from index.css, e.g. cssVar("--series-1"). */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface ChartColors {
  scheme: "light" | "dark";
  text: string;
  grid: string;
  surface: string;
  tooltip: string;
  tooltipText: string;
  speed: Record<Speed, string>;
}

/**
 * The chart colors from index.css (ECharts draws with concrete colors, not CSS variables),
 * re-read when the OS switches between light and dark.
 */
export function useChartColors(): ChartColors {
  const scheme = useColorScheme();
  return useMemo(() => {
    return {
      scheme, // the variables' values depend on it
      text: cssVar("--chart-text"),
      grid: cssVar("--chart-grid"),
      surface: cssVar("--card"),
      tooltip: cssVar("--popover"),
      tooltipText: cssVar("--popover-foreground"),
      speed: Object.fromEntries(SPEEDS.map((s) => [s, cssVar(SPEED_COLOR_VARS[s])])) as Record<Speed, string>,
    };
  }, [scheme]);
}

/** Shared styling: recessive grid and axes, text in the text colors, a hover tooltip. */
export function baseOption(c: ChartColors) {
  return {
    animationDuration: 300,
    textStyle: { fontFamily: "inherit", color: c.text },
    grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: "same", outerBoundsContain: "axisLabel" },
    tooltip: {
      confine: true,
      backgroundColor: c.tooltip,
      borderColor: c.grid,
      textStyle: { color: c.tooltipText },
    },
    legend: { show: false }, // legends are HTML above each chart
    xAxis: { axisLine: { lineStyle: { color: c.grid } }, axisTick: { show: false }, axisLabel: { color: c.text } },
    yAxis: { splitLine: { lineStyle: { color: c.grid } }, axisLabel: { color: c.text } },
  };
}
