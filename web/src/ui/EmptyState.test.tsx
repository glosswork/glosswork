import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyState } from "./EmptyState";

/**
 * An `EmptyState` inside a bordered card is a card inside a card. The primitive cannot know
 * what contains it, so the caller says so with an explicit prop — not a CSS descendant rule,
 * because an appearance changing silently by ancestry is exactly the kind of
 * action-at-a-distance that nests cards unnoticed.
 *
 * The screen-level proof that no nested pair survives is a Playwright assertion
 * (`e2e/ui-visual.spec.ts`); these two cover the primitive's contract.
 */
describe("EmptyState", () => {
  it("carries its own border by default", () => {
    render(<EmptyState title="No records yet" data-testid="empty" />);
    const block = screen.getByTestId("empty");
    expect(block.className).toContain("border-dashed");
    expect(block.className).toContain("rounded-card");
  });

  it("drops the border, and keeps its text and spacing, when bordered={false}", () => {
    render(
      <EmptyState title="No agent labels yet." bordered={false} data-testid="empty">
        Nothing here yet.
      </EmptyState>,
    );
    const block = screen.getByTestId("empty");
    expect(block.className).not.toContain("border");
    expect(block.className).not.toContain("rounded-card");
    // "keeps its text and spacing" is the other half of the decision, so it is asserted rather
    // than assumed: the padding and the centred measure stay.
    expect(block.className).toContain("px-5");
    expect(block.className).toContain("py-7");
    expect(block.className).toContain("text-center");
    expect(block.className).toContain("max-w-lg");
    expect(screen.getByText("No agent labels yet.")).toBeInTheDocument();
    expect(screen.getByText("Nothing here yet.")).toBeInTheDocument();
  });
});
