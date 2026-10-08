/**
 * The trial banner (change 30), against a real uvicorn process configured as a workspace on
 * trial: `GW_TRIAL_ENDS_AT` is `TRIAL_ENDS_AT` and `GW_SUBSCRIBE_URL` is `TRIAL_SUBSCRIBE_URL`
 * (`playwright.config.ts`). The self-hosted case, no trial end and so no banner, is asserted on
 * the ordinary server in `shell.spec.ts`.
 *
 * **The browser's clock is pinned, and timers keep running.** The banner is the browser's own
 * subtraction of its clock from the end time, once a second, so `page.clock.setFixedTime` is
 * what makes an exact `HH:MM` assertable with no waiting and no test-only server setting. The
 * clock is pinned before the first navigation, so the real sign-in runs under it too.
 *
 * **The words are written out here, not imported from the component's constants.** These are the
 * sentences the maintainer approved for a customer to read, and an assertion that imports the
 * constant it checks cannot fail when the constant changes. Text is asserted with locators,
 * never by screenshot (AGENTS.md, Traps).
 */
import { expect, test, type Page } from "@playwright/test";

import {
  E2E_ADMIN_EMAIL,
  E2E_ADMIN_PASSWORD,
  TRIAL_BASE_URL,
  TRIAL_ENDS_AT,
  TRIAL_SUBSCRIBE_URL,
  signInAs,
} from "./constants";

test.use({ baseURL: TRIAL_BASE_URL });

const WIDE = { width: 1280, height: 800 };
/** Below the 960px breakpoint, where the top bar replaces the sidebar (docs/DESIGN.md 9). */
const NARROW = { width: 800, height: 800 };

/** 23 hours 59 minutes before `TRIAL_ENDS_AT`. */
const ONE_MINUTE_IN = "2030-01-01T00:01:00Z";
/** 30 seconds before it: rounded up to the minute, never shown as `00:00`. */
const THIRTY_SECONDS_LEFT = "2030-01-01T23:59:30Z";

async function signInOnTrial(page: Page): Promise<void> {
  await signInAs(page, E2E_ADMIN_EMAIL, E2E_ADMIN_PASSWORD);
}

test("the trial banner counts down on one open page, then says the trial ended", async ({
  page,
}) => {
  await page.setViewportSize(WIDE);
  await page.clock.setFixedTime(ONE_MINUTE_IN);
  // The real password sign-in, with the clock already pinned: `signInAs` returns only once
  // the signed-in block is visible.
  await signInOnTrial(page);

  const banner = page.getByTestId("trial-banner");
  const timeLeft = page.getByTestId("trial-time-left");
  const subscribe = page.getByTestId("trial-subscribe");

  await expect(timeLeft).toHaveText("23:59");
  await expect(page.getByTestId("trial-message")).toHaveText("Your trial has 23:59 left.");
  await expect(subscribe).toHaveText("Subscribe");
  await expect(subscribe).toHaveAttribute("href", TRIAL_SUBSCRIBE_URL);
  // The same tab: a subscribe page that opens beside the workspace is a tab the person has to
  // find their way back from.
  await expect(subscribe).not.toHaveAttribute("target");
  // What a screen reader is given, read from the accessibility tree rather than from an
  // attribute: an `aria-label` on a plain `span` is not in that tree at all.
  await expect(banner).toMatchAriaSnapshot(`
    - region "Trial":
      - paragraph: Your trial has 23 hours 59 minutes left.
      - link "Subscribe"
  `);

  // From here on: no navigation and no reload. Only the clock moves, so only a banner that
  // recomputes by itself can follow it.
  await page.clock.setFixedTime(THIRTY_SECONDS_LEFT);
  await expect(timeLeft).toHaveText("00:01");
  await expect(page.getByTestId("trial-message")).toHaveText("Your trial has 00:01 left.");
  await expect(banner).toMatchAriaSnapshot(`
    - region "Trial":
      - paragraph: Your trial has 0 hours 1 minute left.
  `);

  await page.clock.setFixedTime(TRIAL_ENDS_AT);
  await expect(page.getByTestId("trial-message")).toHaveText("Trial ended.");
  await expect(timeLeft).toHaveCount(0);
  await expect(subscribe).toHaveText("Subscribe");
  await expect(subscribe).toHaveAttribute("href", TRIAL_SUBSCRIBE_URL);
  await expect(banner).toMatchAriaSnapshot(`
    - region "Trial":
      - paragraph: Trial ended.
      - link "Subscribe"
  `);
});

test("the trial banner is on /setup as well as the home page", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await page.clock.setFixedTime(ONE_MINUTE_IN);
  await signInOnTrial(page);
  await expect(page.getByTestId("trial-time-left")).toHaveText("23:59");

  await page.goto("/setup");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await expect(page.getByTestId("trial-time-left")).toHaveText("23:59");
  await expect(page.getByTestId("trial-banner")).toHaveCount(1);
});

for (const viewport of [WIDE, NARROW]) {
  test(`the trial banner sits at the head of the main column and adds no height at ${viewport.width}px`, async ({
    page,
  }) => {
    // Signed in wide and then resized, as every narrow scenario is: below the breakpoint the
    // signed-in block `signInAs` waits for is inside a closed menu.
    await page.setViewportSize(WIDE);
    await page.clock.setFixedTime(ONE_MINUTE_IN);
    await signInOnTrial(page);
    await page.setViewportSize(viewport);
    // `/people` with one person on it is shorter than the window, so any height the strip adds
    // to the page shows as a scroll height past the window's. Not `/setup`: for an
    // administrator that page is about twice the window's height with or without the strip
    // (measured: 1570 pixels without it at 1280 by 800), so it cannot show the difference.
    await page.goto("/people");
    await expect(page.getByTestId("trial-time-left")).toHaveText("23:59");

    const geometry = await page.evaluate(() => {
      const main = document.querySelector("main");
      const strip = document.querySelector('[data-testid="trial-banner"]');
      const person = document.querySelector('[data-testid="current-principal"]');
      if (main === null || strip === null) return null;
      const mainBox = main.getBoundingClientRect();
      const stripBox = strip.getBoundingClientRect();
      return {
        insideMain: main.contains(strip),
        mainTop: mainBox.top,
        mainLeft: mainBox.left,
        stripTop: stripBox.top,
        stripLeft: stripBox.left,
        stripHeight: stripBox.height,
        scrollHeight: document.documentElement.scrollHeight,
        scrollHeightWithoutStrip: (() => {
          const element = strip as HTMLElement;
          element.style.display = "none";
          const height = document.documentElement.scrollHeight;
          element.style.display = "";
          return height;
        })(),
        innerHeight: window.innerHeight,
        personBottom: person === null ? null : person.getBoundingClientRect().bottom,
      };
    });

    expect(geometry, "no <main> or no trial banner on the page").not.toBeNull();
    if (geometry === null) return;
    // Soft assertions: each is a separate claim about where the strip is, and a strip in the
    // wrong place should say every way in which it is wrong, not only the first.
    expect.soft(geometry.stripHeight, "the strip is collapsed to zero height").toBeGreaterThan(0);
    expect.soft(geometry.insideMain, "the strip is outside <main>").toBe(true);
    expect.soft(geometry.stripTop, "the strip is not at the top of <main>").toBe(geometry.mainTop);
    expect.soft(geometry.stripLeft, "the strip does not start at <main>'s left edge").toBe(
      geometry.mainLeft,
    );
    // The subject first: a page that is taller than the window on its own says nothing about
    // what the strip adds, and would fail the next line for the wrong reason.
    expect.soft(
      geometry.scrollHeightWithoutStrip,
      "this page is taller than the window even without the strip; assert on a shorter one",
    ).toBe(geometry.innerHeight);
    expect.soft(geometry.scrollHeight, "the strip made the page taller than the window").toBe(
      geometry.innerHeight,
    );
    if (viewport === WIDE) {
      // The sign-out control lives in this block; pushed below the fold it is out of reach on
      // a page that does not scroll.
      expect.soft(geometry.personBottom, "current-principal is not on the page").not.toBeNull();
      expect.soft(geometry.personBottom ?? Infinity).toBeLessThanOrEqual(geometry.innerHeight);
    }
  });
}
