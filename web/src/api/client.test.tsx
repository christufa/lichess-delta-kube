import { QueryClientProvider, useQuery } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QueryError } from "@/components/common";
import { ApiError, configureApiClient, createQueryClient, shouldRetry } from "./client";
import { getMonths } from "./generated";
import { getMonthsOptions } from "./generated/@tanstack/react-query.gen";

function respond(status: number, body: unknown) {
  return vi.fn(() => Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })));
}

describe("API client", () => {
  beforeEach(() => {
    configureApiClient("http://api.test");
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("calls the same-origin path from the OpenAPI spec", async () => {
    const fetch = respond(200, { months: ["2015-12"] });
    vi.stubGlobal("fetch", fetch);
    const { data } = await getMonths({ throwOnError: true });
    expect(data.months).toEqual(["2015-12"]);
    const request = fetch.mock.calls[0] as unknown as [Request];
    expect(request[0].url).toBe("http://api.test/api/v1/months");
  });

  it("turns error responses into ApiError with the API's detail", async () => {
    vi.stubGlobal("fetch", respond(503, { detail: "Trino is unavailable" }));
    await expect(getMonths({ throwOnError: true })).rejects.toEqual(new ApiError(503, "Trino is unavailable"));

    vi.stubGlobal("fetch", respond(422, { detail: [{ loc: ["query", "from"], msg: "bad month", type: "x" }] }));
    await expect(getMonths({ throwOnError: true })).rejects.toMatchObject({ status: 422, message: "bad month" });
  });

  it("reports a network failure as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(getMonths({ throwOnError: true })).rejects.toMatchObject({ status: 0 });
  });

  it("retries only what can succeed later", () => {
    expect(shouldRetry(0, new ApiError(503, ""))).toBe(true);
    expect(shouldRetry(0, new ApiError(429, ""))).toBe(true);
    expect(shouldRetry(0, new ApiError(0, ""))).toBe(true);
    expect(shouldRetry(0, new ApiError(400, ""))).toBe(false);
    expect(shouldRetry(2, new ApiError(503, ""))).toBe(false);
  });

  it("keeps data fresh for hours (the lake changes monthly)", () => {
    const options = createQueryClient().getDefaultOptions().queries;
    expect(options?.staleTime).toBeGreaterThanOrEqual(60 * 60 * 1000);
    expect(options?.refetchOnWindowFocus).toBe(false);
  });

  it("shows a failed query as an alert with a retry", async () => {
    vi.stubGlobal("fetch", respond(429, { detail: "slow down" }));
    const client = createQueryClient();
    client.setDefaultOptions({ queries: { retry: false } });
    function Months() {
      const q = useQuery(getMonthsOptions());
      return q.isError ? <QueryError error={q.error} onRetry={() => void q.refetch()} /> : <p>loading</p>;
    }
    render(
      <QueryClientProvider client={client}>
        <Months />
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Too many requests");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
