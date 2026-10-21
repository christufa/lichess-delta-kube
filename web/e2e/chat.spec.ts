import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures";

// The chat panel (#69) up to Microsoft's sign-in page. What happens after sign-in (streaming,
// tools, errors) is covered by src/chat/chat-panel.test.tsx against a faked backend.

const TENANT = "00000000-0000-0000-0000-000000000000";
const CHAT_APP = "00000000-0000-0000-0000-000000000003";

async function serveConfig(page: Page, config: object | null) {
  await page.route("**/config.json", (route) =>
    config ? route.fulfill({ json: config }) : route.fulfill({ status: 404, body: "" }),
  );
}

/** Microsoft's sign-in, faked: OIDC metadata, and the authorize page (whose URL is recorded). */
async function fakeMicrosoft(page: Page): Promise<URL[]> {
  const authorize: URL[] = [];
  await page.route("https://login.microsoftonline.com/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/.well-known/openid-configuration")) {
      const base = `https://login.microsoftonline.com/${TENANT}`;
      return route.fulfill({
        json: {
          issuer: `${base}/v2.0`,
          authorization_endpoint: `${base}/oauth2/v2.0/authorize`,
          token_endpoint: `${base}/oauth2/v2.0/token`,
          end_session_endpoint: `${base}/oauth2/v2.0/logout`,
          jwks_uri: `https://login.microsoftonline.com/${TENANT}/discovery/v2.0/keys`,
        },
      });
    }
    if (url.pathname.endsWith("/discovery/instance")) {
      const discovery = `https://login.microsoftonline.com/${TENANT}/v2.0/.well-known/openid-configuration`;
      return route.fulfill({ json: { tenant_discovery_endpoint: discovery, metadata: [] } });
    }
    if (url.pathname.endsWith("/oauth2/v2.0/authorize")) authorize.push(url);
    return route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Sign in</title><p>Microsoft sign-in</p>" });
  });
  return authorize;
}

function askButton(page: Page) {
  return page.getByRole("button", { name: "Ask" });
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("opens beside the page and asks for Microsoft sign-in", async ({ page }) => {
  await serveConfig(page, { tenantId: TENANT, chatClientId: CHAT_APP });
  await page.goto("/");
  await askButton(page).click();

  const panel = page.getByRole("complementary", { name: "Chat" });
  await expect(panel.getByRole("heading", { name: "Ask the lakehouse" })).toBeVisible();
  await expect(panel.getByRole("button", { name: "Sign in with Microsoft" })).toBeVisible();
  await expect(askButton(page)).toHaveAttribute("aria-expanded", "true");

  // Let the entrance animations finish so axe sees the panel at rest (as dashboard.spec.ts does).
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every((a) => a.playState !== "running" || a.effect?.getComputedTiming().iterations === Infinity),
  );
  const results = await new AxeBuilder({ page })
    .include("#chat-panel")
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  expect(results.violations.map((v) => v.id)).toEqual([]);

  // On a phone the panel covers the page; nothing sticks out sideways either way.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);

  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  await expect(askButton(page)).toBeFocused();
});

test("says so when the chat isn't configured", async ({ page }) => {
  await serveConfig(page, null);
  await page.goto("/");
  await askButton(page).click();
  await expect(page.getByText("The chat isn't set up on this site.")).toBeVisible();
});

test("sign-in goes to Microsoft with PKCE for the chat's scope", async ({ page, baseURL }) => {
  await serveConfig(page, { tenantId: TENANT, chatClientId: CHAT_APP });
  const authorize = await fakeMicrosoft(page);
  const errors: string[] = []; // CSP violations show up here (E2E_BASE_URL: nginx's headers)
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  await page.goto("/openings");
  await askButton(page).click();
  await page.getByRole("button", { name: "Sign in with Microsoft" }).click();

  await expect(page.getByText("Microsoft sign-in")).toBeVisible();
  expect(errors).toEqual([]);
  const url = authorize.at(-1);
  expect(url?.searchParams.get("client_id")).toBe(CHAT_APP);
  expect(url?.searchParams.get("response_type")).toBe("code");
  expect(url?.searchParams.get("code_challenge_method")).toBe("S256");
  expect(url?.searchParams.get("scope")).toContain(`api://${CHAT_APP}/Chat.Access`);
  expect(url?.searchParams.get("redirect_uri")).toBe(new URL("/", baseURL).href); // registered in infra/gitops/chat.tf
});
