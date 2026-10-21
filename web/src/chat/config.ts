/**
 * The chat's runtime config: the Entra tenant and the "chess Chat" app the dashboard signs in
 * to. Deployment-specific, so it isn't in the build: the chess-web chart serves it as
 * /config.json from the cluster facts. Without it (local dev, tests) the chat says it's off.
 * For local dev, put a config.json in web/public/ (gitignored).
 */
export interface ChatConfig { tenantId: string; chatClientId: string }

let loaded: Promise<ChatConfig | null> | undefined;

export function loadChatConfig(): Promise<ChatConfig | null> {
  loaded ??= fetch("/config.json", { cache: "no-cache" })
    .then(async (res) => {
      if (!res.ok) return null;
      const body: unknown = await res.json();
      return isChatConfig(body) ? body : null;
    })
    .catch(() => null);
  return loaded;
}

function isChatConfig(v: unknown): v is ChatConfig {
  const guid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (typeof v !== "object" || v === null) return false;
  const { tenantId, chatClientId } = v as Record<string, unknown>;
  return typeof tenantId === "string" && guid.test(tenantId) && typeof chatClientId === "string" && guid.test(chatClientId);
}

/** For tests. */
export function resetChatConfig() {
  loaded = undefined;
}
