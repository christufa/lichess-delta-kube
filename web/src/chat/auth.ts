import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";
import { type ChatConfig, loadChatConfig } from "@/chat/config";

/**
 * Microsoft sign-in for the chat (MSAL, auth code + PKCE, no secret). The redirect flow, not a
 * popup: the page sends Cross-Origin-Opener-Policy same-origin, which cuts a popup off from it.
 * Entra sends the user back to https://<host>/ with the response in the URL hash; main.tsx hands
 * that to `completeSignIn` before the router starts.
 */

let app: Promise<PublicClientApplication> | undefined;

function msal(config: ChatConfig): Promise<PublicClientApplication> {
  app ??= (async () => {
    const pca = new PublicClientApplication({
      auth: {
        clientId: config.chatClientId,
        authority: `https://login.microsoftonline.com/${config.tenantId}`,
        redirectUri: `${window.location.origin}/`,
        postLogoutRedirectUri: `${window.location.origin}/`,
      },
      cache: { cacheLocation: "localStorage" }, // stay signed in across tabs
    });
    await pca.initialize();
    return pca;
  })();
  return app;
}

async function ready(): Promise<{ pca: PublicClientApplication; scopes: string[] } | null> {
  const config = await loadChatConfig();
  if (!config) return null;
  return { pca: await msal(config), scopes: [`api://${config.chatClientId}/Chat.Access`] };
}

/** True when the URL holds an Entra sign-in response (#code=… / #error=…). */
export function isSignInResponse(hash = window.location.hash): boolean {
  return /[#&](code|error)=/.test(hash) && /[#&]state=/.test(hash);
}

/** Process the sign-in response in the URL, if any. Returns the account that signed in. */
export async function completeSignIn(): Promise<AccountInfo | null> {
  const m = await ready();
  if (!m) return null;
  const result = await m.pca.handleRedirectPromise();
  if (result?.account) m.pca.setActiveAccount(result.account);
  return result?.account ?? null;
}

export async function currentAccount(): Promise<AccountInfo | null> {
  const m = await ready();
  if (!m) return null;
  return m.pca.getActiveAccount() ?? m.pca.getAllAccounts()[0] ?? null;
}

export async function signIn(): Promise<void> {
  const m = await ready();
  if (!m) throw new Error("the chat isn't configured");
  await m.pca.loginRedirect({ scopes: m.scopes, prompt: "select_account" });
}

/** Forget the account here (no round trip to Microsoft: other Microsoft sign-ins stay). */
export async function signOut(): Promise<void> {
  const m = await ready();
  if (!m) return;
  await m.pca.clearCache();
  m.pca.setActiveAccount(null);
}

/** A token for the chat API, or null when the user has to sign in (again). */
export async function getToken(): Promise<string | null> {
  const m = await ready();
  if (!m) return null;
  const account = m.pca.getActiveAccount() ?? m.pca.getAllAccounts()[0];
  if (!account) return null;
  try {
    const result = await m.pca.acquireTokenSilent({ scopes: m.scopes, account });
    return result.accessToken;
  } catch (e) {
    if (e instanceof InteractionRequiredAuthError) return null;
    throw e;
  }
}
