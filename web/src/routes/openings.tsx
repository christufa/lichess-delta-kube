import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import type { Openings } from "@/api/generated";
import { getMonthsOptions, getOpeningsOptions } from "@/api/generated/@tanstack/react-query.gen";
import { FilterBar, LoadingBlock, QueryError, SelectField } from "@/components/common";
import { ResultsBar, ResultsLegend } from "@/components/results-bar";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { resultsOf } from "@/lib/data";
import { formatBand, formatInt, formatMonth, formatPercent, monthOptions } from "@/lib/format";
import { usePageTitle } from "@/lib/page";
import { RATING_BANDS, resolveRange, type OpeningsSearch } from "@/lib/search";
import { SPEED_LABELS, SPEEDS, isSpeed } from "@/lib/speeds";

const route = getRouteApi("/openings");

/** The latest month unless the URL says otherwise (the loaded months have a gap, 2016–2026-07). */
const DEFAULT_SPAN = 1;
const LIMIT = 25;
const ANY = "any";

export function OpeningsPage() {
  usePageTitle("Openings");
  const months = useQuery(getMonthsOptions());
  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">Openings</h1>
        <p className="text-muted-foreground text-sm">
          The most played openings for a speed and a rating band (the players' average rating), and how those
          games ended.
        </p>
      </div>
      {months.isPending ? (
        <LoadingBlock className="h-64" label="Loading months" />
      ) : months.isError ? (
        <QueryError error={months.error} onRetry={() => void months.refetch()} />
      ) : months.data.months.length === 0 ? (
        <p>No months have been loaded yet.</p>
      ) : (
        <OpeningsView months={months.data.months} />
      )}
    </div>
  );
}

function OpeningsView({ months }: { months: string[] }) {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const { from, to } = resolveRange(months, search.from, search.to, DEFAULT_SPAN);
  const { speed, band } = search;

  const openings = useQuery({
    ...getOpeningsOptions({
      query: { from, to, limit: LIMIT, ...(speed && { speed }), ...(band !== undefined && { rating_band: band }) },
    }),
    placeholderData: keepPreviousData,
  });

  const set = (patch: Partial<OpeningsSearch>) => {
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true, resetScroll: false });
  };

  const options = monthOptions(months);
  return (
    <>
      <FilterBar label="Openings filters">
        <SelectField label="From" value={from} options={options} onChange={(v) => {
            set({ from: v });
          }} />
        <SelectField label="To" value={to} options={options} onChange={(v) => {
            set({ to: v });
          }} />
        <SelectField
          label="Speed"
          value={speed ?? ANY}
          options={[{ value: ANY, label: "All speeds" }, ...SPEEDS.map((s) => ({ value: s, label: SPEED_LABELS[s] }))]}
          onChange={(v) => {
            set({ speed: isSpeed(v) ? v : undefined });
          }}
        />
        <SelectField
          label="Rating band"
          value={band === undefined ? ANY : String(band)}
          options={[
            { value: ANY, label: "All ratings" },
            ...RATING_BANDS.map((b) => ({ value: String(b), label: formatBand(b) })),
          ]}
          onChange={(v) => {
            set({ band: v === ANY ? undefined : Number(v) });
          }}
        />
      </FilterBar>

      {openings.isPending ? (
        <LoadingBlock className="h-96" label="Loading openings" />
      ) : openings.isError ? (
        <QueryError error={openings.error} onRetry={() => void openings.refetch()} />
      ) : (
        <div
          className="transition-opacity data-[stale=true]:opacity-60"
          data-stale={openings.isPlaceholderData}
          aria-busy={openings.isFetching}
        >
          <OpeningsTable data={openings.data} />
        </div>
      )}
    </>
  );
}

function selectionText(d: Openings): string {
  const range = d.from_month === d.to_month ? formatMonth(d.from_month) : `${formatMonth(d.from_month)} – ${formatMonth(d.to_month)}`;
  const speed = d.speed ? SPEED_LABELS[d.speed].toLowerCase() : "all";
  const band = d.rating_band === null ? "" : `, average rating ${formatBand(d.rating_band)}`;
  return `${formatInt(d.total_games)} ${speed} games${band}, ${range}`;
}

function OpeningsTable({ data }: { data: Openings }) {
  const covered = data.openings.reduce((s, o) => s + o.games, 0);
  return (
    <Card className="gap-4">
      <CardHeader className="gap-1">
        <CardTitle>
          <h2 className="text-base font-semibold">Top {data.openings.length} openings</h2>
        </CardTitle>
        <CardDescription>
          {selectionText(data)}
          {data.total_games > 0 && <>; these openings cover {formatPercent(covered, data.total_games)} of them.</>}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {data.openings.length === 0 ? (
          <p className="text-muted-foreground py-12 text-center text-sm">No games match these filters.</p>
        ) : (
          <>
            <ResultsLegend className="mb-3" />
            <table className="w-full table-fixed text-sm">
              <caption className="sr-only">Most played openings, with each one's results</caption>
              <thead>
                <tr className="text-muted-foreground border-b text-left text-xs">
                  <th scope="col" className="w-8 py-2 font-medium">
                    #
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    Opening
                  </th>
                  <th scope="col" className="w-20 py-2 text-right font-medium sm:w-28">
                    Games
                  </th>
                  <th scope="col" className="hidden w-48 py-2 pl-4 font-medium sm:table-cell md:w-64">
                    Results
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.openings.map((o, i) => {
                  const r = resultsOf(o);
                  return (
                    <tr key={`${o.eco ?? ""}|${o.opening ?? ""}`} className="border-b align-top last:border-0">
                      <td className="text-muted-foreground py-2.5 tabular-nums">{i + 1}</td>
                      <td className="min-w-0 py-2.5 pr-2">
                        <span className="block font-medium break-words">{o.opening ?? "Unknown opening"}</span>
                        <span className="text-muted-foreground text-xs">{o.eco ?? "no ECO code"}</span>
                        {/* On a phone the results bar moves under the name. */}
                        <ResultsBar results={r} className="mt-2 sm:hidden" />
                      </td>
                      <td className="py-2.5 text-right tabular-nums">
                        {formatInt(o.games)}
                        <span className="text-muted-foreground block text-xs">{formatPercent(o.games, data.total_games)}</span>
                      </td>
                      <td className="hidden py-2.5 pl-4 sm:table-cell">
                        <ResultsBar results={r} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </CardContent>
    </Card>
  );
}
