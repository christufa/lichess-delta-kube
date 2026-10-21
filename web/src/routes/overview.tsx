import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { Bot, Gauge, Swords, TrendingDown, TrendingUp, Users, type LucideIcon } from "lucide-react";
import { useMemo, type CSSProperties, type ReactNode } from "react";
import type { Speed, SummaryRow } from "@/api/generated";
import {
  getMonthsOptions,
  getRatingDistributionOptions,
  getSummaryOptions,
} from "@/api/generated/@tanstack/react-query.gen";
import { EChart } from "@/components/chart";
import {
  ChartCard,
  DataTable,
  FilterBar,
  LoadingBlock,
  QueryError,
  SelectField,
  Swatch,
  Toolbar,
} from "@/components/common";
import { Hero } from "@/components/hero";
import CountUp from "@/components/reactbits/count-up";
import SpotlightCard from "@/components/reactbits/spotlight-card";
import { ResultsBar, ResultsLegend } from "@/components/results-bar";
import { Sparkline } from "@/components/sparkline";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  baseOption,
  fade,
  useChartColors,
  verticalGradient,
  type ChartColors,
  type ChartOption,
} from "@/lib/chart-theme";
import {
  allSpeedsSeries,
  medianBucket,
  monthGaps,
  monthKpis,
  ratingHistogram,
  resultsBySpeed,
  seriesBySpeed,
  speedShares,
  type SpeedSeries,
} from "@/lib/data";
import { formatCompact, formatInt, formatMonth, formatPercent, monthOptions } from "@/lib/format";
import { usePageTitle } from "@/lib/page";
import { resolveMonth, resolveRange, type Metric } from "@/lib/search";
import { SPEED_LABELS, SPEEDS } from "@/lib/speeds";
import { cn } from "@/lib/utils";

const route = getRouteApi("/");

export function OverviewPage() {
  usePageTitle("Overview");
  const months = useQuery(getMonthsOptions());
  return (
    <div className="space-y-6">
      {months.isPending ? (
        <>
          <h1 className="sr-only">Lichess, month by month</h1>
          <LoadingBlock className="h-96" label="Loading months" />
        </>
      ) : months.isError ? (
        <>
          <h1 className="text-3xl font-semibold tracking-tight">Lichess, month by month</h1>
          <QueryError error={months.error} onRetry={() => void months.refetch()} />
        </>
      ) : months.data.months.length === 0 ? (
        <>
          <h1 className="text-3xl font-semibold tracking-tight">Lichess, month by month</h1>
          <p>No months have been loaded yet.</p>
        </>
      ) : (
        <>
          <Hero months={months.data.months} />
          <Overview months={months.data.months} />
        </>
      )}
    </div>
  );
}

function Overview({ months }: { months: string[] }) {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const { from, to } = resolveRange(months, search.from, search.to, months.length);
  const metric: Metric = search.metric ?? "games";

  const summary = useQuery({
    ...getSummaryOptions({ query: { from, to } }),
    placeholderData: keepPreviousData,
  });

  const set = (patch: Partial<typeof search>) => {
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true, resetScroll: false });
  };

  const options = monthOptions(months);
  return (
    <>
      <Toolbar>
        <FilterBar label="Date range">
          <SelectField label="From" value={from} options={options} onChange={(v) => {
              set({ from: v });
            }} />
          <SelectField label="To" value={to} options={options} onChange={(v) => {
              set({ to: v });
            }} />
          <p className="text-muted-foreground col-span-2 self-center text-xs sm:ml-auto sm:max-w-xs sm:text-right">
            The charts cover this range; the headline numbers are for its last month.
          </p>
        </FilterBar>
      </Toolbar>

      {summary.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          <LoadingBlock className="h-40 md:col-span-2" label="Loading the summary" />
          <LoadingBlock className="h-72" />
          <LoadingBlock className="h-72" />
        </div>
      ) : summary.isError ? (
        <QueryError error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <div
          className="grid gap-4 transition-opacity md:grid-cols-2 data-[stale=true]:opacity-60"
          data-stale={summary.isPlaceholderData}
          aria-busy={summary.isFetching}
        >
          <Kpis rows={summary.data.rows} month={to} />
          <TrendCard
            rows={summary.data.rows}
            metric={metric}
            onMetric={(m) => {
              set({ metric: m === "players" ? "players" : undefined });
            }}
          />
          <SpeedMixCard rows={summary.data.rows} />
          <ResultsCard rows={summary.data.rows} from={from} to={to} />
        </div>
      )}

      <RatingCard months={months} month={resolveMonth(months, search.month ?? to)} speed={search.speed ?? "blitz"} onChange={set} />
    </>
  );
}

interface Tile {
  label: string;
  icon: LucideIcon;
  /** The value, counted up to when it scrolls into view. */
  value: ReactNode;
  title?: string;
  note?: ReactNode;
  trend: number[];
  color: string;
}

function Kpis({ rows, month }: { rows: SummaryRow[]; month: string }) {
  const k = monthKpis(rows, month);
  if (!k) return null;
  const up = (k.gamesChange ?? 0) >= 0;
  const change =
    k.gamesChange === null || k.previousMonth === null ? undefined : (
      <span
        className={cn(
          "inline-flex items-center gap-1 font-medium",
          up ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400",
        )}
      >
        {up ? <TrendingUp aria-hidden="true" className="size-3.5" /> : <TrendingDown aria-hidden="true" className="size-3.5" />}
        {`${up ? "+" : "−"}${formatPercent(Math.abs(k.gamesChange), 1)} vs ${formatMonth(k.previousMonth)}`}
      </span>
    );
  const tiles: Tile[] = [
    {
      label: "Games",
      icon: Swords,
      value: <CountUp to={k.games} format="compact" decimals={1} mode="roll" />,
      title: formatInt(k.games),
      note: change,
      trend: allSpeedsSeries(rows, (r) => r.games),
      color: "var(--brand-1)",
    },
    {
      label: "Players",
      icon: Users,
      value: <CountUp to={k.players} format="compact" decimals={1} mode="roll" />,
      title: formatInt(k.players),
      note: "played at least one game",
      trend: allSpeedsSeries(rows, (r) => r.players),
      color: "var(--brand-2)",
    },
    {
      label: "Average rating",
      icon: Gauge,
      value: k.avgRating === null ? "–" : <CountUp to={Math.round(k.avgRating)} separator="," mode="roll" />,
      note: "mean of both players",
      trend: allSpeedsSeries(rows, (r) => r.avg_rating),
      color: "var(--brand-3)",
    },
    {
      label: "Games with a bot",
      icon: Bot,
      value: <CountUp to={k.botShare * 100} decimals={1} suffix="%" mode="roll" />,
      note: "a bot on either side",
      trend: allSpeedsSeries(rows, (r) => (r.games > 0 ? r.bot_games / r.games : 0)),
      color: "var(--series-4)",
    },
  ];
  return (
    <section aria-labelledby="kpi-heading" className="md:col-span-2">
      <div className="mb-3 flex items-baseline gap-3">
        <h2 id="kpi-heading" className="text-xl font-semibold tracking-tight">
          {formatMonth(month)}
        </h2>
        <p className="text-muted-foreground text-sm">headline numbers, every speed</p>
      </div>
      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((t, i) => (
          <li key={t.label} className="animate-rise" style={{ "--delay": `${i * 80}ms` } as CSSProperties}>
            <SpotlightCard className="bg-card text-card-foreground flex h-full flex-col rounded-2xl border p-4 shadow-lg shadow-black/5">
              <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                <span
                  aria-hidden="true"
                  className="grid size-6 place-items-center rounded-md"
                  style={{ backgroundColor: `color-mix(in oklch, ${t.color} 18%, transparent)`, color: t.color }}
                >
                  <t.icon className="size-3.5" />
                </span>
                {t.label}
              </p>
              <p className="mt-2 text-2xl font-semibold tracking-tight sm:text-3xl" title={t.title}>
                {t.value}
              </p>
              {t.note && <p className="text-muted-foreground mt-0.5 text-xs">{t.note}</p>}
              <Sparkline values={t.trend} color={t.color} className="mt-auto pt-3" />
            </SpotlightCard>
          </li>
        ))}
      </ul>
    </section>
  );
}

function speedLegend(series: SpeedSeries["series"], colors: ChartColors) {
  return (
    <ul className="text-muted-foreground mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Legend">
      {series.map((s) => (
        <li key={s.speed} className="flex items-center gap-1.5">
          <Swatch color={colors.speed[s.speed]} className="rounded-full" />
          {SPEED_LABELS[s.speed]}
        </li>
      ))}
    </ul>
  );
}

function TrendCard({ rows, metric, onMetric }: { rows: SummaryRow[]; metric: Metric; onMetric: (m: string) => void }) {
  const colors = useChartColors();
  const data = useMemo(() => seriesBySpeed(rows, metric), [rows, metric]);
  const option = useMemo<ChartOption>(() => {
    const base = baseOption(colors);
    // Shade the gaps in the loaded months, so a jump across years doesn't read as a spike.
    const gaps = monthGaps(data.months).map(([a, b]) => [
      { xAxis: formatMonth(a), name: "No data loaded" },
      { xAxis: formatMonth(b) },
    ]);
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "axis", valueFormatter: (v: unknown) => formatInt(Number(v)) },
      xAxis: { ...base.xAxis, type: "category", boundaryGap: false, data: data.months.map(formatMonth) },
      yAxis: { ...base.yAxis, type: "value", axisLabel: { ...base.yAxis.axisLabel, formatter: (v: number) => formatCompact(v) } },
      series: data.series.map((s, i) => ({
        name: SPEED_LABELS[s.speed],
        type: "line",
        data: s.values,
        smooth: 0.25,
        showSymbol: data.months.length === 1,
        symbolSize: 8,
        lineStyle: { width: 2.25 },
        areaStyle: { color: verticalGradient(fade(colors.speed[s.speed], 0.22), fade(colors.speed[s.speed], 0)) },
        color: colors.speed[s.speed],
        emphasis: { focus: "series" },
        animationDelay: i * 120,
        ...(i === 0 && gaps.length > 0
          ? {
              markArea: {
                silent: true,
                itemStyle: { color: fade(colors.text, 0.08) },
                label: { color: colors.text, fontSize: 11, position: "insideTop" },
                data: gaps,
              },
            }
          : {}),
      })),
    };
  }, [data, colors]);

  const noun = metric === "games" ? "Games" : "Players";
  return (
    <ChartCard
      className="md:col-span-2"
      delay={120}
      title={`${noun} per month, by speed`}
      description={
        metric === "games"
          ? "Each game counted once. Months without data are left out (shaded)."
          : "Distinct players who played at least one game at that speed. Months without data are left out (shaded)."
      }
      action={
        <ToggleGroup
          type="single"
          variant="outline"
          size="sm"
          value={metric}
          onValueChange={(v) => {
            if (v) onMetric(v);
          }}
          aria-label="Measure"
        >
          <ToggleGroupItem value="games">Games</ToggleGroupItem>
          <ToggleGroupItem value="players">Players</ToggleGroupItem>
        </ToggleGroup>
      }
      table={
        <DataTable
          caption={`${noun} per month, by speed`}
          columns={["Month", ...data.series.map((s) => SPEED_LABELS[s.speed])]}
          rows={data.months.map((m, i) => [formatMonth(m), ...data.series.map((s) => formatInt(s.values[i] ?? 0))])}
        />
      }
    >
      <span id="trends" className="absolute -top-40" aria-hidden="true" />
      {speedLegend(data.series, colors)}
      <EChart option={option} className="h-80" label={`Line chart: ${noun.toLowerCase()} per month for each speed`} />
    </ChartCard>
  );
}

function SpeedMixCard({ rows }: { rows: SummaryRow[] }) {
  const colors = useChartColors();
  const data = useMemo(() => speedShares(rows), [rows]);
  const option = useMemo<ChartOption>(() => {
    const base = baseOption(colors);
    return {
      ...base,
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        valueFormatter: (v: unknown) => `${Number(v).toFixed(1)}%`,
      },
      xAxis: { ...base.xAxis, type: "category", data: data.months.map(formatMonth) },
      yAxis: { ...base.yAxis, type: "value", max: 100, axisLabel: { ...base.yAxis.axisLabel, formatter: "{value}%" } },
      series: data.series.map((s, i) => ({
        name: SPEED_LABELS[s.speed],
        type: "bar",
        stack: "share",
        barCategoryGap: "24%",
        data: s.values.map((v) => Math.round(v * 1000) / 10),
        color: colors.speed[s.speed],
        itemStyle: { borderColor: colors.surface, borderWidth: 1, borderRadius: i === data.series.length - 1 ? [3, 3, 0, 0] : 0 },
        emphasis: { focus: "series" },
        animationDelay: (idx: number) => idx * 18 + i * 60,
      })),
    };
  }, [data, colors]);

  return (
    <ChartCard
      title="Speed mix"
      delay={200}
      description="Each speed's share of the month's games."
      table={
        <DataTable
          caption="Share of games by speed"
          columns={["Month", ...data.series.map((s) => SPEED_LABELS[s.speed])]}
          rows={data.months.map((m, i) => [formatMonth(m), ...data.series.map((s) => formatPercent(s.values[i] ?? 0, 1))])}
        />
      }
    >
      {speedLegend(data.series, colors)}
      <EChart option={option} label="Stacked bar chart: share of games by speed for each month" />
    </ChartCard>
  );
}

function ResultsCard({ rows, from, to }: { rows: SummaryRow[]; from: string; to: string }) {
  const data = resultsBySpeed(rows);
  const range = from === to ? formatMonth(from) : `${formatMonth(from)} – ${formatMonth(to)}`;
  return (
    <ChartCard
      title="How games end"
      delay={280}
      description={`White wins, draws and black wins by speed, ${range}.`}
      table={
        <DataTable
          caption="Results by speed"
          columns={["Speed", "Games", "White", "Draw", "Black", "Unfinished"]}
          rows={data.map(({ speed, results: r }) => [
            speed === "all" ? "All speeds" : SPEED_LABELS[speed],
            formatInt(r.games),
            formatPercent(r.white, r.games),
            formatPercent(r.draws, r.games),
            formatPercent(r.black, r.games),
            formatPercent(r.unfinished, r.games),
          ])}
        />
      }
    >
      <ResultsLegend className="mb-3" />
      <ul className="space-y-3">
        {data.map(({ speed, results: r }, i) => (
          <li key={speed} className="grid grid-cols-[7.5rem_1fr] items-start gap-3 text-sm">
            <span className={speed === "all" ? "font-semibold" : undefined}>
              {speed === "all" ? "All speeds" : SPEED_LABELS[speed]}
              <span className="text-muted-foreground block text-xs tabular-nums">{formatCompact(r.games)} games</span>
            </span>
            <ResultsBar results={r} delay={300 + i * 70} />
          </li>
        ))}
      </ul>
    </ChartCard>
  );
}

function RatingCard({
  months,
  month,
  speed,
  onChange,
}: {
  months: string[];
  month: string;
  speed: Speed;
  onChange: (patch: { month?: string; speed?: Speed }) => void;
}) {
  const colors = useChartColors();
  const dist = useQuery({
    ...getRatingDistributionOptions({ query: { month, speed: [speed] } }),
    placeholderData: keepPreviousData,
  });
  const hist = useMemo(() => (dist.data ? ratingHistogram(dist.data.rows, speed) : undefined), [dist.data, speed]);
  const median = hist ? medianBucket(hist.buckets) : null;
  const total = hist ? hist.buckets.reduce((s, b) => s + b.playerGames, 0) : 0;

  const option = useMemo<ChartOption>(() => {
    const base = baseOption(colors);
    const color = colors.speed[speed];
    return {
      ...base,
      tooltip: {
        ...base.tooltip,
        trigger: "axis",
        axisPointer: { type: "shadow" },
        formatter: (params: { name: string; value: number }[]) => {
          const p = params[0];
          if (!p) return "";
          const lo = Number(p.name);
          return `${lo}–${lo + 49}<br/><b>${formatInt(p.value)}</b> player-games`;
        },
      },
      xAxis: { ...base.xAxis, type: "category", data: hist?.buckets.map((b) => String(b.rating)) ?? [] },
      yAxis: { ...base.yAxis, type: "value", axisLabel: { ...base.yAxis.axisLabel, formatter: (v: number) => formatCompact(v) } },
      series: [
        {
          name: "Player-games",
          type: "bar",
          barCategoryGap: "12%",
          data: hist?.buckets.map((b) => b.playerGames) ?? [],
          color,
          itemStyle: { borderRadius: [4, 4, 0, 0], color: verticalGradient(color, fade(color, 0.35)) },
          emphasis: { itemStyle: { color } },
          animationDelay: (idx: number) => idx * 14,
          ...(median !== null && {
            markLine: {
              silent: true,
              symbol: "none",
              lineStyle: { color: colors.text, type: [4, 4], width: 1 },
              label: { formatter: "median", color: colors.text, fontSize: 11 },
              data: [{ xAxis: String(median) }],
            },
          }),
        },
      ],
    };
  }, [hist, speed, colors, median]);

  return (
    <ChartCard
      title="Rating distribution"
      delay={120}
      description={
        hist && total > 0
          ? `Players' ratings in ${formatInt(Math.round(total / 2))} rated ${SPEED_LABELS[speed].toLowerCase()} games in ${formatMonth(month)}, in 50-point steps: each game counts once for each player, at their rating then. The median is ${median}–${(median ?? 0) + 49}.${hist.unratedPlayerGames > 0 ? ` ${formatInt(hist.unratedPlayerGames)} more had no rating.` : ""}`
          : `Players' ratings per game in 50-point steps.`
      }
      action={
        <FilterBar label="Rating distribution filters">
          <SelectField
            label="Month"
            value={month}
            options={monthOptions(months)}
            onChange={(v) => {
              onChange({ month: v });
            }}
          />
          <SelectField
            label="Speed"
            value={speed}
            options={SPEEDS.map((s) => ({ value: s, label: SPEED_LABELS[s] }))}
            onChange={(v) => {
              onChange({ speed: v as Speed });
            }}
          />
        </FilterBar>
      }
      table={
        hist && (
          <DataTable
            caption="Player-games by rating"
            columns={["Rating", "Player-games"]}
            rows={hist.buckets.map((b) => [`${b.rating}–${b.rating + 49}`, formatInt(b.playerGames)])}
          />
        )
      }
    >
      {dist.isPending ? (
        <LoadingBlock className="h-80" label="Loading the rating distribution" />
      ) : dist.isError ? (
        <QueryError error={dist.error} onRetry={() => void dist.refetch()} />
      ) : hist && hist.buckets.length > 0 ? (
        <EChart
          option={option}
          className="h-80"
          label={`Bar chart: ${SPEED_LABELS[speed].toLowerCase()} player-games by rating in ${formatMonth(month)}`}
        />
      ) : (
        <p className="text-muted-foreground py-12 text-center text-sm">
          No rated {SPEED_LABELS[speed].toLowerCase()} games in {formatMonth(month)}.
        </p>
      )}
    </ChartCard>
  );
}
