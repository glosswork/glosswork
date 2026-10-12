/**
 * `Chip` (docs/DESIGN.md 7.4).
 *
 * The behaviour that matters most here is the **composition**: a chip with a popover is a
 * `Popover` trigger wearing the chip recipe, so it inherits everything `Popover.test.tsx`
 * asserts. This file checks that the wiring is real — `aria-expanded` on the chip's own
 * sentence, Escape closing it, focus coming back to the chip — and does not re-derive the
 * behaviour, because the point of putting it in the primitive was to assert it once.
 *
 * **No geometry.** 7.4's 28px height and 999px radius are read off the class list, not measured:
 * jsdom returns zeroes from `getBoundingClientRect` (AGENTS.md, Traps).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Chip } from "./Chip";

describe("Chip", () => {
  it("renders the filter as a sentence", () => {
    render(<Chip data-testid="chip">Stage is not Lost</Chip>);
    expect(screen.getByTestId("chip")).toHaveTextContent("Stage is not Lost");
  });

  it("is a 28px capsule with a line-2 border", () => {
    render(<Chip data-testid="chip">Stage is not Lost</Chip>);
    const classes = screen.getByTestId("chip").className;
    expect(classes).toContain("h-7");
    expect(classes).toContain("rounded-full");
    expect(classes).toContain("border-line-2");
  });

  it("draws the add chip dashed (docs/DESIGN.md 7.4)", () => {
    render(
      <Chip data-testid="add" variant="add">
        + Add filter
      </Chip>,
    );
    const classes = screen.getByTestId("add").className;
    expect(classes).toContain("border-dashed");
  });

  it("does not draw the filter chip dashed", () => {
    render(<Chip data-testid="chip">Stage is not Lost</Chip>);
    expect(screen.getByTestId("chip").className).not.toContain("border-dashed");
  });

  it("renders no interactive element when it is only a sentence", () => {
    render(<Chip data-testid="chip">Group by Stage</Chip>);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("removes through a named button, not a bare glyph", async () => {
    const user = userEvent.setup();
    const onRemove = vi.fn();
    render(
      <Chip onRemove={onRemove} removeLabel="Remove filter: Stage is not Lost">
        Stage is not Lost
      </Chip>,
    );
    // A chip row is otherwise a row of identical crosses to a screen reader.
    await user.click(screen.getByRole("button", { name: "Remove filter: Stage is not Lost" }));
    expect(onRemove).toHaveBeenCalledOnce();
  });

  it("acts on click when it has no popover", async () => {
    const user = userEvent.setup();
    const onClick = vi.fn();
    render(<Chip onClick={onClick}>Sort: Next action</Chip>);
    await user.click(screen.getByRole("button", { name: "Sort: Next action" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  describe("composed with Popover", () => {
    function FilterChip({ onOpenChange }: { onOpenChange?: (open: boolean) => void }) {
      return (
        <Chip
          data-testid="chip"
          onRemove={() => {}}
          removeLabel="Remove filter"
          onOpenChange={onOpenChange}
          popover={(close) => (
            <div>
              <input aria-label="Value" />
              <button type="button" onClick={close}>
                Apply
              </button>
            </div>
          )}
        >
          Stage is not Lost
        </Chip>
      );
    }

    const sentence = () => screen.getByRole("button", { name: "Stage is not Lost" });

    it("makes the sentence the popover trigger, with aria-expanded", async () => {
      const user = userEvent.setup();
      render(<FilterChip />);
      expect(sentence()).toHaveAttribute("aria-expanded", "false");

      await user.click(sentence());
      expect(sentence()).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByTestId("popover-panel")).toBeInTheDocument();
    });

    it("inherits Escape-to-close and focus return from the primitive", async () => {
      const user = userEvent.setup();
      render(<FilterChip />);
      await user.click(sentence());
      expect(screen.getByLabelText("Value")).toHaveFocus();

      await user.keyboard("{Escape}");
      expect(screen.queryByTestId("popover-panel")).toBeNull();
      expect(sentence()).toHaveFocus();
    });

    it("paints the selection colour while its popover is open (docs/DESIGN.md 3)", async () => {
      const user = userEvent.setup();
      render(<FilterChip />);
      expect(screen.getByTestId("chip").className).not.toContain("bg-human-soft");

      await user.click(sentence());
      expect(screen.getByTestId("chip").className).toContain("bg-human-soft");

      await user.keyboard("{Escape}");
      expect(screen.getByTestId("chip").className).not.toContain("bg-human-soft");
    });

    it("reports open and close to the call site, which is where a condition commits", async () => {
      const user = userEvent.setup();
      const onOpenChange = vi.fn();
      render(<FilterChip onOpenChange={onOpenChange} />);

      await user.click(sentence());
      await user.click(screen.getByRole("button", { name: "Apply" }));
      expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
    });

    it("keeps the remove button outside the trigger, so both are reachable", async () => {
      const user = userEvent.setup();
      render(<FilterChip />);
      // A button may not contain a button; if it did, only one of the two would be operable.
      await user.tab();
      expect(sentence()).toHaveFocus();
      await user.tab();
      expect(screen.getByRole("button", { name: "Remove filter" })).toHaveFocus();
    });
  });
});

/**
 * The chip's colours are chosen once per property. Two utilities for one property in the same
 * class list are resolved by the stylesheet's order, not the list's, and that is how an open
 * chip never painted as open and a refused chip never painted as refused while both sets of
 * classes sat in the DOM. Whether the right one *paints* is a browser question
 * (`e2e/table-pilot-findings.spec.ts` reads the computed colour); what can be held here is that
 * there is never a second one for it to lose to.
 */
describe("Chip: one colour class per property, in every state", () => {
  const FILLS = ["bg-surface", "bg-human-soft"];
  const TEXTS = ["text-ink", "text-ink-2", "text-bad"];
  const BORDERS = ["border-line-2", "border-bad"];
  const among = (classes: string[], set: string[]) => classes.filter((one) => set.includes(one));

  it.each([
    ["filter", "plain", false, { fill: "bg-surface", text: "text-ink", border: "border-line-2" }],
    ["filter", "plain", true, { fill: "bg-human-soft", text: "text-ink", border: "border-line-2" }],
    ["filter", "refused", false, { fill: "bg-surface", text: "text-bad", border: "border-bad" }],
    ["filter", "refused", true, { fill: "bg-human-soft", text: "text-bad", border: "border-bad" }],
    ["add", "plain", false, { fill: "bg-surface", text: "text-ink-2", border: "border-line-2" }],
    ["add", "plain", true, { fill: "bg-human-soft", text: "text-ink", border: "border-line-2" }],
    ["add", "refused", false, { fill: "bg-surface", text: "text-bad", border: "border-bad" }],
  ] as const)("%s, %s, open %s", async (variant, tone, open, expected) => {
    const user = userEvent.setup();
    render(
      <Chip data-testid="chip" variant={variant} tone={tone} popover={<input aria-label="Value" />}>
        Stage is not Lost
      </Chip>,
    );
    if (open) await user.click(screen.getByRole("button", { name: "Stage is not Lost" }));
    const classes = screen.getByTestId("chip").className.split(/\s+/);
    expect(among(classes, FILLS)).toEqual([expected.fill]);
    expect(among(classes, TEXTS)).toEqual([expected.text]);
    expect(among(classes, BORDERS)).toEqual([expected.border]);
    expect(classes.includes("border-dashed")).toBe(variant === "add");
  });

  it("says its tone in the DOM only when it has one", () => {
    const { rerender } = render(<Chip data-testid="chip">Stage is not Lost</Chip>);
    expect(screen.getByTestId("chip")).not.toHaveAttribute("data-tone");
    rerender(
      <Chip data-testid="chip" tone="refused">
        Stage is not Lost
      </Chip>,
    );
    expect(screen.getByTestId("chip")).toHaveAttribute("data-tone", "refused");
  });
});

