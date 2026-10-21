// ShinyText, adapted from React Bits (reactbits.dev, TextAnimations/ShinyText; MIT + Commons
// Clause, see LICENSE.md in this folder): a band of light sweeps across the text now and
// then. Trimmed to the looping sweep; it stays still with reduced motion and pauses while
// off screen. Colors are concrete CSS colors (they're blended in JS).
import { useEffect, useLayoutEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/utils";

interface ShinyTextProps {
  children: ReactNode;
  color: string;
  shineColor: string;
  /** Seconds per sweep. */
  speed?: number;
  /** Seconds between sweeps. */
  delay?: number;
  angle?: number;
  shineWidth?: number;
  softness?: number;
  className?: string;
}

type Rgba = [number, number, number, number];

const FALLOFF = [0, 0.2, 0.4, 0.6, 0.8, 1];
const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const ease = (t: number) => 0.5 - Math.cos(Math.PI * t) / 2;

let paint: CanvasRenderingContext2D | null = null;

const parseColor = (value: string): Rgba | null => {
  paint ??= document.createElement("canvas").getContext("2d");
  if (!paint) return null;
  paint.fillStyle = "#000000";
  paint.fillStyle = value;
  const resolved = paint.fillStyle;
  if (resolved.startsWith("#")) {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(resolved.slice(i, i + 2), 16));
    return [r ?? 0, g ?? 0, b ?? 0, 1];
  }
  const parts = resolved.match(/[\d.]+/g)?.map(Number);
  if (!parts || parts.length < 3) return null;
  return [parts[0] ?? 0, parts[1] ?? 0, parts[2] ?? 0, parts[3] ?? 1];
};

// Mixes in linear light, so the band doesn't look muddy.
const blend = (from: Rgba, to: Rgba, amount: number) => {
  const channel = (a: number, b: number) =>
    Math.round(Math.pow(Math.pow(a / 255, 2.2) * (1 - amount) + Math.pow(b / 255, 2.2) * amount, 1 / 2.2) * 255);
  const alpha = from[3] * (1 - amount) + to[3] * amount;
  return `rgba(${channel(from[0], to[0])}, ${channel(from[1], to[1])}, ${channel(from[2], to[2])}, ${Math.round(alpha * 1000) / 1000})`;
};

export default function ShinyText({
  children,
  color,
  shineColor,
  speed = 2.5,
  delay = 3,
  angle = 120,
  shineWidth = 40,
  softness = 0.8,
  className,
}: ShinyTextProps) {
  const rootRef = useRef<HTMLSpanElement>(null);
  const settings = { color, shineColor, speed: Math.max(0.1, speed), delay: Math.max(0, delay), angle, shineWidth: clamp(shineWidth, 2, 200), softness: clamp(softness, 0, 1) };
  const settingsRef = useRef(settings);
  useLayoutEffect(() => {
    settingsRef.current = settings;
  });

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let raf = 0;
    let timer = 0;
    let start = 0;
    let visible = true;

    const draw = (pos: number | null) => {
      const s = settingsRef.current;
      const base = parseColor(s.color);
      const shine = parseColor(s.shineColor);
      if (pos === null || !base || !shine) {
        root.style.backgroundImage = `linear-gradient(${s.color}, ${s.color})`;
        return;
      }
      const half = s.shineWidth / 2;
      const core = half * (1 - s.softness);
      const fade = half - core;
      const stops: [number, number][] = [];
      for (const side of [-1, 1]) {
        for (const f of FALLOFF) stops.push([pos + side * (core + fade * (1 - f)), f * f * (3 - 2 * f)]);
      }
      stops.sort((a, b) => a[0] - b[0]);
      const list = stops.map(([at, amount]) => `${blend(base, shine, amount)} ${at.toFixed(2)}%`);
      root.style.backgroundImage = `linear-gradient(${s.angle}deg, ${blend(base, shine, 0)} 0%, ${list.join(", ")}, ${blend(base, shine, 0)} 100%)`;
    };

    const tick = (now: number) => {
      raf = 0;
      const s = settingsRef.current;
      const extent = s.shineWidth / 2 + 2;
      const t = clamp((now - start) / (s.speed * 1000), 0, 1);
      // Right to left, like the original's default.
      draw(100 + extent - (100 + 2 * extent) * ease(t));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
        return;
      }
      draw(null);
      timer = window.setTimeout(sweep, s.delay * 1000);
    };

    const sweep = () => {
      timer = 0;
      if (!visible || raf) return;
      start = performance.now();
      raf = requestAnimationFrame(tick);
    };

    draw(null);
    if (reduce) return undefined;
    const observer = new IntersectionObserver((entries) => {
      visible = entries.some((entry) => entry.isIntersecting);
      if (visible && !raf && !timer) sweep();
    });
    observer.observe(root);
    return () => {
      cancelAnimationFrame(raf);
      window.clearTimeout(timer);
      observer.disconnect();
    };
  }, []);

  return (
    <span
      ref={rootRef}
      className={cn(
        "relative inline-block bg-clip-text bg-no-repeat [-webkit-background-clip:text] [-webkit-text-fill-color:transparent] [background-size:100%_100%]",
        className,
      )}
      style={{ backgroundImage: `linear-gradient(${color}, ${color})` }}
    >
      {children}
    </span>
  );
}
