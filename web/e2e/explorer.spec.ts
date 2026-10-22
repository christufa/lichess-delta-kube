import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures";

// The opening explorer (#65): the board, the next moves from the API (mocked: e2e/fixtures.ts),
// the line in the URL.

function movesTable(page: Page) {
  return page.getByRole("table", { name: /Moves played from this position/ });
}

/** Drag a piece with the mouse through squares (small steps a frame apart, as the board's drag
 * sensor needs), releasing on the last one. */
async function drag(page: Page, ...squares: string[]) {
  const centre = async (sq: string) => {
    const b = await page.locator(`[data-square="${sq}"]`).boundingBox();
    if (!b) throw new Error(`square ${sq} not found`);
    return [b.x + b.width / 2, b.y + b.height / 2] as const;
  };
  let [x, y] = await centre(squares[0] ?? "");
  await page.mouse.move(x, y);
  await page.mouse.down();
  for (const sq of squares.slice(1)) {
    const [tx, ty] = await centre(sq);
    for (let i = 1; i <= 15; i++) {
      await page.mouse.move(x + ((tx - x) * i) / 15, y + ((ty - y) * i) / 15);
      await page.waitForTimeout(16);
    }
    [x, y] = [tx, ty];
  }
  await page.mouse.up();
}

test.beforeEach(async ({ page }) => {
  await mockApi(page);
});

test("shows the moves from the start, most played first", async ({ page }) => {
  await page.goto("/explorer");
  await expect(page.getByRole("heading", { name: "Explorer", level: 1 })).toBeVisible();
  await expect(page.getByRole("group", { name: "Board: the starting position" })).toBeVisible();
  const rows = movesTable(page).getByRole("row");
  await expect(rows.nth(1)).toContainText("e4");
  await expect(rows.nth(1)).toContainText("51,681,998");
  await expect(rows.nth(2)).toContainText("d4");
  await expect(page.getByRole("link", { name: "Example game for e4 on lichess.org" })).toHaveAttribute(
    "href",
    "https://lichess.org/aBcDeFgH",
  );
});

test("picking a move plays it, and Back and the browser go back", async ({ page }) => {
  await page.goto("/explorer");
  await page.getByRole("button", { name: "Play e4" }).click();
  await expect(page).toHaveURL(/moves=e4(&|$)/);
  await expect(page.getByRole("group", { name: "Board: after 1. e4" })).toBeVisible();
  await expect(movesTable(page).getByRole("row").nth(1)).toContainText("c5");

  await page.getByRole("button", { name: "Back" }).click();
  await expect(page.getByRole("group", { name: "Board: the starting position" })).toBeVisible();
  await page.goBack(); // moves are history: back returns to 1. e4
  await expect(page.getByRole("group", { name: "Board: after 1. e4" })).toBeVisible();
});

test("dragging a piece plays the move; an illegal drag doesn't", async ({ page }, info) => {
  test.skip(info.project.name === "phone", "mouse drag; the phone layout is checked below");
  await page.goto("/explorer");
  await expect(movesTable(page)).toBeVisible(); // the board's chunk and the data have loaded
  await expect(page.getByRole("img", { name: "White pawn on e2" })).toBeVisible();
  // Playwright's first synthetic drag on the page never activates the board's drag sensor (a
  // real browser's first drag works: checked by hand), so warm up with a no-op drag first.
  await drag(page, "a2", "b3", "a2"); // lifted and put back: no move
  await expect(page).toHaveURL(/\/explorer$/);
  await drag(page, "e2", "e4");
  await expect(page).toHaveURL(/moves=e4(&|$)/);
  await drag(page, "d8", "d1"); // the queen can't jump
  await expect(page).toHaveURL(/moves=e4(&|$)/);
});

test("a shared line opens at its position; the line's buttons jump back", async ({ page }) => {
  await page.goto("/explorer?moves=e4,c5,Nf3");
  await expect(page.getByRole("group", { name: "Board: after 1. e4 c5 2. Nf3" })).toBeVisible();
  await expect(page.getByText("No move from this position was played at least 5 times")).toBeVisible();
  await page.getByRole("navigation", { name: "Moves played" }).getByRole("button", { name: "1. e4" }).click();
  await expect(page.getByRole("group", { name: "Board: after 1. e4" })).toBeVisible();
});

test("filters reach the API and the URL", async ({ page }) => {
  await page.goto("/explorer");
  await expect(movesTable(page)).toBeVisible();
  const request = page.waitForRequest((r) => r.url().includes("/api/v1/explorer") && r.url().includes("speed=blitz"));
  await page.getByRole("combobox", { name: "Speed" }).click();
  await page.getByRole("option", { name: "Blitz" }).click();
  await request;
  await expect(page).toHaveURL(/speed=blitz/);
});

test("is accessible and fits a phone", async ({ page }) => {
  await page.goto("/explorer");
  await expect(movesTable(page)).toBeVisible();
  await page.waitForFunction(() =>
    document
      .getAnimations()
      .every((a) => a.playState !== "running" || a.effect?.getComputedTiming().iterations === Infinity),
  );
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("the main navigation links to it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Explorer" }).click();
  await expect(page).toHaveURL(/\/explorer$/);
});
