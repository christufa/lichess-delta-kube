import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import type { Openings } from "@/api/generated";
import { getMonthsOptions, getOpeningsOptions } from "@/api/generated/@tanstack/react-query.gen";
import { Crown, Handshake, Swords, Trophy } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { FilterBar, LoadingBlock, QueryError, SelectField, Toolbar } from "@/components/common";
import SpotlightCard from "@/components/reactbits/spotlight-card";
import { ResultsBar, ResultsLegend } from "@/components/results-bar";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { resultsOf, type Results } from "@/lib/data";
import { formatBand, formatInt, formatMonth, formatPercent, monthOptions } from "@/lib/format";
import { usePageTitle } from "@/lib/page";
import { RATING_BANDS, resolveRange, type OpeningsSearch } from "@/lib/search";
import { SPEED_LABELS, SPEEDS, isSpeed } from "@/lib/speeds";
import { cn } from "@/lib/utils";

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
      <div className="animate-rise space-y-3 pt-4 sm:pt-8">
        <p className="text-muted-foreground inline-flex items-center gap-2 text-xs font-medium tracking-widest uppercase">
          <Crown aria-hidden="true" className="text-brand-1 size-3.5" /> Opening book
        </p>
        <h1 className="text-4xl font-semibold tracking-tighter sm:text-6xl">
          <span className="text-gradient">Openings</span>
        </h1>
        <p className="text-muted-foreground max-w-2xl text-base sm:text-lg">
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
      <Toolbar>
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
      </Toolbar>

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
          <Highlights data={openings.data} />
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

type SortKey = "games" | "white" | "draws" | "black";

const SORTS: { key: SortKey; label: string }[] = [
  { key: "games", label: "Games" },
  { key: "white", label: "White wins" },
  { key: "draws", label: "Draws" },
  { key: "black", label: "Black wins" },
];

/** What a sort compares: the game count, or a result's share of the games. */
function sortValue(r: Results, key: SortKey): number {
  if (key === "games") return r.games;
  return r.games > 0 ? r[key] / r.games : 0;
}

/** Three standouts among the listed openings. */
function Highlights({ data }: { data: Openings }) {
  const rows = data.openings.map((o) => ({ o, r: resultsOf(o) }));
  const first = rows[0];
  if (!first) return null;
  const best = (key: SortKey) => rows.reduce((a, b) => (sortValue(b.r, key) > sortValue(a.r, key) ? b : a), first);
  const cards = [
    { icon: Trophy, label: "Most played", pick: best("games"), stat: (r: Results) => `${formatPercent(r.games, data.total_games)} of all games` },
    { icon: Swords, label: "Best for White", pick: best("white"), stat: (r: Results) => `White wins ${formatPercent(r.white, r.games)}` },
    { icon: Handshake, label: "Most drawish", pick: best("draws"), stat: (r: Results) => `Draws ${formatPercent(r.draws, r.games)}` },
  ];
  return (
    <section aria-label={`Highlights among the top ${rows.length}`} className="mb-4">
      <ul className="grid gap-3 md:grid-cols-3">
        {cards.map(({ icon: Icon, label, pick, stat }, i) => (
          <li key={label} className="animate-rise" style={{ "--delay": `${80 + i * 80}ms` } as CSSProperties}>
            <SpotlightCard className="bg-card flex h-full flex-col gap-1 rounded-2xl border p-4 shadow-lg shadow-black/5">
              <p className="text-muted-foreground flex items-center gap-2 text-xs font-medium">
                <Icon aria-hidden="true" className="text-brand-1 size-4" />
                {label}
              </p>
              <p className="font-semibold tracking-tight break-words">{pick.o.opening ?? "Unknown opening"}</p>
              <p className="text-muted-foreground text-xs">
                <span className="text-foreground font-mono">{pick.o.eco ?? "–"}</span> · {stat(pick.r)}
              </p>
            </SpotlightCard>
          </li>
        ))}
      </ul>
    </section>
  );
}

function OpeningsTable({ data }: { data: Openings }) {
  const [sort, setSort] = useState<SortKey>("games");
  const covered = data.openings.reduce((s, o) => s + o.games, 0);
  // The rank is popularity (the API's order); sorting only reorders the rows.
  const rows = useMemo(() => {
    const ranked = data.openings.map((o, i) => ({ o, r: resultsOf(o), rank: i + 1 }));
    return sort === "games" ? ranked : [...ranked].sort((a, b) => sortValue(b.r, sort) - sortValue(a.r, sort));
  }, [data, sort]);
  const most = Math.max(1, ...data.openings.map((o) => o.games));
  const sortLabel = SORTS.find((s) => s.key === sort)?.label.toLowerCase();
  return (
    <Card className="animate-rise gap-4 rounded-2xl shadow-lg shadow-black/5" style={{ "--delay": "200ms" } as CSSProperties}>
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
            <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
              <ResultsLegend />
              <ToggleGroup
                type="single"
                variant="outline"
                size="sm"
                value={sort}
                onValueChange={(v) => {
                  if (v) setSort(v as SortKey);
                }}
                aria-label="Sort by"
              >
                {SORTS.map((s) => (
                  <ToggleGroupItem key={s.key} value={s.key} className="px-2 text-xs sm:px-3">
                    {s.label}
                  </ToggleGroupItem>
                ))}
              </ToggleGroup>
            </div>
            <table className="w-full table-fixed text-sm">
              <caption className="sr-only">
                Most played openings, with each one's results{sort !== "games" && `, sorted by ${sortLabel ?? sort}`}
              </caption>
              <thead>
                <tr className="text-muted-foreground border-b text-left text-xs">
                  <th scope="col" className="w-9 py-2 font-medium">
                    #
                  </th>
                  <th scope="col" className="py-2 font-medium">
                    Opening
                  </th>
                  <th
                    scope="col"
                    className="w-20 py-2 text-right font-medium sm:w-32"
                    aria-sort={sort === "games" ? "descending" : undefined}
                  >
                    Games
                  </th>
                  <th scope="col" className="hidden w-48 py-2 pl-4 font-medium sm:table-cell md:w-64">
                    Results
                  </th>
                </tr>
              </thead>
              <tbody>
                {rows.map(({ o, r, rank }, i) => (
                  <tr
                    key={`${o.eco ?? ""}|${o.opening ?? ""}`}
                    className="animate-fade hover:bg-accent/50 border-b align-top transition-colors last:border-0"
                    style={{ "--delay": `${Math.min(i, 15) * 35}ms` } as CSSProperties}
                  >
                    <td className="py-3">
                      <span
                        className={cn(
                          "grid size-6 place-items-center rounded-full text-xs font-semibold tabular-nums",
                          rank <= 3 ? "from-brand-1 to-brand-2 bg-linear-135 text-white" : "text-muted-foreground bg-muted",
                        )}
                      >
                        {rank}
                      </span>
                    </td>
                    <td className="min-w-0 py-3 pr-2">
                      <span className="block font-medium break-words">{o.opening ?? "Unknown opening"}</span>
                      <span className="text-muted-foreground font-mono text-xs">{o.eco ?? "no ECO code"}</span>
                      {/* On a phone the results bar moves under the name. */}
                      <ResultsBar results={r} className="mt-2 sm:hidden" delay={200 + i * 35} />
                    </td>
                    <td className="py-3 text-right tabular-nums">
                      {formatInt(o.games)}
                      <span className="text-muted-foreground block text-xs">{formatPercent(o.games, data.total_games)}</span>
                      <span aria-hidden="true" className="bg-muted mt-1.5 ml-auto block h-1 w-full max-w-24 overflow-hidden rounded-full">
                        <span
                          className="from-brand-1 to-brand-3 animate-grow-x block h-full rounded-full bg-linear-to-r"
                          style={{ width: `${(o.games / most) * 100}%`, "--delay": `${200 + i * 35}ms` } as CSSProperties}
                        />
                      </span>
                    </td>
                    <td className="hidden py-3 pl-4 sm:table-cell">
                      <ResultsBar results={r} delay={200 + i * 35} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </CardContent>
    </Card>
  );
}
