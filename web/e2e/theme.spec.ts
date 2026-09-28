import { expect, test } from "@playwright/test";

import { signInAsE2eAdmin } from "./constants";

/**
 * DD-41 ships dark mode, and nothing else in the suite can see it.
 *
 * `ui-visual.spec.ts` pins the viewport and never sets `colorScheme`, so all 35 baselines are
 * light, which means the visual project cannot catch a dark palette that never applied. This
 * spec is the gate instead, and it asserts computed color rather than taking pictures: 35 more
 * baselines to review is a real cost, and `getComputedStyle` is a stronger claim than an image
 * of the same thing.
 *
 * The three states matter separately (docs/DESIGN.md 2.1). A single `@media
 * (prefers-color-scheme: dark)` block gets the third one silently wrong: a user who picks Light
 * on a machine set to dark still gets dark. That is why `index.css` defines the dark set twice,
 * once guarded by `:root:not([data-theme="light"])` and once under `:root[data-theme="dark"]`.
 */

/** docs/DESIGN.md 2.1, `--color-ground`, as rgb() because that is what the browser reports. */
const LIGHT_GROUND = "rgb(246, 247, 249)";
const DARK_GROUND = "rgb(18, 20, 27)";

async function bodyBackground(page: import("@playwright/test").Page): Promise<string> {
  return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
}

test.describe("DD-41: the theme applies, in all three states", () => {
  test("light by default, when the system asks for light", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await signInAsE2eAdmin(page);

    expect(await bodyBackground(page)).toBe(LIGHT_GROUND);
    await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.*/);
  });

  test("dark when the system asks for dark, with no choice stored", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await signInAsE2eAdmin(page);

    expect(await bodyBackground(page)).toBe(DARK_GROUND);
  });

  test("an explicit light choice beats a dark system, and survives a reload", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark" });
    await signInAsE2eAdmin(page);
    expect(await bodyBackground(page)).toBe(DARK_GROUND);

    // The control lives in the SIDEBAR, not on a settings page. Addressed
    // through the sidebar explicitly: `getByLabel("Theme")` alone would keep passing from any
    // route once the control became global, asserting nothing about where it actually is.
    await page.getByTestId("sidebar").getByLabel("Theme").selectOption("light");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    expect(await bodyBackground(page)).toBe(LIGHT_GROUND);

    // The whole point of persisting it: the second visit must not repaint.
    await page.reload();
    expect(await bodyBackground(page)).toBe(LIGHT_GROUND);
    expect(await page.evaluate(() => window.localStorage.getItem("gw-theme"))).toBe("light");
  });

  /**
   * The faces are not asserted by screenshot for the reason in the file header, and they need
   * asserting: fontsource's variable packages declare `Bricolage Grotesque Variable` and
   * `Figtree Variable`, not the plain family names. Naming the plain ones matches nothing, so
   * every heading would render in Helvetica Neue with nothing red anywhere.
   */
  /**
   * The other half of the double definition, and the one a mutation caught this spec missing:
   * deleting `:root[data-theme="dark"]` from index.css left every test above passing. The
   * media block cannot serve this case, because the system is asking for light.
   */
  test("an explicit dark choice beats a light system", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "light" });
    await signInAsE2eAdmin(page);
    expect(await bodyBackground(page)).toBe(LIGHT_GROUND);

    await page.getByTestId("sidebar").getByLabel("Theme").selectOption("dark");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    expect(await bodyBackground(page)).toBe(DARK_GROUND);

    await page.reload();
    expect(await bodyBackground(page)).toBe(DARK_GROUND);
  });

  test("the faces the tokens name are the faces that load", async ({ page }) => {
    await signInAsE2eAdmin(page);
    // The wordmark lives on the login page, which is behind us now; `/people`'s own h1 carries
    // the display face, so it is the element to read after signing in. What this test needs is
    // any signed-in page with an `h1` in the display face.
    //
    // `document.querySelector("h1")` below reads the FIRST h1 in the document, and the sidebar
    // sits in front of `main`. That is exactly why the sidebar renders no heading element
    // (asserted in `shell.spec.ts`): if it ever grows one, this
    // test silently starts measuring the workspace name instead of the page title.
    await page.goto("/people");
    await page.getByRole("heading", { level: 1, name: "People & agents" }).waitFor();
    await page.evaluate(() => document.fonts.ready);

    /**
     * The assertion takes the family from the ELEMENT's computed style and then asks whether
     * that family resolves. Checking a hardcoded name instead proves only that the package is
     * installed: a test that did exactly that passed happily with the token stack reordered so
     * the product rendered in Helvetica. What matters is that the name
     * the token uses is a name the browser can find.
     */
    const result = await page.evaluate(async () => {
      const title = document.querySelector("h1") as HTMLElement | null;
      const first = (value: string) => value.split(",")[0].trim().replace(/^['"]|['"]$/g, "");
      const families = {
        display: first(getComputedStyle(title!).fontFamily),
        sans: first(getComputedStyle(document.body).fontFamily),
      };
      await Promise.all(
        Object.values(families).map((family) => document.fonts.load(`400 16px "${family}"`)),
      );
      return {
        families,
        resolves: {
          display: document.fonts.check(`600 24px "${families.display}"`),
          sans: document.fonts.check(`400 14px "${families.sans}"`),
        },
      };
    });

    expect(result.resolves).toEqual({ display: true, sans: true });
    // Named explicitly too, so a failure says which face rather than just "false".
    expect(result.families.display).toBe("Bricolage Grotesque Variable");
    expect(result.families.sans).toBe("Figtree Variable");
  });

  /**
   * A fence with teeth. `--color-accent` was one of the nineteen tokens DD-41 removed, and the
   * focus rule color-mixed it; an undefined var makes `color-mix()` invalid, and the ring would
   * have disappeared from every element in the product with nothing failing.
   */
  test("focus is still visible", async ({ page }) => {
    await signInAsE2eAdmin(page);

    const control = page.getByTestId("sidebar").getByLabel("Theme");
    await control.focus();
    const outline = await control.evaluate((el) => {
      const style = getComputedStyle(el);
      return { width: style.outlineWidth, style: style.outlineStyle, color: style.outlineColor };
    });
    expect(outline.style).toBe("solid");
    expect(outline.width).not.toBe("0px");
    expect(outline.color).not.toBe("rgba(0, 0, 0, 0)");
  });
});
