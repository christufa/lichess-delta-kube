const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2015-12" → "Dec 2015" */
export function formatMonth(month: string): string {
  const [year, m] = month.split("-");
  const name = MONTH_NAMES[Number(m) - 1];
  return name && year ? `${name} ${year}` : month;
}

/** "2015-12" → "2016-01" */
export function nextMonth(month: string): string {
  const [y = 0, m = 0] = month.split("-").map(Number);
  return m === 12 ? `${y + 1}-01` : `${y}-${String(m + 1).padStart(2, "0")}`;
}

const integer = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function formatInt(n: number): string {
  return integer.format(n);
}

/** 12_345_678 → "12.3M" */
export function formatCompact(n: number): string {
  return compact.format(n);
}

/** 0.4567 of 1 → "45.7%"; a share of nothing is "–". */
export function formatPercent(part: number, whole: number, digits = 1): string {
  if (whole <= 0) return "–";
  return `${((part / whole) * 100).toFixed(digits)}%`;
}

/** Lower bound of a 200-point band → "1400–1599" */
export function formatBand(band: number): string {
  return `${band}–${band + 199}`;
}

/** Month picker options, newest first: the recent months are the ones people pick. */
export function monthOptions(months: readonly string[]): { value: string; label: string }[] {
  return [...months].reverse().map((m) => ({ value: m, label: formatMonth(m) }));
}
