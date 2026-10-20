import { defineConfig, devices } from "@playwright/test";

// Smoke tests against the production build (`vite preview`), with the API mocked
// (e2e/fixtures.ts). Run `npm run build` first; CI does. E2E_BASE_URL points them at a
// running server instead, e.g. the lichess-web container (nginx, with its CSP).
const external = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: external ?? "http://localhost:4173",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    // 375 px wide, touch: the layout has to work on a phone.
    { name: "phone", use: { ...devices["Desktop Chrome"], viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true } },
  ],
  webServer: external
    ? undefined
    : {
        command: "npm run preview",
        url: "http://localhost:4173",
        reuseExistingServer: !process.env.CI,
      },
});
