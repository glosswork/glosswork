/**
 * The two filter inputs that commit something other than their own text, **typed key by key**.
 *
 * `userEvent.type` sends one key at a time and re-renders between them, which is the only way
 * these two could ever fail: each was (or would have been) a controlled input drawn from the
 * committed value, and a value set whole never passes through the state that loses a character.
 *
 * The harness holds the committed value in state exactly as `ConditionPopover` and
 * `ConditionRow` do, so the input is re-rendered from what it committed after every key.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import type { FilterableField } from "./filterableFields";
import { ValueInput } from "./ValueInput";

const TEXT_FIELD: FilterableField = {
  key: "variant",
  name: "Variant",
  type: "short_text",
  operators: ["eq", "in"],
};
const RELATION_FIELD: FilterableField = {
  key: "blocks",
  name: "Blocks",
  type: "relation",
  operators: ["linked_to", "linked_to_any"],
};
const DATETIME_FIELD: FilterableField = {
  key: "last_used",
  name: "Last used",
  type: "datetime",
  operators: ["eq", "lt", "lte", "between"],
};
const DATE_FIELD: FilterableField = {
  key: "due",
  name: "Due",
  type: "date",
  operators: ["eq", "lt"],
};

interface HarnessProps {
  field: FilterableField;
  op: string;
  initial?: unknown;
}

/** Every value the input committed, in order, so a test can read the last and count them. */
let commits: unknown[] = [];

function Harness({ field, op: initialOp, initial }: HarnessProps) {
  const [value, setValue] = useState<unknown>(initial);
  const [op, setOp] = useState(initialOp);
  return (
    <>
      <ValueInput
        field={field}
        op={op}
        value={value}
        onChange={(next) => {
          commits.push(next);
          setValue(next);
        }}
      />
      {/* What both real callers do on an operator change: new operator, value reset. */}
      {field.operators.map((candidate) => (
        <button
          key={candidate}
          type="button"
          onClick={() => {
            setOp(candidate);
            setValue(undefined);
          }}
        >
          {`op ${candidate}`}
        </button>
      ))}
      <button type="button" onClick={() => setValue(["x", "y"])}>
        load list
      </button>
      <button type="button" onClick={() => setValue("@today-7d")}>
        load token
      </button>
      <output data-testid="committed">{JSON.stringify(value) ?? "undefined"}</output>
    </>
  );
}

const originalTz = process.env.TZ;
beforeEach(() => {
  commits = [];
  process.env.TZ = "America/New_York";
});
afterEach(() => {
  if (originalTz === undefined) delete process.env.TZ;
  else process.env.TZ = originalTz;
});

describe("the comma list, typed key by key", () => {
  it.each([
    ["a text field", TEXT_FIELD, "in", "Variant value"],
    ["a relation field", RELATION_FIELD, "linked_to_any", "Blocks value"],
  ])("keeps the comma and commits two values for %s", async (_name, field, op, label) => {
    const user = userEvent.setup();
    render(<Harness field={field} op={op} />);
    const box = screen.getByLabelText(label);

    await user.type(box, "v1, v2");

    expect(box).toHaveValue("v1, v2");
    expect(commits.at(-1)).toEqual(["v1", "v2"]);
    expect(screen.getByTestId("committed")).toHaveTextContent('["v1","v2"]');
  });

  it("commits the list as it stands after every key", async () => {
    const user = userEvent.setup();
    render(<Harness field={TEXT_FIELD} op="in" />);
    await user.type(screen.getByLabelText("Variant value"), "a,b");
    expect(commits).toEqual([["a"], ["a"], ["a", "b"]]);
  });

  it("re-reads a committed list that did not come from typing", async () => {
    const user = userEvent.setup();
    render(<Harness field={TEXT_FIELD} op="in" />);
    const box = screen.getByLabelText("Variant value");
    await user.type(box, "v1, v2");
    await user.click(screen.getByRole("button", { name: "load list" }));
    expect(box).toHaveValue("x, y");
  });

  it("shows a list it was opened with", () => {
    render(<Harness field={TEXT_FIELD} op="in" initial={["v1", "v2"]} />);
    expect(screen.getByLabelText("Variant value")).toHaveValue("v1, v2");
  });
});

describe("the date box, typed key by key", () => {
  it("ends holding the typed day and commits one value, the first second of the viewer's day", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lt" />);
    const box = screen.getByLabelText("Last used value");

    await user.type(box, "2026-01-07");

    expect(box).toHaveValue("2026-01-07");
    // Nine keys commit nothing; the tenth commits the instant. One value, not ten.
    const sent = commits.filter((value) => value !== undefined);
    expect(sent).toEqual(["2026-01-07T05:00:00Z"]);
    expect(commits).toHaveLength(10);
    expect(screen.getByTestId("date-value-hint")).toHaveTextContent(
      "A date like 2026-01-07, or a relative date like @today-7d.",
    );
  });

  it("keeps a displayed-format date in the box, commits nothing and says what is accepted", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lt" />);
    const box = screen.getByLabelText("Last used value");

    await user.type(box, "7 Jan 2026");

    expect(box).toHaveValue("7 Jan 2026");
    expect(commits.every((value) => value === undefined)).toBe(true);
    expect(box).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByTestId("date-value-hint")).toHaveTextContent(
      "Not a date this filter understands. Use 2026-01-07 or @today-7d.",
    );
  });

  it("commits a day as typed on a date field", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATE_FIELD} op="lt" />);
    await user.type(screen.getByLabelText("Due value"), "2026-01-07");
    expect(commits.at(-1)).toBe("2026-01-07");
  });

  it("passes a relative token through as typed, every key of it", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lt" />);
    const box = screen.getByLabelText("Last used value");
    await user.type(box, "@today-7d");
    expect(box).toHaveValue("@today-7d");
    expect(commits.at(-1)).toBe("@today-7d");
    expect(commits[0]).toBe("@");
  });

  it("holds a bare day back for 'is' and says to use a range", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="eq" />);
    await user.type(screen.getByLabelText("Last used value"), "2026-01-07");
    expect(commits.every((value) => value === undefined)).toBe(true);
    expect(screen.getByTestId("date-value-hint")).toHaveTextContent(
      '"is" and "is not" need an exact time here. For a whole day, use "is between" with the same day twice.',
    );
  });

  it("re-derives the instant from the typed day when the operator changes", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lt" />);
    const box = screen.getByLabelText("Last used value");
    await user.type(box, "2026-01-07");
    expect(screen.getByTestId("committed")).toHaveTextContent("2026-01-07T05:00:00Z");

    await user.click(screen.getByRole("button", { name: "op lte" }));

    expect(box).toHaveValue("2026-01-07");
    expect(screen.getByTestId("committed")).toHaveTextContent("2026-01-08T04:59:59Z");

    await user.click(screen.getByRole("button", { name: "op eq" }));
    expect(box).toHaveValue("2026-01-07");
    expect(screen.getByTestId("committed")).toHaveTextContent("undefined");
  });

  it("writes a picked day into the box and commits it", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lte" />);
    const picker = screen.getByLabelText("Last used value: pick a day");
    // jsdom's date input takes its value whole, as a native picker delivers it.
    await user.type(picker, "2026-01-07");
    expect(screen.getByLabelText("Last used value")).toHaveValue("2026-01-07");
    expect(commits.at(-1)).toBe("2026-01-08T04:59:59Z");
  });

  it("opens a boundary instant as its day and any other as stored", () => {
    const { unmount } = render(
      <Harness field={DATETIME_FIELD} op="lt" initial="2026-01-07T05:00:00Z" />,
    );
    expect(screen.getByLabelText("Last used value")).toHaveValue("2026-01-07");
    unmount();
    render(<Harness field={DATETIME_FIELD} op="lt" initial="2026-01-06T18:30:00Z" />);
    expect(screen.getByLabelText("Last used value")).toHaveValue("2026-01-06T18:30:00Z");
  });

  it("re-reads a committed value that did not come from typing", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="lt" />);
    const box = screen.getByLabelText("Last used value");
    await user.type(box, "2026-01-07");
    await user.click(screen.getByRole("button", { name: "load token" }));
    expect(box).toHaveValue("@today-7d");
  });

  it("gives each end of a range its own second of the day, under one hint", async () => {
    const user = userEvent.setup();
    render(<Harness field={DATETIME_FIELD} op="between" />);
    await user.type(screen.getByLabelText("Last used lower bound"), "2026-01-07");
    await user.type(screen.getByLabelText("Last used upper bound"), "2026-01-07");
    expect(commits.at(-1)).toEqual(["2026-01-07T05:00:00Z", "2026-01-08T04:59:59Z"]);
    expect(screen.getAllByTestId("date-value-hint")).toHaveLength(1);
  });
});
