import { useQuery } from "@tanstack/react-query";
import { Link, Outlet } from "@tanstack/react-router";
import { getMonthsOptions } from "@/api/generated/@tanstack/react-query.gen";
import { coverage, usePageTitle } from "@/lib/page";

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/openings", label: "Openings" },
] as const;

export function Layout() {
  const months = useQuery(getMonthsOptions());
  return (
    <div className="flex min-h-dvh flex-col">
      <a
        href="#main"
        className="bg-primary text-primary-foreground sr-only z-50 rounded-md px-3 py-2 focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
      >
        Skip to content
      </a>
      <header className="bg-card border-b">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3">
          <Link to="/" className="flex items-center gap-2 font-semibold tracking-tight">
            <img src="/favicon.svg" alt="" width={24} height={24} />
            Lichess lakehouse
          </Link>
          <nav aria-label="Main">
            <ul className="flex gap-1">
              {NAV.map((item) => (
                <li key={item.to}>
                  <Link
                    to={item.to}
                    activeOptions={{ exact: true, includeSearch: false }}
                    className="text-muted-foreground hover:bg-accent hover:text-foreground aria-[current=page]:bg-accent aria-[current=page]:text-foreground rounded-md px-3 py-2 text-sm font-medium"
                  >
                    {item.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        </div>
      </header>
      <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">
        <Outlet />
      </main>
      <footer className="text-muted-foreground border-t text-xs">
        <div className="mx-auto max-w-6xl space-y-1 px-4 py-4">
          <p>
            Games from the{" "}
            <a className="underline underline-offset-2" href="https://database.lichess.org/">
              Lichess open database
            </a>{" "}
            (CC0), loaded into an Iceberg lakehouse on Azure and queried through Trino.
            {months.data && months.data.months.length > 0 && <> Months loaded: {coverage(months.data.months)}.</>}
          </p>
          <p>
            Source:{" "}
            <a className="underline underline-offset-2" href="https://github.com/christufa/lichess-delta-kube">
              christufa/lichess-delta-kube
            </a>
            . Not affiliated with Lichess.
          </p>
        </div>
      </footer>
    </div>
  );
}

export function NotFound() {
  usePageTitle("Not found");
  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="text-muted-foreground">
        <Link to="/" className="underline underline-offset-2">
          Back to the overview
        </Link>
      </p>
    </div>
  );
}
