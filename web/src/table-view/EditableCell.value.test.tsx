/**
 * The table cell shows the value a person reads, not the value the database stores
 * (docs/DESIGN.md 5 and 7.7).
 *
 * **`web/e2e/table-filters.spec.ts` is the measurement**, in a real browser against a real
 * server. This file is the unit-level companion and it exists for the two things that spec cannot
 * reach: a `multi_select`, which the functional fixture has no field of, and the absent-value
 * cases, which no row in that fixture carries.
 *
 * **No pixel is claimed here.** `getBoundingClientRect` returns zeroes under jsdom (AGENTS.md,
 * Traps), so the alignment case below reads a class string exactly as `Pill.test.tsx` does for
 * 7.3's geometry. The one real measurement of a truncated cell's width against its content was
 * taken from a live page.
 */
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { FieldDoc } from "../api/objectTypes";
import type { RecordDoc } from "../api/records";
import { EMPTY_FIELD_VALUE } from "../record-detail/fieldDisplay";
import { EditableCell, type EditableCellProps } from "./EditableCell";

function makeField(overrides: Partial<FieldDoc>): FieldDoc {
  return {
    key: "value",
    name: "Value",
    type: "short_text",
    description: "A field.",
    required: false,
    unique: false,
    indexed: false,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: [],
    display_eligible: true,
    ...overrides,
  };
}

const STAGE = makeField({
  key: "stage",
  name: "Stage",
  type: "single_select",
  options: [
    { value: "prospecting", label: "Prospecting", description: "First contact made." },
    { value: "negotiating", label: "Negotiating", description: "Terms under discussion." },
  ],
});

const TAGS = makeField({
  key: "tags",
  name: "Tags",
  type: "multi_select",
  options: [
    { value: "urgent", label: "Urgent", description: "Needs attention now." },
    { value: "renewal", label: "Renewal", description: "An existing customer." },
  ],
});

/** `canWrite` is left unset, which is the writable path: `EditableCell` reads it as `=== false`. */
const table = { options: { meta: {} } } as unknown as EditableCellProps["table"];

function renderCell(field: FieldDoc, value: unknown) {
  const record = { key: "FLT-1", version: 1, data: { [field.key]: value } } as unknown as RecordDoc;
  render(<EditableCell field={field} record={record} table={table} />);
  return screen.getByRole("button", { name: `Edit ${field.name} for FLT-1` });
}

describe("EditableCell: select values render as pills (docs/DESIGN.md 7.3)", () => {
  it("renders one pill carrying the option's label, never its stored key", () => {
    const cell = renderCell(STAGE, "negotiating");
    const pills = within(cell).getAllByTestId("pill");
    expect(pills).toHaveLength(1);
    expect(pills[0]).toHaveTextContent("Negotiating");
  });

  it("renders one pill per value for a multi_select", () => {
    const cell = renderCell(TAGS, ["urgent", "renewal"]);
    expect(within(cell).getAllByTestId("pill").map((pill) => pill.textContent)).toEqual([
      "Urgent",
      "Renewal",
    ]);
  });

  it("gives the same option key the same tone wherever it is rendered (the key alone)", () => {
    const first = renderCell(STAGE, "negotiating");
    const second = renderCell(TAGS, ["negotiating"]);
    expect(within(second).getByTestId("pill").className).toBe(
      within(first).getByTestId("pill").className,
    );
  });

  it("falls back to the em dash rather than an empty pill when there is no value", () => {
    const cell = renderCell(STAGE, null);
    expect(within(cell).queryByTestId("pill")).toBeNull();
    expect(cell).toHaveTextContent(EMPTY_FIELD_VALUE);
  });

  it("falls back to the em dash for a multi_select holding nothing", () => {
    const cell = renderCell(TAGS, []);
    expect(within(cell).queryByTestId("pill")).toBeNull();
    expect(cell).toHaveTextContent(EMPTY_FIELD_VALUE);
  });

  /**
   * docs/DESIGN.md 10: the cell is a click-to-edit button for every editable type,
   * and a pill inside it is a `<span>`. A nested interactive element inside a button is invalid
   * markup and unreachable by keyboard, so the pill must never become one.
   */
  it("puts no second interactive element inside the cell's button", () => {
    const cell = renderCell(STAGE, "negotiating");
    expect(within(cell).queryAllByRole("button")).toHaveLength(0);
    expect(within(cell).queryAllByRole("link")).toHaveLength(0);
  });
});

describe("EditableCell: numbers, dates and timestamps (docs/DESIGN.md 5)", () => {
  it("groups an integer's separators", () => {
    expect(renderCell(makeField({ name: "Amount", type: "integer" }), 68000)).toHaveTextContent(
      "68,000",
    );
  });

  it("honors a decimal's own scale", () => {
    const field = makeField({ name: "Rate", type: "decimal", config: { scale: 2 } });
    expect(renderCell(field, 1.5)).toHaveTextContent("1.50");
  });

  it("writes a date in words, outside the current year with its year", () => {
    const cell = renderCell(makeField({ name: "Due", type: "date" }), "2021-03-05");
    expect(cell).toHaveTextContent("5 Mar 2021");
  });

  it("writes a timestamp through the one timestamp formatter", () => {
    // The day is the reader's local day, so a machine east of UTC+12 reads the 6th. Asserting
    // the exact weekday here would be asserting the test machine's timezone; what this case is
    // about is that the stored ISO string is not what reaches the screen.
    const cell = renderCell(makeField({ name: "Touched at", type: "datetime" }), "2021-03-05T12:00:00Z");
    expect(cell.textContent).not.toBe("2021-03-05T12:00:00Z");
    expect(cell.textContent).toMatch(/^[56] Mar 2021$/);
  });

  it("leaves an absent number as the em dash rather than as an empty string", () => {
    expect(renderCell(makeField({ name: "Amount", type: "integer" }), null)).toHaveTextContent(
      EMPTY_FIELD_VALUE,
    );
  });
});

describe("EditableCell: the full stored value on hover", () => {
  it("carries the stored text on the cell, which is what a truncated cell hides", () => {
    const long = "A long note, several words longer than the column it has to fit inside.";
    expect(renderCell(makeField({ name: "Notes", type: "long_text" }), long)).toHaveAttribute(
      "title",
      long,
    );
  });

  it("carries the stored form, not the displayed one (5: ISO only on hover)", () => {
    expect(renderCell(makeField({ name: "Due", type: "date" }), "2021-03-05")).toHaveAttribute(
      "title",
      "2021-03-05",
    );
    expect(renderCell(makeField({ name: "Amount", type: "integer" }), 68000)).toHaveAttribute(
      "title",
      "68000",
    );
  });

  it("carries no title at all when there is nothing to reveal", () => {
    expect(renderCell(makeField({ name: "Notes", type: "long_text" }), null)).not.toHaveAttribute(
      "title",
    );
    expect(renderCell(makeField({ name: "Value", type: "short_text" }), "")).not.toHaveAttribute(
      "title",
    );
  });
});

/**
 * Fences, not measurements (AGENTS.md, Traps): both guard behaviour the value formatting
 * deliberately does not alter, so neither could fail on its account.
 */
describe("EditableCell: what value formatting deliberately does not change", () => {
  it("still does not render markdown in a one-line cell", () => {
    const cell = renderCell(makeField({ name: "Notes", type: "long_text" }), "**not bold**");
    expect(cell).toHaveTextContent("**not bold**");
    expect(cell.querySelector("strong")).toBeNull();
  });

  it("still right-aligns a number and left-aligns everything else", () => {
    expect(renderCell(makeField({ name: "Amount", type: "integer" }), 1).className).toContain(
      "text-right",
    );
    expect(renderCell(makeField({ name: "Value", type: "short_text" }), "x").className).toContain(
      "text-left",
    );
  });
});
