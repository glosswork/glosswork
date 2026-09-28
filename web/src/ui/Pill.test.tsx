/**
 * `Pill` (docs/DESIGN.md 7.3).
 *
 * **No geometry is asserted here and none can be.** 7.3 specifies a 24px height and a 999px
 * radius; `getBoundingClientRect` returns zeroes under jsdom, so `toHaveStyle`/rect assertions
 * about either would pass against zero and prove nothing (AGENTS.md, Traps). The class list is
 * read instead, in the pattern `Avatar.test.tsx` already uses for the person/agent shapes, and
 * the pixel values are Playwright's to assert once a page renders a pill.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Pill } from "./Pill";
import { selectTone } from "./vocabulary";

/** The fill/text pair docs/DESIGN.md 3 gives each tone, repeated here so the test is a
 * specification rather than a mirror of the module's own table. */
const EXPECTED_CLASSES = {
  ok: ["bg-ok-soft", "text-ok"],
  warn: ["bg-warn-soft", "text-warn"],
  bad: ["bg-bad-soft", "text-bad"],
  human: ["bg-human-soft", "text-human-ink"],
  neutral: ["bg-sunk", "text-ink-2"],
} as const;

describe("Pill", () => {
  it("shows the option's display label, not its key", () => {
    render(<Pill label="Proposal sent" optionKey="proposal_sent" />);
    const pill = screen.getByTestId("pill");
    expect(pill).toHaveTextContent("Proposal sent");
    expect(pill.textContent).not.toContain("proposal_sent");
  });

  it("puts the option key on hover (docs/DESIGN.md 7.3)", () => {
    render(<Pill label="Proposal sent" optionKey="proposal_sent" />);
    expect(screen.getByTestId("pill")).toHaveAttribute("title", "proposal_sent");
  });

  it("carries no title when there is no key to show", () => {
    render(<Pill label="Proposal sent" />);
    expect(screen.getByTestId("pill")).not.toHaveAttribute("title");
  });

  it("carries the pill test hook", () => {
    render(<Pill label="Won" optionKey="won" data-testid="something-else" />);
    // The hook is the primitive's contract and a call site cannot take it away: the browser
    // tests assert "is a pill" with it.
    expect(screen.getByTestId("pill")).toHaveTextContent("Won");
  });

  it("takes its tone from selectTone over the option key alone", () => {
    for (const key of ["won", "lost", "negotiating", "proposal_sent", "qualified", "new"]) {
      const { unmount } = render(<Pill label={key} optionKey={key} />);
      const classes = screen.getByTestId("pill").className;
      for (const expected of EXPECTED_CLASSES[selectTone(key)]) {
        expect(classes, `${key} -> ${selectTone(key)}`).toContain(expected);
      }
      unmount();
    }
  });

  it("renders the neutral tone when there is no key to hash", () => {
    render(<Pill label="—" optionKey={null} />);
    const classes = screen.getByTestId("pill").className;
    expect(classes).toContain("bg-sunk");
    expect(classes).toContain("text-ink-2");
  });

  it("lets a caller override the hashed tone", () => {
    render(<Pill label="Won" optionKey="won" tone="ok" />);
    expect(screen.getByTestId("pill").className).toContain("bg-ok-soft");
  });

  it("is a capsule, not a Badge's card rectangle", () => {
    render(<Pill label="Won" optionKey="won" />);
    const classes = screen.getByTestId("pill").className;
    expect(classes).toContain("rounded-full");
    expect(classes).toContain("h-6");
    expect(classes).not.toContain("border");
  });
});
