import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel from "@/chat/chat-panel";

// The panel against a faked backend: Microsoft sign-in mocked away, fetch answering the status
// and streaming a UI message stream the way chess-chat (Pydantic AI's Vercel AI adapter) does.

const auth: { account: object | null; token: string | null } = vi.hoisted(() => ({ account: null, token: null }));

vi.mock("@/chat/config", () => ({
  loadChatConfig: () =>
    Promise.resolve({ tenantId: "00000000-0000-0000-0000-000000000000", chatClientId: "00000000-0000-0000-0000-000000000003" }),
}));
vi.mock("@/chat/auth", () => ({
  currentAccount: () => Promise.resolve(auth.account),
  getToken: () => Promise.resolve(auth.token),
  signIn: vi.fn(() => Promise.resolve()),
  signOut: vi.fn(() => Promise.resolve()),
}));

const STATUS = { user: "Me", available: true, model: "anthropic:claude-opus-5-5", spent_today_usd: 0.02, left_today_usd: 4.98 };

function sse(chunks: object[]): Response {
  const body = [...chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`), "data: [DONE]\n\n"].join("");
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream", "x-vercel-ai-ui-message-stream": "v1" },
  });
}

const ANSWER = [
  { type: "start", messageId: "a1" },
  { type: "start-step" },
  { type: "reasoning-start", id: "r1" },
  { type: "reasoning-delta", id: "r1", delta: "The gold summary has it." },
  { type: "reasoning-end", id: "r1" },
  { type: "tool-input-start", toolCallId: "c1", toolName: "run_sql" },
  {
    type: "tool-input-available",
    toolCallId: "c1",
    toolName: "run_sql",
    input: { sql: "SELECT games FROM gold.monthly_summary WHERE month = '2015-12'" },
  },
  { type: "tool-output-available", toolCallId: "c1", output: { columns: ["games"], rows: [[4161162]], row_count: 1 } },
  { type: "finish-step" },
  { type: "start-step" },
  { type: "text-start", id: "t1" },
  { type: "text-delta", id: "t1", delta: "There were **4,161,162** games in 2015-12." },
  { type: "text-end", id: "t1" },
  { type: "finish-step" },
  { type: "finish" },
];

let chatResponse: () => Response;
const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
  const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  if (url.endsWith("/api/v1/chat/status")) return Promise.resolve(Response.json(STATUS));
  if (url.endsWith("/api/v1/chat") && init?.method === "POST") return Promise.resolve(chatResponse());
  return Promise.resolve(new Response("not found", { status: 404 }));
});

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ChatPanel onClose={() => undefined} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  auth.account = { homeAccountId: "u1", username: "me@example.com" };
  auth.token = "token-1";
  chatResponse = () => sse(ANSWER);
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockClear();
});

describe("chat panel", () => {
  it("asks for sign-in when nobody is signed in", async () => {
    auth.account = null;
    renderPanel();
    expect(await screen.findByRole("button", { name: "Sign in with Microsoft" })).toBeInTheDocument();
  });

  it("explains when the account isn't invited (403)", async () => {
    fetchMock.mockImplementationOnce(() =>
      Promise.resolve(Response.json({ code: "not_allowed", detail: "no" }, { status: 403 })),
    );
    renderPanel();
    expect(await screen.findByText("This account isn't invited")).toBeInTheDocument();
  });

  it("streams an answer with its reasoning, the SQL it ran and the result", async () => {
    const user = userEvent.setup();
    renderPanel();
    expect(await screen.findByText("$4.98 left today")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Your question"), "How many games in 2015-12?{Enter}");

    const log = screen.getByRole("log", { name: "Conversation" });
    expect(await within(log).findByText("4,161,162", { selector: "strong" })).toBeInTheDocument();
    expect(within(log).getByText("How many games in 2015-12?")).toBeInTheDocument();
    expect(within(log).getByText("Ran a query")).toBeInTheDocument();
    expect(within(log).getByText("1 row")).toBeInTheDocument();
    expect(within(log).getByText(/SELECT games FROM gold\.monthly_summary/)).toBeInTheDocument();
    expect(within(log).getByText("Thought about it")).toBeInTheDocument();

    const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(new Headers(post?.[1]?.headers).get("Authorization")).toBe("Bearer token-1");
    const sent = post?.[1]?.body;
    const body = JSON.parse(typeof sent === "string" ? sent : "{}") as { messages: { role: string }[] };
    expect(body.messages.map((m) => m.role)).toEqual(["user"]);
  });

  it("shows the backend's reason when it refuses a question", async () => {
    const user = userEvent.setup();
    chatResponse = () =>
      Response.json({ code: "rate_limit", detail: "at most 6 questions a minute" }, { status: 429 });
    renderPanel();
    await user.type(await screen.findByLabelText("Your question"), "Hi{Enter}");
    expect(await screen.findByRole("alert")).toHaveTextContent("at most 6 questions a minute");
  });

  it("stops taking questions when today's budget is used up", async () => {
    fetchMock.mockImplementationOnce(() => Promise.resolve(Response.json({ ...STATUS, left_today_usd: 0 })));
    renderPanel();
    expect(await screen.findByText(/budget is used up/)).toBeInTheDocument();
    expect(screen.getByLabelText("Your question")).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("button", { name: "Send" })).toBeDisabled());
  });
});
