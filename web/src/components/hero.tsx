import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ArrowDown, ArrowRight } from "lucide-react";
import { lazy, Suspense, type CSSProperties, type ReactNode } from "react";
import { getSummaryOptions } from "@/api/generated/@tanstack/react-query.gen";
import BlurText from "@/components/reactbits/blur-text";
import CountUp from "@/components/reactbits/count-up";
import ShinyText from "@/components/reactbits/shiny-text";
import { useColorScheme } from "@/lib/chart-theme";
import { resultsBySpeed } from "@/lib/data";
import { formatInt } from "@/lib/format";
import { coverage } from "@/lib/page";

// WebGL, so its own chunk: the hero's text is on screen before the aurora arrives.
const Aurora = lazy(() => import("@/components/reactbits/aurora"));

const AURORA = {
  dark: ["#5b3fd9", "#2a78d6", "#19c39a"],
  light: ["#7c5cff", "#3d9bff", "#2bd4a0"],
};
const SHINE = {
  dark: { color: "#b4b7cc", shine: "#ffffff" },
  light: { color: "#4b4d61", shine: "#6d4aff" },
};

/** The overview's opening: what this is, and the whole lake in three numbers. */
export function Hero({ months }: { months: string[] }) {
  const scheme = useColorScheme();
  // The whole loaded range (the summary for the default view is the same request).
  const all = useQuery(getSummaryOptions({ query: { from: months[0] ?? "", to: months.at(-1) ?? "" } }));
  const total = all.data ? resultsBySpeed(all.data.rows).find((r) => r.speed === "all")?.results : undefined;

  return (
    <section
      aria-labelledby="hero-title"
      className="bg-card/30 relative isolate -mx-4 -mt-6 overflow-hidden border-b px-4 pt-14 pb-10 sm:mx-0 sm:mt-0 sm:rounded-3xl sm:border sm:px-10 sm:pt-20 sm:pb-12"
    >
      <div aria-hidden="true" className="absolute inset-0 -z-10">
        <Suspense fallback={null}>
          <Aurora
            colorStops={AURORA[scheme]}
            lightMode={scheme === "light"}
            amplitude={1.1}
            blend={0.6}
            height={0.55}
            speed={0.6}
            layers={2}
            opacity={scheme === "light" ? 0.9 : 0.85}
          />
        </Suspense>
        <div className="via-background/50 to-background/95 absolute inset-0 bg-linear-to-b from-transparent" />
      </div>

      <p className="animate-rise bg-background/50 mb-6 inline-flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium backdrop-blur">
        <span aria-hidden="true" className="relative flex size-2">
          <span className="bg-brand-3 absolute inline-flex size-full animate-ping rounded-full opacity-60 motion-reduce:hidden" />
          <span className="bg-brand-3 relative inline-flex size-2 rounded-full" />
        </span>
        <ShinyText color={SHINE[scheme].color} shineColor={SHINE[scheme].shine}>
          Lichess open database · {coverage(months)}
        </ShinyText>
      </p>

      <BlurText
        as="h1"
        id="hero-title"
        text="Lichess, month by month"
        className="max-w-3xl text-4xl leading-[1.05] font-semibold tracking-tighter sm:text-6xl md:text-7xl"
      />
      <p
        className="animate-rise text-muted-foreground mt-5 max-w-2xl text-base sm:text-lg"
        style={{ "--delay": "350ms" } as CSSProperties}
      >
        Every rated standard game in the Lichess open database: how many were played, by whom, at what speed, and
        how they ended.
      </p>

      <div className="animate-rise mt-7 flex flex-wrap gap-3" style={{ "--delay": "500ms" } as CSSProperties}>
        <Link
          to="/openings"
          viewTransition
          className="from-brand-1 to-brand-2 focus-visible:ring-ring group inline-flex items-center gap-2 rounded-full bg-linear-to-r px-5 py-2.5 text-sm font-semibold text-white shadow-brand-1/30 shadow-lg transition-transform hover:-translate-y-0.5 focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
        >
          Explore the top openings
          <ArrowRight aria-hidden="true" className="size-4 transition-transform group-hover:translate-x-0.5" />
        </Link>
        <a
          href="#trends"
          className="bg-background/60 hover:bg-background inline-flex items-center gap-2 rounded-full border px-5 py-2.5 text-sm font-semibold backdrop-blur transition-colors"
        >
          See the trends
          <ArrowDown aria-hidden="true" className="size-4" />
        </a>
      </div>

      <dl className="mt-10 grid grid-cols-3 gap-3 sm:max-w-2xl sm:gap-6">
        <HeroStat label="games in the lake" title={total ? formatInt(total.games) : undefined} delay={650}>
          {total ? <CountUp to={total.games} format="compact" decimals={1} mode="roll" duration={2.2} /> : "–"}
        </HeroStat>
        <HeroStat label="months loaded" delay={750}>
          <CountUp to={months.length} mode="roll" duration={1.6} />
        </HeroStat>
        <HeroStat label="won by white" delay={850}>
          {total ? <CountUp to={(total.white / total.games) * 100} decimals={1} suffix="%" mode="roll" duration={2} /> : "–"}
        </HeroStat>
      </dl>
    </section>
  );
}

function HeroStat({
  label,
  title,
  delay,
  children,
}: {
  label: string;
  title?: string;
  delay: number;
  children: ReactNode;
}) {
  return (
    <div className="animate-rise flex flex-col border-l pl-3 sm:pl-4" style={{ "--delay": `${delay}ms` } as CSSProperties}>
      <dt className="text-muted-foreground order-2 text-xs sm:text-sm">{label}</dt>
      <dd className="text-2xl font-semibold tracking-tight sm:text-4xl" title={title}>
        {children}
      </dd>
    </div>
  );
}
