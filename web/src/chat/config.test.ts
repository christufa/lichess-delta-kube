import { afterEach, describe, expect, it, vi } from "vitest";
import { loadChatConfig, resetChatConfig } from "@/chat/config";

const GOOD = { tenantId: "00000000-0000-0000-0000-000000000000", chatClientId: "00000000-0000-0000-0000-000000000003" };

function serve(res: Response | Error) {
  vi.stubGlobal("fetch", vi.fn(() => (res instanceof Error ? Promise.reject(res) : Promise.resolve(res))));
}

afterEach(() => {
  vi.unstubAllGlobals();
  resetChatConfig();
});

describe("loadChatConfig", () => {
  it("reads /config.json", async () => {
    serve(Response.json(GOOD));
    await expect(loadChatConfig()).resolves.toEqual(GOOD);
  });

  it.each([
    ["missing (404)", new Response("", { status: 404 })],
    ["the app's index.html instead", new Response("<!doctype html>", { headers: { "Content-Type": "text/html" } })],
    ["not GUIDs", Response.json({ tenantId: "x", chatClientId: "y" })],
    ["a network error", new Error("offline")],
  ])("is off when the config is %s", async (_, res) => {
    serve(res);
    await expect(loadChatConfig()).resolves.toBeNull();
  });
});
