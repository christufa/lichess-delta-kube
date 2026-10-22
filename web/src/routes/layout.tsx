import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Sparkles } from "lucide-react";
import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import { getMonthsOptions } from "@/api/generated/@tanstack/react-query.gen";
import { coverage, usePageTitle } from "@/lib/page";
import { cn } from "@/lib/utils";

// Sign-in, the AI SDK and markdown load only when someone opens the chat.
const ChatPanel = lazy(() => import("@/chat/chat-panel"));
const CHAT_OPEN = "chat-open"; // sessionStorage: reopen after the sign-in redirect / a reload

function readChatOpen(): boolean {
  try {
    return sessionStorage.getItem(CHAT_OPEN) === "1";
  } catch {
    return false;
  }
}

const NAV = [
  { to: "/", label: "Overview" },
  { to: "/openings", label: "Openings" },
  { to: "/explorer", label: "Explorer" },
] as const;

/**
 * The page shell: header, main and footer in a column, next to the chat panel (#69): an <aside>
 * of `--chat-width` beside the page on wide screens, a full-screen sheet on small ones. Once
 * opened it stays mounted (hidden when closed), so closing it keeps the conversation.
 */
export function Layout() {
  const months = useQuery(getMonthsOptions());
  const [chatOpen, setChatOpen] = useState(readChatOpen);
  const [chatLoaded, setChatLoaded] = useState(chatOpen);
  const toggle = useRef<HTMLButtonElement>(null);

  function openChat(open: boolean) {
    setChatOpen(open);
    if (open) setChatLoaded(true);
    else toggle.current?.focus();
    try {
      sessionStorage.setItem(CHAT_OPEN, open ? "1" : "0");
    } catch {
      // storage blocked: the panel just won't reopen by itself
    }
  }

  useEffect(() => {
    if (!chatOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") openChat(false);
    };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); };
  }, [chatOpen]);

  return (
    <div className="relative flex min-h-dvh">
      <div aria-hidden="true" className="bg-grid pointer-events-none absolute inset-x-0 top-0 -z-10 h-[48rem]" />
      <div className="flex min-w-0 flex-1 flex-col">
        <a
          href="#main"
          className="bg-primary text-primary-foreground sr-only z-50 rounded-md px-3 py-2 focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
        >
          Skip to content
        </a>
        <header className="glass sticky top-0 z-40 border-b">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3">
            <Link to="/" viewTransition className="group flex items-center gap-2.5 font-semibold tracking-tight">
              <span className="relative grid size-8 place-items-center">
                <span
                  aria-hidden="true"
                  className="from-brand-1 via-brand-2 to-brand-3 absolute inset-0 rounded-lg bg-linear-135 opacity-60 blur-md transition-opacity group-hover:opacity-100"
                />
                <img src="/favicon.svg" alt="" width={28} height={28} className="relative rounded-md" />
              </span>
              <span>
                Lichess <span className="text-muted-foreground font-normal">lakehouse</span>
              </span>
            </Link>
            <div className="flex items-center gap-2">
              <MainNav />
              <button
                ref={toggle}
                type="button"
                onClick={() => { openChat(!chatOpen); }}
                aria-expanded={chatOpen}
                aria-controls="chat-panel"
                className={cn(
                  "group relative flex items-center gap-1.5 overflow-hidden rounded-full border px-2.5 py-1.5 text-sm font-medium transition-colors sm:px-3.5",
                  chatOpen ? "bg-foreground text-background" : "bg-background hover:border-brand-1/40",
                )}
              >
                <Sparkles className={cn("size-4", !chatOpen && "text-brand-1")} aria-hidden="true" />
                {/* Icon only on a phone (the header has three page links); the name stays. */}
                <span className="sr-only sm:not-sr-only">Ask</span>
              </button>
            </div>
          </div>
        </header>
        <main id="main" className="mx-auto w-full max-w-6xl flex-1 px-4 pt-6 pb-16">
          <Outlet />
        </main>
        <footer className="text-muted-foreground border-t text-xs">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-6">
            <p>
              Games from the{" "}
              <a className="hover:text-foreground underline underline-offset-2" href="https://database.lichess.org/">
                Lichess open database
              </a>{" "}
              (CC0), loaded into an Iceberg lakehouse on Azure and queried through Trino.
              {months.data && months.data.months.length > 0 && <> Months loaded: {coverage(months.data.months)}.</>}
            </p>
            <p>
              Source:{" "}
              <a
                className="hover:text-foreground underline underline-offset-2"
                href="https://github.com/christufa/lichess-delta-kube"
              >
                christufa/lichess-delta-kube
              </a>
              . Not affiliated with Lichess.
            </p>
          </div>
        </footer>
      </div>
      {chatLoaded && (
        <aside
          id="chat-panel"
          aria-label="Chat"
          hidden={!chatOpen}
          className="bg-background fixed inset-0 z-50 lg:sticky lg:top-0 lg:z-30 lg:h-dvh lg:w-(--chat-width) lg:shrink-0 lg:border-l"
        >
          <Suspense fallback={<p className="shimmer text-muted-foreground p-4 text-sm">Loading the chat…</p>}>
            <ChatPanel onClose={() => { openChat(false); }} />
          </Suspense>
        </aside>
      )}
    </div>
  );
}

/** The page links, with a pill that slides to the current page. */
function MainNav() {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const list = useRef<HTMLUListElement>(null);
  const pill = useRef<HTMLLIElement>(null);

  useLayoutEffect(() => {
    const measure = () => {
      // The <li> around the current page's link (positioned within the <ul>).
      const current = list.current?.querySelector<HTMLElement>("[aria-current=page]")?.parentElement;
      const el = pill.current;
      if (!el) return;
      el.style.opacity = current ? "1" : "0";
      if (current) {
        el.style.width = `${current.offsetWidth}px`;
        el.style.transform = `translateX(${current.offsetLeft - 4}px)`;
      }
    };
    measure();
    // Web fonts arriving change the widths.
    void document.fonts.ready.then(measure);
  }, [pathname]);

  return (
    <nav aria-label="Main">
      <ul ref={list} className="bg-muted/60 relative flex gap-1 rounded-full border p-1">
        <li
          ref={pill}
          aria-hidden="true"
          className="bg-background absolute inset-y-1 left-1 rounded-full opacity-0 shadow-sm ring-1 ring-black/5 transition-[transform,width] duration-300 ease-out dark:bg-white/10 dark:ring-white/10"
        />
        {NAV.map((item) => (
          <li key={item.to} className="relative">
            <Link
              to={item.to}
              viewTransition
              activeOptions={{ exact: true, includeSearch: false }}
              className="text-muted-foreground hover:text-foreground aria-[current=page]:text-foreground block rounded-full px-3 py-1.5 text-sm font-medium transition-colors sm:px-4"
            >
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

export function NotFound() {
  usePageTitle("Not found");
  return (
    <div className="animate-rise space-y-3 py-16 text-center">
      <p className="text-gradient font-mono text-6xl font-semibold">404</p>
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="text-muted-foreground">
        <Link to="/" className="underline underline-offset-2">
          Back to the overview
        </Link>
      </p>
    </div>
  );
}
