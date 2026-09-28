import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { renderWithProviders } from "../test/renderWithProviders";
import { FieldInput } from "./FieldInput";
import type { FieldDoc } from "../api/objectTypes";

const server = setupServer(
  http.get("/api/v1/principals/directory", () =>
    HttpResponse.json({
      principals: [
        { id: "p1", display_name: "Sarah Okonjo", email: "sarah@example.com", type: "user", is_active: true },
        { id: "p2", display_name: "Priya Shah", email: "priya@example.com", type: "user", is_active: true },
      ],
    }),
  ),
);

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ownerField: FieldDoc = {
  key: "owner",
  name: "Owner",
  type: "user_ref",
  description: "The principal accountable for this record.",
  required: false,
  unique: false,
  indexed: false,
  embed: false,
  default: null,
  config: {},
  position: 0,
  operators: ["eq", "neq", "is_null", "is_not_null"],
  display_eligible: false,
};

describe("FieldInput: the user_ref picker", () => {
  it("lists the directory's entries by display name, with email as secondary text", async () => {
    renderWithProviders(
      <FieldInput
        field={ownerField}
        draft=""
        onDraftChange={vi.fn()}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
        commitOnBlur={false}
        label="Owner value"
        inputClassName="in"
        selectClassName="sel"
        textareaClassName="ta"
      />,
    );

    const select = await screen.findByLabelText("Owner value");
    await waitFor(() => {
      expect(within(select).getByText("Sarah Okonjo (sarah@example.com)")).toBeInTheDocument();
    });
    expect(within(select).getByText("Priya Shah (priya@example.com)")).toBeInTheDocument();
    // The empty option is always present, same as the single_select widget's.
    expect(within(select).getByText("—")).toBeInTheDocument();
  });

  it("commits the selected principal's id, not their name", async () => {
    const user = userEvent.setup();
    const onDraftChange = vi.fn();
    renderWithProviders(
      <FieldInput
        field={ownerField}
        draft=""
        onDraftChange={onDraftChange}
        onCommit={vi.fn()}
        onCancel={vi.fn()}
        commitOnBlur={false}
        label="Owner value"
        inputClassName="in"
        selectClassName="sel"
        textareaClassName="ta"
      />,
    );

    const select = await screen.findByLabelText("Owner value");
    await waitFor(() => expect(within(select).getByText("Priya Shah (priya@example.com)")).toBeInTheDocument());

    await user.selectOptions(select, "p2");

    expect(onDraftChange).toHaveBeenCalledWith("p2");
  });
});

/**
 * `commitOnBlur` must be honoured by all six widget branches, including the fallback `<input>`:
 * hardcoding `onBlur={onCommit}` there commits on blur no matter what the caller asked for, and
 * that branch serves `short_text`, `url`, `integer`, `decimal`, `date` and `datetime`, which is
 * most of the field types a person types into.
 *
 * `record-detail/DetailsCard.tsx` passes `commitOnBlur={false}` precisely because it owns
 * explicit Save and Cancel buttons that a blur-commit pre-empts, and it once got blur-commits
 * anyway. The create dialog also needs the prop to mean what it says.
 *
 * Written as a pair, because only the pair distinguishes "the prop works" from "blur never fires
 * on this branch at all": the `true` case must still commit.
 */
describe("FieldInput: commitOnBlur is honoured by every widget, not only five of six", () => {
  const nameField: FieldDoc = {
    key: "name",
    name: "Name",
    type: "short_text",
    description: "The record's short display name.",
    required: true,
    unique: false,
    indexed: true,
    embed: false,
    default: null,
    config: {},
    position: 0,
    operators: ["eq", "neq", "contains"],
    display_eligible: true,
  };

  function renderNameInput(commitOnBlur: boolean, onCommit: () => void) {
    renderWithProviders(
      <>
        <FieldInput
          field={nameField}
          draft="Acme renewal"
          onDraftChange={() => {}}
          onCommit={onCommit}
          onCancel={() => {}}
          label="Name"
          inputClassName=""
          selectClassName=""
          textareaClassName=""
          commitOnBlur={commitOnBlur}
        />
        <button type="button">Somewhere else</button>
      </>,
    );
  }

  it("does not commit a text field on blur when commitOnBlur is false", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    renderNameInput(false, onCommit);

    await user.click(screen.getByRole("button", { name: "Somewhere else" }));

    expect(onCommit).not.toHaveBeenCalled();
  });

  it("still commits a text field on blur when commitOnBlur is true (the table cell)", async () => {
    const user = userEvent.setup();
    const onCommit = vi.fn();
    renderNameInput(true, onCommit);

    await user.click(screen.getByRole("button", { name: "Somewhere else" }));

    expect(onCommit).toHaveBeenCalledTimes(1);
  });
});
