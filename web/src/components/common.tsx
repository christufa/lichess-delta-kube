import { AlertCircle, RotateCw } from "lucide-react";
import { useId, type CSSProperties, type ReactNode } from "react";
import { ApiError } from "@/api/client";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface Option {
  value: string;
  label: string;
}

interface SelectFieldProps {
  label: string;
  value: string;
  options: readonly Option[];
  onChange: (value: string) => void;
  className?: string;
}

/** A labelled shadcn Select. */
export function SelectField({ label, value, options, onChange, className }: SelectFieldProps) {
  const id = useId();
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <Label htmlFor={id} className="text-muted-foreground text-xs font-medium">
        {label}
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="w-full min-w-36">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

/** Filters sit in one row above what they filter, wrapping on a phone. */
export function FilterBar({ children, label, className }: { children: ReactNode; label: string; className?: string }) {
  return (
    <div role="group" aria-label={label} className={cn("grid grid-cols-2 gap-3 sm:flex sm:flex-wrap sm:items-end", className)}>
      {children}
    </div>
  );
}

/** A frosted panel for a page's main filters, stuck under the header on larger screens. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "glass animate-rise rounded-2xl border p-3 shadow-lg shadow-black/5 md:sticky md:top-[4.5rem] md:z-30",
        className,
      )}
    >
      {children}
    </div>
  );
}

function errorText(error: unknown): { title: string; detail: string } {
  if (error instanceof ApiError) {
    if (error.status === 429) return { title: "Too many requests", detail: "Wait a few seconds and try again." };
    if (error.status === 503)
      return { title: "The data store is waking up", detail: "The query engine is unavailable right now; try again in a minute." };
    if (error.status === 0) return { title: "Can't reach the API", detail: error.message };
    return { title: `The API answered ${error.status}`, detail: error.message };
  }
  return { title: "Something went wrong", detail: error instanceof Error ? error.message : String(error) };
}

export function QueryError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const { title, detail } = errorText(error);
  return (
    <Alert variant="destructive" role="alert">
      <AlertCircle aria-hidden="true" />
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>
        <p>{detail}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RotateCw aria-hidden="true" /> Try again
        </Button>
      </AlertDescription>
    </Alert>
  );
}

export function LoadingBlock({ className, label = "Loading" }: { className?: string; label?: string }) {
  return (
    <div role="status" aria-live="polite" className={cn("w-full", className)}>
      <Skeleton className="h-full min-h-16 w-full rounded-2xl" />
      <span className="sr-only">{label}…</span>
    </div>
  );
}

interface ChartCardProps {
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  /** The chart's numbers as a table, behind a disclosure (for screen readers and copying). */
  table?: ReactNode;
  className?: string;
  /** Entrance delay in ms, to stagger cards. */
  delay?: number;
}

export function ChartCard({ title, description, action, children, table, className, delay = 0 }: ChartCardProps) {
  const headingId = useId();
  return (
    <Card
      className={cn(
        "animate-rise hover:border-brand-1/40 relative min-w-0 gap-4 rounded-2xl shadow-lg shadow-black/5 transition-colors duration-300",
        className,
      )}
      style={{ "--delay": `${delay}ms` } as CSSProperties}
      aria-labelledby={headingId}
      role="region"
    >
      {/* A thin gradient line along the top edge. */}
      <span
        aria-hidden="true"
        className="via-brand-1/60 pointer-events-none absolute inset-x-8 top-0 h-px bg-linear-to-r from-transparent to-transparent"
      />
      <CardHeader className="gap-1">
        <CardTitle>
          <h2 id={headingId} className="text-base font-semibold tracking-tight">
            {title}
          </h2>
        </CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
        {action && <div className="col-span-full mt-2">{action}</div>}
      </CardHeader>
      <CardContent className="min-w-0">
        {children}
        {table && (
          <details className="mt-3 text-sm">
            <summary className="text-muted-foreground hover:text-foreground w-fit cursor-pointer rounded-sm transition-colors select-none">
              Show the numbers
            </summary>
            <div className="mt-2 max-h-80 overflow-auto">{table}</div>
          </details>
        )}
      </CardContent>
    </Card>
  );
}

/** A plain table of a chart's numbers. */
export function DataTable({
  caption,
  columns,
  rows,
}: {
  caption: string;
  columns: readonly string[];
  rows: readonly (readonly (string | number)[])[];
}) {
  return (
    <table className="w-full text-left tabular-nums">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr className="border-b">
          {columns.map((c, i) => (
            <th key={c} scope="col" className={cn("px-2 py-1 font-medium", i > 0 && "text-right")}>
              {c}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={String(row[0])} className="border-b last:border-0">
            {row.map((cell, i) =>
              i === 0 ? (
                <th key={i} scope="row" className="px-2 py-1 font-normal">
                  {cell}
                </th>
              ) : (
                <td key={i} className="px-2 py-1 text-right">
                  {cell}
                </td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Colored square next to a label, so identity never depends on color alone. */
export function Swatch({ color, className }: { color: string; className?: string }) {
  return (
    <span
      aria-hidden="true"
      className={cn("inline-block size-3 shrink-0 rounded-[3px] border border-black/10 dark:border-white/15", className)}
      style={{ backgroundColor: color }}
    />
  );
}
