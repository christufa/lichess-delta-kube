import type { Speed } from "@/api/generated";

/** Lichess speed classes, fastest first. Each keeps its color whatever is filtered. */
export const SPEEDS = [
  "ultrabullet",
  "bullet",
  "blitz",
  "rapid",
  "classical",
  "correspondence",
] as const satisfies readonly Speed[];

export const SPEED_LABELS: Record<Speed, string> = {
  ultrabullet: "UltraBullet",
  bullet: "Bullet",
  blitz: "Blitz",
  rapid: "Rapid",
  classical: "Classical",
  correspondence: "Correspondence",
};

/**
 * CSS variable holding each speed's series color (index.css). The busiest speeds take the
 * first palette slots, which are the most distinct from each other.
 */
export const SPEED_COLOR_VARS: Record<Speed, string> = {
  blitz: "--series-1",
  bullet: "--series-2",
  rapid: "--series-3",
  classical: "--series-4",
  ultrabullet: "--series-5",
  correspondence: "--series-6",
};

export function isSpeed(value: unknown): value is Speed {
  return typeof value === "string" && (SPEEDS as readonly string[]).includes(value);
}
