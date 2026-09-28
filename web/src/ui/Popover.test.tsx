/**
 * `Popover` (docs/DESIGN.md 7.4 and 10).
 *
 * These are the assertions the whole design depends on being made **once**. It is a popover
 * built out of React state precisely so nothing is inherited from an engine — which means Escape,
 * click-outside, focus-in, focus-return and `aria-expanded` are this component's own code, and
 * every filter, group and sort chip gets them by composing with it rather than by
 * re-implementing them. If these fail, they fail here rather than on one screen out of four.
 *
 * The fixture below stands in for a call site, so `Popover`'s contract is asserted apart from
 * any one page that composes it.
 *
 * **Geometry is not asserted.** The panel's anchoring and offset are classes, and jsdom returns
 * zeroes from `getBoundingClientRect` (AGENTS.md, Traps).
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Popover } from "./Popover";

/** A trigger, a panel with two focusable controls, and an unrelated button to click outside. */
function Fixture({ onOpenChange }: { onOpenChange?: (open: boolean) => void }) {
  return (
    <div>
      <button type="button">before</button>
      <Popover trigger="Stage is Won" onOpenChange={onOpenChange}>
        {(close) => (
          <div>
            <input aria-label="Value" />
            <button type="button" onClick={close}>
              Apply
            </button>
          </div>
        )}
      </Popover>
      <button type="button">outside</button>
    </div>
  );
}

const trigger = () => screen.getByRole("button", { name: "Stage is Won" });

describe("Popover", () => {
  it("starts closed, with aria-expanded false and nothing to control", () => {
    render(<Fixture />);
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(trigger()).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("popover-panel")).toBeNull();
  });

  it("opens on click, flips aria-expanded, and points aria-controls at the panel", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());

    const panel = screen.getByTestId("popover-panel");
    expect(trigger()).toHaveAttribute("aria-expanded", "true");
    expect(trigger().getAttribute("aria-controls")).toBe(panel.id);
    expect(panel.id).not.toBe("");
  });

  it("flips aria-expanded back to false on close", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());
    expect(trigger()).toHaveAttribute("aria-expanded", "true");

    await user.keyboard("{Escape}");
    expect(trigger()).toHaveAttribute("aria-expanded", "false");
    expect(trigger()).not.toHaveAttribute("aria-controls");
  });

  it("moves focus into the panel on open", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());
    expect(screen.getByLabelText("Value")).toHaveFocus();
  });

  it("closes on Escape", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());
    expect(screen.getByTestId("popover-panel")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("popover-panel")).toBeNull();
  });

  it("returns focus to the trigger on Escape", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());
    expect(screen.getByLabelText("Value")).toHaveFocus();

    await user.keyboard("{Escape}");
    // Without this the keyboard user is dropped onto <body> and starts again at the top of the
    // document, which is the failure docs/DESIGN.md 10's "reachable and dismissable by keyboard"
    // is about.
    expect(trigger()).toHaveFocus();
  });

  it("closes on a click outside", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());

    await user.click(screen.getByRole("button", { name: "outside" }));
    expect(screen.queryByTestId("popover-panel")).toBeNull();
  });

  it("does not close on a click inside the panel", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());

    await user.click(screen.getByLabelText("Value"));
    expect(screen.getByTestId("popover-panel")).toBeInTheDocument();
  });

  it("closes and returns focus when the panel's own control calls close (the commit point)", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());

    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(screen.queryByTestId("popover-panel")).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it("toggles closed when the trigger is clicked again", async () => {
    const user = userEvent.setup();
    render(<Fixture />);
    await user.click(trigger());
    await user.click(trigger());
    expect(screen.queryByTestId("popover-panel")).toBeNull();
  });

  it("reports every open and close through onOpenChange", async () => {
    const user = userEvent.setup();
    const onOpenChange = vi.fn();
    render(<Fixture onOpenChange={onOpenChange} />);

    await user.click(trigger());
    await user.keyboard("{Escape}");
    expect(onOpenChange.mock.calls).toEqual([[true], [false]]);
  });

  it("is reachable and dismissable from the keyboard alone", async () => {
    const user = userEvent.setup();
    render(<Fixture />);

    await user.tab();
    expect(screen.getByRole("button", { name: "before" })).toHaveFocus();
    await user.tab();
    expect(trigger()).toHaveFocus();

    await user.keyboard("{Enter}");
    // Opened from the keyboard, and the panel's content is where focus went.
    expect(screen.getByLabelText("Value")).toHaveFocus();
    // The rest of the panel is a Tab away, because the panel is the trigger's next sibling in
    // the DOM rather than a portal somewhere else in the document.
    await user.tab();
    expect(screen.getByRole("button", { name: "Apply" })).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("popover-panel")).toBeNull();
    expect(trigger()).toHaveFocus();
  });

  it("focuses the panel itself when it holds nothing focusable", async () => {
    const user = userEvent.setup();
    render(
      <Popover trigger="About" panelTestId="about-panel">
        <p>Prospects we are actively working.</p>
      </Popover>,
    );
    await user.click(screen.getByRole("button", { name: "About" }));
    expect(screen.getByTestId("about-panel")).toHaveFocus();
  });

  it("names the trigger from triggerLabel when its content is not a name", async () => {
    render(
      <Popover trigger={<span aria-hidden="true">&hellip;</span>} triggerLabel="Row actions">
        <p>Edit</p>
      </Popover>,
    );
    expect(screen.getByRole("button", { name: "Row actions" })).toBeInTheDocument();
  });
});
