import { useChat } from "@ai-sdk/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { DefaultChatTransport, isToolUIPart, type UIMessage } from "ai";
import { ArrowUp, Lock, LogOut, Sparkles, Square, SquarePen, TriangleAlert, X } from "lucide-react";
import { type KeyboardEvent, type ReactNode, type SubmitEvent, useEffect, useRef, useState } from "react";
import { ChatHttpError, type ChatStatus, chatFetch, fetchChatStatus } from "@/chat/api";
import { currentAccount, signIn, signOut } from "@/chat/auth";
import { loadChatConfig } from "@/chat/config";
import { Markdown, Reasoning, ToolCall } from "@/chat/message-parts";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const SUGGESTIONS = [
  "How many games were played in 2015-12?",
  "Which openings score best for White above 2000?",
  "How did the share of bullet games change from 2013 to 2015?",
  "What's the most common opening at each speed?",
];

// The backend refuses longer conversations (CHESS_CHAT_MAX_MESSAGES).
const MAX_MESSAGES = 40;

/** The chat panel (#69): sign in with Microsoft, then ask; answers stream in from chess-chat. */
export default function ChatPanel({ onClose }: { onClose: () => void }) {
  const config = useQuery({ queryKey: ["chat", "config"], queryFn: loadChatConfig, staleTime: Infinity });
  const account = useQuery({
    queryKey: ["chat", "account"],
    queryFn: currentAccount,
    enabled: config.data != null,
    staleTime: Infinity,
  });
  const status = useQuery({
    queryKey: ["chat", "status", account.data?.homeAccountId],
    queryFn: fetchChatStatus,
    enabled: account.data != null,
    retry: false,
    staleTime: 30_000,
  });

  let body;
  if (config.isPending || (config.data && account.isPending) || (account.data && status.isPending)) {
    body = <Centered><span className="shimmer text-sm">Loading…</span></Centered>;
  } else if (!config.data) {
    body = (
      <Centered>
        <p className="text-muted-foreground text-sm">The chat isn&apos;t set up on this site.</p>
      </Centered>
    );
  } else if (!account.data || (status.error instanceof ChatHttpError && status.error.status === 401)) {
    body = <SignIn />;
  } else if (status.error instanceof ChatHttpError && status.error.status === 403) {
    body = <NotAllowed />;
  } else if (status.error || !status.data) {
    body = <Problem title="The chat can't be reached right now." onRetry={() => void status.refetch()} />;
  } else if (!status.data.available) {
    body = <Problem title="The chat is switched off right now." detail="Its model isn't configured. Try again later." />;
  } else {
    body = <Conversation status={status.data} />;
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <Sparkles className="text-brand-1 size-4" aria-hidden="true" />
        <h2 className="font-semibold tracking-tight">Ask the lakehouse</h2>
        <Button variant="ghost" size="icon-sm" className="ml-auto" onClick={onClose} aria-label="Close the chat">
          <X />
        </Button>
      </div>
      {body}
    </div>
  );
}

function Centered({ children }: { children: ReactNode }) {
  return <div className="flex flex-1 flex-col items-center justify-center gap-3 p-6 text-center">{children}</div>;
}

function SignIn() {
  const [busy, setBusy] = useState(false);
  return (
    <Centered>
      <div className="from-brand-1/15 to-brand-3/15 grid size-12 place-items-center rounded-2xl bg-linear-135">
        <Lock className="text-brand-1 size-5" aria-hidden="true" />
      </div>
      <h3 className="text-lg font-semibold">Questions in plain English</h3>
      <p className="text-muted-foreground max-w-xs text-sm">
        Claude answers them with read-only SQL over the lakehouse and shows its work. Sign-in is limited to invited
        Microsoft accounts.
      </p>
      <Button
        onClick={() => {
          setBusy(true);
          void signIn().catch(() => { setBusy(false); });
        }}
        disabled={busy}
      >
        Sign in with Microsoft
      </Button>
    </Centered>
  );
}

function NotAllowed() {
  const queryClient = useQueryClient();
  return (
    <Centered>
      <h3 className="font-semibold">This account isn&apos;t invited</h3>
      <p className="text-muted-foreground max-w-xs text-sm">
        You&apos;re signed in, but the chat is open only to accounts the owner has added. The rest of the dashboard
        is open to everyone.
      </p>
      <Button
        variant="outline"
        onClick={() => void signOut().then(() => queryClient.invalidateQueries({ queryKey: ["chat"] }))}
      >
        <LogOut /> Use another account
      </Button>
    </Centered>
  );
}

function Problem({ title, detail, onRetry }: { title: string; detail?: string; onRetry?: () => void }) {
  return (
    <Centered>
      <TriangleAlert className="text-destructive size-5" aria-hidden="true" />
      <p className="font-medium">{title}</p>
      {detail && <p className="text-muted-foreground text-sm">{detail}</p>}
      {onRetry && (
        <Button variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      )}
    </Centered>
  );
}

function usd(n: number) {
  return `$${n.toFixed(2)}`;
}

function Conversation({ status }: { status: ChatStatus }) {
  const queryClient = useQueryClient();
  const [transport] = useState(() => new DefaultChatTransport<UIMessage>({ api: "/api/v1/chat", fetch: chatFetch }));
  const chat = useChat({
    transport,
    experimental_throttle: 50,
    onFinish: () => void queryClient.invalidateQueries({ queryKey: ["chat", "status"] }),
    onError: () => void queryClient.invalidateQueries({ queryKey: ["chat", "status"] }),
  });
  const [input, setInput] = useState("");
  const scroller = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const pinned = useRef(true); // follow the stream unless the reader scrolled up

  const busy = chat.status === "submitted" || chat.status === "streaming";
  const full = chat.messages.length >= MAX_MESSAGES - 1;
  const outOfBudget = status.left_today_usd <= 0;

  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [chat.messages]);

  useEffect(() => textarea.current?.focus(), []);

  function send(text: string) {
    const q = text.trim();
    if (!q || busy || full || outOfBudget) return;
    pinned.current = true;
    chat.clearError();
    void chat.sendMessage({ text: q });
    setInput("");
  }

  function onSubmit(e: SubmitEvent<HTMLFormElement>) {
    e.preventDefault();
    send(input);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <>
      <div className="text-muted-foreground flex items-center gap-2 border-b px-4 py-2 text-xs">
        <span className="truncate">{status.user}</span>
        <span aria-hidden="true">·</span>
        <span className="tabular-nums" title="Model spend left for you today (resets at midnight UTC)">
          {usd(status.left_today_usd)} left today
        </span>
        <Button
          variant="ghost"
          size="icon-xs"
          className="ml-auto"
          onClick={() => {
            void chat.stop();
            chat.setMessages([]);
            chat.clearError();
          }}
          disabled={chat.messages.length === 0}
          aria-label="New chat"
          title="New chat"
        >
          <SquarePen />
        </Button>
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => void signOut().then(() => queryClient.invalidateQueries({ queryKey: ["chat"] }))}
          aria-label="Sign out"
          title="Sign out"
        >
          <LogOut />
        </Button>
      </div>

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
        }}
        className="flex-1 space-y-5 overflow-y-auto px-4 py-4"
        role="log"
        aria-live="polite"
        aria-busy={busy}
        aria-label="Conversation"
      >
        {chat.messages.length === 0 && (
          <div className="space-y-3 pt-4">
            <p className="text-muted-foreground text-sm">
              Ask about games, openings, ratings or speeds. Claude looks at the tables, runs SQL and tells you how it
              got the numbers.
            </p>
            <ul className="space-y-2">
              {SUGGESTIONS.map((s) => (
                <li key={s}>
                  <button
                    type="button"
                    onClick={() => { send(s); }}
                    disabled={outOfBudget}
                    className="hover:border-brand-1/40 hover:bg-accent w-full rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50"
                  >
                    {s}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
        {chat.messages.map((m, i) => (
          <Message key={m.id} message={m} streaming={busy && i === chat.messages.length - 1} />
        ))}
        {chat.status === "submitted" && <p className="shimmer text-muted-foreground text-sm">Thinking…</p>}
        {chat.error && <ChatError error={chat.error} onRetry={() => void chat.regenerate()} />}
      </div>

      <form onSubmit={onSubmit} className="border-t p-3">
        {(full || outOfBudget) && (
          <p className="text-muted-foreground mb-2 text-xs">
            {outOfBudget
              ? "Today's chat budget is used up; it resets at midnight UTC."
              : "This conversation is as long as it can get. Start a new chat to keep going."}
          </p>
        )}
        <div className="focus-within:ring-ring/40 bg-background flex items-end gap-2 rounded-xl border p-2 focus-within:ring-[3px]">
          <label htmlFor="chat-input" className="sr-only">
            Your question
          </label>
          <textarea
            id="chat-input"
            ref={textarea}
            value={input}
            onChange={(e) => { setInput(e.target.value); }}
            onKeyDown={onKeyDown}
            rows={1}
            maxLength={2000}
            placeholder="Ask about the games…"
            disabled={full || outOfBudget}
            className="field-sizing-content max-h-40 min-h-9 flex-1 resize-none bg-transparent px-1 py-1.5 text-sm outline-none"
          />
          {busy ? (
            <Button type="button" size="icon-sm" variant="secondary" onClick={() => void chat.stop()} aria-label="Stop">
              <Square className="fill-current" />
            </Button>
          ) : (
            <Button type="submit" size="icon-sm" disabled={!input.trim() || full || outOfBudget} aria-label="Send">
              <ArrowUp />
            </Button>
          )}
        </div>
        <p className="text-muted-foreground mt-2 text-center text-[11px]">
          Answers come from {status.model.split(":").pop()} and can be wrong: check the SQL.
        </p>
      </form>
    </>
  );
}

function Message({ message, streaming }: { message: UIMessage; streaming: boolean }) {
  if (message.role === "user") {
    const text = message.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
    return (
      <div className="flex justify-end">
        <p className="bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-md px-3.5 py-2 text-sm whitespace-pre-wrap">
          {text}
        </p>
      </div>
    );
  }
  return (
    <div className="animate-fade space-y-2.5">
      {message.parts.map((part, i) => {
        const key = `${message.id}-${i}`;
        if (part.type === "text") return <Markdown key={key} text={part.text} />;
        if (part.type === "reasoning")
          return <Reasoning key={key} text={part.text} streaming={streaming && part.state === "streaming"} />;
        if (isToolUIPart(part)) return <ToolCall key={key} part={part} />;
        return null;
      })}
    </div>
  );
}

function ChatError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const http = error instanceof ChatHttpError ? error : undefined;
  const retryable = !http || ![401, 403, 413].includes(http.status) || http.code === "rate_limit";
  return (
    <div
      role="alert"
      className={cn("border-destructive/30 bg-destructive/5 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm")}
    >
      <TriangleAlert className="text-destructive mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <div className="flex-1">
        <p>{http ? error.message : "Something went wrong while answering."}</p>
        {retryable && http?.code !== "daily_budget" && (
          <button type="button" onClick={onRetry} className="mt-1 text-xs underline underline-offset-2">
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
