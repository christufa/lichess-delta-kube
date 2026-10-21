import { getToken } from "@/chat/auth";

/**
 * The chat backend (chat/, `/api/v1/chat`): a signed-in user's token on every request, and its
 * errors (`{code, detail}`: 401 sign_in, 403 not_allowed, 429 daily_budget / rate_limit,
 * 413 too_large, 503 unavailable) as a ChatHttpError the panel can explain.
 */
export class ChatHttpError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.name = "ChatHttpError";
    this.status = status;
    this.code = code;
  }
}

export interface ChatStatus {
  user: string;
  available: boolean;
  model: string;
  spent_today_usd: number;
  left_today_usd: number;
}

/** fetch with the user's token; non-2xx answers become ChatHttpError. */
export async function chatFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = await getToken();
  if (!token) throw new ChatHttpError(401, "sign_in", "Sign in to chat.");
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(input, { ...init, headers });
  if (!res.ok) {
    let code = "error";
    let detail = `The chat answered ${res.status}.`;
    try {
      const body = (await res.json()) as { code?: string; detail?: string };
      code = body.code ?? code;
      detail = body.detail ?? detail;
    } catch {
      // not JSON (e.g. a proxy error page): keep the generic message
    }
    throw new ChatHttpError(res.status, code, detail);
  }
  return res;
}

export async function fetchChatStatus(): Promise<ChatStatus> {
  const res = await chatFetch("/api/v1/chat/status");
  return (await res.json()) as ChatStatus;
}
