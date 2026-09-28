/**
 * The chip row and its condition popover: docs/DESIGN.md 7.4's sentence chips over a flat AND,
 * with the tree still reachable behind `Advanced`.
 *
 * **What this file is about, and what it deliberately is not.** It asserts the three things the
 * chips are: the **sentence** (through the one display vocabulary, never a word written at a call
 * site), the **commit boundary** (Apply, Enter, close — and at once for a control that cannot be
 * half-typed), and the **AST** each edit produces, which is the chips' own invariant:
 * one chip is a bare condition, two are an `and`, and "Exclude matches" is a `not` node. It does
 * **not** assert that an incomplete condition reaches no network — that needs a request to
 * watch, and it lives in `TableView.incompleteFilter.test.tsx` for the reason that file's
 * header gives.
 *
 * **No geometry.** 7.4's 28px chip is a class, and `getBoundingClientRect` returns zeroes under
 * jsdom (AGENTS.md, Traps), so nothing here claims to measure a pixel.
 */
import { useState } from "react";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";

import { renderWithProviders } from "../test/renderWithProviders";
import {
  filterBuilderFields,
  filterBuilderSystemFields,
} from "../filters/__fixtures__/filterBuilderObjectType";
import type { FilterNode } from "../filters/types";
import { FilterChipRow, type FilterChipError } from "./FilterChipRow";

// `owner` and `created_by` are `user_ref` fields, whose value widget fetches the principal
// directory the moment the field is chosen — the same handler
// `FilterBuilder.test.tsx` installs, for the same reason.
const server = setupServer(
  http.get("/api/v1/principals/directory", () => HttpResponse.json({ principals: [] })),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/**
 * The row is a controlled component — it holds no filter state, by design — so the harness has
 * to feed each change back down, exactly as `TableView` does. A spy alone would leave the row
 * rendering the filter it started with, and every assertion about a *second* edit would then be
 * asserting against a screen the product never shows.
 */
function renderRow(initialFilter: FilterNode | null, error: FilterChipError | null = null) {
  const onChange = vi.fn<(next: FilterNode | null) => void>();

  function Harness() {
    const [filter, setFilter] = useState<FilterNode | null>(initialFilter);
    return (
      <FilterChipRow
        fields={filterBuilderFields}
        systemFields={filterBuilderSystemFields}
        filter={filter}
        onChange={(next) => {
          onChange(next);
          setFilter(next);
        }}
        error={error}
      />
    );
  }

  renderWithProviders(<Harness />);
  return onChange;
}

/** The panel `Popover` mounts while a chip is open. One chip is open at a time by construction:
 * opening a second one's trigger is a click outside the first, which dismisses it. */
function panel() {
  return screen.getByTestId("condition-popover");
}

describe("FilterChipRow: a condition renders as a sentence (docs/DESIGN.md 7.4)", () => {
  it("writes the operator through the display vocabulary and the value through its option label", () => {
    renderRow({ field: "status", op: "neq", value: "blocked" });

    // `neq` -> "is not" and `blocked` -> "Blocked": neither word is written in this component.
    expect(screen.getByTestId("filter-chip-0")).toHaveTextContent("Status is not Blocked");
  });

  it("reads a date's comparison chronologically", () => {
    renderRow({ field: "target_date", op: "lt", value: "@today" });

    // The same `lt` reads "is less than" on `priority`, which is the whole reason
    // `operatorWord` takes a field type.
    expect(screen.getByTestId("filter-chip-0")).toHaveTextContent("Target Date is before @today");
  });

  it("reads a number's comparison as quantity", () => {
    renderRow({ field: "priority", op: "lt", value: 5 });

    expect(screen.getByTestId("filter-chip-0")).toHaveTextContent("Priority is less than 5");
  });

  it("opens with the negation word when the tree wraps the condition in `not`", () => {
    renderRow({ not: { field: "status", op: "eq", value: "active" } });

    // NOT `Status is not Active`: that sentence is `neq`, a different tree, and two chips that
    // read alike while sending different JSON is the failure the chip sentences must avoid. The
    // word itself is `ui/vocabulary.ts`'s, not this component's.
    expect(screen.getByTestId("filter-chip-0")).toHaveTextContent("Except where Status is Active");
  });

  it("names each `×` by the filter it removes (docs/DESIGN.md 10)", () => {
    renderRow({ and: [
      { field: "status", op: "eq", value: "active" },
      { field: "title", op: "contains", value: "Alpha" },
    ] });

    expect(
      screen.getByRole("button", { name: "Remove filter: Status is Active" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Remove filter: Title contains Alpha" }),
    ).toBeInTheDocument();
  });
});

describe("FilterChipRow: the AST a chip serialises to is the AST the builder sent", () => {
  it("commits ONE chip as a bare condition, not as a one-element `and`", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Field"), "status");
    await user.selectOptions(within(panel()).getByLabelText("Status value"), "active");

    expect(onChange).toHaveBeenLastCalledWith({ field: "status", op: "eq", value: "active" });
  });

  it("ANDs a second chip into the tree", async () => {
    const user = userEvent.setup();
    const onChange = renderRow({ field: "status", op: "eq", value: "active" });

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Field"), "priority");
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "is at least");
    await user.type(within(panel()).getByLabelText("Priority value"), "5");
    await user.click(within(panel()).getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenLastCalledWith({
      and: [
        { field: "status", op: "eq", value: "active" },
        { field: "priority", op: "gte", value: 5 },
      ],
    });
  });

  it("wraps the condition in `not` when Exclude matches is switched on", async () => {
    const user = userEvent.setup();
    const onChange = renderRow({ field: "status", op: "eq", value: "active" });

    await user.click(screen.getByRole("button", { name: "Status is Active" }));
    await user.click(within(panel()).getByRole("switch", { name: "Exclude matches" }));

    expect(onChange).toHaveBeenLastCalledWith({
      not: { field: "status", op: "eq", value: "active" },
    });
  });

  it("removes a chip, leaving the remaining condition bare", async () => {
    const user = userEvent.setup();
    const onChange = renderRow({ and: [
      { field: "status", op: "eq", value: "active" },
      { field: "title", op: "contains", value: "Alpha" },
    ] });

    await user.click(screen.getByRole("button", { name: "Remove filter: Status is Active" }));

    expect(onChange).toHaveBeenLastCalledWith({ field: "title", op: "contains", value: "Alpha" });
  });

  it("removes the last chip back to no filter at all", async () => {
    const user = userEvent.setup();
    const onChange = renderRow({ field: "status", op: "eq", value: "active" });

    await user.click(screen.getByRole("button", { name: "Remove filter: Status is Active" }));

    expect(onChange).toHaveBeenLastCalledWith(null);
  });
});

describe("FilterChipRow: the commit boundary", () => {
  it("commits nothing while a value is being typed, and commits it on Apply", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "contains");
    await user.type(within(panel()).getByLabelText("Title value"), "Alpha");

    // Five characters, five complete conditions, and no commits: that is what the explicit
    // boundary buys instead of a debounce.
    expect(onChange).not.toHaveBeenCalled();

    await user.click(within(panel()).getByRole("button", { name: "Apply" }));

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ field: "title", op: "contains", value: "Alpha" });
  });

  it("commits on Enter", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "contains");
    await user.type(within(panel()).getByLabelText("Title value"), "Alpha{Enter}");

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ field: "title", op: "contains", value: "Alpha" });
  });

  it("commits on close, Escape included", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "contains");
    await user.type(within(panel()).getByLabelText("Title value"), "Alpha");
    await user.keyboard("{Escape}");

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith({ field: "title", op: "contains", value: "Alpha" });
  });

  it("commits a select the moment it changes, with no Apply at all", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Field"), "status");
    // Choosing the field alone commits nothing: the condition has no value yet.
    expect(onChange).not.toHaveBeenCalled();

    await user.selectOptions(within(panel()).getByLabelText("Status value"), "blocked");

    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("replaces the chip `+ Add filter` added rather than adding a second one", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Field"), "status");
    await user.selectOptions(within(panel()).getByLabelText("Status value"), "active");
    // Changing one's mind inside the same popover. A row that appended on every commit would
    // now be filtering on both answers at once.
    await user.selectOptions(within(panel()).getByLabelText("Status value"), "blocked");

    expect(onChange).toHaveBeenLastCalledWith({ field: "status", op: "eq", value: "blocked" });
  });

  it("commits an operator that takes no value the moment it is chosen", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "is blank");

    expect(onChange).toHaveBeenCalledWith({ field: "title", op: "is_null" });
  });

  it("applies once, not twice, when Apply is followed by the close it causes", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "contains");
    await user.type(within(panel()).getByLabelText("Title value"), "Alpha");
    await user.click(within(panel()).getByRole("button", { name: "Apply" }));

    // Apply closes the popover, which unmounts the panel, which is also a commit point. Committed
    // twice, `+ Add filter` would add the same chip twice.
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("condition-popover")).not.toBeInTheDocument();
  });

  it("holds an incomplete draft in the popover and disables Apply there (7.4)", async () => {
    const user = userEvent.setup();
    const onChange = renderRow(null);

    await user.click(screen.getByRole("button", { name: "+ Add filter" }));
    await user.selectOptions(within(panel()).getByLabelText("Field"), "priority");
    await user.selectOptions(within(panel()).getByLabelText("Operator"), "is between");
    await user.type(within(panel()).getByLabelText("Priority lower bound"), "5");

    expect(within(panel()).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(within(panel()).getByText("Fill in both ends of the range.")).toBeInTheDocument();

    await user.keyboard("{Escape}");
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("FilterChipRow: the grammar stays reachable behind Advanced", () => {
  it("renders a filter that is not a flat AND as one `Advanced filter` chip", () => {
    renderRow({
      and: [
        { field: "status", op: "eq", value: "active" },
        { or: [
          { field: "title", op: "contains", value: "Alpha" },
          { field: "priority", op: "gt", value: 5 },
        ] },
      ],
    });

    // No sentence chip claims any part of it: a row that showed `Status is Active` beside an
    // `Advanced filter` chip would be showing one branch of the tree twice.
    expect(screen.queryByTestId("filter-chip-0")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "+ Add filter" })).not.toBeInTheDocument();
    expect(screen.getByTestId("filter-chip-advanced")).toHaveTextContent("Advanced filter");
  });

  it("opens the tree builder from that chip, seeded with the loaded tree", async () => {
    const user = userEvent.setup();
    renderRow({
      or: [
        { field: "title", op: "contains", value: "Alpha" },
        { field: "priority", op: "gt", value: 5 },
      ],
    });

    await user.click(screen.getByRole("button", { name: "Advanced filter" }));

    const builder = screen.getByTestId("filter-builder");
    expect(within(builder).getByLabelText("Group type")).toHaveValue("or");
    expect(within(builder).getAllByLabelText("Field")).toHaveLength(2);
  });

  it("keeps an `Advanced` chip beside the sentence chips when the filter IS a flat AND", async () => {
    const user = userEvent.setup();
    renderRow({ field: "status", op: "eq", value: "active" });

    expect(screen.getByTestId("filter-chip-0")).toHaveTextContent("Status is Active");
    await user.click(screen.getByRole("button", { name: "Advanced" }));

    expect(screen.getByTestId("filter-builder")).toBeInTheDocument();
  });
});

describe("FilterChipRow: a refused value is shown against its own chip", () => {
  const refusal: FilterChipError = {
    message: "Invalid date value for field 'target_date': notadate",
    fieldKey: "target_date",
  };

  it("shows the server's message in the popover of the chip that carries the field", async () => {
    const user = userEvent.setup();
    renderRow(
      { and: [
        { field: "status", op: "eq", value: "active" },
        { field: "target_date", op: "gt", value: "notadate" },
      ] },
      refusal,
    );

    await user.click(screen.getByRole("button", { name: /^Target Date is after notadate/ }));

    expect(within(panel()).getByRole("alert")).toHaveTextContent(refusal.message);
  });

  it("says so in the offending chip's accessible name, not in colour alone (docs/DESIGN.md 10)", () => {
    renderRow({ field: "target_date", op: "gt", value: "notadate" }, refusal);

    expect(
      screen.getByRole("button", { name: `Target Date is after notadate — ${refusal.message}` }),
    ).toBeInTheDocument();
  });

  it("leaves the other chips alone", async () => {
    const user = userEvent.setup();
    renderRow(
      { and: [
        { field: "status", op: "eq", value: "active" },
        { field: "target_date", op: "gt", value: "notadate" },
      ] },
      refusal,
    );

    await user.click(screen.getByRole("button", { name: "Status is Active" }));

    expect(within(panel()).queryByRole("alert")).not.toBeInTheDocument();
  });
});
