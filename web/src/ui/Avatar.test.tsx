/**
 * docs/DESIGN.md 6.1 to 6.3, and the invariant: kind is shape, never colour alone.
 *
 * The shape assertions read `rounded-full` / `rounded-card` off the class list rather than
 * measuring anything: `getBoundingClientRect` returns zeroes in jsdom (AGENTS.md, Traps), so
 * geometry is proven in Playwright and the *mapping* from kind to shape is proven here.
 */
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Avatar, Hand, Pair } from "./Avatar";

const DANA = { display_name: "Dana Reyes", type: "user" as const };
const IMPORTER = { display_name: "Nightly Importer", type: "service_account" as const };
const SALES = { label: "sales-agent", display_name: null };

describe("Avatar", () => {
  it("renders a person as a circle and an agent as a square", () => {
    const { rerender } = render(<Avatar kind="person" text="DR" label="Dana Reyes" />);
    const person = screen.getByTestId("avatar");
    expect(person.dataset.kind).toBe("person");
    expect(person.className).toContain("rounded-full");
    expect(person.className).not.toContain("rounded-card");

    rerender(<Avatar kind="agent" text="SA" label="sales-agent" />);
    const agent = screen.getByTestId("avatar");
    expect(agent.dataset.kind).toBe("agent");
    expect(agent.className).toContain("rounded-card");
    expect(agent.className).not.toContain("rounded-full");
  });

  it("pairs each shape with its own palette, so neither can be swapped alone", () => {
    const { rerender } = render(<Avatar kind="person" text="DR" label="Dana Reyes" />);
    expect(screen.getByTestId("avatar").className).toContain("bg-human-soft");
    rerender(<Avatar kind="agent" text="SA" label="sales-agent" />);
    expect(screen.getByTestId("avatar").className).toContain("bg-agent-soft");
  });

  it("carries an accessible name", () => {
    render(<Avatar kind="person" text="DR" label="Dana Reyes" />);
    expect(screen.getByRole("img", { name: "Dana Reyes" })).toBeTruthy();
  });

  it("renders the shape alone rather than a fabricated glyph when there is no text", () => {
    render(<Avatar kind="person" text="" label="Unknown" />);
    expect(screen.getByTestId("avatar").textContent).toBe("");
  });

  it("takes the density sizes 2.4 specifies", () => {
    const { rerender } = render(<Avatar kind="person" text="DR" label="d" size="row" />);
    expect(screen.getByTestId("avatar").className).toContain("h-5");
    rerender(<Avatar kind="person" text="DR" label="d" size="rowCompact" />);
    expect(screen.getByTestId("avatar").className).toContain("h-[18px]");
  });
});

describe("Hand", () => {
  it("renders a person with their initials and name", () => {
    render(<Hand principal={DANA} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("person");
    expect(screen.getByTestId("avatar").textContent).toBe("DR");
    expect(screen.getByTestId("hand").textContent).toContain("Dana Reyes");
  });

  it("renders an agent with its code and label", () => {
    render(<Hand agentLabel={SALES} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
    expect(screen.getByTestId("avatar").textContent).toBe("SA");
    expect(screen.getByTestId("hand").textContent).toContain("sales-agent");
  });

  it("names the person an agent acted for (6.2)", () => {
    render(<Hand principal={DANA} agentLabel={SALES} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
    expect(screen.getByTestId("hand").textContent).toContain("for Dana Reyes");
  });

  it("treats a service account with no label as an agent (6.1)", () => {
    // The case `type` is on the principals sidecar for. Without it this renders a
    // person circle: a wrong answer rendered confidently.
    render(<Hand principal={IMPORTER} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
  });

  it("never fabricates an agent when there is no label (6.5)", () => {
    render(<Hand principal={DANA} agentLabel={null} />);
    expect(screen.getByTestId("avatar").dataset.kind).toBe("person");
    expect(screen.getByTestId("hand").textContent).not.toContain("agent");
  });

  it("falls back to the raw id in mono when the sidecar resolved nothing", () => {
    // The raw-id fallback (DD-25): an id is still useful on its own, and visibly not a name.
    render(<Hand fallbackId="3f9d2c71-5b8e-4a06-9c1d-7e2b4a6f8d13" />);
    const hand = screen.getByTestId("hand");
    expect(hand.textContent).toContain("3f9d2c71");
    expect(hand.querySelector(".font-mono")).toBeTruthy();
  });

  it("keeps the id fallback on an agent write whose principal did not resolve", () => {
    // Found by a failing assertion in RecordDetailView: the "for <person>" clause once
    // rendered only when the principal resolved, so an agent write under an unresolved
    // principal dropped the id entirely -- losing it exactly where attribution matters most.
    render(<Hand agentLabel={SALES} fallbackId="3f9d2c71-5b8e-4a06-9c1d-7e2b4a6f8d13" />);
    const hand = screen.getByTestId("hand");
    expect(hand.textContent).toContain("sales-agent");
    expect(hand.textContent).toContain("3f9d2c71");
    // Not `querySelector(".font-mono")`: an agent avatar carries the mono face itself (6.1),
    // so the first match is the avatar's own two-letter code, not the id.
    const mono = [...hand.querySelectorAll(".font-mono")].map((n) => n.textContent);
    expect(mono).toContain("3f9d2c71-5b8e-4a06-9c1d-7e2b4a6f8d13");
  });

  it("renders the avatar alone when the column has no room for a name (6.4)", () => {
    render(<Hand principal={DANA} avatarOnly />);
    expect(screen.queryByTestId("hand")).toBeNull();
    expect(screen.getByTestId("avatar")).toBeTruthy();
  });
});

describe("Pair", () => {
  it("renders both avatars, person first, captioned with the one ampersand (6.3)", () => {
    render(<Pair principal={DANA} agentLabel={SALES} />);
    const avatars = screen.getAllByTestId("avatar");
    expect(avatars.map((a) => a.dataset.kind)).toEqual(["person", "agent"]);
    expect(screen.getByTestId("pair").parentElement?.textContent).toContain(
      "Dana Reyes & sales-agent",
    );
  });
});
