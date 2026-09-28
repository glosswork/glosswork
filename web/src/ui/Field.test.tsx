import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { inputClass } from "./classes";
import { Checkbox } from "./Checkbox";
import { Field } from "./Field";
import { Select } from "./Select";

describe("Field", () => {
  it("associates the label with the child control and renders help and error as siblings", () => {
    render(
      <div>
        <Field
          id="field-name"
          label="Field name"
          help="Agent-facing. Describe what this field means."
          error="A name is required."
        >
          <input id="field-name" className={inputClass} />
        </Field>
      </div>,
    );
    const input = screen.getByLabelText("Field name");
    expect(input.tagName).toBe("INPUT");
    expect(screen.getByText("Agent-facing. Describe what this field means.")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("A name is required.");
    // The binding constraint: Field adds no wrapper around the control.
    expect(input.parentElement).toBe(screen.getByRole("alert").parentElement);
  });

  it("renders no error paragraph when there is no error", () => {
    render(
      <Field id="plain" label="Plain">
        <input id="plain" />
      </Field>,
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("Checkbox", () => {
  it("keeps the help text out of the accessible name", () => {
    render(
      <Checkbox defaultChecked help="Off makes this field unfindable by any search mode.">
        Include in search index
      </Checkbox>,
    );
    const box = screen.getByLabelText("Include in search index");
    expect(box).toBeChecked();
    expect(
      screen.getByText("Off makes this field unfindable by any search mode."),
    ).toBeInTheDocument();
  });
});

describe("Select", () => {
  it("is a native select with its options", () => {
    render(
      <>
        <label htmlFor="type">Field type</label>
        <Select id="type" defaultValue="short_text">
          <option value="short_text">short_text</option>
          <option value="long_text">long_text</option>
        </Select>
      </>,
    );
    const select = screen.getByLabelText("Field type");
    expect(select.tagName).toBe("SELECT");
    expect(select).toHaveValue("short_text");
  });
});
