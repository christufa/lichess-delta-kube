import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { useMemo } from "react";
import type { Speed, SummaryRow } from "@/api/generated";
import {
  getMonthsOptions,
  getRatingDistributionOptions,
  getSummaryOptions,
} from "@/api/generated/@tanstack/react-query.gen";
import { EChart } from "@/components/chart";
import { baseOption, useChartColors, type ChartColors, type ChartOption } from "@/lib/chart-theme";
import {
  ChartCard,
  DataTable,
  FilterBar,
  LoadingBlock,
  QueryError,
  SelectField,
  Swatch,
} from "@/components/common";
import { ResultsBar, ResultsLegend } from "@/components/results-bar";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import {
  medianBucket,
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

const route = getRouteApi("/");

export function OverviewPage() {
  usePageTitle("Overview");
  const months = useQuery(getMonthsOptions());
  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Lichess, month by month</h1>
        <p className="text-muted-foreground text-sm">
          Every rated and casual game in the Lichess open database: how many were played, by whom, at what
          speed, and how they ended.
        </p>
      </div>
      {months.isPending ? (
        <LoadingBlock className="h-64" label="Loading months" />
      ) : months.isError ? (
        <QueryError error={months.error} onRetry={() => void months.refetch()} />
      ) : months.data.months.length === 0 ? (
        <p>No months have been loaded yet.</p>
      ) : (
        <Overview months={months.data.months} />
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
      <FilterBar label="Date range">
        <SelectField label="From" value={from} options={options} onChange={(v) => {
            set({ from: v });
          }} />
        <SelectField label="To" value={to} options={options} onChange={(v) => {
            set({ to: v });
          }} />
      </FilterBar>

      {summary.isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          <LoadingBlock className="h-24 md:col-span-2" label="Loading the summary" />
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

function Kpis({ rows, month }: { rows: SummaryRow[]; month: string }) {
  const k = monthKpis(rows, month);
  if (!k) return null;
  const change =
    k.gamesChange === null || k.previousMonth === null
      ? undefined
      : `${k.gamesChange >= 0 ? "+" : "−"}${formatPercent(Math.abs(k.gamesChange), 1)} vs ${formatMonth(k.previousMonth)}`;
  const tiles = [
    { label: "Games", value: formatCompact(k.games), title: formatInt(k.games), note: change },
    { label: "Players", value: formatCompact(k.players), title: formatInt(k.players) },
    {
      label: "Average rating",
      value: k.avgRating === null ? "–" : formatInt(Math.round(k.avgRating)),
      note: "mean of both players",
    },
    { label: "Games with a bot", value: formatPercent(k.botShare, 1) },
  ];
  return (
    <section aria-labelledby="kpi-heading" className="md:col-span-2">
      <h2 id="kpi-heading" className="text-muted-foreground mb-2 text-sm font-medium">
        {formatMonth(month)}
      </h2>
      <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((t) => (
          // A <div> per term/description group, styled like a Card (a <dl> allows only that).
          <div key={t.label} className="bg-card text-card-foreground rounded-xl border p-4 shadow-sm">
            <dt className="text-muted-foreground text-xs font-medium">{t.label}</dt>
            <dd className="text-2xl font-semibold tabular-nums" title={t.title}>
              {t.value}
            </dd>
            {t.note && <dd className="text-muted-foreground text-xs">{t.note}</dd>}
          </div>
        ))}
      </dl>
    </section>
  );
}

function speedLegend(series: SpeedSeries["series"], colors: ChartColors) {
  return (
    <ul className="text-muted-foreground mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-label="Legend">
      {series.map((s) => (
        <li key={s.speed} className="flex items-center gap-1.5">
          <Swatch color={colors.speed[s.speed]} />
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
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "axis", valueFormatter: (v: unknown) => formatInt(Number(v)) },
      xAxis: { ...base.xAxis, type: "category", data: data.months.map(formatMonth) },
      yAxis: { ...base.yAxis, type: "value", axisLabel: { ...base.yAxis.axisLabel, formatter: (v: number) => formatCompact(v) } },
      series: data.series.map((s) => ({
        name: SPEED_LABELS[s.speed],
        type: "line",
        data: s.values,
        showSymbol: data.months.length === 1,
        symbolSize: 8,
        lineStyle: { width: 2 },
        color: colors.speed[s.speed],
        emphasis: { focus: "series" },
      })),
    };
  }, [data, colors]);

  const noun = metric === "games" ? "Games" : "Players";
  return (
    <ChartCard
      className="md:col-span-2"
      title={`${noun} per month, by speed`}
      description={
        metric === "games"
          ? "Each game counted once. Months without data are left out."
          : "Distinct players who played at least one game at that speed. Months without data are left out."
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
      {speedLegend(data.series, colors)}
      <EChart option={option} label={`Line chart: ${noun.toLowerCase()} per month for each speed`} />
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
      series: data.series.map((s) => ({
        name: SPEED_LABELS[s.speed],
        type: "bar",
        stack: "share",
        barCategoryGap: "20%",
        data: s.values.map((v) => Math.round(v * 1000) / 10),
        color: colors.speed[s.speed],
        itemStyle: { borderColor: colors.surface, borderWidth: 1 },
      })),
    };
  }, [data, colors]);

  return (
    <ChartCard
      title="Speed mix"
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
        {data.map(({ speed, results: r }) => (
          <li key={speed} className="grid grid-cols-[7.5rem_1fr] items-start gap-3 text-sm">
            <span className={speed === "all" ? "font-semibold" : undefined}>
              {speed === "all" ? "All speeds" : SPEED_LABELS[speed]}
              <span className="text-muted-foreground block text-xs tabular-nums">{formatCompact(r.games)} games</span>
            </span>
            <ResultsBar results={r} />
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
  const total = hist ? hist.buckets.reduce((s, b) => s + b.players, 0) : 0;

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
          return `${lo}–${lo + 49}<br/><b>${formatInt(p.value)}</b> players`;
        },
      },
      xAxis: { ...base.xAxis, type: "category", data: hist?.buckets.map((b) => String(b.rating)) ?? [] },
      yAxis: { ...base.yAxis, type: "value", axisLabel: { ...base.yAxis.axisLabel, formatter: (v: number) => formatCompact(v) } },
      series: [
        {
          name: "Players",
          type: "bar",
          barCategoryGap: "10%",
          data: hist?.buckets.map((b) => b.players) ?? [],
          color,
          itemStyle: { borderRadius: [3, 3, 0, 0] },
        },
      ],
    };
  }, [hist, speed, colors]);

  return (
    <ChartCard
      title="Rating distribution"
      description={
        hist && total > 0
          ? `${formatInt(total)} rated ${SPEED_LABELS[speed].toLowerCase()} players in ${formatMonth(month)}, by their rating in 50-point steps; the median player is at ${median}–${(median ?? 0) + 49}.${hist.unratedPlayers > 0 ? ` ${formatInt(hist.unratedPlayers)} more had no rating.` : ""}`
          : `Players by rating in 50-point steps.`
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
            caption="Players by rating"
            columns={["Rating", "Players"]}
            rows={hist.buckets.map((b) => [`${b.rating}–${b.rating + 49}`, formatInt(b.players)])}
          />
        )
      }
    >
      {dist.isPending ? (
        <LoadingBlock className="h-72" label="Loading the rating distribution" />
      ) : dist.isError ? (
        <QueryError error={dist.error} onRetry={() => void dist.refetch()} />
      ) : hist && hist.buckets.length > 0 ? (
        <EChart
          option={option}
          label={`Bar chart: ${SPEED_LABELS[speed].toLowerCase()} players by rating in ${formatMonth(month)}`}
        />
      ) : (
        <p className="text-muted-foreground py-12 text-center text-sm">
          No rated {SPEED_LABELS[speed].toLowerCase()} games in {formatMonth(month)}.
        </p>
      )}
    </ChartCard>
  );
}
