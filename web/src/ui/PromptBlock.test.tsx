import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { PromptBlock } from "./PromptBlock";

/**
 * `docs/DESIGN.md` 7.10, built for the first-run screen.
 *
 * The load-bearing assertion is the first one. The block renders parts of its text in the mono
 * face, so the DOM is several nodes while the clipboard takes one string; splitting the string
 * on its own mono segments is what keeps those two the same text. A template
 * rendered separately from a template copied is the defect this test exists to prevent.
 */
const TEXT =
  "Connect to https://northwind.glosswork.app/mcp and call describe_capabilities before you write.";
const MONO = ["https://northwind.glosswork.app/mcp", "describe_capabilities"];

afterEach(() => {
  vi.restoreAllMocks();
});

function stubClipboard(impl: () => Promise<void>) {
  // jsdom has no clipboard, and a real deployment may have none either.
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText: vi.fn(impl) },
    configurable: true,
  });
  return navigator.clipboard.writeText as ReturnType<typeof vi.fn>;
}

describe("PromptBlock", () => {
  it("copies exactly the text it displays", async () => {
    const writeText = stubClipboard(() => Promise.resolve());
    const { container } = render(<PromptBlock text={TEXT} mono={MONO} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(writeText).toHaveBeenCalledWith(TEXT);
    // The rendered text and the copied text are one string, whatever the node structure is.
    expect(container.querySelector("[data-testid='prompt-block-text']")?.textContent).toBe(TEXT);
  });

  it("renders each declared segment in the mono face", () => {
    render(<PromptBlock text={TEXT} mono={MONO} />);
    for (const segment of MONO) {
      expect(screen.getByText(segment).className).toContain("font-mono");
    }
  });

  it("says so when the clipboard refuses, rather than appearing to have worked", async () => {
    // The Clipboard API is gated on a secure context, so a plain-HTTP deployment on
    // anything but localhost rejects or is absent. No e2e run can reach this, because
    // E2E_BASE_URL is http://localhost.
    stubClipboard(() => Promise.reject(new Error("denied")));
    render(<PromptBlock text={TEXT} mono={MONO} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not copy automatically. Select the text and copy it by hand.",
    );
  });

  it("says so when there is no clipboard at all", async () => {
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    render(<PromptBlock text={TEXT} mono={MONO} />);

    await userEvent.click(screen.getByRole("button", { name: "Copy" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("keeps the text selectable, which is the fallback the alert points at", () => {
    render(<PromptBlock text={TEXT} mono={MONO} />);
    const block = screen.getByTestId("prompt-block-text");
    expect(block.className).not.toContain("select-none");
  });

  it("carries a wrapping guard, so a long URL cannot overflow a narrow viewport", () => {
    // docs/DESIGN.md 9: nothing reachable at 1280 is unreachable at 800. A mono URL is one
    // unbreakable token and needs a guard, like the one AccessTokensPanel.tsx:90 applies.
    //
    // The guard is `break-words` on the paragraph, NOT `break-all` on the segments: `break-all`
    // broke `describe_capabilities` mid-identifier at full width. This asserts the
    // class only; whether the text actually fits is layout, and jsdom returns zeroes for
    // layout, so the narrow rendering was checked in a real browser instead.
    render(<PromptBlock text={TEXT} mono={MONO} />);
    expect(screen.getByTestId("prompt-block-text").className).toContain("break-words");
    expect(screen.getByText(MONO[1]).className).not.toContain("break-all");
  });
});
