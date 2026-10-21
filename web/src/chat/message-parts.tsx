import { type DynamicToolUIPart, getToolName, type ToolUIPart } from "ai";
import { Brain, ChevronRight, Database, LoaderCircle, TriangleAlert } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

/** The model's answer: markdown with tables (GFM). Raw HTML is never rendered. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="chat-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noreferrer noopener">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

/** The model's summarized reasoning, folded away. */
export function Reasoning({ text, streaming }: { text: string; streaming: boolean }) {
  if (!text.trim()) return null;
  return (
    <details className="group text-muted-foreground text-xs">
      <summary className="hover:text-foreground flex cursor-pointer list-none items-center gap-1.5 select-none">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />
        <Brain className="size-3.5" aria-hidden="true" />
        {streaming ? <span className="shimmer">Thinking…</span> : "Thought about it"}
      </summary>
      <p className="border-border mt-1.5 ml-1.5 border-l pl-3 leading-relaxed whitespace-pre-wrap">{text}</p>
    </details>
  );
}

const TOOL_LABELS: Record<string, string> = {
  list_tables: "Listed the tables",
  describe_table: "Looked at a table",
  run_sql: "Ran a query",
  get_game: "Looked up a game",
};

/** One call to the lakehouse MCP server: what ran (the SQL) and what came back. */
export function ToolCall({ part }: { part: ToolUIPart | DynamicToolUIPart }) {
  const name = getToolName(part);
  const input = (part.input ?? {}) as Record<string, unknown>;
  const running = part.state === "input-streaming" || part.state === "input-available";
  const failed = part.state === "output-error";
  const output = part.state === "output-available" ? parseOutput(part.output) : undefined;
  const detail =
    typeof input.table === "string" ? input.table : typeof input.game_id === "string" ? input.game_id : undefined;

  return (
    <details className="group bg-muted/40 rounded-lg border text-xs">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 select-none">
        {running ? (
          <LoaderCircle className="text-brand-1 size-3.5 animate-spin" aria-hidden="true" />
        ) : failed ? (
          <TriangleAlert className="text-destructive size-3.5" aria-hidden="true" />
        ) : (
          <Database className="text-brand-2 size-3.5" aria-hidden="true" />
        )}
        <span className="font-medium">{running ? "Querying the lakehouse…" : (TOOL_LABELS[name] ?? name)}</span>
        {detail && <code className="text-muted-foreground truncate font-mono">{detail}</code>}
        {output?.rowCount !== undefined && (
          <span className="text-muted-foreground ml-auto shrink-0 tabular-nums">
            {output.rowCount} {output.rowCount === 1 ? "row" : "rows"}
          </span>
        )}
        <ChevronRight
          className={cn("text-muted-foreground size-3.5 shrink-0 transition-transform group-open:rotate-90", output?.rowCount === undefined && "ml-auto")}
          aria-hidden="true"
        />
      </summary>
      <div className="space-y-2 border-t px-3 py-2">
        {typeof input.sql === "string" && <pre className="chat-code">{input.sql}</pre>}
        {failed && <p className="text-destructive">{part.errorText}</p>}
        {output && <pre className="chat-code text-muted-foreground max-h-48">{output.preview}</pre>}
      </div>
    </details>
  );
}

/** MCP tool results arrive as JSON (an object, or its text). run_sql's has row_count. */
function parseOutput(raw: unknown): { rowCount?: number; preview: string } {
  let value = raw;
  if (typeof raw === "string") {
    try {
      value = JSON.parse(raw);
    } catch {
      return { preview: raw.slice(0, 2000) };
    }
  }
  const rowCount =
    typeof value === "object" && value !== null && typeof (value as { row_count?: unknown }).row_count === "number"
      ? (value as { row_count: number }).row_count
      : undefined;
  const text = JSON.stringify(value ?? null, null, 2);
  return { rowCount, preview: text.length > 2000 ? `${text.slice(0, 2000)}\n…` : text };
}
