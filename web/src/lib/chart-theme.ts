import type { EChartsCoreOption } from "echarts/core";
import { useMemo, useSyncExternalStore } from "react";
import type { Speed } from "@/api/generated";
import { SPEED_COLOR_VARS, SPEEDS } from "./speeds";

export type ChartOption = EChartsCoreOption;

const darkQuery = "(prefers-color-scheme: dark)";
const reducedMotionQuery = "(prefers-reduced-motion: reduce)";

/** Whether a media query matches, kept current. */
function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(query);
      mql.addEventListener("change", onChange);
      return () => {
        mql.removeEventListener("change", onChange);
      };
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}

/** Light or dark, following the OS. */
export function useColorScheme(): "light" | "dark" {
  return useMediaQuery(darkQuery) ? "dark" : "light";
}

/** The OS asks for less motion: charts draw without animating. */
export function useReducedMotion(): boolean {
  return useMediaQuery(reducedMotionQuery);
}

/** The value of a CSS custom property from index.css, e.g. cssVar("--series-1"). */
function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export interface ChartColors {
  scheme: "light" | "dark";
  reducedMotion: boolean;
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
  const reducedMotion = useReducedMotion();
  return useMemo(() => {
    return {
      scheme, // the variables' values depend on it
      reducedMotion,
      text: cssVar("--chart-text"),
      grid: cssVar("--chart-grid"),
      surface: cssVar("--card"),
      tooltip: cssVar("--popover"),
      tooltipText: cssVar("--popover-foreground"),
      speed: Object.fromEntries(SPEEDS.map((s) => [s, cssVar(SPEED_COLOR_VARS[s])])) as Record<Speed, string>,
    };
  }, [scheme, reducedMotion]);
}

/** A hex color from index.css at some opacity, for gradient fills ("#3987e5", 0.3 → rgba). */
export function fade(color: string, alpha: number): string {
  const hex = /^#([0-9a-f]{6})$/i.exec(color)?.[1];
  if (!hex) return color;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r ?? 0}, ${g ?? 0}, ${b ?? 0}, ${alpha})`;
}

/** A top-to-bottom gradient fill for areas and bars. */
export function verticalGradient(top: string, bottom: string) {
  return {
    type: "linear",
    x: 0,
    y: 0,
    x2: 0,
    y2: 1,
    colorStops: [
      { offset: 0, color: top },
      { offset: 1, color: bottom },
    ],
  };
}

/**
 * Shared styling: recessive dashed grid, text in the text colors, a hover tooltip, and an
 * entry animation that eases out (none with reduced motion).
 */
export function baseOption(c: ChartColors) {
  return {
    animation: !c.reducedMotion,
    animationDuration: 1100,
    animationEasing: "cubicOut" as const,
    animationDurationUpdate: 600,
    animationEasingUpdate: "cubicInOut" as const,
    textStyle: { fontFamily: "inherit", color: c.text },
    grid: { left: 8, right: 16, top: 16, bottom: 8, outerBoundsMode: "same", outerBoundsContain: "axisLabel" },
    tooltip: {
      confine: true,
      backgroundColor: c.tooltip,
      borderColor: c.grid,
      borderRadius: 10,
      padding: [8, 12],
      textStyle: { color: c.tooltipText, fontFamily: "inherit" },
      extraCssText: "box-shadow: 0 12px 32px -12px rgb(0 0 0 / 0.35); backdrop-filter: blur(8px);",
    },
    legend: { show: false }, // legends are HTML above each chart
    xAxis: { axisLine: { lineStyle: { color: c.grid } }, axisTick: { show: false }, axisLabel: { color: c.text } },
    yAxis: { splitLine: { lineStyle: { color: c.grid, type: [4, 4] } }, axisLabel: { color: c.text } },
  };
}
