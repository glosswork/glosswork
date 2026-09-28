/**
 * `ActivityEvent` (docs/DESIGN.md 7.8): the one component a comment and a
 * version-producing write both render through. Presentational only (DD-3's frontend clause), so
 * every case below hands it fixture data rather than fetching anything.
 *
 * **Geometry is not asserted.** `getBoundingClientRect` is zeroes under jsdom (AGENTS.md, Traps),
 * so 7.8's grid and the time's right alignment are read off the class list — the pattern
 * `Pill.test.tsx` and `Avatar.test.tsx` already use — and get a real assertion from Playwright
 * on the pages that render one.
 */
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { ActivityEvent } from "./ActivityEvent";

const AGENT_HAND = { agentLabel: { label: "sales-agent", display_name: null } };
const PERSON_HAND = { principal: { display_name: "Sam Okafor", type: "user" as const } };

describe("ActivityEvent", () => {
  it("lays out on 7.8's grid, with a line rule beneath", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Good. Keep the phasing."
        isAgentAuthored={false}
      />,
    );
    const event = screen.getByTestId("activity-event");
    expect(event.className).toContain("grid-cols-[24px_1fr]");
    expect(event.className).toContain("border-b");
  });

  it("renders the header as a Hand, never a second attribution primitive", () => {
    render(
      <ActivityEvent
        hand={AGENT_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored
      />,
    );
    expect(screen.getByTestId("hand")).toHaveTextContent("sales-agent");
    expect(screen.getByTestId("avatar").dataset.kind).toBe("agent");
  });

  it("names the person an agent acted for, through Hand's own clause", () => {
    render(
      <ActivityEvent
        hand={{ ...AGENT_HAND, principal: { display_name: "Sam Okafor", type: "user" } }}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored
      />,
    );
    expect(screen.getByTestId("hand")).toHaveTextContent("for Sam Okafor");
  });

  it("renders the time through ui/datetime.ts, right-aligned, with the ISO form in dateTime", () => {
    // `formatTimestamp`'s "Today"/"Yesterday" wording depends on the clock the test runs under
    // (`ui/datetime.test.ts` pins that separately), so this only proves the wiring: the visible
    // text is not the raw ISO string, and the ISO form rides in `dateTime` and `title` instead
    // (docs/DESIGN.md 5).
    const { container } = render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2020-01-05T09:14:00Z"
        body="Good."
        isAgentAuthored={false}
      />,
    );
    const time = container.querySelector("time") as HTMLTimeElement;
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("dateTime", "2020-01-05T09:14:00Z");
    expect(time).toHaveAttribute("title", "2020-01-05T09:14:00Z");
    expect(time.textContent).not.toBe("2020-01-05T09:14:00Z");
    expect(time.className).toContain("ml-auto");
  });

  it("renders the body a caller supplies, markdown or plain, without deciding which", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body={<strong data-testid="rich-body">Rich body</strong>}
        isAgentAuthored={false}
      />,
    );
    expect(screen.getByTestId("rich-body")).toBeInTheDocument();
  });

  it("renders a changed field as a pill reading 'Field: old → new', new emphasized", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored={false}
        changes={[
          { id: "stage", fieldLabel: "Stage", oldValue: "Proposal sent", newValue: "Negotiating" },
        ]}
      />,
    );
    const pill = screen.getByTestId("activity-change-stage");
    expect(pill).toHaveTextContent("Stage: Proposal sent");
    expect(pill).toHaveTextContent("Negotiating");
    const strong = pill.querySelector("strong");
    expect(strong).not.toBeNull();
    expect(strong).toHaveTextContent("Negotiating");
  });

  it("collapses a change with no old value to '<Field> edited' rather than a bare arrow", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored={false}
        changes={[{ id: "notes", fieldLabel: "Notes", oldValue: null, newValue: "" }]}
      />,
    );
    expect(screen.getByTestId("activity-change-notes")).toHaveTextContent("Notes edited");
  });

  it("renders no change pills when the event carries none", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Good."
        isAgentAuthored={false}
      />,
    );
    expect(screen.queryByTestId(/^activity-change-/)).not.toBeInTheDocument();
  });

  it("carries section 3's feed wash only when the event is agent-authored", () => {
    const { rerender } = render(
      <ActivityEvent
        hand={AGENT_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored
      />,
    );
    expect(screen.getByTestId("activity-event").style.backgroundColor).toContain("color-mix");

    rerender(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Good."
        isAgentAuthored={false}
      />,
    );
    expect(screen.getByTestId("activity-event").style.backgroundColor).toBe("");
  });

  it("renders a per-change action beside its own pill, and a per-event action for the caller", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Call summary."
        isAgentAuthored={false}
        changes={[
          {
            id: "stage",
            fieldLabel: "Stage",
            oldValue: "Proposal sent",
            newValue: "Negotiating",
            action: <button type="button">Revert this change</button>,
          },
        ]}
        action={<button type="button">Revert record to this version</button>}
      />,
    );
    expect(
      screen.getByTestId("activity-change-stage").querySelector("button"),
    ).toHaveTextContent("Revert this change");
    expect(
      screen.getByRole("button", { name: "Revert record to this version" }),
    ).toBeInTheDocument();
  });

  it("takes a caller-supplied test id, so several events on one page stay addressable", () => {
    render(
      <ActivityEvent
        hand={PERSON_HAND}
        timestamp="2026-09-11T09:14:00Z"
        body="Good."
        isAgentAuthored={false}
        data-testid="activity-event-version-abc123"
      />,
    );
    expect(screen.getByTestId("activity-event-version-abc123")).toBeInTheDocument();
    expect(screen.queryByTestId("activity-event")).not.toBeInTheDocument();
  });
});
