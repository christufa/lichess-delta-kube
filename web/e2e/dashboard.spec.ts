import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures";

/**
 * Waits for the entrance animations (CSS and Web Animations, not endless ones like a
 * loading pulse) to finish, so axe sees the page at rest rather than mid-fade.
 */
async function settle(page: Page) {
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every((a) => a.playState !== "running" || a.effect?.getComputedTiming().iterations === Infinity),
  );
}

/** Fails on any axe violation of WCAG 2.x A/AA rules. */
async function expectNoA11yViolations(page: Page) {
  await settle(page);
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const summary = results.violations.map((v) => `${v.id}: ${v.help} (${v.nodes.map((n) => n.target.join(" ")).join(", ")})`);
  expect(summary).toEqual([]);
}

/** The main navigation (the pages also link to each other in their content). */
function nav(page: Page) {
  return page.getByRole("navigation", { name: "Main" });
}

/** Nothing sticks out sideways (the phone layout at 375 px). */
async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

/** Console errors and CSP violations while the page runs (expected to stay empty). */
function collectErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  page.on("pageerror", (e) => errors.push(e.message));
  return errors;
}

test.describe("overview", () => {
  test("shows the latest month, the charts and the rating distribution", async ({ page }) => {
    const errors = collectErrors(page);
    await mockApi(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1, name: "Lichess, month by month" })).toBeVisible();
    // The hero sums every loaded month (fixtures: 38 months, 109,250,000 games).
    const hero = page.getByRole("region", { name: "Lichess, month by month" });
    await expect(hero.getByTitle("109,250,000")).toBeVisible();
    await expect(hero.getByText("months loaded")).toBeVisible();
    await expect(hero.getByText("38", { exact: true })).toBeAttached(); // the counter's final value, for screen readers
    await expect(page.getByRole("heading", { name: "Sep 2026" })).toBeVisible();
    await expect(page.getByText("Games", { exact: true }).first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Games per month, by speed" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Speed mix" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "How games end" })).toBeVisible();
    await expect(page.getByRole("img", { name: /games per month for each speed/ })).toBeVisible();
    await expect(page.getByRole("img", { name: /blitz player-games by rating in Sep 2026/ })).toBeVisible();
    await expect(page.getByText(/2,000 more had no rating/)).toBeVisible(); // player-games (fixtures: 2000)
    await expect(page).toHaveTitle("Overview · Lichess lakehouse");

    await expectNoHorizontalScroll(page);
    await expectNoA11yViolations(page);
    expect(errors).toEqual([]);
  });

  test("filters live in the URL", async ({ page }) => {
    await mockApi(page);
    await page.goto("/?from=2014-01&to=2014-12&metric=players&speed=bullet&month=2014-06");

    await expect(page.getByRole("heading", { name: "Dec 2014" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Players per month, by speed" })).toBeVisible();
    await expect(page.getByRole("img", { name: /bullet player-games by rating in Jun 2014/ })).toBeVisible();

    await page.getByRole("radio", { name: "Games" }).click();
    await expect(page.getByRole("heading", { name: "Games per month, by speed" })).toBeVisible();
    await expect(page).not.toHaveURL(/metric=/);
  });

  test("the data behind a chart is available as a table", async ({ page }) => {
    await mockApi(page);
    await page.goto("/?from=2015-10&to=2015-12");
    const card = page.getByRole("region", { name: "Speed mix" });
    await card.getByText("Show the numbers").click();
    await expect(card.getByRole("table")).toContainText("Nov 2015");
  });

  test("an API failure shows an error with a retry", async ({ page }) => {
    await mockApi(page, { fail: /summary/ });
    await page.goto("/");
    // The client retries a 503 twice (1 s, then 2 s) before it gives up.
    await expect(page.getByRole("alert")).toContainText("The data store is waking up", { timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Try again" })).toBeVisible();
    await expectNoA11yViolations(page);
  });
});

test.describe("openings", () => {
  test("lists the top openings with their results", async ({ page }) => {
    const errors = collectErrors(page);
    await mockApi(page);
    await page.goto("/openings");

    await expect(page.getByRole("heading", { level: 1, name: "Openings" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "Openings" })).toHaveAttribute("aria-current", "page");
    const table = page.getByRole("table", { name: /Most played openings/ });
    await expect(table.getByRole("row")).toHaveCount(11); // header + 10
    await expect(table).toContainText("Scandinavian Defense");
    await expect(page.getByText(/1,250,000 all games, Sep 2026/)).toBeVisible();
    const highlights = page.getByRole("region", { name: "Highlights among the top 10" });
    await expect(highlights.getByRole("listitem").filter({ hasText: "Most played" })).toContainText("Scandinavian Defense");
    await expect(page).toHaveTitle("Openings · Lichess lakehouse");

    await expectNoHorizontalScroll(page);
    await expectNoA11yViolations(page);
    expect(errors).toEqual([]);
  });

  test("speed and rating band filters reach the API and the URL", async ({ page }) => {
    await mockApi(page);
    await page.goto("/openings");
    await expect(page.getByRole("table").getByText("Scandinavian Defense")).toBeVisible();

    const request = page.waitForRequest((r) => r.url().includes("/api/v1/openings") && r.url().includes("speed=blitz"));
    await page.getByLabel("Speed").click();
    await page.getByRole("option", { name: "Blitz" }).click();
    await request;
    await expect(page).toHaveURL(/speed=blitz/);

    const banded = page.waitForRequest((r) => r.url().includes("rating_band=1400"));
    await page.getByLabel("Rating band").click();
    await page.getByRole("option", { name: "1400–1599" }).click();
    await banded;
    await expect(page.getByText(/blitz games, average rating 1400–1599/)).toBeVisible();
  });

  test("sorting reorders the rows but keeps the popularity rank", async ({ page }) => {
    await mockApi(page);
    await page.goto("/openings");
    const table = page.getByRole("table", { name: /Most played openings/ });
    const firstRow = table.getByRole("row").nth(1);
    await expect(firstRow).toContainText("Scandinavian Defense");

    // Fixtures: White's share grows down the list, so the 10th is best for White.
    await page.getByRole("radio", { name: "White wins" }).click();
    await expect(firstRow).toContainText("Sicilian Defense: Bowdler Attack");
    await expect(firstRow.getByRole("cell").first()).toHaveText("10");
    await expect(page.getByRole("listitem").filter({ hasText: "Best for White" })).toContainText("Bowdler Attack");
  });

  test("navigates from the overview", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await nav(page).getByRole("link", { name: "Openings" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "Openings" })).toBeVisible();
    await expect(nav(page).getByRole("link", { name: "Openings" })).toHaveAttribute("aria-current", "page");
  });

  test("the hero's call to action leads to the openings", async ({ page }) => {
    await mockApi(page);
    await page.goto("/");
    await page.getByRole("link", { name: "Explore the top openings" }).click();
    await expect(page).toHaveURL(/\/openings$/);
  });
});

test.describe("reduced motion", () => {
  test.use({ reducedMotion: "reduce" });

  test("the page is usable at once, without CSS animations", async ({ page }) => {
    const errors = collectErrors(page);
    await mockApi(page);
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "Lichess, month by month" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Games per month, by speed" })).toBeVisible();
    const longCssAnimations = await page.evaluate(
      () =>
        document
          .getAnimations()
          .filter((a) => a instanceof CSSAnimation && Number(a.effect?.getComputedTiming().duration) > 1).length,
    );
    expect(longCssAnimations).toBe(0);
    await expectNoA11yViolations(page);
    expect(errors).toEqual([]);
  });
});

test("unknown pages say so", async ({ page }) => {
  await mockApi(page);
  await page.goto("/nope");
  await expect(page.getByRole("heading", { name: "Page not found" })).toBeVisible();
});
