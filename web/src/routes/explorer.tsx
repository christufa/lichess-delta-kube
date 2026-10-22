import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import type { Explorer, ExplorerMove } from "@/api/generated";
import { getExplorerOptions, getMonthsOptions } from "@/api/generated/@tanstack/react-query.gen";
import { ChevronLeft, ExternalLink, FlipVertical2, Map as MapIcon, RotateCcw } from "lucide-react";
import { useMemo, useState, type CSSProperties } from "react";
import { Chessboard, defaultPieces, type PieceRenderObject } from "react-chessboard";
import { FilterBar, LoadingBlock, QueryError, SelectField, Toolbar } from "@/components/common";
import { ResultsBar, ResultsLegend } from "@/components/results-bar";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  EXPLORER_BANDS,
  MAX_PLIES,
  extend,
  formatExplorerBand,
  numbered,
  parseExplorerBand,
  replay,
  type ExplorerSearch,
} from "@/lib/explorer";
import type { Results } from "@/lib/data";
import { formatInt, formatMonth, formatPercent, monthOptions } from "@/lib/format";
import { usePageTitle } from "@/lib/page";
import { SPEED_LABELS, SPEEDS, isSpeed } from "@/lib/speeds";
import { cn } from "@/lib/utils";

const route = getRouteApi("/explorer");
const ANY = "any";

const PIECE_NAMES: Record<string, string> = { P: "pawn", N: "knight", B: "bishop", R: "rook", Q: "queen", K: "king" };

/** The board's own pieces, each named ("White knight on g1"): the board makes every piece a
 * draggable button, which needs a name for screen readers. */
const NAMED_PIECES: PieceRenderObject = Object.fromEntries(
  Object.entries(defaultPieces).map(([code, render]) => {
    const name = `${code.startsWith("w") ? "White" : "Black"} ${PIECE_NAMES[code.slice(1)] ?? "piece"}`;
    return [
      code,
      (props?: Parameters<typeof render>[0]) => (
        <span role="img" aria-label={props?.square ? `${name} on ${props.square}` : name} className="contents">
          {render(props)}
        </span>
      ),
    ];
  }),
);

export function ExplorerPage() {
  usePageTitle("Explorer");
  const months = useQuery(getMonthsOptions());
  return (
    <div className="space-y-6">
      <div className="animate-rise space-y-3 pt-4 sm:pt-8">
        <p className="text-muted-foreground inline-flex items-center gap-2 text-xs font-medium tracking-widest uppercase">
          <MapIcon aria-hidden="true" className="text-brand-1 size-3.5" /> Opening explorer
        </p>
        <h1 className="text-4xl font-semibold tracking-tighter sm:text-6xl">
          <span className="text-gradient">Explorer</span>
        </h1>
        <p className="text-muted-foreground max-w-2xl text-base sm:text-lg">
          Play moves on the board and see what Lichess players chose next, how those games ended and the
          strongest game that went that way. Move orders reaching the same position count together.
        </p>
      </div>
      {months.isPending ? (
        <LoadingBlock className="h-96" label="Loading months" />
      ) : months.isError ? (
        <QueryError error={months.error} onRetry={() => void months.refetch()} />
      ) : (
        <ExplorerView months={months.data.months} />
      )}
    </div>
  );
}

function ExplorerView({ months }: { months: string[] }) {
  const search = route.useSearch();
  const navigate = route.useNavigate();
  const line = useMemo(() => replay(search.moves ? search.moves.split(",") : []), [search.moves]);
  const [orientation, setOrientation] = useState<"white" | "black">("white");
  const { from, to, speed, band } = search;

  const explorer = useQuery({
    ...getExplorerOptions({
      query: {
        fen: line.fen,
        ...(from && { from }),
        ...(to && { to }),
        ...(speed && { speed }),
        ...(band !== undefined && { rating_band: band }),
      },
    }),
    placeholderData: keepPreviousData,
  });

  const set = (patch: Partial<ExplorerSearch>) => {
    void navigate({ search: (prev) => ({ ...prev, ...patch }), replace: true, resetScroll: false });
  };
  // Moving through the line is history (back/forward work); filters replace.
  const goTo = (moves: readonly string[]) => {
    void navigate({
      search: (prev) => ({ ...prev, moves: moves.length > 0 ? moves.join(",") : undefined }),
      resetScroll: false,
    });
  };
  const play = (move: string | { from: string; to: string }) => {
    const next = extend(line.moves, move);
    if (next) goTo(next);
    return next !== null;
  };

  const monthChoices = [{ value: ANY, label: "All months" }, ...monthOptions(months)];
  return (
    <>
      <Toolbar>
        <FilterBar label="Explorer filters">
          <SelectField label="From" value={from ?? ANY} options={monthChoices} onChange={(v) => {
              set({ from: v === ANY ? undefined : v });
            }} />
          <SelectField label="To" value={to ?? ANY} options={monthChoices} onChange={(v) => {
              set({ to: v === ANY ? undefined : v });
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
              ...EXPLORER_BANDS.map((b) => ({ value: String(b), label: formatExplorerBand(b) })),
            ]}
            onChange={(v) => {
              set({ band: v === ANY ? undefined : parseExplorerBand(v) });
            }}
          />
        </FilterBar>
      </Toolbar>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
        <Card className="animate-rise h-fit gap-4 rounded-2xl shadow-lg shadow-black/5">
          <CardContent className="space-y-4">
            <div
              className="overflow-hidden rounded-lg"
              role="group"
              aria-label={`Board: ${line.moves.length === 0 ? "the starting position" : `after ${numbered(line.moves).map((m) => m.label).join(" ")}`}`}
            >
              <Chessboard
                options={{
                  id: "explorer",
                  pieces: NAMED_PIECES,
                  position: line.fen,
                  boardOrientation: orientation,
                  animationDurationInMs: 200,
                  allowDrawingArrows: false,
                  darkSquareStyle: { backgroundColor: "var(--board-dark)" },
                  lightSquareStyle: { backgroundColor: "var(--board-light)" },
                  // Coordinates in the other square colour, so they read on both.
                  darkSquareNotationStyle: { color: "var(--board-light)", fontWeight: 600 },
                  lightSquareNotationStyle: { color: "var(--board-dark)", fontWeight: 600 },
                  onPieceDrop: ({ sourceSquare, targetSquare }) =>
                    targetSquare !== null && play({ from: sourceSquare, to: targetSquare }),
                }}
              />
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="sm" onClick={() => {
                  goTo([]);
                }} disabled={line.moves.length === 0}>
                <RotateCcw /> Start
              </Button>
              <Button variant="outline" size="sm" onClick={() => {
                  goTo(line.moves.slice(0, -1));
                }} disabled={line.moves.length === 0}>
                <ChevronLeft /> Back
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="ml-auto"
                onClick={() => {
                  setOrientation((o) => (o === "white" ? "black" : "white"));
                }}
              >
                <FlipVertical2 /> Flip
              </Button>
            </div>
            <nav aria-label="Moves played">
              {line.moves.length === 0 ? (
                <p className="text-muted-foreground text-sm">The starting position. Drag a piece or pick a move.</p>
              ) : (
                <ol className="flex flex-wrap gap-1 font-mono text-sm">
                  {numbered(line.moves).map((m) => (
                    <li key={m.ply}>
                      <button
                        type="button"
                        onClick={() => {
                          goTo(line.moves.slice(0, m.ply));
                        }}
                        aria-current={m.ply === line.moves.length ? "step" : undefined}
                        className="hover:bg-accent aria-[current=step]:bg-primary aria-[current=step]:text-primary-foreground rounded px-1.5 py-0.5"
                      >
                        {m.label}
                      </button>
                    </li>
                  ))}
                </ol>
              )}
            </nav>
          </CardContent>
        </Card>

        {explorer.isPending ? (
          <LoadingBlock className="h-96" label="Loading moves" />
        ) : explorer.isError ? (
          <QueryError error={explorer.error} onRetry={() => void explorer.refetch()} />
        ) : (
          <div
            className="min-w-0 transition-opacity data-[stale=true]:opacity-60"
            data-stale={explorer.isPlaceholderData}
            aria-busy={explorer.isFetching}
          >
            <MovesTable data={explorer.data} plies={line.moves.length} onPlay={play} />
          </div>
        )}
      </div>
    </>
  );
}

function resultsOfMove(m: ExplorerMove): Results {
  const decided = m.white_wins + m.draws + m.black_wins;
  return { white: m.white_wins, draws: m.draws, black: m.black_wins, unfinished: m.games - decided, games: m.games };
}

function selection(d: Explorer): string {
  const months =
    d.from_month && d.to_month
      ? d.from_month === d.to_month
        ? formatMonth(d.from_month)
        : `${formatMonth(d.from_month)} – ${formatMonth(d.to_month)}`
      : d.from_month
        ? `since ${formatMonth(d.from_month)}`
        : d.to_month
          ? `until ${formatMonth(d.to_month)}`
          : "all months";
  const speed = d.speed ? SPEED_LABELS[d.speed].toLowerCase() : "all";
  const band = d.rating_band === null ? "" : `, average rating ${formatExplorerBand(d.rating_band)}`;
  return `${formatInt(d.games)} ${speed} games${band}, ${months}`;
}

function MovesTable({ data, plies, onPlay }: { data: Explorer; plies: number; onPlay: (san: string) => boolean }) {
  const most = Math.max(1, ...data.moves.map((m) => m.games));
  return (
    <Card className="animate-rise gap-4 rounded-2xl shadow-lg shadow-black/5" style={{ "--delay": "120ms" } as CSSProperties}>
      <CardHeader className="gap-1">
        <CardTitle>
          <h2 className="text-base font-semibold">Next moves</h2>
        </CardTitle>
        <CardDescription>{selection(data)}</CardDescription>
      </CardHeader>
      <CardContent>
        {data.moves.length === 0 ? (
          <p className="text-muted-foreground py-12 text-center text-sm">
            {plies >= MAX_PLIES
              ? `The explorer covers each game's first ${MAX_PLIES} moves (plies).`
              : "No move from this position was played at least 5 times in a month with these filters."}
          </p>
        ) : (
          <>
            <ResultsLegend className="mb-3" />
            <table className="w-full table-fixed text-sm">
              <caption className="sr-only">Moves played from this position, most played first, with results</caption>
              <thead>
                <tr className="text-muted-foreground border-b text-left text-xs">
                  <th scope="col" className="w-16 py-2 font-medium sm:w-20">
                    Move
                  </th>
                  <th scope="col" className="w-20 py-2 text-right font-medium sm:w-28">
                    Games
                  </th>
                  <th scope="col" className="hidden py-2 pl-4 font-medium sm:table-cell">
                    Results
                  </th>
                  <th scope="col" className="w-14 py-2 text-right font-medium sm:w-20">
                    <abbr title="Average rating of both players">Avg</abbr>
                  </th>
                  <th scope="col" className="w-8 py-2 sm:w-10">
                    <span className="sr-only">Example game</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.moves.map((m, i) => {
                  const r = resultsOfMove(m);
                  return (
                    <tr
                      key={m.move}
                      className="animate-fade hover:bg-accent/50 border-b align-top transition-colors last:border-0"
                      style={{ "--delay": `${Math.min(i, 15) * 30}ms` } as CSSProperties}
                    >
                      <td className="py-2.5">
                        <button
                          type="button"
                          onClick={() => {
                            onPlay(m.move);
                          }}
                          className="hover:bg-primary hover:text-primary-foreground rounded-md px-2 py-1 font-mono font-semibold"
                          aria-label={`Play ${m.move}`}
                        >
                          {m.move}
                        </button>
                        <ResultsBar results={r} className="mt-2 sm:hidden" showLabels={false} delay={150 + i * 30} />
                      </td>
                      <td className="py-2.5 text-right tabular-nums">
                        {formatInt(m.games)}
                        <span className="text-muted-foreground block text-xs">{formatPercent(m.games, data.games)}</span>
                        <span aria-hidden="true" className="bg-muted mt-1.5 ml-auto block h-1 w-full max-w-24 overflow-hidden rounded-full">
                          <span
                            className="from-brand-1 to-brand-3 animate-grow-x block h-full rounded-full bg-linear-to-r"
                            style={{ width: `${(m.games / most) * 100}%`, "--delay": `${150 + i * 30}ms` } as CSSProperties}
                          />
                        </span>
                      </td>
                      <td className="hidden py-2.5 pl-4 sm:table-cell">
                        <ResultsBar results={r} delay={150 + i * 30} />
                      </td>
                      <td className="py-2.5 text-right tabular-nums">
                        {m.avg_rating === null ? "–" : formatInt(Math.round(m.avg_rating))}
                      </td>
                      <td className="py-2.5 text-right">
                        {m.example_game_id && (
                          <a
                            href={`https://lichess.org/${m.example_game_id}`}
                            target="_blank"
                            rel="noreferrer noopener"
                            className={cn("text-muted-foreground hover:text-foreground inline-flex rounded p-1")}
                            title={`Example game${m.example_rating === null ? "" : ` (average rating ${formatInt(Math.round(m.example_rating))})`} on lichess.org`}
                            aria-label={`Example game for ${m.move} on lichess.org`}
                          >
                            <ExternalLink className="size-4" aria-hidden="true" />
                          </a>
                        )}
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
