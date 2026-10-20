import { QueryClient } from "@tanstack/react-query";
import { client } from "./generated/client.gen";

/** A failed API call: the HTTP status (0 = no response) and the API's `detail`. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function detailOf(body: unknown): string | undefined {
  if (typeof body === "string" && body.length > 0 && body.length < 300) return body;
  if (typeof body !== "object" || body === null || !("detail" in body)) return undefined;
  const { detail } = body;
  if (typeof detail === "string") return detail;
  // 422: a list of validation errors
  if (Array.isArray(detail)) {
    return detail
      .map((d: unknown) => (typeof d === "object" && d !== null && "msg" in d ? String(d.msg) : ""))
      .filter(Boolean)
      .join("; ");
  }
  return undefined;
}

/**
 * Same-origin API: cloudflared routes chess.generops.dev/api/ to chess-api, `npm run dev`
 * proxies it, and the Playwright tests mock it. `baseUrl` is only set by unit tests.
 */
export function configureApiClient(baseUrl = "") {
  client.setConfig({ baseUrl });
  client.interceptors.error.clear();
  client.interceptors.error.use((error, response) => {
    if (error instanceof ApiError) return error;
    if (!response) return new ApiError(0, "Can't reach the API. Check your connection.");
    return new ApiError(response.status, detailOf(error) ?? response.statusText);
  });
}

const HOUR = 60 * 60 * 1000;

/** Retry network errors, 429 and 5xx a couple of times; never 4xx (the request is wrong). */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (!(error instanceof ApiError)) return false;
  return error.status === 0 || error.status === 429 || error.status >= 500;
}

/**
 * The lake changes once a month (the API caches for an hour), so data stays fresh for
 * hours and is kept for a day; no refetch on focus.
 */
export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 6 * HOUR,
        gcTime: 24 * HOUR,
        refetchOnWindowFocus: false,
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
    },
  });
}
