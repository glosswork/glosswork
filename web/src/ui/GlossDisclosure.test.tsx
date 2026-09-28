/**
 * `GlossDisclosure` (docs/DESIGN.md 7.11): the disclosure semantics
 * generalized out of `table-view/AboutDisclosure.tsx`. `TableView.test.tsx`'s "keeps the
 * description one click away" test already pins the table page's own call site (the `About`
 * label, `about-toggle`/`about-panel`); this file is the primitive's own coverage — the default
 * test ids, a caller's override, and the three properties 7.11 asks of any disclosure: starts
 * closed, `aria-controls` absent until open, and no heading of its own.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GlossDisclosure } from "./GlossDisclosure";

const DESCRIPTION = "Where this deal sits in the pipeline. Move it forward as it progresses.";

describe("GlossDisclosure", () => {
  it("starts closed, with no aria-controls and no panel in the document", () => {
    render(<GlossDisclosure label="?" description={DESCRIPTION} />);
    const toggle = screen.getByTestId("gloss-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("gloss-panel")).not.toBeInTheDocument();
  });

  it("opens on click, points aria-controls at the panel's id, and shows the description", async () => {
    const user = userEvent.setup();
    render(<GlossDisclosure label="?" description={DESCRIPTION} />);
    await user.click(screen.getByTestId("gloss-toggle"));

    const toggle = screen.getByTestId("gloss-toggle");
    const panel = screen.getByTestId("gloss-panel");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.getAttribute("aria-controls")).toBe(panel.getAttribute("id"));
    expect(panel).toHaveTextContent(DESCRIPTION);
  });

  it("closes on a second click, unmounting the panel and dropping aria-controls again", async () => {
    const user = userEvent.setup();
    render(<GlossDisclosure label="?" description={DESCRIPTION} />);
    await user.click(screen.getByTestId("gloss-toggle"));
    await user.click(screen.getByTestId("gloss-toggle"));

    expect(screen.getByTestId("gloss-toggle")).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByTestId("gloss-toggle")).not.toHaveAttribute("aria-controls");
    expect(screen.queryByTestId("gloss-panel")).not.toBeInTheDocument();
  });

  it("renders the caller's label as the toggle's own accessible name", () => {
    render(<GlossDisclosure label="?" description={DESCRIPTION} />);
    expect(screen.getByRole("button", { name: "?" })).toBeInTheDocument();
  });

  it("takes the caller's own test ids, overriding the defaults", () => {
    render(
      <GlossDisclosure
        label="About"
        description="A funded, sponsored workstream."
        toggleTestId="about-toggle"
        panelTestId="about-panel"
      />,
    );
    expect(screen.getByTestId("about-toggle")).toBeInTheDocument();
    expect(screen.queryByTestId("gloss-toggle")).not.toBeInTheDocument();
  });

  it("emits no heading of its own, open or closed (docs/DESIGN.md 7.11)", async () => {
    const user = userEvent.setup();
    render(<GlossDisclosure label="?" description={DESCRIPTION} />);
    expect(screen.queryAllByRole("heading")).toHaveLength(0);

    await user.click(screen.getByTestId("gloss-toggle"));
    expect(screen.queryAllByRole("heading")).toHaveLength(0);
  });
});
